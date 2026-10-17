"""Executable application gates using real NET models and the shared Board."""
import base64
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

import pytest

from ciw.industrial_lab import (IndustrialLab, checked_spec, edit_specification,
                                project_board, run_view)
from ciw.core.identities import canonical_json
from ciw.session import Session
from ciw.system_spec import compile_spec, demo_spec
from ciw.system_workflow import RUN, COMPILE, VERIFY, execute
from ciw.visual_board import default_view, validate_view, render_html, demo_board
from ciw.visual_board_server import BoardWorkbench, make_server


def raw(spec=None):
    return json.dumps(spec or demo_spec(cells=8), indent=2).encode()


def edit(board, **overrides):
    request = {"schema": "ciw.board-parameter-edit.v1", "base_board_ref": board["record_digest"],
               "node_id": "thermal", "parameter": "left_temperature_k", "replacement": 318.15}
    request.update(overrides)
    return request


def work(tmp_path, **kw):
    return IndustrialLab(raw(), tmp_path / "lab", **kw)


def test_projection_uses_existing_board_and_preserves_coupling_metadata():
    spec = demo_spec(cells=8); original = deepcopy(spec)
    board = project_board(spec)
    assert board["schema"] == "ciw.system-board.v1"
    assert {n["kind"] for n in board["nodes"]} == {"MODEL"}
    assert {e["kind"] for e in board["edges"]} == {"SEMANTIC"}
    assert [e["metadata"] for e in board["edges"]] == spec["couplings"]
    assert spec == original
    assert project_board(spec) == board
    assert board["claims"]["provider_execution"] is False


@pytest.mark.parametrize("value", [True, "318.15", {}, [], None, float("nan"), float("inf"), -1, 1e100, 313.15])
def test_bad_values_refused(value):
    spec = demo_spec(cells=8); board = project_board(spec)
    with pytest.raises((ValueError, TypeError)):
        edit_specification(spec, board, edit(board, replacement=value))


@pytest.mark.parametrize("override", [
    {"schema": "arbitrary"}, {"base_board_ref": "sha256:" + "0"*64},
    {"node_id": "absent"}, {"node_id": []}, {"parameter": []},
    {"parameter": "length_m"}, {"parameter": "area_m2"}, {"parameter": "model_id"},
    {"node_id": "mechanical", "parameter": "boundary", "replacement": "fixed"},
    {"node_id": "sensor", "parameter": "seed", "replacement": 1.5},
    {"command": "execute unbound provider"},
])
def test_unsupported_edits_refuse(override):
    spec = demo_spec(cells=8); board = project_board(spec)
    with pytest.raises((ValueError, TypeError)):
        edit_specification(spec, board, edit(board, **override))


def test_tampered_projection_refused():
    spec = demo_spec(cells=8); board = project_board(spec)
    board["nodes"][0]["label"] = "other"
    with pytest.raises(ValueError, match="stale"):
        edit_specification(spec, board, edit(board))


@pytest.mark.parametrize("mode", ["too-large", "empty", "wrong-schema", "duplicate-json", "too-many-cells", "too-many-steps", "unknown-model"])
def test_source_boundaries(mode):
    spec = demo_spec(cells=8)
    if mode == "too-large": value = b" " * 65537
    elif mode == "empty": value = b""
    elif mode == "duplicate-json": value = b'{"schema":"a","schema":"b"}'
    else:
        if mode == "wrong-schema": spec["schema"] = "invented"
        elif mode == "too-many-cells": spec = demo_spec(cells=128)
        elif mode == "too-many-steps":
            spec["clock"]["duration"] = 2500.0
        else: spec["nodes"][0]["model_id"] = "unbound-model.v1"
        value = raw(spec)
    with pytest.raises((ValueError, TypeError)):
        checked_spec(value)


def test_preview_and_view_do_not_execute_or_mutate(tmp_path):
    w = work(tmp_path); original = deepcopy((w.specification, w.board, w.session.results))
    with patch("ciw.industrial_lab.run_specification", side_effect=AssertionError("unexpected execution")):
        p = w.preview(edit(w.board))
        view = default_view(w.board); view["positions"] = {"node:thermal": {"x": 100, "y": 200}}
        saved = w.retain_view(view)
        assert validate_view(saved["view"], w.board)
        assert p["preview"]["compilation"] == compile_spec(p["preview"]["specification"])
    assert (w.specification, w.board, w.session.results) == original
    assert p["preview"]["claims"]["provider_execution"] is False
    with pytest.raises(ValueError, match="disabled"): w.run_baseline()


