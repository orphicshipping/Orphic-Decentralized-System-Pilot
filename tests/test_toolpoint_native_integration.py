"""Current manufacturing reducers and top-level QC dispatch, with no release authority."""
from copy import deepcopy
import importlib.util
import json

import pytest

from ciw.manufacturing_contracts import content_identity, load
from ciw.manufacturing_runtime import evaluate as manufacture
from ciw.polymer_cli import main
from ciw.polymer_quality_example import manufacturing_request, source
from ciw.toolpoint_stream_containment import evaluate, gate, verify
from ciw.toolpoint_stream_example import demo
from ciw.toolpoint_stream_store import StreamStore, initialize


@pytest.mark.parametrize("instrument", ["stream", "store", "detector", "outcome"])
def test_native_qc_help_routes(instrument, capsys):
    with pytest.raises(SystemExit) as exit:
        main(["qc", instrument, "--help"])
    assert exit.value.code == 0
    assert "net polymer qc " + instrument in capsys.readouterr().out


@pytest.mark.parametrize("instrument,expected", [("stream", 2), ("detector", 2), ("outcome", 2)])
def test_native_qc_saved_demo_replays_but_never_qualifies(instrument, expected, tmp_path, capsys):
    path = tmp_path / "report.json"
    assert main(["qc", instrument, "demo", "--output", str(path)]) == expected
    report = load(path)
    assert report["result"]["authority"]["action_authorized"] is False
    assert report["result"]["authority"]["shipment_release"] == "not_authorized"
    assert main(["qc", instrument, "verify", str(path)]) == 0
    before = path.read_bytes()
    assert main(["qc", instrument, "demo", "--output", str(path)]) == 1
    assert path.read_bytes() == before
    capsys.readouterr()


def test_native_help_discovers_all_instruments(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["qc", "--help"])
    assert exit.value.code == 0
    help_text = capsys.readouterr().out
    assert all(name in help_text for name in ("stream", "store", "detector", "outcome"))


@pytest.mark.parametrize("instrument", ["stream", "detector"])
def test_unavailable_snapshot_profile_is_structured_refusal(instrument, tmp_path, capsys):
    # The qualified current repository deliberately does not vendor the old reducer.
    if importlib.util.find_spec("ciw.toolpoint_quality") is not None:
        pytest.skip("Optional snapshot adapter was separately supplied")
    path = tmp_path / "unexpected.json"
    assert main(["qc", instrument, "demo", "--profile", "legacy", "--output", str(path)]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "REFUSE"
    assert not path.exists()


def held_manufacturing():
    request = manufacturing_request()
    request["events"].append({
        "event_id": "synthetic.postpack-hold", "at_s": 11,
        "actor_id": "synthetic.inspector", "authorization_ref": source("inspector"),
        "kind": "quality_hold", "data": {"serial": "p001",
            "reason": "Independent post-pack inspection concern", "evidence_ref": source("concern")},
    })
    return manufacture(request)


def test_reopened_clean_streams_cannot_clear_current_v2_postpack_hold(tmp_path):
    manufacturing = held_manufacturing()
    before = deepcopy(manufacturing)
    path = tmp_path / "cache.sqlite3"
    initialize(path)
    anchors = []
    for index in range(2):
        report = demo(case="nominal", cycle_index=index)
        spec, head = report["spec_ref"], report["receipts"][-1]["receipt_ref"]
        StreamStore(path).import_report(str(index), report, expected_spec_ref=spec, expected_head_ref=head)
        anchors.append((spec, head))
    streams = [StreamStore(path).export(str(index), expected_spec_ref=spec, expected_head_ref=head)
               for index, (spec, head) in enumerate(anchors)]
    assert all(s["result"]["status"] == "NO_PROCESS_HOLD_IDENTIFIED" for s in streams)
    report = evaluate({"schema": "ciw.toolpoint-stream-containment-request.v1",
        "manufacturing_report": manufacturing, "streams": streams, "assessed_at_s": 12})
    assert manufacturing == before
    assert verify(report)["status"] == "PASS"
    quality = report["result"]["quality"]
    assert quality["p001"]["recorded_inspection"] == "PASS"
    assert quality["p001"]["state"] == quality["a001"]["state"] == "HELD"
    assert quality["p004"]["state"] == "SCRAPPED"
    assert report["result"]["packages"]["synthetic.package"]["state"] == "HELD"
    assert gate(report, serials=["a001"], action="release_review")["status"] == "HELD"
    assert report["result"]["authority"]["shipment_release"] == "not_authorized"
    assert any(f["code"] == "EXISTING_MANUFACTURING_QC_HOLD" for f in report["result"]["findings"])


@pytest.mark.parametrize("target", ["manufacturing", "projection"])
def test_rehashed_hold_removal_rejected(target):
    request = {"schema": "ciw.toolpoint-stream-containment-request.v1",
        "manufacturing_report": held_manufacturing(),
        "streams": [demo(case="nominal", cycle_index=i) for i in range(2)], "assessed_at_s": 12}
    if target == "manufacturing":
        forged = request["manufacturing_report"]
        forged["result"]["instances"]["p001"]["quality_holds"] = []
        forged["result_ref"] = content_identity(forged["result"])
        with pytest.raises(ValueError):
            evaluate(request)
    else:
        forged = evaluate(request)
        forged["result"]["quality"]["a001"]["state"] = "ELIGIBLE_FOR_REVIEW"
        forged["result_ref"] = content_identity(forged["result"])
        with pytest.raises(ValueError):
            verify(forged)
