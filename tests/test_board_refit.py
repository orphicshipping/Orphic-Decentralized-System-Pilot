from copy import deepcopy
import json
from pathlib import Path
import pytest
from ciw.board_refit import BoardRefitHost, BoardRefitRefusal
from ciw.core.identities import content_identity
from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.visual_board import demo_board
from ciw.visual_board_server import BoardWorkbench


def inputs():
    board, source = demo_board(), make_demo_run()
    grant = {"schema": "ciw.board-refit-grant.v1", "grant_id": "operator-grant",
             "board_ref": board["record_digest"], "source_evidence_id": source["evidence_id"],
             "source_ref": content_identity(source), "allow_baseline": True,
             "targets": [{"node_id": "statistics", "parameter": "channel"}],
             "max_candidate_runs": 2, "max_previews": 2}
    return board, source, grant


def host_for(tmp_path, *, board=None, source=None, grant=None, **kwargs):
    b, s, g = inputs()
    return BoardRefitHost(b if board is None else board, tmp_path / "host",
                         source=s if source is None else source, grant=g if grant is None else grant, **kwargs)


def preview(host, replacement="v"):
    return host.call("ncp_board_preview", {"base_board_ref": host.board["record_digest"],
                     "node_id": "statistics", "parameter": "channel", "replacement": replacement})


def validated(host, replacement="v"):
    p = preview(host, replacement)
    v = host.call("ncp_board_validate", {"preview_ref": p["preview_ref"]})
    return p, v


def candidate_args(host, *, replacement="v", attempt="candidate-1"):
    p, v = validated(host, replacement)
    b = host.call("ncp_board_baseline", {"attempt_id": "baseline-1"})
    return {"preview_ref": p["preview_ref"], "validation_ref": v["validation_ref"],
            "baseline_ref": b["baseline_ref"], "attempt_id": attempt}


def refuse(code, fn):
    with pytest.raises(BoardRefitRefusal) as exc:
        fn()
    assert exc.value.code == code


def test_complete_journey_preserves_baseline_and_reuses_results(tmp_path):
    board, source, grant = inputs()
    original = deepcopy((board, source, grant))
    with host_for(tmp_path, board=board, source=source, grant=grant) as host:
        args = candidate_args(host)
        before = host.call("ncp_board_baseline", {"attempt_id": "baseline-1"})
        value = host.call("ncp_board_recompute", args)
        assert value["status"] == "completed"
        assert value["rerun_nodes"] == ["statistics", "spectrum"]
        assert value["reused_nodes"] == ["energy_statistics"]
        assert value["node_result_refs"]["energy_statistics"] == before["node_result_refs"]["energy_statistics"]
        assert value["node_parameters"]["statistics"]["channel"] == "v"
        assert value["node_parameters"]["spectrum"]["channel"] == "q"
        assert value["accepted"] is False and value["review_status"] == "unreviewed"
        comparison = host.call("ncp_board_compare", {"candidate_ref": value["candidate_ref"]})
        rows = {row["node_id"]: row for row in comparison["nodes"]}
        assert rows["statistics"]["data_changed"] is True
        assert rows["spectrum"]["data_changed"] is False
        assert rows["energy_statistics"]["recomputation"] == "REUSED"
        assert host.call("ncp_board_baseline", {"attempt_id": "baseline-1"}) == before
        assert host.call("ncp_board_recompute", args) == value
        assert (board, source, grant) == original
        bundles = list((tmp_path / "host").glob("attempt-*/workspace.json"))
        assert len(bundles) == 2
        assert sum(len(json.loads(path.read_text())["executions"]) for path in bundles) == 5


