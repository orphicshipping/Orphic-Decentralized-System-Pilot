"""Actual pinned SRA submodules, synthetic graphs, and external official MCP."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading

import ciw
import pytest

from ciw import schematic_profile as profile
from ciw.adapters.protocol import AdapterRefusal
from ciw.agent_api import encode
from ciw.telemetry import digest

PACKAGE = Path(ciw.__file__).resolve().parents[1]
# Independently stated synthetic topology: no checkout fixture or private data
# is required when this same test is copied beside a clean installed wheel.
SOURCE = {
    "experiment_id": "synthetic-profile-assessment",
    "schematic": {
        "schema": "NsObservabilitySchematic@0.1",
        "meta": {"id": "synthetic-quadratic-drag", "schema": "NsObservabilitySchematic@0.1"},
        "nodes": [
            {"id": "u", "kind": "variable", "attrs": {"role": "input", "units": "N"}},
            {"id": "x", "kind": "variable", "attrs": {"role": "state", "units": "m/s"}},
            {"id": "xdot", "kind": "variable", "attrs": {"role": "state_dot", "units": "m/s^2"}},
            {"id": "y", "kind": "variable", "attrs": {"role": "output", "units": "m/s"}},
            {"id": "f", "kind": "function", "attrs": {
                "class": "nonlinear", "model_ref": "jspt.reference.quadratic_drag", "x_star": [1.0],
                "c": 0.5, "law": "xdot = u - c * x * abs(x); y = x", "chart": "identity", "units": "SI"}},
            {"id": "ySensor", "kind": "measurement", "attrs": {"quality": "undeclared"}},
            {"id": "observer", "kind": "observer", "attrs": {"status": "UNRESOLVED"}},
        ],
        "edges": [{"kind": kind, "src": start, "dst": end, "attrs": {}}
                  for kind, start, end in (("input", "u", "f"), ("input", "x", "f"),
                                           ("output", "f", "xdot"), ("output", "f", "y"),
                                           ("actuates", "u", "f"), ("measures", "ySensor", "y"),
                                           ("observes", "observer", "f"), ("senses", "observer", "ySensor"))],
    },
    "queries": ["x"],
}


def request():
    return {key: deepcopy(SOURCE[key]) for key in ("experiment_id", "schematic", "queries")}


@pytest.fixture(scope="module")
def checkout():
    selected = os.environ.get("CIW_SRA_CHECKOUT")
    if not selected:
        pytest.skip("Set CIW_SRA_CHECKOUT to the actual pinned a6e7958 checkout for SRA qualification")
    path = Path(selected)
    assert path.is_absolute()
    return path


@pytest.fixture
def host(checkout):
    with profile.SchematicHost(checkout=checkout, python=sys.executable, principal="synthetic-owner") as value:
        yield value


def call(host, name, arguments):
    response = host.call("ncp_schematic_" + name, arguments)
    assert response["status"] == "ok", response
    return response["data"]


def receipt(host):
    return call(host, "assess", {"request": request()})["receipt"]


def test_discovery_never_grants_and_is_exactly_bounded(host):
    names = {item["name"] for item in host.descriptions()}
    assert names == {"ncp_schematic_" + name for name in ("describe", "validate", "retrieve", "assess", "verify_receipt")}
    described = call(host, "describe", {})
    assert described["runtime_commit"] == "a6e79585950bb6860e5edce5ebd2cce39ea481f2"
    assert described["authorizes_execution"] is False
    assert described["limits"]["nodes"] == 256 and described["limits"]["edges"] == 1024
    assert described["authority"]["companion_execution"] == "not_performed"
    assert described["authority"]["binding"] == "caller_assertion_only"


def test_actual_validation_and_retrieval_preserve_declared_content(host):
    source = request()
    validated = call(host, "validate", {"schematic": source["schematic"]})
    retrieved = call(host, "retrieve", {"schematic": source["schematic"], "queries": source["queries"]})
    assert validated["data"]["schematic"] == source["schematic"]
    assert retrieved["data"]["neighborhoods"]["x"] == ["f", "observer", "u", "x", "xdot", "y"]
    assert request() == source


def test_assess_and_full_verification_preserve_distinct_native_occurrences(host):
    original = receipt(host)
    before = deepcopy(original)
    verified = call(host, "verify_receipt", {"receipt": original})
    fresh = verified["reproduction_receipt"]
    assert verified["source_receipt_digest"] == original["receipt_digest"]
    assert verified["numerical_match"] is True and verified["independent"] is False
    assert original == before
    old, new = original["bundle"], fresh["bundle"]
    assert old["session_id"] != new["session_id"]
    assert old["steps"][0]["execution_id"] != new["steps"][0]["execution_id"]
    assert old["steps"][0]["result_id"] != new["steps"][0]["result_id"]
    assert old["steps"][0]["numerical_result"] == new["steps"][0]["numerical_result"]
    assert old["steps"][0]["execution_id"] != old["verification"]["reproduction"]["execution_id"]
    assert "bundle_digest" not in old
    assert "subject_ref" not in old["verification"]
    text = encode(original).decode()
    assert "repository_root" not in text and "python_executable" not in text
    assert old["runtimes"]["sra"]["launch_binding"] == "host_selected"
    assert original["receipt_digest"] == profile._receipt_digest(original)
    assert len(encode({"name": "ncp_schematic_verify_receipt", "arguments": {"receipt": original}})) <= 65536
    assert len(encode(host.mcp_result("ncp_schematic_assess", {"status": "ok", "data": original}))) <= 262144


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(extra=True),
    lambda r: r["authority"].update(source_authentication="verified"),
    lambda r: r["authority"].update(authorizes_execution=0),
    lambda r: r["authority"].update(independent_verification=0),
    lambda r: r["bundle"]["runtimes"]["sra"].update(revision="0" * 40),
    lambda r: r["bundle"]["runtimes"]["sra"].update(source_tree="0" * 40),
    lambda r: r["bundle"]["runtimes"]["sra"].update(python_sha256="0" * 64),
    lambda r: r["bundle"]["verification"].update(independent=True),
    lambda r: r["bundle"]["verification"].update(subject_ref="sha256:" + "0" * 64),
    lambda r: r["bundle"]["steps"][0]["result"].update(extra=True),
    lambda r: r["bundle"]["configuration"].update(companion_execution="permitted"),
])
def test_resealed_full_receipt_mutations_refuse(host, mutation):
    value = receipt(host)
    mutation(value)
    value["receipt_digest"] = profile._receipt_digest(value)
    assert host.call("ncp_schematic_verify_receipt", {"receipt": value})["status"] == "refused"


def test_fully_resealed_scientific_tamper_fails_actual_reproduction(host):
    value = receipt(host)
    for step in (value["bundle"]["steps"][0], value["bundle"]["verification"]["reproduction"]):
        step["result"]["data"]["decisions"][0]["reason"] = "forged eligible conclusion"
        step["result"]["result_id"] = digest({k: v for k, v in step["result"].items() if k != "result_id"})
        step["result_id"] = step["result"]["result_id"]
        step["result_sha256"] = digest(step["result"])
        step["numerical_result"]["data"] = deepcopy(step["result"]["data"])
        step["numerical_result_id"] = digest(step["numerical_result"])
    value["receipt_digest"] = profile._receipt_digest(value)
    host._native(value, host._adapter.runtime_identity())
    assert host.call("ncp_schematic_verify_receipt", {"receipt": value})["status"] == "refused"


@pytest.mark.parametrize("path_key", ["repository_root", "python_executable"])
def test_no_caller_binding_at_any_depth(host, path_key):
    value = receipt(host)
    value["bundle"]["runtimes"]["sra"][path_key] = "/caller/selected"
    value["receipt_digest"] = profile._receipt_digest(value)
    assert host.call("ncp_schematic_verify_receipt", {"receipt": value})["reason"] == "payload_binding_refused"
    assert host.call("ncp_schematic_validate", {"schematic": SOURCE["schematic"], path_key: "/caller"})["status"] == "refused"


@pytest.mark.parametrize("fault", ["nodes", "edges", "queries", "duplicate", "unknown-query", "extra", "owner"])
def test_closed_limits_refuse_before_native_assessment(host, fault, monkeypatch):
    value = request()
    if fault == "nodes": value["schematic"]["nodes"] *= 40
    if fault == "edges": value["schematic"]["edges"] *= 129
    if fault == "queries": value["queries"] *= 17
    if fault == "duplicate": value["schematic"]["nodes"].append(deepcopy(value["schematic"]["nodes"][0]))
    if fault == "unknown-query": value["queries"] = ["not-present"]
    if fault == "extra": value["extra"] = True
    if fault == "owner": value["schematic"]["nodes"].append({"id": "fake", "kind": "certificate", "attrs": {"owner": "intruder"}})
    def forbidden(*args): raise AssertionError("invalid contract reached a child process")
    monkeypatch.setattr(host._workflow, "_execute", forbidden)
    monkeypatch.setattr(host._adapter, "runtime_identity", forbidden)
    assert host.call("ncp_schematic_assess", {"request": value})["status"] == "refused"


def test_receipt_issuance_closes_verification_input_budget(host):
    value = request()
    value["schematic"]["meta"]["untrusted_note"] = "x" * 14000
    assert len(encode({"name": "ncp_schematic_assess", "arguments": {"request": value}})) < 65536
    result = host.call("ncp_schematic_assess", {"request": value})
    assert result["status"] == "refused" and result["reason"] == "receipt_verification_budget_exceeded"


def test_projection_directive_stays_untrusted_json_and_render_imports_refuse(host):
    node = 'node"]\n%%{init: {"securityLevel":"loose"}}%%\n'
    graph = {"schema": "NsObservabilitySchematic@0.1", "meta": {"schema": "NsObservabilitySchematic@0.1"},
             "nodes": [{"id": node, "kind": "variable", "attrs": {}}], "edges": []}
    assert call(host, "validate", {"schematic": graph})["data"]["schematic"] == graph
    for name in ("ncp_schematic_project", "ncp_schematic_mermaid", "ncp_schematic_usd", "ncp_schematic_run"):
        assert host.call(name, {})["reason"] == "unknown_schematic_operation"
    for module in ("schematics.agent", "schematics.adapters.structure", "schematics.adapters.covariance",
                   "schematics.project_mermaid", "schematics.project_usd"):
        code, raw = host._adapter._run("__import__(__import__('sys').argv[1])", [module])
        assert code != 0 and raw == b""


def test_eligible_schematic_does_not_execute_or_import_companions(host):
    value = request()
    function = next(node for node in value["schematic"]["nodes"] if node["kind"] == "function")
    function["attrs"].update({"class": "linear", "A": [[-1.0]], "x_order": ["x"], "y_order": ["y"],
                              "sigma_x": [[0.25]], "unit": "1", "frame": "test"})
    assessed = call(host, "assess", {"request": value})
    data = assessed["receipt"]["bundle"]["steps"][0]["result"]["data"]
    assert data["schematic"]["nodes"] == value["schematic"]["nodes"]
    assert any(item["status"] == "ELIGIBLE" for item in data["decisions"])
    assert assessed["authority"]["companion_execution"] == "not_performed"
    code, raw = host._adapter._run(
        "from schematics.io import from_dict\nfrom schematics.annotate import invalidate_stale_results\n"
        "from schematics.eligibility import decide\nfrom schematics.retrieve import blanket\n"
        "assert not any(n.startswith('schematics.adapters') or n=='schematics.agent' for n in sys.modules)\nprint('no-companions')", [])
    assert code == 0 and raw == b"no-companions\n"


@pytest.mark.parametrize('owner', ['jspt', 'plsr', 'rci', 'cse'])
def test_certificate_owner_is_declared_scientific_content_not_execution_authority(host, owner):
    value = request()
    value['schematic']['nodes'].append({'id': 'old-certificate', 'kind': 'certificate',
        'attrs': {'owner': owner, 'result': 'SAMPLED', 'A': [[-1.0]]}})
    value['schematic']['edges'].append({'kind': 'linearizes', 'src': 'old-certificate', 'dst': 'f', 'attrs': {}})
    assessed = call(host, 'assess', {'request': value})
    saved = assessed['receipt']
    output = saved['bundle']['steps'][0]['result']['data']
    certificate = next(node for node in output['schematic']['nodes'] if node['id'] == 'old-certificate')
    assert certificate['attrs']['owner'] == owner
    assert certificate['attrs']['A'] == [[-1.0]]
    assert saved['authority']['certificate_authentication'] == 'not_performed'
    assert call(host, 'verify_receipt', {'receipt': saved})['numerical_match'] is True


def test_interpreter_drift_refuses_before_executing_changed_binding(checkout, tmp_path, monkeypatch):
    interpreter = tmp_path / 'selected-python'
    interpreter.symlink_to(sys.executable)
    with profile.SchematicHost(checkout=checkout, python=interpreter, principal='synthetic-owner') as selected:
        assert call(selected, 'validate', {'schematic': SOURCE['schematic']})['outcome'] == 'validated'
        interpreter.unlink()
        interpreter.write_bytes(b'not an executable; must never be launched\n')
        def forbidden(*args, **kwargs): raise AssertionError('changed interpreter reached execution')
        monkeypatch.setattr(selected._adapter, '_run', forbidden)
        refused = selected.call('ncp_schematic_validate', {'schematic': SOURCE['schematic']})
        assert refused['status'] == 'refused' and refused['code'] == 'RUNTIME_PIN_MISMATCH'


def test_serialized_full_input_and_unicode_mcp_envelope_are_bounded(host, monkeypatch):
    value = request()
    value['schematic']['meta']['note'] = '\u2603' * 12000
    arguments = {'request': value}
    assert len(json.dumps(arguments, ensure_ascii=False).encode()) < 65536
    assert len(encode({'name': 'ncp_schematic_assess', 'arguments': arguments})) > 65536
    def forbidden(*args, **kwargs): raise AssertionError('over-budget input reached a child')
    monkeypatch.setattr(host._adapter, 'runtime_identity', forbidden)
    assert host.call('ncp_schematic_assess', arguments)['reason'] == 'argument_budget_exceeded'
    result = {'status': 'ok', 'data': {'text': '\u2603' * 22000}}
    assert len(encode(result)) < 262144
    with pytest.raises(profile.SchematicRefusal, match='response_budget_exceeded'):
        host.mcp_result('ncp_schematic_assess', result)


def test_reopen_reuses_binding_but_fresh_replay_ids(checkout):
    with profile.SchematicHost(checkout=checkout, python=sys.executable, principal="synthetic-owner") as first:
        value = receipt(first)
    with profile.SchematicHost(checkout=checkout, python=sys.executable, principal="synthetic-owner") as second:
        response = call(second, "verify_receipt", {"receipt": value})
        assert response["source_receipt_digest"] == value["receipt_digest"]
        assert response["reproduction_receipt"]["bundle"]["session_id"] != value["bundle"]["session_id"]


def test_busy_call_does_not_reset_existing_deadline_or_release_owner(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = host._invoke
    def hold(name, args):
        entered.set()
        assert release.wait(5)
        return original(name, args)
    monkeypatch.setattr(host, "_invoke", hold)
    worker = threading.Thread(target=lambda: host.call("ncp_schematic_describe", {}))
    worker.start()
    assert entered.wait(5)
    try:
        deadline = host._adapter.deadline
        assert host.call("ncp_schematic_describe", {})["reason"] == "host_busy"
        assert host._adapter.deadline == deadline
        with pytest.raises(profile.SchematicRefusal, match="host_busy"):
            host.close()
        assert not host._closed
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert call(host, "describe", {})["profile_id"] == profile.PROFILE


def test_real_child_deadline_and_output_limits_leave_host_usable(host):
    with pytest.raises(AdapterRefusal) as caught:
        host._adapter._run("import time; time.sleep(8)", [])
    assert caught.value.code == "TIMEOUT"
    with pytest.raises(AdapterRefusal) as caught:
        host._adapter._run("print('x'*200000)", [])
    assert caught.value.code == "OUTPUT_LIMIT"
    assert call(host, "describe", {})["profile_id"] == profile.PROFILE


_INTERRUPTED_HOST = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ciw import coordinator
from ciw.schematic_profile import SchematicHost, SchematicRefusal
checkout, stage = sys.argv[2:]
host = SchematicHost(checkout=checkout, python=sys.executable, principal='synthetic-owner')
original_try, original_unlock, original_close = coordinator._try_lock, coordinator._lock_release, os.close
original_invoke = host._invoke
state = {'calls': 0, 'owned': None, 'replacement': None}
other = host._lease.path.with_name('other-owner.lease')
def acquire(fd):
    state['owned'] = fd
    if stage == 'before_acquire':
        raise KeyboardInterrupt('before lock')
    if stage == 'before_acquire_io':
        raise OSError('private acquisition path must not escape')
    result = original_try(fd)
    if result and stage == 'after_acquire':
        raise KeyboardInterrupt('after lock')
    if result and stage == 'after_acquire_io':
        raise OSError('private acquired path must not escape')
    return result
def invoke(name, args):
    state['calls'] += 1
    if stage in ('during_call', 'paired_cleanup'):
        print(json.dumps({'entered': True}), flush=True)
        time.sleep(60)
    return original_invoke(name, args)
def unlock(fd):
    if stage in ('before_unlock', 'paired_cleanup'):
        raise OSError('private release path must not escape')
    original_unlock(fd)
    if stage == 'after_unlock':
        raise OSError('private released path must not escape')
def close(fd):
    original_close(fd)
    if stage == 'close_after_effect' and fd == state['owned'] and state['replacement'] is None:
        replacement = os.open(other, os.O_CREAT | os.O_RDWR, 0o600)
        assert replacement == fd, (replacement, fd)
        assert original_try(replacement)
        state['replacement'] = replacement
        raise OSError('private closed path must not escape')
coordinator._try_lock, coordinator._lock_release, os.close = acquire, unlock, close
host._invoke = invoke
print(json.dumps({'ready': True, 'path': str(host._lease.path), 'other': str(other)}), flush=True)
assert sys.stdin.readline().strip() == 'run'
try:
    host.call('ncp_schematic_describe', {})
    raise AssertionError('injected failure disappeared')
except KeyboardInterrupt:
    outcome = 'interrupted'
except SchematicRefusal as exc:
    assert str(exc) == 'schematic_runtime_io_refused', str(exc)
    assert isinstance(exc.__cause__, OSError)
    if stage == 'paired_cleanup':
        assert isinstance(exc.__cause__.__context__, KeyboardInterrupt)
    outcome = str(exc)
finally:
    coordinator._try_lock, coordinator._lock_release, os.close = original_try, original_unlock, original_close
    host._invoke = original_invoke
assert host._adapter.deadline is None
print(json.dumps({'done': True, 'outcome': outcome, 'calls': state['calls']}), flush=True)
assert sys.stdin.readline().strip() == 'continue'
assert host.call('ncp_schematic_describe', {})['status'] == 'ok'
if state['replacement'] is not None:
    original_unlock(state['replacement'])
    original_close(state['replacement'])
host.close()
print(json.dumps({'recovered': True}), flush=True)
'''


def _child_line(child):
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        assert selector.select(25), 'finite subprocess did not reach its checkpoint'
    line = child.stdout.readline()
    assert line, f'child exited: {child.poll()}'
    return json.loads(line)


def _separate_process_can_lock(path):
    code = ("import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(PACKAGE)!r})\n"
            "from ciw.coordinator import _Lease\n"
            "lease=_Lease(Path(sys.argv[1]))\n"
            "try: print(int(lease.try_acquire()),flush=True)\nfinally: lease.release()\n")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, path],
                            capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip() == '1'


@pytest.mark.parametrize('stage', ['before_acquire', 'after_acquire', 'before_acquire_io',
                                  'after_acquire_io', 'during_call', 'before_unlock',
                                  'after_unlock', 'close_after_effect', 'paired_cleanup'])
def test_real_interruption_cleanup_keeps_failed_host_alive_and_releases_only_its_lease(checkout, stage):
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', _INTERRUPTED_HOST,
                              str(PACKAGE), str(checkout), stage],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
    try:
        ready = _child_line(child)
        assert ready['ready']
        child.stdin.write('run\n')
        child.stdin.flush()
        if stage in ('during_call', 'paired_cleanup'):
            assert _child_line(child)['entered']
            assert not _separate_process_can_lock(ready['path'])
            os.kill(child.pid, signal.SIGINT)
        done = _child_line(child)
        assert done['done']
        assert done['calls'] == (0 if 'acquire' in stage else 1)
        assert child.poll() is None  # Keep failed worker alive during independent probes.
        assert _separate_process_can_lock(ready['path'])
        if stage == 'close_after_effect':
            assert not _separate_process_can_lock(ready['other'])
        child.stdin.write('continue\n')
        child.stdin.flush()
        assert _child_line(child)['recovered']
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, out + err
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_relocated_checkout_verifies_without_payload_paths(checkout, tmp_path):
    relocated = tmp_path / 'sra-relocated'
    subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', str(checkout), str(relocated)],
                   check=True, timeout=60)
    subprocess.run(['git', 'checkout', '--quiet', '--detach', profile.PIN], cwd=relocated,
                   check=True, timeout=60)
    with profile.SchematicHost(checkout=checkout, python=sys.executable, principal='synthetic-owner') as original:
        value = receipt(original)
    with profile.SchematicHost(checkout=relocated, python=sys.executable, principal='synthetic-owner') as moved:
        result = call(moved, 'verify_receipt', {'receipt': value})
        assert result['numerical_match'] is True
        assert result['source_receipt_digest'] == value['receipt_digest']
        before = value['bundle']['runtimes']['sra']
        assert before == result['reproduction_receipt']['bundle']['runtimes']['sra']
        # A caller cannot cure source drift through a fresh content digest.
        (relocated / 'src/schematics/retrieve.py').write_text('# changed pinned source\n')
        refused = moved.call('ncp_schematic_verify_receipt', {'receipt': value})
        assert refused['status'] == 'refused' and refused['code'] == 'SOURCE_PIN_MISMATCH'


def test_real_official_mcp_sdk_parity_and_reconnect(checkout, tmp_path):
    pytest.importorskip("mcp", reason="Official MCP transport dependency is required")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    script = tmp_path / "server.py"
    script.write_text(f"import sys\nsys.path.insert(0,{str(PACKAGE)!r})\n"
                      "from ciw.schematic_profile import SchematicHost\nfrom ciw.mcp_server import serve_sdk\n"
                      f"with SchematicHost(checkout={str(checkout)!r},python=sys.executable,principal='synthetic-owner') as host:\n"
                      " serve_sdk(host,name='odsp-schematic')\n")
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(script)])
        saved = None
        for iteration in range(2):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 5
                    assert (await client.list_resources()).resources == []
                    assert (await client.list_resource_templates()).resource_templates == []
                    if iteration == 0:
                        value = await client.call_tool("ncp_schematic_assess", {"request": request()})
                        assert not value.is_error
                        saved = value.structured_content["data"]["receipt"]
                    else:
                        value = await client.call_tool("ncp_schematic_verify_receipt", {"receipt": saved})
                        assert not value.is_error
                        assert value.structured_content["data"]["source_receipt_digest"] == saved["receipt_digest"]
                    refused = await client.call_tool("ncp_schematic_agent_run", {})
                    assert refused.is_error
        with profile.SchematicHost(checkout=checkout, python=sys.executable, principal="synthetic-owner") as sdk:
            verified = profile.invoke_schematic(sdk, "ncp_schematic_verify_receipt", {"receipt": saved})
            assert verified["numerical_match"] is True
    asyncio.run(run())
