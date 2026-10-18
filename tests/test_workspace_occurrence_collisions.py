"""Recording operations cannot alias retained verification reproductions."""
import base64
from pathlib import Path

import pytest

from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def call(session, kind, payload=None):
    reply = session.handle({"protocol_version": 1, "request_id": "identity-regression",
                            "type": kind, "payload": payload or {}})
    assert reply["type"] == "response", reply
    return reply["payload"]


@pytest.mark.parametrize("modern", [False, True])
def test_resealed_recording_cannot_alias_hidden_verification_occurrence(tmp_path, modern):
    session = Session(make_demo_run(), tmp_path / "original")
    source = call(session, "source.add", {
        "kind": "machine-manifest", "label": "synthetic encoder",
        "bytes_b64": base64.b64encode(
            (ROOT / "examples/corrections/encoder-original.json").read_bytes()).decode(),
    })
    summary = call(session, "operation.execute", {
        "operation_id": "ciw.encoder-position.v1", "parameters": {"source_id": source["source_id"]},
    })
    bundle = call(session, "bundle.get", {"bundle_id": summary["bundle_id"]})
    if modern:
        call(session, "operation.execute", {"operation_id": "statistics.v1"})
    else:
        call(session, "analysis.stats")
    path = session.save_workspace(tmp_path / "workspace.json")
    # The untouched workspace remains inspectable without execution bindings.
    Session.from_workspace(path, tmp_path / "valid-reopen")
    saved = read_json(path)
    hidden = bundle["verification"]["reproduction"]["execution_id"]
    saved["results"][0]["execution_id"] = hidden
    if modern:
        saved["executions"][0]["execution_id"] = hidden
        seal(saved["results"][0])
        seal(saved["executions"][0])
    write_json(path, saved)
    with pytest.raises(ValueError, match="Identity collision"):
        Session.from_workspace(path, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()

