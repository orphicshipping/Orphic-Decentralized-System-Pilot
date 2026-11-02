"""Real pinned TBRT numerical cases and bounded profile/replay refusal gates."""
import asyncio
from copy import deepcopy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import ciw
import pytest

from ciw import clocksync_profile as profile
from ciw.adapters.protocol import AdapterRefusal
from ciw.coordinator import _Lease

PACKAGE = Path(ciw.__file__).resolve().parents[1]
DEVICE = {"clock_id": "fixture:device", "time_scale": "device-monotonic", "unit": "s"}
REFERENCE = {"clock_id": "fixture:reference", "time_scale": "reference-monotonic", "unit": "s"}
# Independent analytical case: J=[2,3,1], delta=6.5, J C J.T=22.75.
INPUT = {
    "execution_id": "fixture:clock-occurrence-1",
    "observation": {"device_time": 103, "frame": DEVICE, "evidence_id": "fixture:timestamp-17",
        "received_at": {"value": 2000, "frame": REFERENCE},
        "known_at": {"value": 3000, "frame": {"clock_id": "fixture:knowledge", "time_scale": "other", "unit": "s"}}},
    "model": {"model_id": "fixture:clock-map", "source_frame": DEVICE, "reference_frame": REFERENCE,
        "device_origin": 100, "reference_origin": 1000, "skew": 2, "offset": .5,
        "valid_device_interval": [100, 110], "synchronization_evidence_ids": ["fixture:asserted-sync"]},
    "expected_reference": REFERENCE,
    "joint_covariance": {"order": ["device_time", "skew", "offset"],
        "matrix": [[1, .25, .5], [.25, 1, .125], [.5, .125, 4]],
        "cross_covariance_policy": "declared", "evidence_ids": ["fixture:asserted-joint-covariance"]},
}


@pytest.fixture(scope="module")
def checkout():
    raw = os.environ.get("CIW_CLOCKSYNC_CHECKOUT")
    if not raw:
        pytest.skip("CIW_CLOCKSYNC_CHECKOUT must select the existing 40507060 pinned TBRT checkout")
    path = Path(raw).absolute()
    assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == profile.PIN
    return path


@pytest.fixture(scope="module")
def host(checkout):
    with profile.ClockSyncHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner") as bound:
        yield bound


def invoke(host, name="ncp_clocksync_reconcile", **args):
    return profile.invoke_clocksync(host, name, args or ({"request": deepcopy(INPUT)} if name == "ncp_clocksync_reconcile" else {}))


@pytest.fixture(scope="module")
def receipt(host):
    return invoke(host)


def reseal(value):
    value["request_digest"] = profile._digest(value["request"])
    value["numerical_result_id"] = "sha256:" + profile._digest(value["numerical_result"])
    value["receipt_digest"] = profile._receipt_digest(value)


def test_description_is_closed_and_non_authorizing(host):
    assert {row["name"] for row in host.descriptions()} == set(profile.TOOLS)
    assert all(row["annotations"]["readOnlyHint"] and not row["annotations"]["destructiveHint"] for row in host.descriptions())
    description = invoke(host, "ncp_clocksync_describe")
    assert description["runtime"]["revision"] == profile.PIN
    assert description["runtime"]["module"] == "tbrt.clock"
    assert set(description["runtime"]["dependencies"]) == {"numpy", "scipy"}
    assert description["authority"] == profile.AUTHORITY
    assert description["authority"]["source_binding_verification"] == "caller_assertion_only"
    assert description["authority"]["authorizes_execution"] is False
    assert description["comparison_policy"]["cross_platform_bitwise_guarantee"] is False
    assert "not_binary_attestation" in description["comparison_policy"]["dependency_identity"]
    assert description["limits"]["argument_bytes"] == 65536
    assert description["limits"]["mcp_envelope_bytes"] == 262144
    assert description["limits"]["kernel_cpu_seconds"] == 3
    assert description["limits"]["kernel_address_space_bytes"] == 256 * 1024 * 1024
    assert host.list_resources() == [] and host.resource_templates == []
    assert "repository_root" not in description["runtime"] and "python_executable" not in description["runtime"]


