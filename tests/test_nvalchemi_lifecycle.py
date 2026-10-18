"""NET Session lifecycle with an EXPLICIT TRANSPORT DOUBLE, not ALCHEMI physics.

The native gate is scripts/check_atomic_nvalchemi.py and must not close with skips.
"""
from copy import deepcopy
from threading import RLock
from unittest.mock import patch

import pytest

from ciw import nvalchemi_contract as c
from ciw.nvalchemi_adapter import AlchemiAdapter, register_schema
from ciw.adapters.protocol import AdapterRefusal
from ciw.instruments import make_demo_run
from ciw.operations.registry import OperationRegistry, default_registry
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json
from test_nvalchemi_contract import prediction, runtime_fixture


class TransportDouble(AlchemiAdapter):
    def __init__(self):
        self.device = "cpu"
        self._evaluation_lock = RLock()
        self._transport = None
        self.calls = 0
    def runtime_identity(self):
        return runtime_fixture()
    def invoke(self, operation_id, inputs):
        self.calls += 1
        data = prediction(inputs["configuration"])
        data["environment_sha256"] = inputs["environment_sha256"]
        request = c.canonical({"schema": "ciw.adapter-request.v1", "operation_id": operation_id, "inputs": inputs})
        response = c.canonical({"schema": "ciw.adapter-response.v1", "status": "ok", "data": data})
        self._transport = (request, response)
        return data


def request(session, parameters=None):
    return session.handle({"protocol_version": 1, "request_id": "lj-test", "type": "operation.execute",
                           "payload": {"operation_id": c.OPERATION,
                                       "parameters": c.example_input() if parameters is None else parameters}})["payload"]


def setup_session(tmp_path):
    adapter = TransportDouble()
    registry = OperationRegistry()
    registry.register(adapter.operation())
    session = Session(make_demo_run(), tmp_path, operations=registry)
    return adapter, session


def test_explicit_binding_only_and_idempotent_offline_registration(tmp_path):
    register_schema()
    register_schema()
    assert c.OPERATION not in {item["operation_id"] for item in default_registry().describe()}
    session = Session(make_demo_run(), tmp_path)
    result = request(session)
    assert result["status"] == "refused"
    assert result["execution"]["refusal"]["code"] == "operation_unavailable"


def test_real_session_retention_reopen_and_fresh_execution_with_double(tmp_path):
    adapter, session = setup_session(tmp_path / "initial")
    first = request(session)
    assert first["status"] == "completed"
    assert first["result"]["verification_status"] == "not_verified"
    assert first["result"]["verification_id"] is None
    assert first["result"]["data"]["numerical_check"]["status"] == "pass"
    assert adapter.calls == 1
    path = session.save_workspace(tmp_path / "workspace.json")
    original = path.read_bytes()
    with patch.object(adapter, "invoke", side_effect=AssertionError("offline reopen must not execute")):
        restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.results == session.results and restored.executions == session.executions
    assert path.read_bytes() == original
    assert request(restored)["status"] == "refused"  # no provider rehydration from saved JSON
    second = request(session)
    assert second["status"] == "completed"
    assert second["result"]["result_id"] != first["result"]["result_id"]
    assert second["execution"]["execution_id"] != first["execution"]["execution_id"]
    assert second["result"]["data"]["numerical_check_id"] != first["result"]["data"]["numerical_check_id"]


def test_provider_failure_retains_refusal_without_prediction(tmp_path):
    adapter, session = setup_session(tmp_path)
    with patch.object(adapter, "invoke", side_effect=AdapterRefusal("RUNTIME_UNAVAILABLE", "Explicit test refusal")):
        result = request(session)
    assert result["status"] == "refused" and result["result"] is None
    assert len(session.executions) == 1 and not session.results


@pytest.mark.parametrize("mutation", ["runtime", "input", "role", "check"])
def test_resealed_workspace_mutations_refuse_offline(tmp_path, mutation):
    _, session = setup_session(tmp_path / "initial")
    assert request(session)["status"] == "completed"
    path = session.save_workspace(tmp_path / "workspace.json")
    saved = read_json(path)
    result, execution = saved["results"][0], saved["executions"][0]
    if mutation == "runtime":
        result["runtime"]["device_binding"] = "cuda:0"
        execution["runtime"]["device_binding"] = "cuda:0"
    elif mutation == "input": result["data"]["atomic_input"]["positions"][0][0] += .1
    elif mutation == "role": result["role"] = "verification"
    else: result["data"]["numerical_check"]["status"] = "fail"
    seal(result)
    seal(execution)
    write_json(path, saved)
    with pytest.raises(ValueError):
        Session.from_workspace(path, tmp_path / "rejected")
