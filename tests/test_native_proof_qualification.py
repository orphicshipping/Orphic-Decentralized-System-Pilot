"""Actual imported native kernel/commitment equivalence and retained Kani checks."""
from copy import deepcopy
import importlib.util
from itertools import product
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qualification = load("odsp_native_proof_qualification", REPO / "native/odsp-proof/qualification.py")


def artifact(name):
    path = Path(os.getenv(name, "/nonexistent"))
    if not path.is_file():
        if os.getenv("ODSP_REQUIRE_NATIVE_PROOF") == "1":
            pytest.fail(name + " must identify the actual local qualification artifact")
        pytest.skip(name + " is required for native qualification")
    return path


def test_source_binding_rejects_changed_imported_guest(tmp_path):
    lock = qualification.check_sources()
    for relative in lock["files"]:
        output = tmp_path / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, output)
    qualification.check_sources(tmp_path)
    guest = tmp_path / "instruments/execution/compute-runtime/zk/guest-device-boundary/src/kernel.rs"
    guest.write_bytes(guest.read_bytes() + b"\n// changed after qualification\n")
    with pytest.raises(ValueError, match="source binding"):
        qualification.check_sources(tmp_path)


def test_actual_kani_output_and_failure_refusals():
    result = json.loads(artifact("ODSP_KANI_RESULT").read_bytes())
    assert qualification.check_kani_result(result)["successful"] == 4
    for change in ("failed", "missing", "unresolved", "compiler", "errors"):
        changed = deepcopy(result)
        if change == "failed":
            changed["verification_results"]["summary"]["failed"] = 1
        elif change == "missing":
            changed["verification_results"]["results"].pop()
        elif change == "unresolved":
            changed["property_details"][0]["property_details"]["undetermined"] = 1
        elif change == "compiler":
            changed["tools"]["rustc"] = "unbound compiler"
        else:
            changed["error_details"] = []
        with pytest.raises(ValueError):
            qualification.check_kani_result(changed)


def test_actual_native_kernel_matches_existing_python_and_commitments():
    from ciw.device_boundary import compute
    commitments = load("odsp_existing_execution_commitments",
                       REPO / "instruments/execution/compute-runtime/execution/commitments.py")
    binary = artifact("ODSP_DEVICE_KERNEL_BINARY")
    rng = random.Random(0xDB0103)
    values = list(product((-(2**40), -1, 0, 1, 2**40), (-(2**20), -1, 0, 1, 2**20),
                          (-(2**60), -1, 0, 1, 2**60), (1, 2**20)))
    values += [(rng.randint(-2**40, 2**40), rng.randint(-2**20, 2**20),
                rng.randint(-2**60, 2**60), rng.randint(1, 2**20)) for _ in range(512)]
    for count, gain, offset, denominator in values:
        raw = b"DBI1" + rng.randbytes(32) + struct.pack("<qqqQ", count, gain, offset, denominator)
        expected = compute(raw)
        output = subprocess.run([str(binary)], input=raw, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={"LANG": "C.UTF-8"}, timeout=2, check=True).stdout
        assert output == (b"OK" + expected
                          + bytes.fromhex(commitments.commit_hex(commitments.INPUT_TAG, [raw]))
                          + bytes.fromhex(commitments.commit_hex(commitments.OUTPUT_TAG, [expected])))
    print("native_kernel_python_and_commitment_equivalence=" + str(len(values)))


def test_actual_native_kernel_rejects_invalid_wire_and_domain():
    from ciw.device_boundary import compute
    binary = artifact("ODSP_DEVICE_KERNEL_BINARY")
    valid = b"DBI1" + bytes(32) + struct.pack("<qqqQ", 0, 0, 0, 1)
    cases = [(valid[:length], 2) for length in range(68)]
    cases += [(valid + b"x", 2), (valid + b"xx", 2), (b"X" + valid[1:], 2)]
    for values in ((2**40+1, 0, 0, 1), (-2**40-1, 0, 0, 1),
                   (0, 2**20+1, 0, 1), (0, -2**20-1, 0, 1),
                   (0, 0, 2**60+1, 1), (0, 0, -2**60-1, 1),
                   (0, 0, 0, 0), (0, 0, 0, 2**20+1)):
        cases.append((b"DBI1" + bytes(32) + struct.pack("<qqqQ", *values), 3))
    for raw, code in cases:
        with pytest.raises(ValueError):
            compute(raw)
        output = subprocess.run([str(binary)], input=raw, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={"LANG": "C.UTF-8"}, timeout=2, check=True).stdout
        assert output == b"ERR" + struct.pack("<I", code)
    print("native_kernel_python_refusal_equivalence=" + str(len(cases)))


def test_sp1_preflight_is_read_only_and_does_not_claim_proof(tmp_path):
    before = qualification.digest(qualification.HERE / "source-lock.json")
    result = qualification.sp1_preflight(fork=tmp_path / "absent", rustc=None, scratch=tmp_path)
    assert result["native_sp1_verified"] is False
    assert result["registry_mutated"] is False and result["execution_authorized"] is False
    assert {row["gate"] for row in result["blockers"]} >= {
        "sp1_checkout", "succinct_compiler", "guest_dependency_lock", "device_guest_registration"}
    assert before == qualification.digest(qualification.HERE / "source-lock.json")