def test_correlated_reference_and_distinct_source_receipt_knowledge_times(receipt):
    numerical = receipt["numerical_result"]
    assert numerical["event_time_delta"] == 6.5
    assert numerical["reference_origin"] == 1000
    assert numerical["event_time"] == 1006.5
    assert numerical["jacobian"] == [2, 3, 1]
    assert numerical["variance"] == 22.75
    assert numerical["standard_uncertainty"] == math.sqrt(22.75)
    assert numerical["joint_covariance"] == INPUT["joint_covariance"]["matrix"]
    assert numerical["observation"] == INPUT["observation"]
    assert numerical["observation"]["received_at"]["value"] == 2000
    assert numerical["observation"]["known_at"]["value"] == 3000
    assert numerical["propagation"] == "first-order.v1"
    assert receipt["request"] == INPUT
    assert receipt["request_digest"] == profile._digest(INPUT)


@pytest.mark.parametrize("matrix,expected", [
    ([[0, 0, 0], [0, 0, 0], [0, 0, 0]], 0),
    ([[1, -1, 0], [-1, 1, 0], [0, 0, 0]], 1),
    ([[1, 0, 0], [0, 0, 0], [0, 0, 4]], 8),
])
def test_exact_deterministic_singular_and_diagonal_covariances(host, matrix, expected):
    request = deepcopy(INPUT)
    request["joint_covariance"]["matrix"] = matrix
    assert invoke(host, request=request)["numerical_result"]["variance"] == expected


@pytest.mark.parametrize("time_value,expected", [(100, .5), (110, 20.5)])
def test_nominal_applicability_endpoints_remain_inclusive(host, time_value, expected):
    request = deepcopy(INPUT)
    request["observation"]["device_time"] = time_value
    assert invoke(host, request=request)["numerical_result"]["event_time_delta"] == expected


def test_exact_anchored_pair_survives_lossy_convenience_scalar_and_replay(host):
    request = deepcopy(INPUT)
    request["model"].update(reference_origin=2**54, skew=1, offset=0)
    request["observation"]["device_time"] = 100.25
    value = invoke(host, request=request)
    result = value["numerical_result"]
    assert result["reference_origin"] == 2**54
    assert result["event_time_delta"] == .25
    assert result["event_time"] == 2**54
    assert Fraction(result["reference_origin"]) + Fraction(result["event_time_delta"]) != Fraction(result["event_time"])
    assert result["event_time_representation"] == "rounded_binary64_convenience"
    assert invoke(host, "ncp_clocksync_replay", receipt=value)["outcome"] == "verified"


def test_same_request_is_stable_replay_preserves_occurrence_and_explicit_new_id_changes_digest(host, receipt):
    assert profile.canonical(invoke(host)) == profile.canonical(receipt)
    replayed = invoke(host, "ncp_clocksync_replay", receipt=receipt)
    assert replayed["outcome"] == "verified" and replayed["canonical_receipt_match"]
    assert replayed["replayed_execution_id"] == receipt["execution_id"]
    assert replayed["replayed_receipt_digest"] == receipt["receipt_digest"]
    request = deepcopy(INPUT)
    request["execution_id"] = "fixture:explicit-second-occurrence"
    fresh = invoke(host, request=request)
    assert fresh["receipt_digest"] != receipt["receipt_digest"]
    assert fresh["numerical_result_id"] == receipt["numerical_result_id"]
    assert fresh["request_digest"] != receipt["request_digest"]


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("expected_reference"),
    lambda r: r["model"].update(synchronization_evidence_ids=[]),
    lambda r: r["joint_covariance"].update(cross_covariance_policy="unknown"),
    lambda r: r["joint_covariance"].update(cross_covariance_policy="declared_zero"),
    lambda r: r["joint_covariance"]["order"].reverse(),
    lambda r: r["joint_covariance"].update(evidence_ids=[]),
    lambda r: r["observation"].update(device_time=True),
    lambda r: r["observation"].update(device_time="103"),
    lambda r: r["observation"]["frame"].update(unit="ms"),
    lambda r: r["model"].update(skew=float("nan")),
    lambda r: r["model"].update(offset=float("inf")),
    lambda r: r["model"].update(valid_device_interval=[100, 110, 120]),
    lambda r: r["joint_covariance"].update(matrix=[[1, 0], [0, 1]]),
    lambda r: r.update(batch=[deepcopy(INPUT)]),
])
def test_invalid_closed_contract_refuses_before_any_runtime_probe(host, monkeypatch, mutation):
    request = deepcopy(INPUT)
    mutation(request)
    def deny(*a, **k):
        pytest.fail("invalid request reached a child process")
    monkeypatch.setattr(host._adapter, "_run", deny)
    assert host.call("ncp_clocksync_reconcile", {"request": request})["status"] == "refused"


