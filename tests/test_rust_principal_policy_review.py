"""Independent regressions against the host-selected native policy executable.

These tests exercise the real binary, with deliberate transport and registry
faults where stated. They do not qualify a complete native execution engine.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from ciw import control_gateway, principal_policy as oracle
from ciw import rust_principal_policy as native
from ciw.adapters.subprocess import RuntimeCleanupUncertain
from test_rust_principal_policy import full_policy, limits, valid_arguments


@pytest.fixture(scope="session")
def review_binding():
    selected = os.environ.get("ODSP_NATIVE_POLICY_BINARY")
    assert selected, "independent native qualification requires an explicit binary"
    binary = Path(selected)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    build = json.loads(subprocess.check_output([str(binary), "identity-v1"], timeout=3))
    root = Path(__file__).resolve().parents[1]
    for name, expected in build["native_sources_sha256"].items():
        assert sha256((root / name).read_bytes()).hexdigest() == expected, name
    return dict(binary=binary, expected_sha256=sha256(binary.read_bytes()).hexdigest(),
                process_policy=limits())


def decision(policy, method, *args):
    try:
        return True, getattr(policy, method)(*args)
    except (ValueError, TypeError, OverflowError):
        return False, None


@pytest.mark.parametrize("tool,field", [
    ("ncp_domain_run", "request"), ("ncp_nise_validate", "request"),
    ("ncp_skill_run", "inputs"),
])
def test_review_python_surrogate_pairs_are_not_authorized_as_unicode_scalars(review_binding, tool, field):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    python = oracle.PrincipalPolicy(lambda: value)
    args = valid_arguments(tool, oracle._specs()[tool]["inputSchema"])
    for payload in ("\ud83c\udf0d", "a\ud83c\udf0db", {"\ud83c\udf0d": 1},
                    ["\ud83c\udf0d"], "\ud83c", "\udf0d"):
        candidate = {**args, field: {"value": payload}}
        expected = decision(python, "authorize", "alice", tool, candidate)
        assert expected == (False, None)
        assert decision(rust, "authorize", "alice", tool, candidate) == expected, repr(payload)


def test_review_policy_metadata_preserves_distinct_python_code_points(review_binding):
    value = full_policy()
    grant = value["principals"][0]
    unusual = ["\U0001f30d", "\ud83c\udf0d", "\ud800", "\udfff", "\ue000", "\ue001",
               "\\ud800", "\U0010ffff", "\uffff", "\x00", "\U000f0000", "~", "~~",
               "~ud800;", "~u0041;", "~ud83c;~udf0d;"]
    grant["scientific_operations"].extend(unusual)
    rule = next(r for r in grant["operations"] if r["name"] == "ncp_domain_run")
    rule["constraints"] = {"request.value": [{key: index for index, key in enumerate(unusual)}]}
    python = oracle.PrincipalPolicy(lambda: value)
    assert python.snapshot() == value
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    assert rust.snapshot() == value
    assert rust.check_principal("alice") == python.check_principal("alice")
    assert rust.context("alice") == python.context("alice")


def test_review_valid_unicode_and_transport_markers_remain_exact(review_binding):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    python = oracle.PrincipalPolicy(lambda: value)
    for payload in ("\U0001f30d", "\ue000\ue001\ue002", "\U000f0000", "\\ud800", "\U0010ffff",
                    "~", "~~", "~ud800;", "~u0041;", "~ud83c;~udf0d;", "~uD800;", "~u123",
                    {"\ue000": 1, "\ue001": True, "\U0001f30d": -0.0},
                    {"~": 1, "~~": 2, "~ud800;": 3, "~u0041;": 4}, [1, 1.0, True, False]):
        args = {"domain": "sequence", "request": {"value": payload}}
        expected = decision(python, "authorize", "alice", "ncp_domain_run", args)
        assert expected[0]
        assert decision(rust, "authorize", "alice", "ncp_domain_run", args) == expected


def test_review_current_registered_schema_drift_refuses_old_native_binding(review_binding, monkeypatch):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    assert rust.authorize("alice", "ncp_capabilities_list", {})["enabled"] is True
    changed = deepcopy(control_gateway.TOOLS["ncp_capabilities_list"])
    changed["inputSchema"] = {"type": "object", "properties": {"changed": {"type": "boolean"}},
                              "required": ["changed"], "additionalProperties": False}
    monkeypatch.setitem(control_gateway.TOOLS, "ncp_capabilities_list", changed)
    assert decision(oracle.PrincipalPolicy(lambda: value), "authorize", "alice", "ncp_capabilities_list", {}) == (False, None)
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize("alice", "ncp_capabilities_list", {})
    with pytest.raises(oracle.PrincipalRefusal):
        rust.runtime_identity()


def test_review_catalog_drift_during_real_native_process_withholds_result(review_binding, monkeypatch):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    real = native._bounded_process
    executions = []
    def execute(*args, **kwargs):
        result = real(*args, **kwargs)
        executions.append(result[0])
        changed = deepcopy(control_gateway.TOOLS["ncp_capabilities_list"])
        changed["inputSchema"]["required"] = ["missing"]
        monkeypatch.setitem(control_gateway.TOOLS, "ncp_capabilities_list", changed)
        return result
    monkeypatch.setattr(native, "_bounded_process", execute)
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize("alice", "ncp_capabilities_list", {})
    assert executions == [0]


def test_review_current_binary_identity_does_not_attest_changed_file(review_binding, tmp_path):
    copied = tmp_path / "policy"
    copied.write_bytes(review_binding["binary"].read_bytes())
    copied.chmod(0o500)
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **{**review_binding, "binary": copied})
    assert rust.runtime_identity()["binary_sha256"] == review_binding["expected_sha256"]
    copied.chmod(0o700)
    copied.write_bytes(b"changed native executable\n")
    with pytest.raises(oracle.PrincipalRefusal):
        rust.runtime_identity()
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize("alice", "ncp_capabilities_list", {})


def test_review_real_native_reply_cannot_be_replayed_for_another_principal(review_binding, monkeypatch):
    value = full_policy()
    other = deepcopy(value["principals"][0])
    other["principal"], other["enabled"] = "bob", False
    value["principals"].append(other)
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    real = native._bounded_process
    captured = []
    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        captured.append(result)
        return result
    monkeypatch.setattr(native, "_bounded_process", capture)
    assert rust.authorize("alice", "ncp_capabilities_list", {})["principal"] == "alice"
    assert len(captured) == 1 and captured[0][0] == 0
    monkeypatch.setattr(native, "_bounded_process", lambda *a, **k: captured[0])
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize("bob", "ncp_capabilities_list", {})
    value["principals"][0]["operations"] = []
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize("alice", "ncp_capabilities_list", {})


@pytest.mark.parametrize("form", ["direct", "cause", "group"])
def test_review_uncertain_native_cleanup_is_sticky(review_binding, monkeypatch, form):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    launches = []
    def fail(*args, **kwargs):
        launches.append(1)
        held = RuntimeCleanupUncertain()
        if form == "direct":
            raise held
        if form == "group":
            raise ExceptionGroup("review cleanup", [held])
        try:
            raise held
        except RuntimeCleanupUncertain as exc:
            raise OSError("review enclosing cleanup") from exc
    monkeypatch.setattr(native, "_bounded_process", fail)
    for _ in range(2):
        with pytest.raises(oracle.PrincipalRefusal):
            rust.authorize("alice", "ncp_capabilities_list", {})
    assert launches == [1]
    assert isinstance(rust._cleanup_uncertain, RuntimeCleanupUncertain)


def test_review_native_wire_refuses_corruption_with_bounded_actual_output(review_binding, monkeypatch):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    original = native._bounded_process
    captured = []
    def capture(*args, **kwargs):
        captured.append(kwargs["stdin"])
        return original(*args, **kwargs)
    monkeypatch.setattr(native, "_bounded_process", capture)
    rust.authorize("alice", "ncp_capabilities_list", {})
    raw = captured[-1]
    envelope = json.loads(raw)
    mutations = [b"", b"\xff", b"{" + raw[1:-1] + b',"schema":"duplicate"}',
                 raw + b"{}", b" " * (native.MAX_INPUT + 1)]
    for key, replacement in (("schema", "unknown"), ("catalog_sha256", "0" * 64),
                             ("policy_json", '{"a":1,"\\u0061":2}'),
                             ("arguments_json", '{"x":1,"\\u0078":2}')):
        mutations.append(json.dumps({**envelope, key: replacement}, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode("ascii"))
    for malformed in mutations:
        process = subprocess.run([str(review_binding["binary"]), "policy-v1"], input=malformed,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        assert process.returncode == 0
        assert len(process.stdout) <= native.MAX_OUTPUT and process.stderr == b""
        assert json.loads(process.stdout)["status"] == "refused"


def test_review_no_python_decision_fallback_for_fresh_scope_and_resource_changes(review_binding, monkeypatch):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    def forbidden(*args, **kwargs):
        raise AssertionError("Python decision fallback invoked")
    for method in ("snapshot", "check_principal", "authorize", "authorize_context", "authorize_resource", "context"):
        monkeypatch.setattr(oracle.PrincipalPolicy, method, forbidden)
    for function in ("validate_policy", "_request", "_rule", "_direct_science"):
        monkeypatch.setattr(oracle, function, forbidden)
    uri = "ncp://proto-corpus/" + "a" * 64 + "/overview"
    accepted = rust.authorize_resource("alice", uri)
    accepted["reference_scopes"].clear()  # Returned data grants no mutable authority.
    assert rust.authorize_resource("alice", uri)["reference_scopes"]
    value["principals"][0]["reference_scopes"] = []
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize_resource("alice", uri)
    value["principals"][0]["enabled"] = False
    for method, args in (("context", ("alice",)), ("check_principal", ("alice",)),
                         ("authorize_context", ("alice", "ncp_capabilities_list", {}))):
        with pytest.raises(oracle.PrincipalRefusal):
            getattr(rust, method)(*args)


def test_review_native_grants_preserve_tenant_bound_retention_and_original_source_revocation(
        review_binding, monkeypatch, tmp_path):
    from ciw.logistics_profile import LogisticsHost
    from logistics_fixture_helpers import Fixture
    node = shutil.which("node")
    assert node, "actual retained logistics profile qualification requires Node"
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **review_binding)
    def forbidden(*args, **kwargs):
        raise AssertionError("Python decision fallback invoked")
    for method in ("snapshot", "check_principal", "authorize", "authorize_context", "authorize_resource", "context"):
        monkeypatch.setattr(oracle.PrincipalPolicy, method, forbidden)
    for function in ("validate_policy", "_request", "_rule", "_direct_science"):
        monkeypatch.setattr(oracle, function, forbidden)
    def host(tenant):
        return LogisticsHost(transport=Fixture(), principal="alice", tenant=tenant,
                             roles=["pilot:agent-read", "pilot:compute-read"], allowed_kinds=["catalog"],
                             node=node, synthetic=True)
    first, second = host("review-tenant-a"), host("review-tenant-b")
    a, b = rust.facade("alice", logistics=first), rust.facade("alice", logistics=second)
    assert first.store.owner != second.store.owner
    artifact = a.invoke("ncp_logistics_catalog", {})["artifact_id"]
    assert a.invoke("ncp_logistics_retained_read", {"artifact_id": artifact})["artifact_id"] == artifact
    assert b.call("ncp_logistics_retained_read", {"artifact_id": artifact})["status"] == "refused"
    assert a.call("ncp_logistics_catalog", {"tenant": "review-tenant-b"})["status"] == "refused"
    value["principals"][0]["operations"] = [row for row in value["principals"][0]["operations"]
        if row["name"] not in {"ncp_logistics_catalog", "ncp_logistics_inspect"}]
    assert a.call("ncp_logistics_retained_read", {"artifact_id": artifact})["status"] == "refused"
    assert len(first.transport.calls) == 1 and second.transport.calls == []
