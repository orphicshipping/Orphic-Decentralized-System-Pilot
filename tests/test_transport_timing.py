"""Timing reports retain failures and observations without promising real-time control."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, patch

import pytest
from websockets.asyncio.server import serve

from ciw import transport_timing as timing
from ciw.instruments import make_demo_run
from ciw.server import WorkbenchServer
from ciw.session import Session, read_json


def response(session="session-one", run="run-one", evidence="evidence-one"):
    return {"protocol_version": 1, "request_id": "probe", "type": "response", "payload": {
        "session_id": session, "run": {"run_id": run, "evidence_id": evidence,
        "instrument": "fixture", "channels": {"q": {}}, "metadata": {}},
        "selection": {"run_id": run, "revision": 0, "channel": "q"}, "results": []}}


def observed(values=(10, 30, 20), *, outcomes=None, budget_ms=None):
    ticks = []
    for index, value in enumerate(values):
        start = index * 10_000_000_000
        ticks.extend((start, start + round(value * 1_000_000)))
    request = AsyncMock(side_effect=outcomes or [response() for _ in values])
    with patch.object(timing, "request_remote", request), patch.object(timing.time, "perf_counter_ns", side_effect=ticks):
        report = asyncio.run(timing.measure_transport(samples=len(values), budget_ms=budget_ms))
    for call in request.await_args_list:
        assert call.args == (timing.DEFAULT_URL, "session.get", {})
        assert call.kwargs == {"timeout_s": 3}
    return report


def test_deterministic_metrics_use_declared_nearest_rank_population():
    report = observed(budget_ms=20)
    assert report["summary"] == {
        "status": "observed_budget_exceeded", "attempted": 3, "successful": 3, "failed": 0,
        "timeout_count": 0, "observed_budget_exceedances": 1, "successful_budget_exceedances": 1,
        "identity_stable": True,
        "identities": [{"session_id": "session-one", "run_id": "run-one", "evidence_id": "evidence-one"}],
        "aggregation_valid": True, "metrics_population": "successful_samples_only",
        "percentile_method": "nearest_rank",
        "latency_ms": {"count": 3, "minimum_ms": 10, "p50_ms": 20, "p95_ms": 30, "maximum_ms": 30}}
    assert report["hard_real_time_qualified"] is False
    assert [row["budget_exceeded"] for row in report["samples"]] == [False, True, False]
    assert timing.validate_report(report) is report


def test_success_with_and_without_budget_are_distinct():
    assert observed((10,), budget_ms=10)["summary"]["status"] == "observed_within_budget"
    report = observed((10,))
    assert report["summary"]["status"] == "observed"
    assert report["samples"][0]["budget_exceeded"] is None


def test_failures_remain_visible_outside_successful_only_percentiles():
    report = observed((10, 3000, 20, 1), budget_ms=50,
                      outcomes=[response(), TimeoutError("private secret"), response(), OSError("private secret")])
    summary = report["summary"]
    assert summary["status"] == "incomplete"
    assert (summary["attempted"], summary["successful"], summary["failed"], summary["timeout_count"]) == (4, 2, 2, 1)
    assert summary["latency_ms"]["maximum_ms"] == 20
    assert summary["latency_ms"]["count"] == 2
    assert summary["observed_budget_exceedances"] == 1
    assert summary["successful_budget_exceedances"] == 0
    assert report["samples"][1]["error"] == "timeout"
    assert report["samples"][3]["error"] == "transport_error"
    assert "private secret" not in json.dumps(report)


def test_all_failed_observations_do_not_report_zero_latency():
    report = observed((1, 2), outcomes=[TimeoutError(), ValueError()])
    summary = report["summary"]
    assert summary["status"] == "incomplete"
    assert summary["successful"] == 0 and summary["failed"] == 2
    assert summary["latency_ms"] is None and summary["identity_stable"] is None
    assert summary["aggregation_valid"] is False
    assert report["samples"][1]["error"] == "invalid_response"


@pytest.mark.parametrize("changed", [{"session": "session-two"}, {"run": "run-two"}, {"evidence": "evidence-two"}])
def test_identity_change_invalidates_aggregation_but_retains_every_observation(changed):
    report = observed((1, 2, 3), outcomes=[response(), response(**changed), response()])
    assert report["summary"]["status"] == "invalid_identity_change"
    assert report["summary"]["successful"] == 3
    assert report["summary"]["aggregation_valid"] is False
    assert report["summary"]["latency_ms"] is None
    assert len(report["summary"]["identities"]) == 2
    assert len(report["samples"]) == 3


@pytest.mark.parametrize("invalid", [None, [], {}, {"protocol_version": True},
    {"protocol_version": 1, "request_id": "x", "type": "error", "payload": {"message": "secret"}}])
def test_malformed_responses_are_failed_samples(invalid):
    report = observed((1,), outcomes=[invalid])
    assert report["samples"][0]["error"] == "invalid_response"
    assert report["summary"]["latency_ms"] is None


@pytest.mark.parametrize("mutation", [
    lambda r: r["payload"].pop("results"),
    lambda r: r["payload"].update(session_id=""),
    lambda r: r["payload"]["run"].update(evidence_id=123),
    lambda r: r["payload"]["selection"].update(revision=True),
    lambda r: r["payload"]["selection"].update(run_id="other"),
])
def test_malformed_session_identity_is_not_success(mutation):
    value = response()
    mutation(value)
    assert observed((1,), outcomes=[value])["samples"][0]["error"] == "invalid_response"


@pytest.mark.parametrize("url", ["ws://example.org", "ws://192.168.1.2", "http://127.0.0.1",
    "ws://user:secret@127.0.0.1:8765", "ws://127.0.0.1:8765/?token=secret",
    "ws://127.0.0.1:8765/#secret", "ws://127.0.0.1:8765/spatial", "ws://127.0.0.1:0",
    "ws://127.0.0.1:99999", "ws://127.0.0.1:8765\n", "ws://127.0.0.1:8765/?", None])
def test_endpoint_refusal_precedes_network_and_does_not_repeat_credentials(url):
    with patch.object(timing, "request_remote", new=AsyncMock()) as transport:
        with pytest.raises(ValueError) as error:
            asyncio.run(timing.measure_transport(url, samples=1))
        assert "secret" not in str(error.value)
        transport.assert_not_awaited()


@pytest.mark.parametrize("url", ["ws://127.0.0.1:8765", "ws://localhost:8765/", "wss://[::1]:8765"])
def test_loopback_endpoint_forms(url):
    assert timing.validate_endpoint(url) == url


@pytest.mark.parametrize("parameters", [{"samples": 0}, {"samples": 101}, {"samples": True},
    {"samples": 1.5}, {"timeout_s": 0}, {"timeout_s": 31}, {"timeout_s": float("nan")},
    {"timeout_s": True}, {"samples": 100, "timeout_s": 2}, {"budget_ms": 0},
    {"budget_ms": float("inf")}, {"budget_ms": True}, {"timeout_s": 10**400}, {"budget_ms": 10**400}])
def test_campaign_bounds_precede_network(parameters):
    with patch.object(timing, "request_remote", new=AsyncMock()) as transport:
        with pytest.raises(ValueError):
            asyncio.run(timing.measure_transport(**parameters))
        transport.assert_not_awaited()


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(hard_real_time_qualified=True),
    lambda r: r["summary"].update(failed=0),
    lambda r: r["summary"].update(aggregation_valid=1),
    lambda r: r["samples"][0].update(elapsed_ms=-1),
    lambda r: r["samples"][0].update(elapsed_ms=float("nan")),
    lambda r: r["samples"][0].update(budget_exceeded=True),
    lambda r: r["samples"][0].update(index=True),
    lambda r: r["parameters"].update(samples=2),
])
def test_inspection_recomputes_semantics_even_if_digest_is_recomputed(mutation):
    report = observed((1,), outcomes=[TimeoutError()])
    mutation(report)
    if not math_contains_nan(report):
        report["record_digest"] = timing._digest(report)
    with pytest.raises(ValueError):
        timing.validate_report(report)


def math_contains_nan(value):
    return "NaN" in json.dumps(value)


def test_digest_detects_retained_observation_change():
    report = observed((1,))
    report["host"]["release"] += " changed"
    with pytest.raises(ValueError, match="digest"):
        timing.validate_report(report)


def test_new_report_and_inspect_do_not_contact_endpoint_or_replace_file(tmp_path, capsys):
    report = observed((1,))
    target = tmp_path / "timing.json"
    timing.write_report(target, report)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        timing.write_report(target, observed((2,)))
    assert target.read_bytes() == original
    with patch.object(timing, "request_remote", new=AsyncMock()) as transport:
        assert timing.main(["inspect", str(target)]) == 0
        assert json.loads(capsys.readouterr().out) == report
        assert timing.main(["run", "--output", str(target)]) == 2
        transport.assert_not_awaited()
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("outcomes,budget,exit_code", [([response()], None, 0),
    ([response()], 1, 1), ([TimeoutError()], None, 1)])
def test_cli_saves_complete_campaign_even_when_observed_budget_or_request_fails(tmp_path, outcomes, budget, exit_code):
    target = tmp_path / "report.json"
    command = ["run", "--samples", "1", "--output", str(target)]
    if budget is not None:
        command += ["--budget-ms", str(budget)]
    with patch.object(timing, "request_remote", new=AsyncMock(side_effect=outcomes)), \
            patch.object(timing.time, "perf_counter_ns", side_effect=[0, 2_000_000]):
        assert timing.main(command) == exit_code
    assert timing.validate_report(read_json(target))["summary"]["attempted"] == 1


def test_real_socket_probe_does_not_mutate_session_or_server_files(tmp_path):
    async def exercise():
        session = Session(make_demo_run(), tmp_path)
        before = deepcopy(session.snapshot())
        files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        bridge = WorkbenchServer(session)
        async with serve(bridge.handler, "127.0.0.1", 0) as listener:
            url = "ws://127.0.0.1:" + str(listener.sockets[0].getsockname()[1])
            report = await timing.measure_transport(url, samples=2, timeout_s=2)
        assert report["summary"]["successful"] == 2
        assert report["summary"]["status"] == "observed"
        assert report["summary"]["identities"][0]["session_id"] == session.session_id
        assert session.snapshot() == before
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == files
        assert session.results == session.executions == {}
    asyncio.run(exercise())


def test_real_silent_endpoint_has_retained_timeout():
    async def silent(websocket):
        await websocket.wait_closed()

    async def exercise():
        async with serve(silent, "127.0.0.1", 0) as listener:
            url = "ws://127.0.0.1:" + str(listener.sockets[0].getsockname()[1])
            report = await asyncio.wait_for(timing.measure_transport(url, samples=1, timeout_s=.05), 3)
        assert report["samples"][0]["error"] == "timeout"
        assert report["summary"]["timeout_count"] == 1
        assert report["summary"]["latency_ms"] is None
    asyncio.run(exercise())


def test_derived_timeout_total_cannot_be_replaced_by_boolean():
    report = observed((1,))
    report["parameters"].update(timeout_s=1, configured_total_request_timeout_s=True)
    report["record_digest"] = timing._digest(report)
    with pytest.raises(ValueError, match="parameters"):
        timing.validate_report(report)
