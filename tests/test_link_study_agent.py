"""The Link MCP profile executes existing science with exact retained ingress."""
from copy import deepcopy
import json
import time

import pytest

from ciw import link_contract as c, link_study as study
from ciw.agent_mcp import from_profile
from ciw.control_gateway import ControlHost
from ciw.link_study_agent import demo_config, prepare
from ciw.link_study_cli import example


def call(host, name, **args):
    reply = host.call(name, args)
    assert reply['status'] == 'ok', reply
    return reply['data']


def run(host):
    graph = call(host, 'ncp_plan_template', baseline='baseline')['graph']
    plan = call(host, 'ncp_plan_create', source='source', baseline='baseline', graph=graph)
    call(host, 'ncp_plan_admit', plan_id=plan['plan_id'], expected_digest=plan['digest'])
    args = dict(plan_id=plan['plan_id'], expected_digest=plan['digest'], attempt='first')
    job = call(host, 'ncp_run_submit', **args)
    for _ in range(500):
        job = call(host, 'ncp_run_get', run_id=job['run_id'])
        if job['state'] not in {'queued', 'running'}:
            break
        time.sleep(.01)
    assert job['state'] == 'completed', job
    assert call(host, 'ncp_run_submit', **args)['run_id'] == job['run_id']
    return job


def test_real_link_operations_preserve_request_and_resume(tmp_path, monkeypatch):
    from ciw import link_serial
    monkeypatch.setattr(link_serial, 'capture_serial', lambda *a, **k: pytest.fail('serial I/O'))
    original = json.dumps(example(), indent=3).encode() + b'\n'
    profile = prepare(original, tmp_path / 'profile')
    with from_profile(profile, instrument='link-scalar') as owner, ControlHost(owner) as host:
        caps = call(host, 'ncp_capabilities_list')
        assert len(caps['items']) == 4
        assert len({item['capability_id'] for item in caps['items']}) == 4
        job = run(host)
        assert len(job['result']['execution_ids']) == 4
        artifact = job['result']['artifacts']['graph-run']['artifact_id']
        result = call(host, 'ncp_artifact_read', artifact=artifact, selector=['nodes', 'model', 'result', 'data'])['data']
        assert result['mean'] == pytest.approx(2.03)
        assert result['variance'] == pytest.approx(.00190808)
        assert result['inspection_qualification'] == 'HELD'
        assert owner._artifacts[owner._resolve('request')] == original
        assert owner.capabilities()['execution_budget']['used'] == 1
    with from_profile(profile, instrument='link-scalar', resume=True) as owner, ControlHost(owner, resume=True) as host:
        assert call(host, 'ncp_run_get', run_id=job['run_id']) == job
        assert owner.capabilities()['execution_budget']['used'] == 1
    assert (profile.parent / 'request.json').read_bytes() == original


@pytest.mark.parametrize('attack', ['wrong_predecessor', 'literal_result', 'context', 'omit_decode'])
def test_ineligible_plan_refuses_before_admission(tmp_path, attack):
    profile = demo_config(tmp_path / 'profile')
    with from_profile(profile, instrument='link-scalar') as owner, ControlHost(owner) as host:
        graph = call(host, 'ncp_plan_template', baseline='baseline')['graph']
        if attack == 'wrong_predecessor':
            graph['nodes'][2]['inputs']['calibrated']['node_id'] = 'decode'
        elif attack == 'literal_result':
            graph['nodes'][1]['parameters']['decoded'] = {'data': {}}
        elif attack == 'context':
            graph['model_id'] = 'forged.model'
        else:
            graph['nodes'] = graph['nodes'][1:]
        result = host.call('ncp_plan_create', dict(source='source', baseline='baseline', graph=graph))
        assert result['status'] == 'refused'
        assert owner.capabilities()['execution_budget']['used'] == 0


@pytest.mark.parametrize('attack', ['request_bytes', 'request_context', 'missing_request', 'source_ref', 'candidate_domains'])
def test_host_refuses_changed_source_binding(tmp_path, attack):
    profile = demo_config(tmp_path / 'profile')
    value = json.loads(profile.read_bytes())
    if attack == 'request_bytes':
        path = profile.parent / 'request.json'
        path.write_bytes(path.read_bytes() + b'\n')
    elif attack == 'request_context':
        path = profile.parent / 'request.json'
        request = json.loads(path.read_bytes())
        request['model']['expected_mean'] = 99
        path.write_text(json.dumps(request))
    elif attack == 'missing_request':
        del value['inputs']['request']
    elif attack == 'source_ref':
        path = profile.parent / 'source.json'
        source = json.loads(path.read_bytes())
        source['metadata']['input_bytes_ref'] = 'sha256:'+'0'*64
        from ciw.core.identities import evidence_id
        source['evidence_id'] = evidence_id(source)
        path.write_text(json.dumps(source))
    else:
        value['candidate_domains'] = {'baseline': {}}
    profile.write_text(json.dumps(value))
    with pytest.raises((ValueError, KeyError)):
        from_profile(profile, instrument='link-scalar')
    assert not (profile.parent / 'agent-output').exists()


@pytest.mark.parametrize('token', ['1.20', '1.200', '-0.00', '0.000000000000000001'])
def test_lexical_value_survives_control_and_report(tmp_path, token):
    from test_link_study import request_from
    from ciw import link_study_io as io
    line = (token + ' N\r\n').encode('ascii')
    request = request_from([line], tmp_path)
    request['model']['expected_mean'] = float(token)
    raw = json.dumps(request, indent=2).encode() + b'\n'
    profile = prepare(raw, tmp_path / 'profile')
    with from_profile(profile, instrument='link-scalar') as owner, ControlHost(owner) as host:
        job = run(host)
        artifact = job['result']['artifacts']['graph-run']['artifact_id']
        row = call(host, 'ncp_artifact_read', artifact=artifact,
                   selector=['nodes', 'decode', 'result', 'data', 'rows', 0])['data']
        assert c.unpack(row['raw'], 128) == line
        model = call(host, 'ncp_artifact_read', artifact=artifact,
                     selector=['nodes', 'model', 'result', 'data'])['data']
        assert model['standard_uncertainty'] is None
        assert model['inspection_qualification'] == 'HELD'
    io.run(request, tmp_path / 'study', raw=raw)
    io.report(tmp_path / 'study', tmp_path / 'report.html')
    assert '<td>' + token + '</td>' in (tmp_path / 'report.html').read_text()


def test_unsupported_exponent_is_retained_and_refused(tmp_path):
    from test_link_study import request_from
    raw = b'1.20e-9 N\r\n'
    request = request_from([raw], tmp_path)
    profile = prepare(c.bounded(request, study.MAX_BYTES), tmp_path / 'profile')
    assert c.unpack(study.gateway.validate_capture(request['capture'])['observations'][0]['raw']) == raw
    with from_profile(profile, instrument='link-scalar') as owner:
        outcome = owner.execute('source', 'baseline', 'unsupported-grammar')
        assert outcome['status'] == 'incomplete'
        assert owner._get('request') == request