def test_same_session_baseline_candidate_reopen_and_fresh_replay(tmp_path):
    w = work(tmp_path, allow_run=True, engine="local")
    baseline = w.run_baseline(); original = deepcopy((w.board, w.baseline, w.session.results))
    request = edit(w.board)
    with pytest.raises(ValueError, match="Compile and retain"):
        w.run_candidate(request, baseline["baseline_ref"])
    w.preview(request)
    candidate = w.run_candidate(request, baseline["baseline_ref"])
    assert len(w.session.results) == 6
    assert w.board == original[0] and w.baseline == original[1]
    assert all(w.session.results[k] == v for k, v in original[2].items())
    assert baseline["lab"]["source_evidence_id"] == candidate["lab"]["source_evidence_id"]
    assert baseline["lab"]["source_result_id"] != candidate["lab"]["source_result_id"]
    assert baseline["lab"]["trajectory"]["temperature_k"] != candidate["lab"]["trajectory"]["temperature_k"]
    assert candidate["lab"]["verification"]["report"]["status"] == "PASS"
    assert candidate["receipt"]["claims"]["reduction_comparison_executed"] is False
    assert w.state()["lab_candidate"] == candidate["lab"]
    before = deepcopy(w.session.results)
    with patch("ciw.system_models.simulate", side_effect=AssertionError("reopen executed physics")):
        restored = Session.from_workspace(w.output_dir / "session/workspace.json", tmp_path / "restore")
    assert restored.results == before
    plan = next(r for r in restored.results.values() if r["operation_id"] == COMPILE)
    replay = execute(restored, RUN, {"plan": plan, "source_result_id": plan["result_id"], "engine": "local"})
    original_candidate = w.baseline["candidate"]
    assert replay["execution_id"] != original_candidate["execution_id"]
    assert replay["result_id"] != original_candidate["result_id"]
    assert replay["data"]["simulation"] == original_candidate["data"]["simulation"]
    assert original_candidate == w.session.results[original_candidate["result_id"]]
    assert all(r["verification_id"] is None for r in before.values())
    assert len({r["data"]["verification_id"] for r in before.values() if r["operation_id"] == VERIFY}) == 2
    # Original input formatting is retained, not replaced by normalized JSON.
    source = w.session.workbench.get_source(w.source["source_id"])
    assert base64.b64decode(source["bytes_b64"]) == raw()


def test_wrong_baseline_and_repeated_baseline_refuse(tmp_path):
    w = work(tmp_path, allow_run=True, engine="local"); b = w.run_baseline(); req = edit(w.board); w.preview(req)
    with pytest.raises(ValueError, match="already retained"): w.run_baseline()
    with pytest.raises(ValueError, match="different baseline"): w.run_candidate(req, "result-wrong")
    with pytest.raises(ValueError, match="promotion"): w.run_candidate(req, b["baseline_ref"], "sha256:" + "0"*64)
    with pytest.raises(ValueError, match="expansion"): w.qualify_expansion(req, b["baseline_ref"])
    assert len(w.session.results) == 3


def test_worker_execution_is_real_subprocess(tmp_path):
    w = work(tmp_path, allow_run=True, engine="subprocess")
    result = w.run_baseline()
    assert w.baseline["candidate"]["data"]["execution_runtime"]["engine"] == "subprocess"
    assert result["lab"]["verification"]["report"]["status"] == "PASS"


def test_failure_is_not_promoted(tmp_path):
    spec = demo_spec(cells=8)
    spec["tolerances"]["analytic_temperature_K"] = 1e-12
    w = IndustrialLab(raw(spec), tmp_path / "lab", allow_run=True, engine="local")
    result = w.run_baseline()
    assert result["lab"]["verification"]["report"]["status"] == "FAIL"
    assert result["lab"]["claims"]["physical_validation_status"] == "not_assessed"


def test_source_no_clobber_and_authority(tmp_path):
    w = work(tmp_path)
    with pytest.raises(FileExistsError): work(tmp_path)
    with pytest.raises(ValueError): IndustrialLab(raw(), tmp_path / "other", engine="oci")
    assert not (tmp_path / "other").exists()
    with pytest.raises(ValueError): IndustrialLab(raw(), tmp_path / "other", allow_run=1)