@pytest.mark.parametrize("path", [
    ("observation", "device_time"), ("model", "device_origin"), ("model", "reference_origin"),
    ("model", "skew"), ("model", "offset"), ("observation", "received_at", "value"),
    ("joint_covariance", "matrix", 0, 0),
])
def test_inexact_integer_conversion_refuses_before_provider(host, monkeypatch, path):
    request = deepcopy(INPUT)
    target = request
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = 2**54 + 1
    monkeypatch.setattr(host._adapter, "_run", lambda *a, **k: pytest.fail("input precision loss reached provider"))
    assert host.call("ncp_clocksync_reconcile", {"request": request}) == {"status": "refused", "reason": "input_precision_loss"}


@pytest.mark.parametrize("mutation", [
    lambda r: r["expected_reference"].update(clock_id="other"),
    lambda r: r["expected_reference"].update(time_scale="UTC"),
    lambda r: r["model"]["reference_frame"].update(clock_id="other"),
    lambda r: r["observation"]["frame"].update(clock_id="other"),
    lambda r: r["observation"].update(device_time=111),
    lambda r: r["model"].update(skew=0),
    lambda r: r["model"].update(valid_device_interval=[110, 100]),
    lambda r: r["joint_covariance"]["matrix"][0].__setitem__(1, .5),
    lambda r: r["joint_covariance"].update(matrix=[[-1, 0, 0], [0, 1, 0], [0, 0, 1]]),
    lambda r: r["joint_covariance"].update(matrix=[[1, 2, 0], [2, 1, 0], [0, 0, 1]]),
    lambda r: r["joint_covariance"].update(matrix=[[0, .1, 0], [.1, 1, 0], [0, 0, 1]]),
    lambda r: r["model"].update(skew=1e308),
])
def test_real_provider_refuses_wrong_frames_extrapolation_and_invalid_covariance(host, mutation):
    request = deepcopy(INPUT)
    # JSON roundtrip removes fixture aliases so the independently supplied
    # destination does not silently change with a mutation to a model frame.
    request = json.loads(json.dumps(request))
    mutation(request)
    assert host.call("ncp_clocksync_reconcile", {"request": request}) == {"status": "refused", "reason": "clock_model_refused"}


@pytest.mark.parametrize("path,changed", [
    (("numerical_result", "event_time"), 1111.0),
    (("numerical_result", "event_time_delta"), 7.0),
    (("numerical_result", "reference_origin"), 999.0),
    (("numerical_result", "variance"), 1.0),
    (("numerical_result", "standard_uncertainty"), 1.0),
    (("numerical_result", "jacobian", 0), 1.0),
    (("numerical_result", "joint_covariance", 0, 0), 2.0),
    (("numerical_result", "observation", "known_at", "value"), 4000.0),
    (("numerical_result", "model", "synchronization_evidence_ids", 0), "fabricated:sync"),
    (("numerical_result", "reference_frame", "clock_id"), "fabricated:frame"),
    (("execution_id",), "fabricated:execution"),
    (("request", "observation", "evidence_id"), "fabricated:observation"),
])
def test_every_result_field_and_request_is_compared_after_attacker_reseals(host, receipt, path, changed):
    value = deepcopy(receipt)
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = changed
    reseal(value)
    result = invoke(host, "ncp_clocksync_replay", receipt=value)
    assert result["outcome"] == "mismatch" and not result["canonical_receipt_match"]
    assert result["request_digest_match"] and result["numerical_digest_match"] and result["receipt_digest_match"]


