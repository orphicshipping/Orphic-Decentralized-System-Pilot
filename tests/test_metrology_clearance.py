"""Real pinned native execution; synthetic geometry is not physical qualification."""

import io
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from ciw import metrology_clearance as c
from ciw.agent_api import encode, parse
from ciw.agent_mcp import from_profile, serve
from ciw.core.identities import evidence_id
from ciw.session import Session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/metrology-clearance"


@pytest.fixture(scope="session")
def provider(tmp_path_factory):
    root = tmp_path_factory.mktemp("native") / "cse"
    source = Path(
        os.environ.get("NCP_CSE_SOURCE", ROOT / "instruments/domain/bim-estimator")
    )
    shutil.copytree(
        source,
        root,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"),
    )

    def git(*args):
        return (
            subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.PIPE
            )
            .decode()
            .strip()
        )

    git("init")
    git("config", "core.autocrlf", "false")
    git("add", "-f", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Exact native source fixture",
    )
    assert git("rev-parse", "HEAD^{tree}") == c.NATIVE_TREE
    return c.Provider(root, git("rev-parse", "HEAD"))


@pytest.fixture
def inspection():
    return parse((EXAMPLES / "uncertain-inspection.json").read_bytes())


@pytest.fixture(scope="session")
def native_result(provider):
    source = c.make_source((EXAMPLES / "uncertain-inspection.json").read_bytes())
    result = provider.execute(source, {})
    c.validate_payload(c.OP, result, source, {}, {})
    return source, result


def test_real_provider_retains_uncertainty_without_physical_authority(native_result):
    source, result = native_result
    n = result["native"]
    assert n["status"] == "assessed"
    assert n["posterior"]["verdict"] == "UNRESOLVED"
    assert n["likelihood"]["pose_sigma"] > n["likelihood"]["sampling_sigma"]
    assert result["authority"] == c.AUTHORITY
    assert result["source_ref"] == c.bytes_ref(c.source_bytes(source))


