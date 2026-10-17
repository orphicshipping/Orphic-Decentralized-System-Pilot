"""Combined owner contracts remain inspectable without execution authority."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from ciw import engineering_thread as engineering
from ciw import engineering_thread_io as thread
from ciw import link_contract as contract
from ciw import link_study as study
from ciw.agent_api import AgentHost
from ciw.link_study_cli import example
from ciw.session import Session


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@pytest.mark.parametrize("control_ref", [
    "f16fe2a95694b841f32e033d218e3faa88a5c8a784755053d3a8b6ca4db7f2c8",
    "24848f23442bf87b16ce81d48fdd5e19b0a7f7f15a1f1e0d4732cf3f60f47f9d",
])
def test_reviewed_historical_compiler_reopens_without_restoring_execution(tmp_path, monkeypatch, control_ref):
    previous = engineering.compiler_identity()
    previous["files"]["engineering_thread.py"] = "sha256:b4cfac86e1bd63dcb3317153c3b6588412cb43db897180c5d03c35494e0eab7d"
    previous["files"]["control_plane.py"] = "sha256:" + control_ref
    previous["files"].update({
        "production.py": "sha256:90c6c45483e2bc058a57d2a5b303380740ddf04965946234fbf4fc24674c8d2b",
        "control_contracts.py": "sha256:696738d7779fc24644ea92b2b68f3f780c6e087d93b9143e29673772d065881c",
        "production_gates.py": "sha256:043bd8d63825e0fa2410cc238aba226b17c2eda15f50398c21b5268bb13fcca8",
    })
    request = encoded(example())
    intent = encoded(engineering.example_intent("scalar", contract.bytes_ref(request)))
    with monkeypatch.context() as historical:
        historical.setattr(engineering, "compiler_identity", lambda: deepcopy(previous))
        compiled = thread.compile_packet(intent, request, tmp_path / "compiled", host_profile="scalar")
        review = thread.run_packet(tmp_path / "compiled", tmp_path / "executed", host_profile="scalar",
                                   expected_compiled_ref=compiled["compiled_ref"], allow_compute=True)
    before = {path.relative_to(tmp_path / "executed"): path.read_bytes()
              for path in (tmp_path / "executed").rglob("*") if path.is_file()}
    dispatches = []

    def forbidden(*args, **kwargs):
        dispatches.append("Session.handle")
        raise AssertionError("Historical review dispatched an operation")

    monkeypatch.setattr(Session, "handle", forbidden)
    assert encoded(thread.inspect_thread(tmp_path / "executed", host_profile="scalar",
                   expected_thread_ref=review["thread_ref"])) == encoded(review)
    restored = thread.restore_thread(tmp_path / "executed", tmp_path / "restored", host_profile="scalar",
                                      expected_thread_ref=review["thread_ref"])
    assert restored["execution_ids"] == review["execution_ids"]
    assert restored["active_authority_restored"] is False and restored["new_scientific_executions"] == 0
    assert {path.relative_to(tmp_path / "restored"): path.read_bytes()
            for path in (tmp_path / "restored").rglob("*") if path.is_file()} == before
    if encoded(previous) != encoded(engineering.compiler_identity()):
        with pytest.raises(ValueError, match="runtime|compiler"):
            thread.run_packet(tmp_path / "compiled", tmp_path / "denied", host_profile="scalar",
                              expected_compiled_ref=compiled["compiled_ref"], allow_compute=True)
        assert not (tmp_path / "denied").exists()
    assert dispatches == []


@pytest.mark.parametrize("fault", ["compiler", "gate", "unknown_version", "extra_file"])
def test_unknown_or_contradictory_compiler_refuses_offline_review(fault):
    from ciw.operations.runner import seal
    request = encoded(example())
    intent = encoded(engineering.example_intent("scalar", contract.bytes_ref(request)))
    compiled = engineering.compile_intent(intent, request, host_profile="scalar")
    if fault == "compiler":
        compiled["compiler"]["files"]["control_plane.py"] = "sha256:" + "0" * 64
    elif fault == "gate":
        compiled["bindings"]["gates"][engineering.GATE]["files"]["control_plane.py"] = "sha256:" + "0" * 64
        compiled["bindings"] = seal(compiled["bindings"])
    elif fault == "unknown_version":
        compiled["compiler"]["version"] = "unreviewed"
    else:
        compiled["compiler"]["files"]["untrusted.py"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        engineering.check_compilation(seal(compiled), intent, request, host_profile="scalar")


def checker_inputs(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/check_ncp_integrated.py"
    spec = importlib.util.spec_from_file_location("ncp_integration_checker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'installed_package', lambda: tmp_path / 'installed/ciw/__init__.py')
    producer = tmp_path / "producer"
    producer.mkdir()
    summary = encoded({"status": "PASS_SOFTWARE_SEAM_ONLY", "cases": dict.fromkeys(module.CASES, {})})
    (producer / "summary.json").write_bytes(summary)
    golden_root = tmp_path / "golden"
    golden_root.mkdir()
    golden = encoded({"reviews": dict.fromkeys(module.CASES, {}), "fixtures": {
        name: {"request_ref": "sha256:" + "0" * 64, "capture_ref": "sha256:" + "0" * 64}
        for name in module.CASES}})
    golden_path = golden_root / "journey.json"
    golden_path.write_bytes(golden)
    args = ["--producer-root", str(producer), "--summary-ref", module.reference(summary),
            "--golden-journey", str(golden_path), "--golden-ref", module.reference(golden)]
    return module, producer, golden_root, args


def test_checker_rejects_output_inside_golden_history_before_writing(tmp_path, monkeypatch):
    module, _, golden, args = checker_inputs(tmp_path, monkeypatch)
    destination = golden / "valid-execution/new-output"
    with pytest.raises((AssertionError, ValueError, FileNotFoundError)):
        module.main([*args, "--output-dir", str(destination)])
    assert not destination.exists()


def test_checker_rejects_changed_producer_envelope_before_cli(tmp_path, monkeypatch):
    module, producer, _, args = checker_inputs(tmp_path, monkeypatch)
    request = producer / "valid/analysis/study/request.json"
    request.parent.mkdir(parents=True)
    request.write_bytes(b"{}")
    capture = producer / "valid/capture/capture.json"
    capture.parent.mkdir()
    capture.write_bytes(b"{}")
    dispatches = []

    def forbidden(*args, **kwargs):
        dispatches.append("subprocess")
        raise AssertionError("CLI called before producer envelope binding")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    with pytest.raises(AssertionError):
        module.main([*args, "--output-dir", str(tmp_path / "output")])
    assert dispatches == []


def test_engineering_workspace_and_native_catalog_share_read_only_host(tmp_path, monkeypatch):
    request = encoded(example())
    intent = encoded(engineering.example_intent("scalar", contract.bytes_ref(request)))
    compiled = thread.compile_packet(intent, request, tmp_path / "compiled", host_profile="scalar")
    with pytest.raises(PermissionError):
        thread.run_packet(tmp_path / "compiled", tmp_path / "denied",
                          expected_compiled_ref=compiled["compiled_ref"], host_profile="scalar")
    assert not (tmp_path / "denied").exists()
    review = thread.run_packet(tmp_path / "compiled", tmp_path / "executed",
                               expected_compiled_ref=compiled["compiled_ref"],
                               host_profile="scalar", allow_compute=True)
    assert review["status"] == "EVIDENCE_REQUIRED"
    workspace_path = tmp_path / "executed/run/session/workspace.json"
    workspace_bytes = workspace_path.read_bytes()
    workspace = json.loads(workspace_bytes)
    model = next(item["data"] for item in workspace["results"] if item["operation_id"] == study.OPS[2])
    assert model["mean"] == pytest.approx(2.03)
    assert model["variance"] == pytest.approx(0.00190808)
    dispatches = []

    def forbidden(*args, **kwargs):
        dispatches.append((args, kwargs))
        raise AssertionError("Read-only inspection dispatched scientific work")

    monkeypatch.setattr(Session, "handle", forbidden)
    registry = study.registry(bind=False)
    catalog = encoded(registry.catalog())
    with AgentHost(registry=registry, inputs={"workspace": workspace_bytes, "catalog": catalog}) as host:
        capabilities = host.capabilities()
        assert capabilities["host_policy"]["allowed_operations"] == []
        assert capabilities["output_enabled"] is False
        assert capabilities["execution_budget"]["used"] == 0
        saved_catalog = host.inspect("catalog")
        assert saved_catalog["binding"]["sha256"] == contract.bytes_ref(catalog)
        for field, identifier in (("executions", "execution_id"), ("results", "result_id")):
            response = host.inspect("workspace", [field])
            original = {item[identifier]: item for item in workspace[field]}
            assert encoded(response["data"]) == encoded(original)
            assert set(response["data"]) == set(review[f"{identifier}s"])
        with pytest.raises(ValueError, match="read-only; output capability was not granted"):
            host.execute("workspace", "catalog", "forbidden")
        assert host.capabilities()["execution_budget"]["used"] == 0
    assert dispatches == []
    assert workspace_path.read_bytes() == workspace_bytes