@pytest.mark.parametrize("field", ["request_digest", "numerical_result_id", "receipt_digest"])
def test_digest_ids_are_verified_in_addition_to_content(host, receipt, field):
    value = deepcopy(receipt)
    value[field] = ("" if field == "request_digest" else "sha256:") + "0" * 64
    assert invoke(host, "ncp_clocksync_replay", receipt=value)["outcome"] == "mismatch"


@pytest.mark.parametrize("mutation", [
    lambda r: r["request"].pop("expected_reference"),
    lambda r: r["numerical_result"].update(extra=1),
    lambda r: r["numerical_result"]["model"].update(extra=1),
    lambda r: r["numerical_result"]["observation"]["known_at"].update(extra=1),
    lambda r: r["comparison_policy"].update(comparison="numerical_tolerance"),
    lambda r: r["comparison_policy"].update(cross_platform_bitwise_guarantee=True),
    lambda r: r["authority"].update(authorizes_execution=True),
    lambda r: r["authority"].update(source_binding_verification="authenticated"),
    lambda r: r["runtime"].update(python_executable="/tmp/other"),
    lambda r: r["runtime"]["dependencies"].update(extra="installed"),
    lambda r: r.update(extra=1),
])
def test_malformed_full_receipt_never_wakes_science_even_when_rehashed(host, receipt, monkeypatch, mutation):
    value = deepcopy(receipt)
    mutation(value)
    reseal(value)
    monkeypatch.setattr(host._adapter, "_run", lambda *a, **k: pytest.fail("malformed replay reached provider"))
    result = invoke(host, "ncp_clocksync_replay", receipt=value)
    assert result["outcome"] == "invalid_contract" and not result["canonical_receipt_match"]