def test_precise_pose_disagreement_is_held(provider):
    source = c.make_source((EXAMPLES / "inspection.json").read_bytes())
    result = provider.execute(source, {})
    c.validate_payload(c.OP, result, source, {}, {})
    assert result["native"]["status"] == "held"
    assert "independent pose disagrees" in result["native"]["reason"]
    assert result["native"]["posterior"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("pose.basis", "model_alignment"),
        ("pose.basis", "unknown"),
        ("calibration.prior_cross_covariance", "unknown"),
        ("calibration.applicability", "expired"),
        ("calibration.applicability", "unknown"),
    ],
)
def test_unqualified_evidence_never_assesses(provider, inspection, field, value):
    group, key = field.split(".")
    inspection[group][key] = value
    source = c.make_source(encode(inspection))
    result = provider.execute(source, {})
    c.validate_payload(c.OP, result, source, {}, {})
    assert result["native"]["status"] == "held"
    assert result["native"]["likelihood"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_field",
        "profile",
        "url",
        "nan",
        "boolean",
        "units",
        "frame",
        "pose_capture",
        "pose_model",
        "calibration_capture",
        "requirement_model",
        "requirement_frame",
        "model_hash",
        "duplicate_key",
        "covariance_asymmetric",
        "covariance_singular",
        "covariance_negative",
        "few_points",
        "many_points",
        "negative_sigma",
        "zero_extent",
        "confidence",
        "bytes_budget",
    ],
)
def test_contract_refusals(inspection, mutation):
    s = inspection
    if mutation == "unknown_field":
        s["execute"] = "robot-offset"
    elif mutation == "profile":
        s["profile"] = "manufacturing-flatness.v1"
    elif mutation == "url":
        s["inspection_ref"] = "https://example.com/cad"
    elif mutation == "nan":
        s["scan"]["points_m"][0][0] = float("nan")
    elif mutation == "boolean":
        s["scan"]["points_m"][0][0] = True
    elif mutation == "units":
        s["scan"]["unit"] = "mm"
    elif mutation == "frame":
        s["scan"]["frame"] = "other"
    elif mutation == "pose_capture":
        s["pose"]["capture_ref"] = "other"
    elif mutation == "pose_model":
        s["pose"]["model_sha256"] = "sha256:" + "0" * 64
    elif mutation == "calibration_capture":
        s["calibration"]["capture_ref"] = "other"
    elif mutation == "requirement_model":
        s["requirement"]["model_sha256"] = "sha256:" + "0" * 64
    elif mutation == "requirement_frame":
        s["requirement"]["frame"] = "other"
    elif mutation == "model_hash":
        s["model"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "covariance_asymmetric":
        s["pose"]["covariance"][0][1] = 0.001
    elif mutation == "covariance_singular":
        s["pose"]["covariance"][0][0] = 0
    elif mutation == "covariance_negative":
        s["pose"]["covariance"][0][0] = -0.01
    elif mutation == "few_points":
        s["scan"]["points_m"] = s["scan"]["points_m"][:9]
    elif mutation == "many_points":
        s["scan"]["points_m"] = [[0, 0, 0]] * 1025
    elif mutation == "negative_sigma":
        s["calibration"]["sensor_sigma_m"] = -0.1
    elif mutation == "zero_extent":
        s["requirement"]["proposed"]["extents"][0] = 0
    elif mutation == "confidence":
        s["requirement"]["confidence"] = 1
    raw = json.dumps(s, allow_nan=True).encode()
    if mutation == "duplicate_key":
        raw = raw[:-1] + b',"origin":"recorded"}'
    if mutation == "bytes_budget":
        raw += b" " * c.MAX_BYTES
    with pytest.raises((ValueError, TypeError)):
        c.inspection(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "authority",
        "source",
        "probability",
        "verdict",
        "uncertainty",
        "pose_ref",
        "capture",
        "target",
        "missing_evidence",
        "invariant",
        "extra_field",
    ],
)
def test_retained_result_tampering(native_result, mutation):
    source, result = deepcopy(native_result)
    n = result["native"]
    if mutation == "authority":
        result["authority"]["actuation"] = "permitted"
    elif mutation == "source":
        result["source_ref"] = "sha256:" + "0" * 64
    elif mutation == "probability":
        n["posterior"]["risks"][0]["p_violates"] = 0
    elif mutation == "verdict":
        n["posterior"]["verdict"] = "SATISFIED"
    elif mutation == "uncertainty":
        n["likelihood"]["noise_sigma"] *= 2
    elif mutation == "pose_ref":
        n["likelihood"]["pose_source_id"] = "other"
    elif mutation == "capture":
        n["evidence"]["scan_digest"] = "0" * 64
    elif mutation == "target":
        n["likelihood"]["element_name"] = "other"
    elif mutation == "missing_evidence":
        n["evidence"] = None
    elif mutation == "invariant":
        n["invariants_passed"] = False
    elif mutation == "extra_field":
        n["machine_command"] = "stop"
    with pytest.raises(ValueError):
        c.validate_payload(c.OP, result, source, {}, {})


def test_source_envelope_exact_bytes(inspection):
    raw = encode(inspection) + b"\n "
    source = c.make_source(raw)
    assert c.source_bytes(source) == raw
    source["channels"]["inspection"]["values"] = [2.0]
    source["evidence_id"] = evidence_id(source)
    with pytest.raises(ValueError):
        c.source_bytes(source)