def test_preview_validation_and_discovery_never_execute(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider execution during planning")
    monkeypatch.setattr(BoardWorkbench, "run_baseline", forbidden)
    monkeypatch.setattr(BoardWorkbench, "run_candidate", forbidden)
    with host_for(tmp_path) as host:
        assert len(host.descriptions()) == 7
        p, v = validated(host)
        assert p["affected_nodes"] == ["statistics", "spectrum"]
        assert p["unaffected_nodes"] == ["energy_statistics"]
        assert p["provider_execution"] is False
        assert v["source_selection_checked"] is True and v["provider_execution"] is False
        assert host.call("ncp_board_status", {})["baseline_ref"] is None
        assert list((tmp_path / "host").glob("attempt-*")) == []


def test_resume_recovers_exact_outcomes_without_provider_execution(tmp_path, monkeypatch):
    board, source, grant = inputs()
    host = host_for(tmp_path, board=board, source=source, grant=grant, investigation_ref="investigation-1")
    args = candidate_args(host)
    candidate = host.call("ncp_board_recompute", args)
    host.close()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider replay after recovery")
    monkeypatch.setattr(BoardWorkbench, "run_baseline", forbidden)
    monkeypatch.setattr(BoardWorkbench, "run_candidate", forbidden)
    with host_for(tmp_path, board=board, source=source, grant=grant, investigation_ref="investigation-1", resume=True) as recovered:
        assert recovered.call("ncp_board_recompute", args) == candidate
        assert recovered.call("ncp_board_candidate", {"candidate_ref": candidate["candidate_ref"]}) == candidate
        assert recovered.call("ncp_board_status", {})["baseline_ref"] == args["baseline_ref"]


def test_interrupted_candidate_is_claimed_durably_and_never_replayed(tmp_path, monkeypatch):
    board, source, grant = inputs()
    host = host_for(tmp_path, board=board, source=source, grant=grant)
    args = candidate_args(host)
    calls = []
    def interrupted(*_args, **_kwargs):
        calls.append(1)
        raise KeyboardInterrupt()
    monkeypatch.setattr(BoardWorkbench, "run_candidate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        host.call("ncp_board_recompute", args)
    with host_for(tmp_path, board=board, source=source, grant=grant, resume=True) as recovered:
        refuse("attempt_outcome_unknown", lambda: recovered.call("ncp_board_recompute", args))
        refuse("attempt_outcome_unknown", lambda: recovered.call("ncp_board_recompute", {**args, "attempt_id": "renamed-retry"}))
        assert recovered.call("ncp_board_status", {})["attempts"][-1]["status"] == "outcome_unknown"
    assert calls == [1]


def test_failed_final_checkpoint_requires_reconnect_and_does_not_replay(tmp_path, monkeypatch):
    board, source, grant = inputs()
    host = host_for(tmp_path, board=board, source=source, grant=grant)
    args = candidate_args(host)
    original_save = host._store.save
    saves = []
    def fail_final(state, artifacts):
        saves.append(1)
        if len(saves) == 2:
            raise OSError("simulated storage failure")
        return original_save(state, artifacts)
    monkeypatch.setattr(host._store, "save", fail_final)
    with pytest.raises(OSError):
        host.call("ncp_board_recompute", args)
    with pytest.raises(ValueError, match="closed"):
        host.call("ncp_board_status", {})
    with host_for(tmp_path, board=board, source=source, grant=grant, resume=True) as recovered:
        refuse("attempt_outcome_unknown", lambda: recovered.call("ncp_board_recompute", args))
        assert recovered.call("ncp_board_status", {})["candidate_refs"] == []


def test_grant_revocation_applies_to_discovery_planning_and_retained_results(tmp_path):
    authorized = [True]
    def authority():
        if not authorized[0]:
            raise PermissionError("revoked")
    with host_for(tmp_path, authority_check=authority) as host:
        args = candidate_args(host)
        candidate = host.call("ncp_board_recompute", args)
        authorized[0] = False
        for action in (host.descriptions, lambda: preview(host),
                       lambda: host.call("ncp_board_candidate", {"candidate_ref": candidate["candidate_ref"]}),
                       lambda: host.call("ncp_board_recompute", args)):
            refuse("authorization_refused", action)


def test_budget_and_exact_attempt_inputs_are_enforced(tmp_path):
    board, source, grant = inputs()
    grant["max_candidate_runs"] = 1
    with host_for(tmp_path, board=board, source=source, grant=grant) as host:
        first = candidate_args(host)
        host.call("ncp_board_recompute", first)
        second = candidate_args(host, replacement="energy", attempt="candidate-2")
        refuse("budget_exhausted", lambda: host.call("ncp_board_recompute", second))
        second["attempt_id"] = first["attempt_id"]
        refuse("attempt_conflict", lambda: host.call("ncp_board_recompute", second))
        refuse("baseline_already_established", lambda: host.call("ncp_board_baseline", {"attempt_id": "baseline-2"}))


@pytest.mark.parametrize("changes,code", [
    ({"base_board_ref": "sha256:" + "0" * 64}, "stale_board"),
    ({"node_id": "spectrum"}, "scope_refused"),
    ({"replacement": "pressure"}, "invalid_arguments"),
    ({"replacement": "q"}, "invalid_arguments"),
    ({"replacement": True}, "invalid_arguments"),
    ({"grant": {}}, "invalid_arguments"),
])
def test_model_cannot_escape_target_domain_or_supply_authority(tmp_path, changes, code):
    with host_for(tmp_path) as host:
        args = {"base_board_ref": host.board["record_digest"], "node_id": "statistics", "parameter": "channel", "replacement": "v"}
        refuse(code, lambda: host.call("ncp_board_preview", {**args, **changes}))


def test_resume_refuses_changed_bindings_and_active_writer(tmp_path):
    board, source, grant = inputs()
    host = host_for(tmp_path, board=board, source=source, grant=grant)
    with pytest.raises(ValueError, match="active writer"):
        host_for(tmp_path, board=board, source=source, grant=grant, resume=True)
    host.close()
    changed = deepcopy(grant)
    changed["max_candidate_runs"] += 1
    with pytest.raises(ValueError, match="bindings differ"):
        host_for(tmp_path, board=board, source=source, grant=changed, resume=True)


def test_candidate_requires_exact_validation_and_baseline(tmp_path):
    with host_for(tmp_path) as host:
        first = candidate_args(host)
        p, v = validated(host, "energy")
        refuse("validation_mismatch", lambda: host.call("ncp_board_recompute", {**first, "preview_ref": p["preview_ref"]}))
        refuse("baseline_mismatch", lambda: host.call("ncp_board_recompute", {**first, "baseline_ref": "sha256:" + "0" * 64}))
        assert host.call("ncp_board_status", {})["budgets"]["candidate_runs_used"] == 0


def test_preview_budget_is_deduplicated(tmp_path):
    board, source, grant = inputs()
    grant["max_previews"] = 1
    with host_for(tmp_path, board=board, source=source, grant=grant) as host:
        p = preview(host)
        assert preview(host) == p
        refuse("budget_exhausted", lambda: preview(host, "energy"))


def test_baseline_disabled_by_operator_grant(tmp_path):
    board, source, grant = inputs()
    grant["allow_baseline"] = False
    with host_for(tmp_path, board=board, source=source, grant=grant) as host:
        validated(host)
        refuse("authorization_refused", lambda: host.call("ncp_board_baseline", {"attempt_id": "b"}))
        assert host.call("ncp_board_status", {})["attempts"] == []


def test_validation_checks_source_channel_not_only_board_domain(tmp_path):
    board, source, grant = inputs()
    board["nodes"][3]["parameters"]["channel"]["domain"]["values"].append("unrecorded")
    board = seal(board)
    grant["board_ref"] = board["record_digest"]
    with host_for(tmp_path, board=board, source=source, grant=grant) as host:
        p = preview(host, "unrecorded")
        refuse("invalid_arguments", lambda: host.call("ncp_board_validate", {"preview_ref": p["preview_ref"]}))
        assert host.call("ncp_board_status", {})["attempts"] == []


def test_resume_refuses_changed_catalog_contracts(tmp_path, monkeypatch):
    from ciw.semantic_capabilities import SemanticRegistry
    board, source, grant = inputs()
    host = host_for(tmp_path, board=board, source=source, grant=grant)
    host.close()
    original = SemanticRegistry.catalog
    def changed_catalog(self):
        catalog = original(self)
        catalog["audit_contract_change"] = True
        return catalog
    monkeypatch.setattr(SemanticRegistry, "catalog", changed_catalog)
    with pytest.raises(ValueError, match="bindings differ"):
        host_for(tmp_path, board=board, source=source, grant=grant, resume=True)