@pytest.mark.parametrize("path", [("revision",), ("python_sha256",), ("python_version",), ("source_tree",), ("dependencies", "numpy")])
def test_retained_runtime_identity_is_evidence_not_a_launch_binding(host, receipt, monkeypatch, path):
    value = deepcopy(receipt)
    target = value["runtime"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "different-runtime"
    reseal(value)
    original = host._adapter._run
    def run(code, arguments, stdin=b""):
        assert code != profile._BOOTSTRAP, "mismatched retained runtime launched science"
        return original(code, arguments, stdin)
    monkeypatch.setattr(host._adapter, "_run", run)
    assert invoke(host, "ncp_clocksync_replay", receipt=value)["outcome"] == "runtime_mismatch"


def test_current_host_dependency_drift_refuses(host, receipt, monkeypatch):
    monkeypatch.setattr(host._adapter, "_dependencies", {"numpy": "different", "scipy": None})
    assert host.call("ncp_clocksync_replay", {"receipt": receipt}) == {"status": "refused", "reason": "clocksync_runtime_refused"}


@pytest.mark.parametrize("key", ["principal", "owner", "checkout", "python_executable", "pin", "repository", "configuration", "policy", "endpoint"])
def test_caller_cannot_select_host_bindings_at_any_request_level(host, key):
    for path in ((), ("request",), ("request", "model"), ("request", "expected_reference")):
        arguments = {"request": deepcopy(INPUT)}
        target = arguments
        for part in path:
            target = target[part]
        target[key] = "untrusted"
        assert host.call("ncp_clocksync_reconcile", arguments)["status"] == "refused"


def test_full_receipt_required_and_tool_set_has_no_batch_export_or_clock_control(host):
    assert host.call("ncp_clocksync_replay", {"receipt_digest": "sha256:" + "0" * 64})["status"] == "refused"
    for operation in ("set_clock", "batch", "export", "retain", "synchronize"):
        assert host.call("ncp_clocksync_" + operation, {})["status"] == "refused"


def test_request_and_full_mcp_envelope_budgets(host, receipt):
    assert host.call("ncp_clocksync_reconcile", {"request": {"large": "x" * 65536}}) == {
        "status": "refused", "reason": "argument_budget_exceeded"}
    assert len(profile.canonical({"name": "ncp_clocksync_replay", "arguments": {"receipt": receipt}})) <= 65536
    assert len(profile.canonical(host.mcp_result("ncp_clocksync_reconcile", {"status": "ok", "data": receipt}))) <= 262144
    with pytest.raises(profile.ClockSyncRefusal, match="response_budget_exceeded"):
        host.mcp_result("ncp_clocksync_describe", {"status": "ok", "data": {"text": "\u2603" * 30000}})


@pytest.mark.parametrize("count,expected", [(8, "ok"), (16, "refused")])
def test_every_issued_unicode_receipt_fits_replay_call_admission(host, count, expected):
    request = deepcopy(INPUT)
    request["model"]["synchronization_evidence_ids"] = [str(i) + "\u2603" * 250 for i in range(count)]
    request["joint_covariance"]["evidence_ids"] = [str(i) + "\u2603" * 250 for i in range(count)]
    response = host.call("ncp_clocksync_reconcile", {"request": request})
    assert response["status"] == expected
    if expected == "ok":
        receipt = response["data"]
        assert len(profile.canonical({"name": "ncp_clocksync_replay", "arguments": {"receipt": receipt}})) <= 65536
        assert invoke(host, "ncp_clocksync_replay", receipt=receipt)["outcome"] == "verified"
    else:
        assert response["reason"] == "receipt_replay_budget_exceeded"


def test_parent_does_not_import_science_and_calls_do_not_retain_or_alias_results(host):
    before = {name for name in sys.modules if name == "tbrt" or name.startswith("tbrt.")}
    request = deepcopy(INPUT)
    receipt = invoke(host, request=request)
    request["observation"]["device_time"] = 0
    receipt["numerical_result"]["observation"]["device_time"] = 0
    assert invoke(host)["numerical_result"]["observation"]["device_time"] == 103
    assert {name for name in sys.modules if name == "tbrt" or name.startswith("tbrt.")} == before
    assert sorted(path.name for path in Path(host._guard_directory.name).iterdir()) == ["call.lease"]


def test_runtime_source_and_interpreter_pins_refuse_drift(checkout, tmp_path):
    clone = tmp_path / "pin"
    subprocess.run(["git", "clone", "--quiet", "--shared", str(checkout), str(clone)], check=True)
    with profile.ClockSyncHost(checkout=clone, python_executable=sys.executable, principal="fixture-owner") as host:
        target = clone / "src/tbrt/clock.py"
        target.write_bytes(target.read_bytes() + b"\n# controlled source drift\n")
        assert host.call("ncp_clocksync_describe", {}) == {"status": "refused", "reason": "clocksync_runtime_refused"}
    with pytest.raises(AdapterRefusal):
        profile.ClockSyncHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner",
                              expected_python_sha256="0" * 64)
    with pytest.raises(profile.ClockSyncRefusal):
        profile.ClockSyncHost(checkout="relative", python_executable=sys.executable, principal="fixture-owner")


def test_busy_call_cannot_reset_active_deadline_or_release_active_lease(host, monkeypatch):
    original = host._adapter._run
    entered, release = threading.Event(), threading.Event()
    results = []
    def run(code, arguments, stdin=b""):
        if code == profile._BOOTSTRAP:
            entered.set()
            assert release.wait(10)
        return original(code, arguments, stdin)
    monkeypatch.setattr(host._adapter, "_run", run)
    active = threading.Thread(target=lambda: results.append(host.call("ncp_clocksync_reconcile", {"request": INPUT})))
    active.start()
    try:
        assert entered.wait(5)
        deadline = host._adapter.deadline
        assert host.call("ncp_clocksync_describe", {}) == {"status": "refused", "reason": "clocksync_busy"}
        assert host._adapter.deadline == deadline
        lease = _Lease(Path(host._guard_directory.name) / "call.lease")
        try:
            assert not lease.try_acquire()
        finally:
            lease.release()
    finally:
        release.set()
        active.join(timeout=10)
    assert not active.is_alive() and results[0]["status"] == "ok"
    assert host._adapter.deadline is None


