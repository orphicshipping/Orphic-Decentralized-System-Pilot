"""Real local Domain H pipeline, native analysis and durable no-retry custody."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal, localcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
from unittest.mock import patch

import pytest

from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import content_ref, version_ref
from ciw.offline_survey import OfflineSurveyFlow
from ciw.survey_analysis_pipeline import (MODEL, OPERATION, PipelineRefusal,
    PipelineOutcomeUnknown, SurveyAnalysisPipeline, validate_artifact)


ROOT = Path(__file__).resolve().parents[1]


def example():
    spec = importlib.util.spec_from_file_location('domain_h_pipeline_example',
        ROOT / 'examples/domain-h-survey-analysis/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared(tmp_path):
    return example().prepare(tmp_path / 'synthetic')


def custody(p):
    return (p.root / 'pipeline/queue/queue.json').read_bytes()


def forbidden(*args, **kwargs):
    pytest.fail('pipeline reran work, used an external source or invoked a service')


def test_real_sql_native_analysis_covariance_and_exact_lineage(prepared):
    import ciw.survey_analysis_pipeline as module
    import ciw.data_analysis as analysis
    from ciw.adapters.oscillator import summarize_values
    p = prepared
    with patch.object(module, 'select_rows', wraps=module.select_rows) as sql, \
         patch.object(analysis, 'numerical', wraps=analysis.numerical) as numerical, \
         patch('ciw.adapters.oscillator.summarize_values', wraps=summarize_values) as statistics, \
         patch('socket.create_connection', side_effect=forbidden), \
         patch('subprocess.Popen', side_effect=forbidden), \
         patch('ciw.services.invoke', side_effect=forbidden):
        result = p.active.run('first')
    artifact = result['artifact']
    assert sql.call_count == numerical.call_count == statistics.call_count == 1
    assert numerical.call_args.args[0] == 'uncertainty.propagate.v1'
    assert [row['point_id'] for row in artifact['transform']['rows']] == ['ASSET-A', 'ASSET-B']
    assert artifact['analysis']['value']['statistics']['mean'] == 10.1875
    with localcontext() as ctx:
        ctx.prec = 50
        variance = (Decimal('.012')**2 + Decimal('.015')**2) / 4
        sigma = variance.sqrt()
    value = artifact['analysis']['value']
    assert value['propagation']['result']['covariance']['matrix'][0][0] == pytest.approx(float(variance), rel=1e-14)
    assert value['uncertainty']['mean']['standard_uncertainty'] == pytest.approx(float(sigma), rel=1e-14)
    assert value['uncertainty']['mean']['coverage_probability'] is None
    assert all(value['uncertainty'][k]['status'] == 'not_qualified' for k in ('minimum', 'maximum', 'rms'))
    source = artifact['input']
    assert source['capture_ref'] == p.captured['capture_ref']
    assert source['raw_ref'] == content_ref(p.files['capture.json'])
    assert source['model'] == MODEL and source['schema_ref'] == artifact['transform']['schema_ref']
    assert source['capture_receipt']['principal'] == 'survey-operator'
    assert source['capture_receipt']['tenant'] == 'synthetic-company'
    assert artifact['pipeline']['implementation']['source_refs']['survey_analysis_pipeline.py'] \
           == content_ref((ROOT / 'src/ciw/survey_analysis_pipeline.py').read_bytes())
    for row in artifact['transform']['rows']:
        assert row['frame_id'] == 'synthetic-site-grid' and row['unit'] == 'm'
        assert row['sample_time_s'] == 990 and row['clock_id'] == 'synthetic-reference'
    assert result['status'] == 'held' and artifact['review']['status'] == 'pending_human_review'
    assert result['claims']['authenticated_time'] is False
    assert result['claims']['nsl_admission'] == 'not_verified'
    assert result['claims']['authorizes_state_admission'] is False
    assert validate_artifact(artifact, expected_ref=result['artifact_ref']) == artifact
    assert p.ledger.verify_chain()['local_chain_consistent'] is True


def test_same_attempt_and_fresh_host_reopen_never_reexecute_or_append(prepared):
    p = prepared
    first = p.active.run('idempotent')
    before = custody(p)
    count = p.ledger.verify_chain()['records']
    with patch('ciw.survey_analysis_pipeline.select_rows', side_effect=forbidden), \
         patch('ciw.data_analysis.numerical', side_effect=forbidden), \
         patch.object(p.ledger, 'assess', side_effect=forbidden), \
         patch('ciw.survey_analysis_pipeline.Store.keep', side_effect=forbidden):
        replay = p.active.run('idempotent')
        reopened = p.pipeline().run('idempotent')
    assert first == replay == reopened
    assert custody(p) == before and p.ledger.verify_chain()['records'] == count


def test_explicit_new_attempt_preserves_values_with_new_occurrence(prepared):
    a = prepared.active.run('a')
    b = prepared.active.run('b')
    assert a['job_id'] != b['job_id'] and a['artifact_id'] != b['artifact_id']
    assert a['artifact_ref'] != b['artifact_ref']
    assert a['reproducible_value_ref'] == b['reproducible_value_ref']
    assert a['artifact']['analysis']['value'] == b['artifact']['analysis']['value']
    assert a['artifact']['analysis']['lineage'] != b['artifact']['analysis']['lineage']


def test_concurrent_same_attempt_has_one_sql_and_numerical_execution(prepared):
    import ciw.data_analysis as analysis
    entered, release = Event(), Event()
    original = analysis.numerical
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    with patch.object(analysis, 'numerical', side_effect=delayed) as calls:
        with ThreadPoolExecutor(1) as pool:
            first = pool.submit(prepared.active.run, 'concurrent')
            assert entered.wait(5)
            try:
                with pytest.raises(PipelineRefusal, match='worker_busy'):
                    prepared.active.run('concurrent')
            finally:
                release.set()
            result = first.result(timeout=10)
    assert calls.call_count == 1 and prepared.active.run('concurrent')['artifact_id'] == result['artifact_id']


def test_lost_worker_is_unknown_and_never_resumed(prepared):
    p = prepared
    submitted = p.active.queue.enqueue(OPERATION, p.active._request, 'interrupted')
    claim = p.active.queue.claim_next('simulated-old-worker')
    p.active.queue._release(claim)  # Kernel lease disappears as in a process exit.
    with patch.object(p.active, '_produce', side_effect=forbidden):
        result = p.active.run('interrupted')
        assert result['status'] == 'outcome_unknown' and 'artifact' not in result
        assert p.active.queue.inspect(submitted['job_id'])['fence'] > claim.fence
        with pytest.raises(PipelineRefusal, match='unknown_outcome_review'):
            p.active.run('do-not-hide-uncertainty')


def test_retention_reply_loss_keeps_evidence_unknown_without_retry(prepared):
    p = prepared
    original, retained = p.active.store.keep, []
    def lost(*args, **kwargs):
        retained.append(original(*args, **kwargs))
        raise OSError('synthetic lost retention acknowledgement')
    with patch.object(p.active.store, 'keep', side_effect=lost):
        with pytest.raises(PipelineOutcomeUnknown):
            p.active.run('uncertain')
    assert len(retained) == 1
    assert p.active.store.native(retained[0])['status'] == 'held'
    with patch.object(p.active, '_produce', side_effect=forbidden):
        assert p.active.run('uncertain')['status'] == 'outcome_unknown'
        with pytest.raises(PipelineRefusal, match='unknown_outcome_review'):
            p.active.run('new-identity')


def test_committed_completion_reply_loss_is_resolved_by_readback_without_retry(prepared):
    original = prepared.active.queue.complete
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('synthetic completion reply lost after commit')
    with patch.object(prepared.active.queue, 'complete', side_effect=lost):
        with pytest.raises(PipelineOutcomeUnknown):
            prepared.active.run('completed-reply-lost')
    with patch.object(prepared.active, '_produce', side_effect=forbidden):
        assert prepared.active.inspect('completed-reply-lost')['status'] == 'held'
        assert prepared.active.run('completed-reply-lost')['status'] == 'held'


@pytest.mark.parametrize('revoke', ['pipeline', 'source', 'science', 'principal'])
def test_current_authority_refuses_before_transform_and_saved_disclosure(prepared, revoke):
    p = prepared
    first = p.active.run('before-revocation')
    before = custody(p)
    if revoke == 'pipeline':
        p.state['pipeline_allowed'] = False
    elif revoke == 'source':
        p.state['source_allowed'] = False
    elif revoke == 'science':
        p.state['policy']['principals'][0]['scientific_operations'] = []
    else:
        p.state['policy']['principals'][0]['enabled'] = False
    with patch.object(p.active, '_produce', side_effect=forbidden):
        with pytest.raises(ValueError):
            p.active.run('must-not-start')
        with pytest.raises(ValueError):
            p.active.inspect('before-revocation')
    assert custody(p) == before
    assert validate_artifact(first['artifact'], expected_ref=first['artifact_ref'])['status'] == 'held'


@pytest.mark.parametrize('stage', ['analysis', 'retain', 'complete'])
def test_late_admission_refusal_keeps_uncertain_attempt_without_resend(prepared, stage):
    prepared.state['deny_action'] = stage
    with pytest.raises(PipelineOutcomeUnknown):
        prepared.active.run('late')
    prepared.state['deny_action'] = None
    assert prepared.active.inspect('late')['status'] == 'outcome_unknown'
    with patch.object(prepared.active, '_produce', side_effect=forbidden):
        assert prepared.active.run('late')['status'] == 'outcome_unknown'


def test_drift_and_staleness_refuse_derived_work_without_rewriting_original(prepared):
    p = prepared
    first = p.active.run('original')
    original = p.ledger.snapshot(p.survey.source_id, p.captured['capture_ref'])
    changed = parse(original)
    changed[0]['height_m'] = 11
    drift = p.ledger.capture(p.survey.source_id, p.survey.source_ref, canonical(changed),
        attempt='new-drift-capture', observed={'value': 990, 'frame': p.example.FRAME})
    assert p.ledger.receipt(p.survey.source_id, drift)['body']['checks']['calibration'] == 'out_of_tolerance'
    with patch.object(p.active, '_produce', side_effect=forbidden):
        with pytest.raises(ValueError):
            p.active.run('after-drift')
        with pytest.raises(ValueError):
            p.active.inspect('original')
    assert p.ledger.snapshot(p.survey.source_id, p.captured['capture_ref']) == original
    assert validate_artifact(first['artifact'], expected_ref=first['artifact_ref'])['status'] == 'held'


def test_stale_source_refuses_before_attempt_insertion(prepared):
    p = prepared
    before = custody(p)
    p.state['now'] = 1101
    with patch.object(p.active, '_produce', side_effect=forbidden):
        with pytest.raises(ValueError):
            p.active.run('stale')
    assert custody(p) == before


def test_breaking_source_schema_refuses_old_and_new_pipeline_bindings(prepared):
    p = prepared
    doc = parse(p.files['source.json'])
    known = parse(p.files['known-good.json'])
    data = parse(p.files['capture.json'])
    doc['version'] = 2
    doc['table']['columns']['datum'] = 'string'
    for row in known + data:
        row['datum'] = 'synthetic-local-only'
    raw_known = canonical(known)
    doc['calibration']['known_good_ref'] = content_ref(raw_known)
    source_ref = p.ledger.register_source(doc, raw_known)
    captured = p.ledger.capture(p.survey.source_id, source_ref, canonical(data),
        attempt='breaking-schema-capture', observed={'value': 990, 'frame': p.example.FRAME})
    with pytest.raises(ValueError):
        p.active.run('superseded')
    changed = OfflineSurveyFlow(ledger=p.ledger, source_id=p.survey.source_id, source_ref=source_ref,
                               binding=parse(p.survey._binding_raw))
    with pytest.raises(PipelineRefusal, match='breaking_schema'):
        SurveyAnalysisPipeline(p.root / 'new-pipeline', survey=changed, capture_ref=captured,
            model=MODEL, expected_model_ref=version_ref(MODEL), admit=lambda *args: True)
    assert not (p.root / 'new-pipeline/queue').exists()


@pytest.mark.parametrize('attack', ['value', 'uncertainty', 'selection', 'schema', 'claims', 'runtime'])
def test_rehashed_but_contradictory_artifact_refuses(prepared, attack):
    value = deepcopy(prepared.active.run('original')['artifact'])
    if attack == 'value':
        value['analysis']['value']['statistics']['mean'] = 99
    elif attack == 'uncertainty':
        value['analysis']['value']['uncertainty']['mean']['standard_uncertainty'] = 0
    elif attack == 'selection':
        value['transform']['rows'] = value['transform']['rows'][:1]
    elif attack == 'schema':
        value['input']['schema_ref'] = 'sha256:' + '0' * 64
    elif attack == 'claims':
        value['claims']['authorizes_state_admission'] = True
    else:
        value['analysis']['value']['runtime']['environment']['numpy'] = 'manufactured'
    # Recompute outer hashes to test actual relationships, not only byte pins.
    core = {k: v for k, v in value['analysis'].items() if k not in ('receipt_ref', 'retention')}
    value['analysis']['receipt_ref'] = version_ref(core)
    value['reproducible_value']['analysis_value'] = value['analysis']['value']
    value['reproducible_value_ref'] = version_ref(value['reproducible_value'])
    with pytest.raises(ValueError):
        validate_artifact(value, expected_ref=version_ref(value))


def test_historical_reader_never_opens_current_store_source_or_provider(prepared):
    result = prepared.active.run('original')
    with patch('ciw.survey_analysis_pipeline.select_rows', side_effect=forbidden), \
         patch('ciw.data_analysis.numerical', side_effect=forbidden), \
         patch('ciw.data_instruments.DataInstrumentLedger.inspect_capture', side_effect=forbidden), \
         patch('ciw.survey_analysis_pipeline.Store.native', side_effect=forbidden):
        value = validate_artifact(result['artifact'], expected_ref=result['artifact_ref'])
    assert value['status'] == 'held'
    with pytest.raises(PipelineRefusal, match='pin_mismatch'):
        validate_artifact(value, expected_ref='sha256:' + '0' * 64)


def test_exact_true_admission_and_callback_failure_are_closed(prepared):
    for index, admit in enumerate((lambda *args: 1,
                                   lambda *args: (_ for _ in ()).throw(RuntimeError('PRIVATE-CALLBACK-DETAIL')))):
        with pytest.raises(PipelineRefusal, match='fresh_admission') as failure:
            SurveyAnalysisPipeline(prepared.root / ('not-admitted-' + str(index)), survey=prepared.survey,
                capture_ref=prepared.captured['capture_ref'], model=MODEL,
                expected_model_ref=version_ref(MODEL), admit=admit)
        assert 'PRIVATE-CALLBACK-DETAIL' not in str(failure.value)
        assert not (prepared.root / ('not-admitted-' + str(index)) / 'queue').exists()


def test_admission_cannot_rebind_request_after_initial_check(prepared):
    holder = {}
    def admit(action, intent, context):
        if action == 'run':
            holder['active'].capture_ref = 'sha256:' + '0' * 64
        return True
    active = SurveyAnalysisPipeline(prepared.root / 'rebind-probe', survey=prepared.survey,
        capture_ref=prepared.captured['capture_ref'], model=MODEL, expected_model_ref=version_ref(MODEL), admit=admit)
    holder['active'] = active
    before = (prepared.root / 'rebind-probe/queue/queue.json').read_bytes()
    with patch.object(active, '_produce', side_effect=forbidden):
        with pytest.raises(PipelineRefusal, match='binding_changed'):
            active.run('must-not-start')
    assert (prepared.root / 'rebind-probe/queue/queue.json').read_bytes() == before


def test_example_separate_process_reopen_uses_exact_pin(tmp_path):
    path = tmp_path / 'pilot'
    receipt = example().create(path)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(ROOT / 'src')}
    result = subprocess.run([sys.executable, str(ROOT / 'examples/domain-h-survey-analysis/run.py'),
        '--inspect', str(path / 'analysis-first.json'), '--expected-ref', receipt['first']['artifact_ref']],
        env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)['mode'] == 'historical_artifact_only_current_authority_not_restored'
    changed = path / 'same-json-different-bytes.json'
    changed.write_bytes(b' ' + (path / 'analysis-first.json').read_bytes())
    bad_pin = subprocess.run([sys.executable, str(ROOT / 'examples/domain-h-survey-analysis/run.py'),
        '--inspect', str(changed), '--expected-ref', receipt['first']['artifact_ref']],
        env=env, capture_output=True, timeout=20)
    assert bad_pin.returncode != 0 and b'byte_pin_or_budget_refused' in bad_pin.stderr
    independent = subprocess.run([sys.executable, str(ROOT / 'examples/domain-h-survey-analysis/verify.py'),
        '--pilot-root', str(path), '--expected-first-ref', receipt['first']['artifact_ref'],
        '--expected-rerun-ref', receipt['rerun']['artifact_ref'], '--code-root', str(ROOT / 'src/ciw')],
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, timeout=20)
    assert independent.returncode == 0, independent.stderr.decode()
    check = json.loads(independent.stdout)
    assert check['status'] == 'verified_held_offline' and check['retained_attempts'] == 2
    assert check['declared_reference']['variance_m2'] == '0.00009225'
