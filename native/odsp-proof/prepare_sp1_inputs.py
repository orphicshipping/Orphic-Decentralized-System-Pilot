"""Prepare exact existing guest/descriptor test inputs without a proof claim.

Only new private output files are written. The existing guest registry and all
imported sources remain unchanged. Native build/prove/verify commands below
are future qualification inputs, not completed executions.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import json
from pathlib import Path
import struct
import sys

from qualification import REPO, check_sources, digest


def prepare(output):
    lock = check_sources()
    sys.path.insert(0, str(REPO / "src"))
    from ciw.device_boundary import PROGRAM, compute
    output = Path(output).resolve()
    output.mkdir(mode=0o700, exist_ok=False)
    descriptor = output / "descriptor.bin"
    descriptor.write_bytes(PROGRAM)
    cases = []
    for name, values in (("positive", (127, 23, -91, 1000)),
                         ("lower", (-2**40, 2**20, -2**60, 1)),
                         ("upper", (2**40, 2**20, 2**60, 2**20))):
        raw = b"DBI1" + bytes.fromhex("a7"*32) + struct.pack("<qqqQ", *values)
        expected = compute(raw)
        (output / (name + ".input.bin")).write_bytes(raw)
        (output / (name + ".output.bin")).write_bytes(expected)
        proof = str(output / (name + ".proof.bin"))
        verify = ["SP1_HOST", "verify", "VERIFIED_DEVICE_ELF", str(descriptor), proof,
                  "registered", raw.hex(), expected.hex(), "0"]
        altered = bytes([expected[0] ^ 1]) + expected[1:]
        cases.append({"name": name, "input_hex": raw.hex(), "output_hex": expected.hex(),
                      "prove_command": ["SP1_HOST", "prove", "VERIFIED_DEVICE_ELF", str(descriptor), raw.hex(), proof],
                      "verify_command": verify,
                      "wrong_output_command": verify[:-2] + [altered.hex(), "0"]})
    result = {"schema": "odsp.native-sp1-build-inputs.v1", "source_base": lock["base_revision"],
              "source_sha256": lock["files"], "sp1": lock["sp1"], "cases": cases,
              "command_placeholders": {"SP1_HOST": "independently built original zk/sp1-adapter sp1-host binary",
                                       "VERIFIED_DEVICE_ELF": "two byte-identical execution.build outputs for guest-device-boundary"},
              "environment": {"SP1_PROVER": "cpu", "NETWORK_PRIVATE_KEY": "must_be_absent",
                              "SP1_PRIVATE_KEY": "must_be_absent"},
              "native_commands_executed": False, "registry_mutated": False,
              "native_sp1_verified": False, "execution_authorized": False}
    result["prepared_files_sha256"] = {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}
    (output / "build-inputs.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.output)
    print(json.dumps({"prepared_cases": len(result["cases"]), "native_sp1_verified": False,
                      "registry_mutated": False}, sort_keys=True))