def test_http_uses_original_fixed_transport(tmp_path):
    w = work(tmp_path, allow_run=True, engine="local")
    server = make_server(w); thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    def send(method, path, value=None, **headers):
        c = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        body = json.dumps(value) if value is not None else None
        h = {"Content-Type": "application/json", **headers}
        c.request(method, path, body=body, headers=h); r = c.getresponse(); data = r.read();c.close()
        return r.status, data
    try:
        assert send("GET", "/api/state")[0] == 403
        h = {"X-NET-Board-Token": server.board_token}
        assert send("GET", "/api/state", **h)[0] == 200
        assert send("GET", "/api/state", **{**h,"Origin":"https://outside.invalid"})[0] == 403
        assert send("GET", "/api/state", **{**h,"Host":"outside.invalid"})[0] == 403
        assert send("POST", "/api/shell", {}, **h)[0] == 404
        assert send("POST", "/api/baseline", {"board_ref":"wrong"}, **h)[0] == 400
        code, body = send("POST", "/api/baseline", {"board_ref":w.board["record_digest"]}, **h)
        assert code == 200
        assert json.loads(body)["lab"]["source_result_id"] == w.baseline["candidate"]["result_id"]
        assert send("POST", "/api/expand", {"request":edit(w.board), "baseline_ref":w.baseline["candidate"]["result_id"]}, **h)[0] == 400
        req = edit(w.board)
        code, body = send("POST", "/api/edit", req, **h)
        assert code == 200 and len(w.session.results) == 3
        code, body = send("POST", "/api/candidate", {"request": req,
                         "baseline_ref": w.baseline["candidate"]["result_id"], "promotion_ref": None}, **h)
        assert code == 200 and len(w.session.results) == 6
        assert json.loads(body)["lab"] == run_view(w.latest_candidate)
        code, body = send("GET", "/api/state", **h)
        assert code == 200 and json.loads(body)["lab_candidate"] == run_view(w.latest_candidate)
        assert len(w.session.results) == 6
        code, body = send("GET", "/")
        assert code == 200 and w.source["evidence_id"].encode() not in body
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_original_board_still_runs_its_own_profile(tmp_path):
    from ciw.instruments import make_demo_run
    b = BoardWorkbench(demo_board(), tmp_path / "original", source=make_demo_run(), allow_run=True)
    r = b.run_baseline()
    assert "lab" not in r and "run" in r
    assert r["run"]["status"] == "completed"


def test_offline_html_has_no_execution_or_external_resources():
    h = render_html(project_board(demo_spec(cells=8))).decode()
    assert "connect-src 'none'" in h
    assert "script-src 'sha256-" in h
    assert "__SCRIPT__" not in h and "__CSP__" not in h


def test_cli_real_demo_run_and_no_clobber(tmp_path):
    from ciw.industrial_lab_cli import main
    demo = tmp_path / "demo"
    assert main(["demo", "--output-dir", str(demo)]) == 0
    assert main(["demo", "--output-dir", str(demo)]) == 2
    board = json.loads((demo / "board.json").read_text())
    request = tmp_path / "edit.json";request.write_text(json.dumps(edit(board)))
    result = tmp_path / "run"
    assert main(["run", str(demo / "specification.json"), "--output-dir", str(result), "--engine", "local", "--edit", str(request)]) == 0
    summary = json.loads((result / "summary.json").read_text())
    assert summary["candidate_result_id"] is not None
    assert Path(summary["workspace"]).is_file() and Path(summary["review"]).is_file()


def test_source_is_reopenable_before_any_execution(tmp_path):
    w = work(tmp_path)
    with patch("ciw.system_models.simulate", side_effect=AssertionError("inspection executed")):
        restored = Session.from_workspace(w.session.output_dir / "workspace.json", tmp_path / "reopened")
    source = restored.workbench.get_source(w.source["source_id"])
    assert base64.b64decode(source["bytes_b64"]) == raw()
    assert not restored.results


def test_state_returns_detached_data(tmp_path):
    w = work(tmp_path, allow_run=True, engine="local")
    w.run_baseline()
    state = w.state()
    state["board"]["nodes"][0]["label"] = "changed"
    state["lab_baseline"]["trajectory"]["temperature_k"][0][0] = -999
    assert w.state()["board"]["nodes"][0]["label"] != "changed"
    assert w.state()["lab_baseline"]["trajectory"]["temperature_k"][0][0] != -999
