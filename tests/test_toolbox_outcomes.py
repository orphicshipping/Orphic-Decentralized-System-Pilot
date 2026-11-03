"""Returned runtime outcomes halt recipes; source payloads remain ordinary data.

The integration cases use a real principal facade, control checkpoint and retained
store. Faults affect executor admission or a real worker process, not canonical
service responses. They perform no scientific execution.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ciw import coordinator
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.principal_policy import PrincipalPolicy, owner_for
from ciw.services import invoke
from ciw.toolbox_skills import SkillRegistry, SkillRunner, _step_outcome, digest


TOOLS = (
    'ncp_plan_template', 'ncp_plan_create', 'ncp_plan_admit',
    'ncp_run_submit', 'ncp_run_get', 'ncp_run_cancel',
    'ncp_coordinate', 'ncp_retained_read',
)


@pytest.fixture
def control(tmp_path, monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    grant = {
        'principal': 'alice', 'enabled': True,
        'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
        'scientific_operations': ['statistics.v1'],
        'resources': ['ncp://plans/', 'ncp://runs/', 'ncp://artifacts/', 'ncp://retained/'],
        'reference_scopes': [], 'handoff_recipients': [],
    }
    policy = PrincipalPolicy(lambda: {
        'schema': 'ciw.principal-policy.v1', 'revision': 'toolbox-outcomes',
        'principals': [grant],
    })
    with from_profile(demo_config(tmp_path / 'demo')) as owner:
        with ControlHost(owner, retained_owner=owner_for('alice')) as host:
            yield SimpleNamespace(owner=owner, host=host,
                                  facade=policy.facade('alice', control=host),
                                  temporary=tmp_path)


def admitted_plan(control):
    graph = invoke('ncp_plan_template', {'baseline': 'baseline'},
                   host=control.facade)['graph']
    plan = invoke('ncp_plan_create', {
        'source': 'source', 'baseline': 'baseline', 'graph': graph,
    }, host=control.facade)
    invoke('ncp_plan_admit', {'plan_id': plan['plan_id'], 'expected_digest': plan['digest']},
           host=control.facade)
    return plan


def recipe(control, first_tool, arguments, dependent_tool, locator):
    definition = {
        'schema': 'ncp.client-skill.v1', 'skill_id': 'runtime-outcome', 'version': '1.0.0',
        'description': 'Synthetic local runtime outcome and dependent-read regression.',
        'inputSchema': {'type': 'object', 'properties': {}, 'required': [],
                        'additionalProperties': False},
        'steps': [
            {'step_id': 'primary', 'tool': first_tool,
             'arguments': {key: {'literal': value} for key, value in arguments.items()}},
            {'step_id': 'dependent', 'tool': dependent_tool,
             'arguments': {locator: {'step': 'primary', 'path': [locator]}}},
        ],
    }
    registry = SkillRegistry([definition], facade=control.facade)
    admissions = []

    def admit(intent):
        admissions.append(deepcopy(intent))
        return True

    def fresh_runner():
        return SkillRunner(facade=control.facade, registry=registry,
                           tenant='synthetic-outcome-tests',
                           pins={'odsp': 'local-source-fixture'},
                           replay_identity='outcome-tests', admit_step=admit)

    return fresh_runner, admissions


def observe_calls(control, monkeypatch):
    """Spy on actual principal dispatch without substituting its result."""
    original = control.facade.call
    observed = []

    def call(name, arguments):
        response = original(name, arguments)
        observed.append({'name': name, 'arguments': deepcopy(arguments),
                         'response': deepcopy(response)})
        return response

    monkeypatch.setattr(control.facade, 'call', call)
    return observed


def halted(control, monkeypatch, tool, arguments, dependent_tool, locator, expected):
    fresh_runner, admissions = recipe(control, tool, arguments, dependent_tool, locator)
    runner = fresh_runner()
    observed = observe_calls(control, monkeypatch)
    executions = coordinator._EXECUTIONS
    result = runner.run('runtime-outcome', '1.0.0', {}, attempt='one-attempt')
    assert result['status'] == expected
    assert result['steps_completed'] == 0
    assert result['authorizes_execution'] is False
    assert [row['name'] for row in observed] == [tool]
    assert [row['step_id'] for row in admissions] == ['primary']
    assert observed[0]['response']['status'] == 'ok'
    actual = observed[0]['response']['data']

    final = runner.receipt(result['artifact_id'])['receipt']
    assert final['status'] == expected and final['steps'] == []
    assert final['authorizes_execution'] is False
    assert final['evidence_admission'] is False
    assert final['freshness_or_truth_established'] is False
    stop = final['halted_step']
    assert stop['step_id'] == 'primary'
    assert stop['result_sha256'] == digest(actual)
    retained = runner.receipt(stop['artifact_id'])['receipt']
    assert retained['status'] == 'step_' + expected
    assert retained['result'] == actual  # All fields, including the original locator.
    assert retained['result'][locator] == actual[locator]
    assert retained['result_sha256'] == stop['result_sha256']
    assert retained['admission']['arguments'] == arguments
    for record in (final, retained):
        assert record['receipt_sha256'] == digest({
            key: value for key, value in record.items() if key != 'receipt_sha256'
        })

    # Both the live runner and a new client see the durable attempt guard. Neither
    # a pending submission nor a lost result permits automatic recipe replay.
    for repeated in (runner, fresh_runner()):
        with pytest.raises(ValueError, match='skill_attempt_used_or_capacity_reached'):
            repeated.run('runtime-outcome', '1.0.0', {}, attempt='one-attempt')
    assert [row['name'] for row in observed] == [tool]
    assert [row['step_id'] for row in admissions] == ['primary']
    assert control.owner.capabilities()['execution_budget']['used'] == 0
    assert coordinator._EXECUTIONS == executions
    return actual, final


@pytest.mark.parametrize(('executor_fault', 'state', 'outcome'), [
    (True, 'unknown', 'outcome_unknown'),
    (False, 'queued', 'pending'),
])
def test_actual_submit_outcome_halts_without_dependent_or_replay(
        control, monkeypatch, executor_fault, state, outcome):
    plan = admitted_plan(control)
    submissions = []

    def submit(function, run_id):
        submissions.append((function, run_id))
        if executor_fault:
            raise RuntimeError('synthetic executor admission failure')
        # Deliberately leave the real, persisted control job queued.
        return None

    monkeypatch.setattr(control.host._executor, 'submit', submit)
    arguments = {'plan_id': plan['plan_id'], 'expected_digest': plan['digest'],
                 'attempt': 'canonical-submit'}
    actual, _ = halted(control, monkeypatch, 'ncp_run_submit', arguments,
                       'ncp_run_get', 'run_id', outcome)
    assert len(submissions) == 1
    assert submissions[0][0] == control.host._work
    assert submissions[0][1] == actual['run_id']
    assert actual['state'] == state and actual['result'] is None
    assert actual['uri'] == 'ncp://runs/' + actual['run_id']
    assert control.host.get_run(actual['run_id']) == actual
    assert control.host._store.load()[0]['jobs'][actual['run_id']] == actual


@pytest.mark.parametrize('state', ['cancelled', 'failed'])
def test_actual_terminal_run_record_is_not_recipe_completion(control, monkeypatch, state):
    plan = admitted_plan(control)
    monkeypatch.setattr(control.host._executor, 'submit', lambda *args: None)
    queued = invoke('ncp_run_submit', {
        'plan_id': plan['plan_id'], 'expected_digest': plan['digest'], 'attempt': 'terminal-setup',
    }, host=control.facade)
    assert queued['state'] == 'queued'
    if state == 'cancelled':
        invoke('ncp_run_cancel', {'run_id': queued['run_id']}, host=control.facade)
    else:
        def reject_execution(plan, job):
            raise ValueError('synthetic execution admission refusal')

        monkeypatch.setattr(control.host, '_execution_authorizer', reject_execution)
        control.host._work(queued['run_id'])
    actual, _ = halted(control, monkeypatch, 'ncp_run_get', {'run_id': queued['run_id']},
                       'ncp_run_get', 'run_id', state)
    assert actual['state'] == state
    assert control.host._store.load()[0]['jobs'][actual['run_id']] == actual
    if state == 'failed':
        assert actual['result']['execution_outcome'] == 'not_started'


LOST_WORKER = r'''
import json, os, sys
from ciw import coordinator
from ciw.retention import Store
owner, key = sys.argv[1:]
arguments = {'domain': 'sequence', 'example': True}
store = Store(owner, evict=False)
job, lease = coordinator._admit(
    store, key, coordinator._digest('ncp_domain_run', arguments, None), 'ncp_domain_run')
assert lease is not None and lease.fd is not None
print(json.dumps({'job': job, 'executions': coordinator._EXECUTIONS}), flush=True)
assert sys.stdin.read(1) == 'x'
# A real process loss releases the kernel lease, but leaves its admitted job.
# No domain operation was dispatched.
os._exit(77)
'''


@pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'),
                    reason='Actual process-loss qualification requires POSIX no-follow locks')
def test_actual_lost_worker_recall_halts_and_preserves_failure_locator(control, monkeypatch):
    key = 'lost-worker'
    source = str(Path(coordinator.__file__).resolve().parents[1])
    environment = {**os.environ, 'PYTHONPATH': source, 'PYTHONDONTWRITEBYTECODE': '1'}
    child = subprocess.Popen(
        [sys.executable, '-B', '-c', LOST_WORKER, control.host.retained.owner, key],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=control.temporary, env=environment,
    )
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        assert ready, 'worker did not publish its admission within the bounded wait'
        started = json.loads(child.stdout.readline())
        assert started['job']['status'] == 'running' and started['executions'] == 0
        assert child.poll() is None
        # The live worker really owns its lease; a canonical recall cannot settle it.
        live = invoke('ncp_coordinate', {'action': 'recall', 'idempotency_key': key},
                      host=control.facade)
        assert live['status'] == 'running' and 'artifact_id' not in live
        _, error = child.communicate('x', timeout=10)
        assert child.returncode == 77, error
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()

    executions = coordinator._EXECUTIONS
    actual, _ = halted(control, monkeypatch, 'ncp_coordinate',
                       {'action': 'recall', 'idempotency_key': key},
                       'ncp_retained_read', 'artifact_id', 'outcome_unknown')
    assert actual['status'] == 'failed' and actual['error'] == 'worker_lost'
    assert actual['replayed'] is True
    locator = actual['artifact_id']
    assert locator.startswith('failure-')
    assert control.host.retained.native(locator) == {
        'error': 'worker_lost', 'operation': 'ncp_domain_run',
    }
    # Recovery has settled once. Later canonical recall keeps the failure locator
    # and never performs the admitted scientific operation.
    assert coordinator.recover(control.host.retained)['jobs'] == []
    later = invoke('ncp_coordinate', {'action': 'recall', 'idempotency_key': key},
                   host=control.facade)
    assert later['status'] == 'failed' and later['artifact_id'] == locator
    assert list(Path(os.environ['NCP_RETAIN_DIR']).rglob('failure-*.json')) == [
        control.host.retained._path(locator)
    ]
    assert coordinator._EXECUTIONS == executions
    assert control.owner.capabilities()['execution_budget']['used'] == 0


def test_nested_retained_source_statuses_do_not_halt_recipe(control, monkeypatch):
    untrusted = {
        'status': 'failed', 'state': 'unknown', 'execution_outcome': 'unknown',
        'error': 'worker_lost', 'record': {'status': 'incomplete'},
        'source_text': 'pending cancelled refused outcome_unknown',
    }
    locator = control.host.retained.keep(untrusted, kind='synthetic-source', catalog={})
    fresh_runner, admissions = recipe(control, 'ncp_retained_read', {'artifact_id': locator},
                                     'ncp_retained_read', 'artifact_id')
    runner = fresh_runner()
    observed = observe_calls(control, monkeypatch)
    executions = coordinator._EXECUTIONS
    result = runner.run('runtime-outcome', '1.0.0', {}, attempt='read-source')
    assert result['status'] == 'completed' and result['steps_completed'] == 2
    assert [row['name'] for row in observed] == ['ncp_retained_read', 'ncp_retained_read']
    assert [row['step_id'] for row in admissions] == ['primary', 'dependent']
    final = runner.receipt(result['artifact_id'])['receipt']
    assert 'halted_step' not in final
    assert len(final['steps']) == 2
    for step, call in zip(final['steps'], observed, strict=True):
        actual = call['response']['data']
        saved = runner.receipt(step['artifact_id'])['receipt']
        assert call['response']['status'] == 'ok'
        assert actual['bundle'] == untrusted and actual['artifact_id'] == locator
        assert saved['status'] == 'step_completed'
        assert saved['result'] == actual and saved['result_sha256'] == digest(actual)
    assert control.owner.capabilities()['execution_budget']['used'] == 0
    assert coordinator._EXECUTIONS == executions


@pytest.mark.parametrize(('result', 'expected'), [
    ({'status': 'unknown'}, 'outcome_unknown'),
    ({'status': 'outcome_unknown'}, 'outcome_unknown'),
    ({'execution_outcome': 'unknown'}, 'outcome_unknown'),
    ({'status': 'queued'}, 'pending'),
    ({'status': 'running'}, 'pending'),
    ({'status': 'pending'}, 'pending'),
    ({'status': 'refused'}, 'refused'),
    ({'status': 'failed'}, 'failed'),
    ({'status': 'cancelled'}, 'cancelled'),
    ({'status': 'incomplete'}, 'incomplete'),
])
def test_top_level_runtime_status_classifier(result, expected):
    # Small classifier-only coverage supplements the real integration paths above.
    assert _step_outcome('ncp_retained_read', {}, result) == expected