@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18"])
def test_mcp_runner_reopen_retry_and_replay(tmp_path, provider, version):
    profile = c.prepare(
        (EXAMPLES / "uncertain-inspection.json").read_bytes(), tmp_path / "profile"
    )
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "net_execute",
                "arguments": {
                    "source": "source",
                    "graph": "baseline",
                    "attempt": "first",
                },
            },
        },
    ]
    with from_profile(
        profile, instrument="bim-clearance", clearance_provider=provider
    ) as host:
        output = io.BytesIO()
        serve(host, io.BytesIO(b"\n".join(encode(r) for r in requests) + b"\n"), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        response = replies[-1]["result"]["structuredContent"]
        assert response["status"] == "completed", response
        retry = host.call("net_execute", requests[-1]["params"]["arguments"])
        assert (
            retry["reused_response"]
            and retry["execution_ids"] == response["execution_ids"]
        )
        replay = host.replay("first", "second")
        assert replay["status"] == "completed"
        assert replay["execution_ids"] != response["execution_ids"]
    with from_profile(
        profile, instrument="bim-clearance", clearance_provider=provider, resume=True
    ) as host:
        assert (
            host.execute("source", "baseline", "first")["execution_ids"]
            == response["execution_ids"]
        )
    # Offline source/result reopening needs the registered validator, no numerical provider.
    workspace = profile.parent / "agent-output/first/workspace.json"
    session = Session.from_workspace(workspace, output_dir=tmp_path / "offline")
    assert len(session.results) == 1
    assert (
        next(iter(session.results.values()))["data"]["native"]["posterior"]["verdict"]
        == "UNRESOLVED"
    )


def test_no_grant_no_execution_and_policy_override(tmp_path, provider):
    profile = c.prepare(
        (EXAMPLES / "uncertain-inspection.json").read_bytes(), tmp_path / "profile"
    )
    p = parse(profile.read_bytes())
    p["allow_operations"] = []
    profile.write_bytes(encode(p))
    with (
        from_profile(profile, instrument="bim-clearance") as host,
        pytest.raises(ValueError, match="not enabled"),
    ):
        host.execute("source", "baseline", "first")
    p["candidate_domains"] = {"anything": {}}
    profile.write_bytes(encode(p))
    with pytest.raises(ValueError, match="editable"):
        from_profile(profile, instrument="bim-clearance", clearance_provider=provider)


def test_graph_and_path_injection_refused(tmp_path, provider):
    profile = c.prepare(
        (EXAMPLES / "uncertain-inspection.json").read_bytes(), tmp_path / "profile"
    )
    g = parse((profile.parent / "experiment.json").read_bytes())
    g["nodes"][0]["parameters"] = {"cad_path": "/tmp/model.ifc"}
    from ciw.operations.runner import seal

    (profile.parent / "experiment.json").write_bytes(encode(seal(g)))
    with from_profile(
        profile, instrument="bim-clearance", clearance_provider=provider
    ) as host:
        with pytest.raises(ValueError, match="fixed graph"):
            host.execute("source", "baseline", "first")
        with pytest.raises(ValueError):
            host.execute("/tmp/model.ifc", "baseline", "second")


def test_provider_tree_pin_refuses_drift(provider):
    root = provider.adapter.repository_root
    stray = root / "unapproved.py"
    stray.write_text("raise RuntimeError()")
    try:
        with pytest.raises(c.AdapterRefusal):
            provider.runtime_identity()
    finally:
        stray.unlink()


@pytest.mark.parametrize(
    "mean,sigma,probability,verdict",
    [
        (1.0, 0.0, 0.0, "SATISFIED"),
        (-1.0, 0.0, 1.0, "VIOLATED"),
        (0.0, 1.0, 0.5, "UNRESOLVED"),
        (0.0, 0.0, 0.0, "SATISFIED"),
    ],
)
def test_analytic_risk_boundary(mean, sigma, probability, verdict, inspection):
    requirement = deepcopy(inspection["requirement"])
    requirement["minimum_m"] = 0.0
    p = requirement["proposed"]
    value = {
        "decision": {
            "proposed": {
                "origin": p["origin"],
                "angle": p["yaw"],
                "extents": p["extents"],
            },
            "required_clearance": 0.0,
            "confidence": requirement["confidence"],
            "position_sigma": requirement["position_sigma_m"],
            "label": requirement["id"],
        },
        "risks": [
            {
                "element_name": "test",
                "clearance_mean": mean,
                "clearance_sigma": sigma,
                "required_clearance": 0.0,
                "p_violates": probability,
                "direction": [0, 0, 1],
                "witness": [0, 0, 0],
            }
        ],
        "p_any_violation_lower": probability,
        "p_any_violation_upper": probability,
        "verdict": verdict,
        "scene_version": "reference-case",
    }
    c._assessment(value, requirement)
