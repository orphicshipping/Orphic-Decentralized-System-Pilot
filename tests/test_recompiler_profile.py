"""Pinned NSR subprocess qualification; synthetic inputs, no live source claims."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import ciw
import pytest

from ciw import recompiler_profile as profile
from ciw.adapters.protocol import AdapterRefusal
from ciw.coordinator import _Lease

PACKAGE = Path(ciw.__file__).resolve().parents[1]
# Independently stated synthetic two-compartment fixture, not kernel source.
INPUT = {"state_id": "fixture:unreconciled-mass-pair", "estimate": [62., 41.],
    "covariance": [[4., 0.], [0., 4.]], "state_labels": ["mass_1", "mass_2"],
    "state_units": ["kg", "kg"], "frame_ref": "fixture:two-compartment-system",
    "constraints": {"constraint_id": "fixture:declared-closed-mass-100kg-v1", "coefficients": [[1., 1.]],
                    "rhs": [100.], "row_units": ["kg"], "coefficient_policy": "declared_exact"},
    "crosscov_policy": "declared_zero", "max_normalized_residual": 10.828,
    "evidence_refs": ["fixture:synthetic-observation-pair"], "execution_id": "example:affine-exact-001"}


@pytest.fixture(scope="module")
def checkout():
    raw = os.environ.get("CIW_NSR_CHECKOUT")
    if not raw:
        pytest.skip("CIW_NSR_CHECKOUT must select the existing daf43fc pinned NSR checkout")
    path = Path(raw).absolute()
    assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == profile.PIN
    return path


@pytest.fixture(scope="module")
def host(checkout):
    return profile.RecompilerHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner")


def invoke(host, name="ncp_recompiler_reconcile", **args):
    return profile.invoke_recompiler(host, name, args or {"request": deepcopy(INPUT)})


def digest(value):
    return sha256(profile.canonical(value)).hexdigest()


def test_discovery_has_three_bounded_non_authorizing_tools(host):
    descriptions = host.descriptions()
    assert {row["name"] for row in descriptions} == set(profile.TOOLS)
    assert all(row["annotations"]["readOnlyHint"] and not row["annotations"]["destructiveHint"] for row in descriptions)
    description = profile.invoke_recompiler(host, "ncp_recompiler_describe", {})
    assert description["runtime"]["revision"] == profile.PIN
    assert description["runtime"]["module"] == "cbsr"
    assert "repository_root" not in description["runtime"]
    assert description["source_authenticated"] is False
    assert description["authorizes_execution"] is description["authorizes_state_admission"] is False
    assert description["limits"]["kernel_cpu_seconds"] == 3
    assert description["limits"]["kernel_address_space_bytes"] == 256 * 1024 * 1024
    assert host.list_resources() == [] and host.resource_templates == []


@pytest.mark.parametrize("mutation,expected", [
    ({}, "accepted"),
    ({"max_normalized_residual": .1}, "held"),
    ({"crosscov_policy": "unknown"}, "refused"),
    ({"covariance": [[-1., 0.], [0., 4.]]}, "refused"),
    ({"state_labels": ["duplicate", "duplicate"]}, "invalid_contract"),
    ({"estimate": [True, 41.]}, "invalid_contract"),
    ({"unsupported": "never an import or executable selector"}, "invalid_contract"),
])
def test_scientific_statuses_and_invalid_contract_are_preserved(host, mutation, expected):
    request = {**deepcopy(INPUT), **mutation}
    value = invoke(host, request=request)
    assert value["outcome"] == expected
    assert value["source_binding_verification"] == "caller_assertion_only"
    assert value["authorizes_execution"] is value["authorizes_state_admission"] is False
    if expected == "invalid_contract":
        assert value["receipt"] is None
    else:
        assert value["receipt"]["status"] == expected
        assert value["receipt"]["execution_id"] == request["execution_id"]
        assert value["receipt"]["request"] == request
        assert value["receipt"]["claims"] == profile.CLAIMS
        assert value["receipt"]["candidate"] == {"estimate": request["estimate"], "covariance": request["covariance"]}


@pytest.mark.parametrize("mutation,exit_code,outcome", [
    ({"max_normalized_residual": .1}, 3, "held"),
    ({"crosscov_policy": "unknown"}, 3, "refused"),
    ({"state_labels": ["duplicate", "duplicate"]}, 2, "invalid_contract"),
])
def test_raw_cli_nonzero_scientific_status_is_not_misclassified(host, checkout, tmp_path, mutation, exit_code, outcome):
    request = {**deepcopy(INPUT), **mutation}
    code = "import runpy,sys; sys.path.insert(0,sys.argv[1]); runpy.run_module('cbsr',run_name='__main__')"
    raw = subprocess.run([sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(tmp_path / "bytecode"),
                          "-c", code, str(checkout / "src")], input=profile.canonical(request),
                         cwd=tmp_path, capture_output=True, timeout=10)
    assert raw.returncode == exit_code
    if exit_code == 3:
        assert json.loads(raw.stdout)["status"] == outcome
    else:
        assert json.loads(raw.stderr)["error"] == "invalid_contract"
    assert invoke(host, request=request)["outcome"] == outcome


def test_exact_replay_keeps_full_receipt_and_new_execution_changes_only_occurrence_identity(host):
    first = invoke(host)["receipt"]
    second = invoke(host)["receipt"]
    assert profile.canonical(first) == profile.canonical(second)
    assert first["reconciled"] == {"estimate": [60.5, 39.5], "covariance": [[2., -2.], [-2., 2.]]}
    changed = deepcopy(INPUT)
    changed["execution_id"] = "fixture:second-occurrence"
    later = invoke(host, request=changed)["receipt"]
    assert later["result_id"] != first["result_id"]
    assert later["numerical_result_id"] == first["numerical_result_id"]
    verified = invoke(host, "ncp_recompiler_verify_receipt", receipt=first)
    assert verified["outcome"] == "verified"
    assert verified["replayed_execution_id"] == first["execution_id"]
    assert verified["replayed_result_id"] == first["result_id"]


def test_asserted_source_identity_is_retained_not_authenticated(host):
    request = {**deepcopy(INPUT), "source_result_id": "fixture:unverified-source", "source_result_digest": "a" * 64}
    receipt = invoke(host, request=request)["receipt"]
    verified = invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)
    assert verified["outcome"] == "verified"
    assert verified["source_authenticated"] is False
    assert verified["source_binding_verification"] == receipt["source_binding_verification"] == "caller_assertion_only"
    assert receipt["request"]["source_result_digest"] == "a" * 64


@pytest.mark.parametrize("field,mutation", [
    ("reconciled", {"estimate": [50., 50.], "covariance": [[2., -2.], [-2., 2.]]}),
    ("reason", "fabricated decision"), ("request_digest", "0" * 64),
    ("numerical_result_id", "sha256:" + "0" * 64), ("result_id", "sha256:" + "0" * 64),
    ("execution_id", "fixture:substituted-execution"), ("input_state_id", "fixture:substituted-state"),
    ("evidence_refs", ["fixture:unbound-evidence"]), ("residual_rank", 2),
    ("rational_constraint_residual_zero", False),
])
def test_full_receipt_fields_are_compared_even_if_attacker_rehashes_content(host, field, mutation):
    receipt = invoke(host)["receipt"]
    receipt[field] = mutation
    if field != "result_id":
        receipt["result_id"] = "sha256:" + digest({k: v for k, v in receipt.items() if k != "result_id"})
    value = invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)
    assert value["outcome"] == "mismatch"
    assert value["canonical_receipt_match"] is False


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("arithmetic"), lambda r: r.update(extra=True),
    lambda r: r.update(claims={**profile.CLAIMS, "physical_validity": True}),
    lambda r: r.update(source_binding_verification="authenticated"),
    lambda r: r.update(schema="not-nsr"), lambda r: r.update(status="qualified"),
    lambda r: r.update(result_id=True), lambda r: r.update(request=[]),
])
def test_receipt_schema_or_claim_tampering_is_invalid_contract(host, mutation):
    receipt = invoke(host)["receipt"]
    mutation(receipt)
    value = invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)
    assert value["outcome"] == "invalid_contract"
    assert value["canonical_receipt_match"] is False


def test_invalid_retained_request_cannot_become_verified_by_rehashing(host):
    receipt = invoke(host)["receipt"]
    del receipt["request"]["execution_id"]
    receipt["request_digest"] = digest(receipt["request"])
    receipt["result_id"] = "sha256:" + digest({k: v for k, v in receipt.items() if k != "result_id"})
    value = invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)
    assert value["request_digest_match"] is value["result_digest_match"] is True
    assert value["outcome"] == "invalid_contract" and value["canonical_receipt_match"] is False


@pytest.mark.parametrize("field", ["candidate", "constraints"])
def test_nested_unknown_fields_cannot_be_verified_by_rehashing(host, field):
    receipt = invoke(host)["receipt"]
    if field == "candidate":
        receipt["candidate"]["unknown"] = "synthetic-extra"
    else:
        receipt["request"]["constraints"]["unknown"] = "synthetic-extra"
    receipt["request_digest"] = digest(receipt["request"])
    receipt["result_id"] = "sha256:" + digest({k: v for k, v in receipt.items() if k != "result_id"})
    value = invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)
    assert value["outcome"] == ("mismatch" if field == "candidate" else "invalid_contract")
    assert value["canonical_receipt_match"] is False


def test_replayed_receipt_is_complete_and_not_shallow_alias(host):
    request = deepcopy(INPUT)
    value = invoke(host, request=request)
    request["estimate"][0] = -100
    assert value["receipt"]["request"]["estimate"][0] == 62.
    value["receipt"]["candidate"]["estimate"][0] = -200
    assert invoke(host)["receipt"]["candidate"]["estimate"][0] == 62.


@pytest.mark.parametrize("name,args", [
    ("ncp_recompiler_reconcile", {"request": INPUT, "checkout": "/tmp/override"}),
    ("ncp_recompiler_reconcile", {"request": INPUT, "principal": "intruder"}),
    ("ncp_recompiler_describe", {"python_executable": "/bin/sh"}),
    ("ncp_recompiler_mutate", {}),
    ("ncp_recompiler_verify_receipt", {"result_id": "sha256:" + "0" * 64}),
])
def test_payload_cannot_select_binding_or_verify_only_an_id(host, name, args):
    assert host.call(name, args)["status"] == "refused"


def test_byte_and_complete_unicode_envelope_limits(host):
    assert host.call("ncp_recompiler_reconcile", {"request": {"huge": "x" * 65536}})["reason"] == "argument_budget_exceeded"
    request = deepcopy(INPUT)
    request["evidence_refs"] = [str(i) + "\u2603" * 250 for i in range(40)]
    assert len(profile.canonical({"request": request})) < profile.MAX_ARGUMENT
    result = host.call("ncp_recompiler_reconcile", {"request": request})
    assert result == {"status": "refused", "reason": "receipt_verification_budget_exceeded"}
    assert len(profile.canonical(host.mcp_result("ncp_recompiler_reconcile", result))) <= profile.MAX_RESPONSE
    with pytest.raises(profile.RecompilerRefusal, match="response_budget_exceeded"):
        host.mcp_result("ncp_recompiler_describe", {"status": "ok", "data": {"text": "\u2603" * 30000}})


def test_every_issued_receipt_fits_full_verify_admission_envelope(host):
    for count in (4, 7, 10):
        request = deepcopy(INPUT)
        request["evidence_refs"] = [str(i) + "x" * 3990 for i in range(count)]
        response = host.call("ncp_recompiler_reconcile", {"request": request})
        if count == 10:
            assert response == {"status": "refused", "reason": "receipt_verification_budget_exceeded"}
        else:
            assert response["status"] == "ok"
            receipt = response["data"]["receipt"]
            assert len(profile.canonical({"name": "ncp_recompiler_verify_receipt", "arguments": {"receipt": receipt}})) <= profile.MAX_ARGUMENT
            assert invoke(host, "ncp_recompiler_verify_receipt", receipt=receipt)["outcome"] == "verified"


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_payload_is_rejected_before_runtime(host, number):
    value = deepcopy(INPUT)
    value["estimate"][0] = number
    assert host.call("ncp_recompiler_reconcile", {"request": value})["status"] == "refused"


def test_runtime_pin_and_interpreter_binding_refuse_drift(checkout, tmp_path):
    clone = tmp_path / "pin"
    subprocess.run(["git", "clone", "--quiet", "--shared", str(checkout), str(clone)], check=True)
    h = profile.RecompilerHost(checkout=clone, python_executable=sys.executable, principal="fixture-owner")
    target = clone / "src/cbsr/affine.py"
    target.write_bytes(target.read_bytes() + b"\n# source mutation fixture\n")
    assert h.call("ncp_recompiler_describe", {}) == {"status": "refused", "reason": "recompiler_runtime_refused"}
    with pytest.raises(AdapterRefusal):
        profile.RecompilerHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner",
                               expected_python_sha256="0" * 64)
    with pytest.raises(profile.RecompilerRefusal):
        profile.RecompilerHost(checkout="relative", python_executable=sys.executable, principal="fixture-owner")


def test_real_concurrent_call_refuses_without_releasing_active_lease(host, monkeypatch):
    original = host._adapter._run
    entered, release = threading.Event(), threading.Event()
    result = []
    def run(code, arguments, stdin=b""):
        if code == profile._BOOTSTRAP:
            entered.set()
            assert release.wait(10)
        return original(code, arguments, stdin)
    monkeypatch.setattr(host._adapter, "_run", run)
    thread = threading.Thread(target=lambda: result.append(host.call("ncp_recompiler_reconcile", {"request": INPUT})))
    thread.start()
    try:
        assert entered.wait(5)
        assert host.call("ncp_recompiler_describe", {}) == {"status": "refused", "reason": "recompiler_busy"}
        lease = _Lease(Path(host._lease_directory.name) / "call.lease")
        try:
            assert not lease.try_acquire()
        finally:
            lease.release()
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and result[0]["data"]["outcome"] == "accepted"
    assert host.call("ncp_recompiler_describe", {})["status"] == "ok"


def test_no_kernel_import_in_parent(host):
    before = {name for name in sys.modules if name == "cbsr" or name.startswith("cbsr.")}
    invoke(host)
    assert {name for name in sys.modules if name == "cbsr" or name.startswith("cbsr.")} == before


@pytest.mark.parametrize("boundary", ["acquire", "release"])
def test_sdk_io_failure_is_sanitized_with_original_diagnostic_chain(host, monkeypatch, boundary):
    failure = OSError("synthetic private path /private/policy")
    original = _Lease.release
    def broken(self):
        if boundary == "release":
            original(self)
        raise failure
    with monkeypatch.context() as patch:
        patch.setattr(_Lease, "try_acquire" if boundary == "acquire" else "release", broken)
        with pytest.raises(profile.RecompilerRefusal, match="^recompiler_runtime_io_refused$") as caught:
            profile.invoke_recompiler(host, "ncp_recompiler_describe", {})
    assert caught.value.__cause__ is failure
    assert "/private/" not in str(caught.value)
    assert host.call("ncp_recompiler_describe", {})["status"] == "ok"


@pytest.mark.parametrize("fault", ["memory", "cpu", "wall", "output"])
def test_child_resource_faults_refuse_and_release_admission(host, monkeypatch, fault):
    # Controlled process faults test resource guards, not scientific validity.
    original = host._adapter._run
    guard = profile._BOOTSTRAP.split("root = pathlib.Path", 1)[0]
    code = {"memory": "value = bytearray(400 * 1024 * 1024)",
            "cpu": "while True: pass", "wall": "import time; time.sleep(20)",
            "output": "print('x' * 400000)"}[fault]
    def run(program, arguments, stdin=b""):
        return original(guard + code if program == profile._BOOTSTRAP else program, arguments, stdin)
    started = time.monotonic()
    with monkeypatch.context() as patch:
        patch.setattr(host._adapter, "_run", run)
        result = host.call("ncp_recompiler_reconcile", {"request": INPUT})
    assert result == {"status": "refused", "reason": "recompiler_runtime_refused"}
    assert time.monotonic() - started < 10
    assert host.call("ncp_recompiler_describe", {})["status"] == "ok"


@pytest.mark.parametrize("fault", ["OSError", "SIGINT"])
def test_live_failed_call_releases_lease_at_ownership_transfer(checkout, tmp_path, fault):
    program = r'''
import json, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ciw.recompiler_profile import RecompilerHost
cfg=json.loads(sys.argv[2])
h=RecompilerHost(checkout=cfg['checkout'],python_executable=sys.executable,principal='fixture-owner')
lease_path=str(Path(h._lease_directory.name)/'call.lease')
primary=OSError('controlled after-acquisition error')
injected=False
def wait(path):
    deadline=time.monotonic()+30
    while not Path(path).exists():
        if time.monotonic()>deadline: raise TimeoutError('fixture handshake')
        time.sleep(.005)
def trace(frame,event,arg):
    global injected
    if frame.f_code is RecompilerHost._call.__code__ and event=='line' and not injected:
        lease=frame.f_locals.get('lease')
        if lease is not None and lease.fd is not None:
            injected=True
            if cfg['fault']=='OSError': raise primary
            Path(cfg['ready']).write_text(lease_path)
            wait(cfg['resume'])
    return trace
try:
    sys.settrace(trace)
    h.call('ncp_recompiler_describe',{})
except BaseException as exc:
    result={'exception':type(exc).__name__,'same_primary':exc is primary or exc.__cause__ is primary,
        'context':None if exc.__context__ is None else type(exc.__context__).__name__}
finally:
    sys.settrace(None)
result.update(injected=injected,lease_path=lease_path)
Path(cfg['result']).write_text(json.dumps(result))
wait(cfg['stop'])
assert h.call('ncp_recompiler_describe',{})['status']=='ok'
'''
    cfg = {"checkout": str(checkout), "fault": fault, **{key: str(tmp_path / key) for key in ("ready", "resume", "result", "stop")}}
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c", program, str(PACKAGE), json.dumps(cfg)],
                             cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def wait(key):
        deadline = time.monotonic() + 20
        while not Path(cfg[key]).exists():
            if child.poll() is not None:
                out, err = child.communicate(timeout=5)
                pytest.fail(out + err)
            assert time.monotonic() < deadline
            time.sleep(.005)
        return Path(cfg[key]).read_text()
    try:
        if fault == "SIGINT":
            wait("ready")
            child.send_signal(signal.SIGINT)
        result = json.loads(wait("result"))
        assert result["injected"]
        assert result["context"] == (None if fault == "SIGINT" else "OSError")
        assert result["exception"] == ("KeyboardInterrupt" if fault == "SIGINT" else "RecompilerRefusal")
        if fault == "OSError":
            assert result["same_primary"]
        assert child.poll() is None
        probe = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from ciw.coordinator import _Lease; l=_Lease(Path(sys.argv[2])); assert l.try_acquire(); l.release()"
        observed = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(PACKAGE), result["lease_path"]],
                                  cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert observed.returncode == 0, observed.stderr
        assert child.poll() is None
        Path(cfg["stop"]).write_text("go")
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, out + err
    finally:
        Path(cfg["resume"]).write_text("go")
        Path(cfg["stop"]).write_text("go")
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_real_mcp_subprocess_reconnect_matches_sdk_receipt(host, checkout, tmp_path):
    pytest.importorskip("mcp", reason="official MCP dependency is required for external-client qualification")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    expected = invoke(host)
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import sys\nsys.path.insert(0," + repr(str(PACKAGE)) + ")\n"
        "from ciw.recompiler_profile import RecompilerHost\nfrom ciw.mcp_server import serve_sdk\n"
        "host=RecompilerHost(checkout=" + repr(str(checkout)) + ",python_executable=sys.executable,principal='fixture-owner')\n"
        "serve_sdk(host,name='odsp-nsr-fixture')\n")
    async def run():
        for _ in range(2):
            params = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(launcher)], cwd=str(tmp_path))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.discover()
                    assert len((await client.list_tools()).tools) == 3
                    result = await client.call_tool("ncp_recompiler_reconcile", {"request": INPUT})
                    assert result.structured_content["data"] == expected
                    verified = await client.call_tool("ncp_recompiler_verify_receipt", {"receipt": expected["receipt"]})
                    assert verified.structured_content["data"]["outcome"] == "verified"
                    denied = await client.call_tool("ncp_recompiler_reconcile", {"request": INPUT, "principal": "intruder"})
                    assert denied.is_error
    asyncio.run(run())