@pytest.mark.parametrize("fault", ["memory", "cpu", "wall", "output"])
def test_real_child_resource_faults_refuse_and_release_admission(host, monkeypatch, fault):
    original = host._adapter._run
    program = {"memory": "value = bytearray(400 * 1024 * 1024)",
        "cpu": "while True: pass", "wall": "import time; time.sleep(20)",
        "output": "print('x' * 400000)"}[fault]
    def run(code, arguments, stdin=b""):
        return original(program if code == profile._BOOTSTRAP else code, arguments, stdin)
    started = time.monotonic()
    with monkeypatch.context() as patch:
        patch.setattr(host._adapter, "_run", run)
        assert host.call("ncp_clocksync_reconcile", {"request": INPUT}) == {
            "status": "refused", "reason": "clocksync_runtime_refused"}
    assert time.monotonic() - started < 10
    assert host.call("ncp_clocksync_describe", {})["status"] == "ok"


def test_expired_call_deadline_refuses_and_releases_lease(host, monkeypatch):
    original = host._runtime
    def runtime():
        host._adapter.deadline = time.monotonic() - 1
        return original()
    with monkeypatch.context() as patch:
        patch.setattr(host, "_runtime", runtime)
        assert host.call("ncp_clocksync_describe", {}) == {"status": "refused", "reason": "clocksync_deadline_exceeded"}
    assert host.call("ncp_clocksync_describe", {})["status"] == "ok"


@pytest.mark.parametrize("boundary", ["acquire", "release"])
def test_io_failure_is_sanitized_preserving_diagnostic_chain(host, monkeypatch, boundary):
    failure = OSError("controlled /private/path failure")
    original = _Lease.release
    def broken(self):
        if boundary == "release":
            original(self)
        raise failure
    with monkeypatch.context() as patch:
        patch.setattr(_Lease, "try_acquire" if boundary == "acquire" else "release", broken)
        with pytest.raises(profile.ClockSyncRefusal, match="^clocksync_runtime_io_refused$") as caught:
            host.call("ncp_clocksync_describe", {})
    assert caught.value.__cause__ is failure
    assert host.call("ncp_clocksync_describe", {})["status"] == "ok"


def test_close_is_idempotent_and_refuses_further_calls(checkout):
    host = profile.ClockSyncHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner")
    path = Path(host._guard_directory.name)
    host.close()
    host.close()
    assert not path.exists()
    assert host.call("ncp_clocksync_describe", {}) == {"status": "refused", "reason": "clocksync_host_closed"}


def test_official_mcp_reconnect_preserves_full_sdk_receipt(host, checkout, tmp_path, receipt):
    pytest.importorskip("mcp", reason="official MCP dependency is required for external-client qualification")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import sys\nsys.path.insert(0," + repr(str(PACKAGE)) + ")\n"
        "from ciw.clocksync_profile import ClockSyncHost\nfrom ciw.mcp_server import serve_sdk\n"
        "with ClockSyncHost(checkout=" + repr(str(checkout)) + ",python_executable=sys.executable,principal='fixture-owner') as host:\n"
        "    serve_sdk(host,name='odsp-clocksync-fixture')\n")
    async def run():
        for _ in range(2):
            params = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(launcher)], cwd=str(tmp_path))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.discover()
                    assert len((await client.list_tools()).tools) == 3
                    result = await client.call_tool("ncp_clocksync_reconcile", {"request": INPUT})
                    assert result.structured_content == {"status": "ok", "data": receipt}
                    replayed = await client.call_tool("ncp_clocksync_replay", {"receipt": receipt})
                    assert replayed.structured_content["data"]["outcome"] == "verified"
                    refused = await client.call_tool("ncp_clocksync_reconcile", {"request": INPUT, "principal": "intruder"})
                    assert refused.is_error
    asyncio.run(run())
