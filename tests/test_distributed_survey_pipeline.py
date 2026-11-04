"""Real local PostgreSQL/JetStream and synthetic canonical survey execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from threading import Event
import time
from unittest.mock import patch
from uuid import uuid4
import zipfile

import pytest

from ciw.campaign_contracts import canonical, parse
from ciw.distributed_queue import QueueRefusal
from ciw.distributed_services import DistributedServices
from ciw.distributed_survey_pipeline import DistributedSurveyPipeline, RUN
from ciw.principal_policy import PrincipalRefusal, PrincipalOutcomeUnknown
from ciw.services import invoke
from ciw.survey_analysis_pipeline import PipelineRefusal
from test_distributed_coordinator import runtime, active_connection
from test_survey_pipeline_profile import example


@pytest.fixture(scope='module', autouse=True)
def custody_capture(runtime):
    yield
    destination = os.getenv('ODSP_SURVEY_DISTRIBUTED_CUSTODY')
    if destination:
        helper = Path(__file__).resolve().parents[1] / 'tools/distributed-coordinator/restore_fixture.py'
        spec = importlib.util.spec_from_file_location('survey_integration_restore_fixture', helper)
        module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        checkpoint = module.capture(runtime, runtime.root / 'survey-qualification.dump')
        root = Path(destination); root.mkdir(mode=0o700, exist_ok=False)
        shutil.copyfile(checkpoint.archive, root/'coordinator.dump')
        (root/'custody.json').write_text(checkpoint.snapshot_json)
        (root/'checkpoint.json').write_text(json.dumps({
            'archive_sha256':checkpoint.archive_sha256, 'snapshot_sha256':checkpoint.snapshot_sha256,
            'scope':'quiesced local synthetic SQL custody; no broker data, credentials or host authority',
            'volatile_runtime_scratch':True},sort_keys=True)+'\n')


@pytest.fixture
def composed(runtime, tmp_path, monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'synthetic/survey/retained'))
    p = example().bind(tmp_path / 'synthetic')
    generation = 'survey-' + uuid4().hex
    service = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped,
        generation=generation, lease_seconds=3)
    yield p, service, generation
    destination = os.getenv('ODSP_SURVEY_DISTRIBUTED_CUSTODY')
    if destination:
        # Preserve unique synthetic local artifacts and native SQLite backups
        # before volatile fixture scratch disappears. Exclude lock/WAL files;
        # no broker config or host environment is under this synthetic root.
        archive = Path(destination + '-local-members.zip')
        with zipfile.ZipFile(archive, 'a', compression=zipfile.ZIP_DEFLATED) as bundle:
            files = [f for f in p.root.rglob('*') if f.is_file() and not f.is_symlink()
                and not f.name.endswith(('.lock', '-wal', '-shm'))]
            manifest = {}
            for path in sorted(files):
                raw = path.read_bytes()
                if raw.startswith(b'SQLite format 3\0'):
                    backup = tmp_path / ('backup-' + uuid4().hex + '.sqlite')
                    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as source, \
                            sqlite3.connect(backup) as target:
                        source.backup(target)
                    raw = backup.read_bytes()
                relative = str(path.relative_to(p.root))
                assert len(raw) <= 8*1024*1024
                bundle.writestr(generation + '/' + relative,raw)
                manifest[relative] = {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
            bundle.writestr(generation + '/preserved-members.json', canonical(manifest))


def expire(runtime, service, job):
    with active_connection(runtime.dsn) as db:
        db.execute("UPDATE odsp_distributed_v1.jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE queue_id=%s AND job_id=%s", (service.queue.queue_id, job['job_id']))


def raw_job(runtime, service, job):
    with active_connection(runtime.dsn) as db:
        return db.execute("SELECT status,result,dispatch_intent FROM odsp_distributed_v1.jobs "
            "WHERE queue_id=%s AND job_id=%s", (service.queue.queue_id, job['job_id'])).fetchone()


def native_count(p):
    return len(parse((p.source.active.queue.directory / 'queue.json').read_bytes())['jobs'])


def test_real_jetstream_canonical_execution_and_permanent_idempotency(composed, runtime):
    import ciw.data_analysis as analysis
    p, service, generation = composed
    with ThreadPoolExecutor(4) as pool:
        jobs = list(pool.map(lambda _: service.enqueue(attempt='once'), range(4)))
    assert len({j['job_id'] for j in jobs}) == 1 and sum(not j['replayed'] for j in jobs) == 1
    async def cycle():
        import nats
        client = await nats.connect(runtime.nats_url, token=runtime.token, allow_reconnect=False)
        try:
            bridge = service.bridge(client.jetstream(), stream='survey_' + uuid4().hex[:12], durable='worker')
            await bridge.provision()
            first = await service.cycle(bridge, 'worker')
            assert await service.cycle(bridge, 'worker', timeout=.1) is None
            assert (await bridge.js.consumer_info(bridge.stream, bridge.durable)).num_ack_pending == 0
            return first
        finally:
            await client.close()
    with patch.object(analysis, 'numerical', wraps=analysis.numerical) as calls:
        first = asyncio.run(cycle())
        assert first['status'] == 'completed' and first['result']['service_result']['status'] == 'held'
        custody = (p.source.active.queue.directory / 'queue.json').read_bytes()
        reopened = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation)
        replay = reopened.enqueue(attempt='once')
        assert replay['result'] == first['result'] and replay['replayed']
        assert reopened.execute_one('after-restart') is None
        assert calls.call_count == 1 and native_count(p) == 1
        assert (p.source.active.queue.directory / 'queue.json').read_bytes() == custody
    result = first['result']
    assert result['fence']['job_id'] == first['job_id'] and result['fence']['number'] == first['fence']
    assert result['service_result']['statistics']['mean'] == 10.1875
    assert not result['claims']['authenticated_time'] and not result['claims']['hosted_qualified']
    assert [e['state'] for e in service.history(first['job_id'])] == ['queued', 'claimed', 'dispatching', 'completed']
    assert [r['event']['phase'] for r in p.chain.verify(p.anchor.read())] == ['admitted', 'succeeded']
    if os.getenv('ODSP_SURVEY_DISTRIBUTED_CAPTURE'):
        root = Path(os.environ['ODSP_SURVEY_DISTRIBUTED_CAPTURE']); root.mkdir(mode=0o700, exist_ok=False)
        artifact = p.source.active.inspect('once')['artifact']
        (root/'artifact.json').write_bytes(canonical(artifact))
        (root/'job.json').write_bytes(canonical(first))
        (root/'history.json').write_bytes(canonical(service.history(first['job_id'])))
        (root/'audit.jsonl').write_bytes(p.chain.path.read_bytes())
        (root/'anchor.json').write_bytes(canonical(p.anchor.read()))
        (root/'native-queue.json').write_bytes(custody)


@pytest.mark.parametrize('mode', ['operation', 'science', 'profile', 'source', 'pipeline', 'constraints'])
def test_current_admissions_and_source_holds_block_queued_dispatch(composed, runtime, mode):
    p, service, _ = composed
    job = service.enqueue(attempt='revoked')
    grant = p.source.state['policy']['principals'][0]
    if mode == 'operation': grant['operations'] = []
    elif mode == 'science': grant['scientific_operations'] = []
    elif mode == 'profile': p.state['decision'] = False
    elif mode == 'source': p.source.state['source_allowed'] = False
    elif mode == 'pipeline': p.source.state['pipeline_allowed'] = False
    else:
        next(g for g in grant['operations'] if g['name'] == RUN)['constraints'] = {'attempt': ['other']}
    with patch.object(p.source.active, 'run', side_effect=AssertionError('unauthorized native execution')):
        with pytest.raises(ValueError): service.execute_one('worker')
    assert raw_job(runtime, service, job) == ('queued', None, None)
    assert native_count(p) == 0 and p.chain.verify(p.anchor.read()) == []


def test_seal_prevents_direct_local_mcp_and_reopened_native_execution(composed):
    p, service, generation = composed
    request = {'plan_ref': p.source.active.plan_ref, 'attempt': 'bypass'}
    with pytest.raises(PipelineRefusal, match='distributed_claim_required'):
        p.source.active.run('bypass')
    assert p.profile.call(RUN, request)['effect_state'] == 'unknown'
    with pytest.raises(PipelineRefusal, match='distributed_claim_required'):
        p.source.pipeline().run('bypass')
    assert native_count(p) == 0
    # A new generation cannot silently reset this directory's durable binding.
    with pytest.raises(PipelineRefusal, match='distributed_binding_changed'):
        DistributedSurveyPipeline(service.queue._dsn, host=p.wrapped, generation=generation + '-new')


@pytest.mark.parametrize('stage', ['audit', 'analysis', 'retained'])
def test_lost_fence_withholds_publication_and_never_resends(composed, runtime, stage):
    import ciw.data_analysis as analysis
    p, service, _ = composed
    job = service.enqueue(attempt='lost')
    queued = service.enqueue(attempt='already-queued')
    claim = service.queue.claim_next('expired-worker'); service.queue.begin_dispatch(claim)
    original_cas = p.wrapped._audit.compare_and_set_anchor
    original_numeric, original_keep = analysis.numerical, p.source.active.store.keep
    observed = []
    def cas(before, after):
        answer = original_cas(before, after)
        if stage == 'audit' and after['sequence'] == 1: expire(runtime, service, job)
        return answer
    def compute(*a, **k):
        value = original_numeric(*a, **k); observed.append('analysis')
        if stage == 'analysis': expire(runtime, service, job)
        return value
    def keep(*a, **k):
        value = original_keep(*a, **k); observed.append(value)
        if stage == 'retained': expire(runtime, service, job)
        return value
    with patch.object(p.wrapped._audit, 'compare_and_set_anchor', side_effect=cas), \
         patch.object(analysis, 'numerical', side_effect=compute), \
         patch.object(p.source.active.store, 'keep', side_effect=keep):
        with pytest.raises((ValueError, PrincipalOutcomeUnknown)):
            service._dispatch(RUN, {'plan_ref': p.source.active.plan_ref, 'attempt': 'lost'}, claim)
    service.queue.recover()
    assert service.inspect(job['job_id'])['status'] == 'outcome_unknown'
    assert service.enqueue(attempt='lost')['status'] == 'outcome_unknown'
    assert ('analysis' in observed) is (stage != 'audit')
    assert any(x.startswith('retained-') for x in observed) is (stage == 'retained')
    with pytest.raises(QueueRefusal, match='stale_claim'):
        service.queue.complete(claim, {})
    with pytest.raises(QueueRefusal, match='unknown_outcome'):
        service.enqueue(attempt='new-after-unknown')
    with pytest.raises(QueueRefusal, match='unknown_outcome'):
        service.execute_one('must-not-start')
    assert raw_job(runtime, service, queued) == ('queued', None, None)
    assert raw_job(runtime, service, job)[1] is None


def test_foreign_adapter_cannot_reuse_claim_and_matching_attempt_is_required(composed, runtime):
    p, service, generation = composed
    job = service.enqueue(attempt='owned')
    claim = service.queue.claim_next('owner'); service.queue.begin_dispatch(claim)
    reopened = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation)
    with pytest.raises(QueueRefusal, match='not_owned'):
        reopened._dispatch(RUN, {'plan_ref': p.source.active.plan_ref, 'attempt': 'owned'}, claim)
    with pytest.raises(PrincipalRefusal):
        service._dispatch(RUN, {'plan_ref': p.source.active.plan_ref, 'attempt': 'changed'}, claim)
    with pytest.raises(PrincipalRefusal):
        service.queue.enqueue(RUN, {'plan_ref': p.source.active.plan_ref, 'attempt': 'argument'}, 'different')
    expire(runtime, service, job); service.queue.recover()
    assert native_count(p) == 0


def test_lost_retention_ack_is_unknown_in_both_stores(composed, runtime):
    p, service, _ = composed
    job = service.enqueue(attempt='retention-ack')
    original, retained = p.source.active.store.keep, []
    def lose(*a, **k):
        retained.append(original(*a, **k))
        raise OSError('synthetic lost local retention acknowledgement')
    with patch.object(p.source.active.store, 'keep', side_effect=lose):
        with pytest.raises(PrincipalOutcomeUnknown): service.execute_one('worker')
    assert len(retained) == 1 and p.source.active.store.native(retained[0])['status'] == 'held'
    assert service.inspect(job['job_id'])['status'] == 'outcome_unknown'
    assert p.source.active.inspect('retention-ack')['status'] == 'outcome_unknown'
    assert service.execute_one('no-retry') is None


def test_lost_pg_completion_ack_keeps_completed_result_without_second_compute(composed):
    p, service, _ = composed
    job = service.enqueue(attempt='pg-ack')
    original = service.queue.complete
    def lose(claim, result):
        original(claim, result)
        raise OSError('synthetic lost completion acknowledgement')
    with patch.object(service.queue, 'complete', side_effect=lose):
        with pytest.raises(OSError): service.execute_one('worker')
    with patch.object(p.source.active, 'run', side_effect=AssertionError('replayed completed calculation')):
        assert service.queue.recover() == []
        assert service.inspect(job['job_id'])['status'] == 'completed'
        assert service.enqueue(attempt='pg-ack')['status'] == 'completed'
        assert service.execute_one('later') is None
    assert native_count(p) == 1


@pytest.mark.parametrize('mode', ['read-grant', 'source-drift'])
def test_completed_pg_result_has_fresh_read_admission_and_source_hold(composed, runtime, mode):
    p, service, _ = composed
    job = service.enqueue(attempt='disclosure'); service.execute_one('worker')
    if mode == 'read-grant':
        grant = p.source.state['policy']['principals'][0]
        grant['operations'] = [g for g in grant['operations'] if g['name'] != 'ncp_pipeline_inspect']
    else:
        changed = parse(p.source.files['capture.json']); changed[0]['height_m'] = 11
        p.source.ledger.capture(p.source.survey.source_id, p.source.survey.source_ref,
            canonical(changed), attempt='drift', observed={'value':990,'frame':p.source.example.FRAME})
    with pytest.raises(ValueError): service.inspect(job['job_id'])
    assert raw_job(runtime, service, job)[0] == 'completed'
    assert native_count(p) == 1


def test_worker_process_exit_and_pg_crash_recovery_never_retries(composed, runtime):
    p, service, generation = composed
    completed = service.enqueue(attempt='before-restart'); service.execute_one('worker')
    lost = service.enqueue(attempt='child-interrupted')
    ctx = multiprocessing.get_context('fork')
    def child():
        active = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation, lease_seconds=.3)
        claim = active.queue.claim_next('child-worker'); active.queue.begin_dispatch(claim)
        os._exit(0)
    process = ctx.Process(target=child); process.start(); process.join(timeout=10)
    assert process.exitcode == 0 and raw_job(runtime, service, lost)[0] == 'dispatching'
    runtime.stop_process('pg', crash=True); time.sleep(.35); runtime.start_postgres()
    reopened = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation)
    assert reopened.inspect(completed['job_id'])['status'] == 'completed'
    assert reopened.queue.recover()[0]['job_id'] == lost['job_id']
    assert reopened.inspect(lost['job_id'])['status'] == 'outcome_unknown'
    assert reopened.execute_one('after-restart') is None and native_count(p) == 1


def test_outbox_puback_loss_and_broker_restart_do_not_repeat_execution(composed, runtime):
    p, service, _ = composed
    job = service.enqueue(attempt='outbox')
    stream = 'outbox_' + uuid4().hex[:12]
    async def exercise():
        import nats
        client = await nats.connect(runtime.nats_url, token=runtime.token, allow_reconnect=False)
        bridge = service.bridge(client.jetstream(), stream=stream, durable='worker', ack_wait=.2)
        await bridge.provision()
        with patch.object(service.queue, 'notification_published', side_effect=OSError('synthetic lost PubAck custody')):
            with pytest.raises(OSError): await bridge.publish_pending()
        assert len(service.queue.pending_notifications()) == 1
        await client.close()
        runtime.stop_process('nats', crash=True); runtime.start_nats()
        client = await nats.connect(runtime.nats_url, token=runtime.token, allow_reconnect=False)
        try:
            bridge = service.bridge(client.jetstream(), stream=stream, durable='worker', ack_wait=.2)
            await bridge.bind()
            result = await service.cycle(bridge, 'worker')
            assert result['status'] == 'completed'
            with patch.object(p.source.active, 'run', side_effect=AssertionError('duplicate notification recomputed')):
                # Republish exact terminal wake-up without finite-window broker
                # deduplication. PG is still the permanent authority.
                payload = canonical({'schema':'ciw.distributed-wakeup.v1','queue_id':service.queue.queue_id,
                    'job_id':job['job_id'],'request_sha256':job['request_sha256']})
                await bridge.js.publish(bridge.subject, payload, stream=stream)
                assert (await bridge.process_one('duplicate', service._dispatch))['status'] == 'completed'
        finally:
            await client.close()
    asyncio.run(exercise())
    assert native_count(p) == 1 and service.inspect(job['job_id'])['status'] == 'completed'


def test_generic_distributed_read_adapter_still_refuses_pipeline_mutation(composed, runtime):
    p, service, _ = composed
    with pytest.raises(PrincipalRefusal):
        DistributedServices(runtime.dsn, host=p.wrapped, generation='not-generic', operations=[RUN])
    with pytest.raises(TypeError): service.enqueue(attempt='sql', sql='SELECT 1')
    with pytest.raises(ValueError):
        service.queue.enqueue(RUN, {'plan_ref':p.source.active.plan_ref,'attempt':'bad','sql':'SELECT 1'}, 'bad')
    assert native_count(p) == 0


def test_parallel_workers_share_one_durable_attempt_and_heartbeat(composed, runtime):
    import ciw.data_analysis as analysis
    p, service, generation = composed
    service = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation, lease_seconds=.6)
    other = DistributedSurveyPipeline(runtime.dsn, host=p.wrapped, generation=generation, lease_seconds=.6)
    job = service.enqueue(attempt='long-computation')
    original = analysis.numerical
    def slow(*a, **kw):
        time.sleep(1.3)
        return original(*a, **kw)
    with patch.object(analysis, 'numerical', side_effect=slow) as calls, \
            patch.object(service.queue, 'renew', wraps=service.queue.renew) as renew, \
            patch.object(other.queue, 'renew', wraps=other.queue.renew) as other_renew:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(q.execute_one, 'worker-' + str(i)) for i,q in enumerate((service,other))]
            answers = [f.result(timeout=20) for f in futures]
        assert sum(a is not None for a in answers) == 1
        assert calls.call_count == 1
        assert renew.call_count + other_renew.call_count >= 2
        # Either worker can win. Inspect the actual committed completion rather
        # than assuming thread scheduling or a particular renewal mutex.
        assert service.inspect(job['job_id'])['status'] == 'completed'
    assert native_count(p) == 1


def test_heartbeat_live_check_refuses_revocation_during_locked_canonical_call(composed, runtime):
    import ciw.data_analysis as analysis
    p, _, generation = composed
    service = DistributedSurveyPipeline(runtime.dsn,host=p.wrapped,generation=generation,lease_seconds=.6)
    job = service.enqueue(attempt='revoke-running')
    entered, release, rejected = Event(), Event(), Event()
    numerical, renew = analysis.numerical, service.queue.renew
    def compute(*a, **kw):
        entered.set()
        assert release.wait(timeout=5)
        return numerical(*a, **kw)
    def renewal(claim):
        try:
            return renew(claim)
        except Exception:
            rejected.set()
            raise
    with patch.object(analysis,'numerical',side_effect=compute), \
            patch.object(service.queue,'renew',side_effect=renewal):
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.execute_one,'worker')
            try:
                assert entered.wait(timeout=5)
                p.source.state['policy']['principals'][0]['scientific_operations']=[]
                assert rejected.wait(timeout=3)
            finally:
                release.set()
            with pytest.raises(ValueError): future.result(timeout=10)
    assert raw_job(runtime,service,job)[1] is None
    expire(runtime,service,job);service.queue.recover()
    assert raw_job(runtime,service,job)[0]=='outcome_unknown'


@pytest.mark.parametrize('stage', ['before-retain', 'after-retain', 'settlement'])
def test_late_admission_or_read_revocation_never_publishes_a_pg_result(composed, runtime, stage):
    p, service, _ = composed
    job = service.enqueue(attempt='late-revocation')
    original = p.source.active.store.keep
    def retain(*a, **kw):
        if stage == 'before-retain':
            p.source.state['pipeline_allowed'] = False
            p.source.active._guard('retain')
        result = original(*a, **kw)
        if stage == 'after-retain': p.source.state['source_allowed'] = False
        return result
    complete = service.queue.complete
    def settle(claim, value):
        if stage == 'settlement':
            grant = p.source.state['policy']['principals'][0]
            grant['operations'] = [g for g in grant['operations'] if g['name'] != 'ncp_pipeline_inspect']
        return complete(claim, value)
    with patch.object(p.source.active.store, 'keep', side_effect=retain), \
            patch.object(service.queue, 'complete', side_effect=settle):
        with pytest.raises(ValueError): service.execute_one('worker')
    assert raw_job(runtime, service, job)[1] is None
    # Recovery does not turn source or policy revocation into permission.
    expire(runtime, service, job)
    service.queue.recover()
    assert raw_job(runtime, service, job)[0] == 'outcome_unknown'
    assert service.execute_one('must-not-resend') is None


@pytest.mark.parametrize('bad', [False, None, 1])
def test_native_distributed_consistency_check_requires_exact_true(composed, bad):
    p, service, _ = composed
    with pytest.raises(PipelineRefusal, match='distributed_claim_refused'):
        with p.source.active.execution_scope(service._binding, lambda:bad):
            p.source.active.run('bad-host-check')
    assert native_count(p) == 0


def test_seal_tamper_and_host_rebinding_are_refused(composed, runtime):
    p, service, _ = composed
    path = p.source.active.directory / 'distributed-execution.json'
    value = parse(path.read_bytes()); value['binding'] = '0'*64
    path.write_bytes(canonical(value))
    with pytest.raises(PipelineRefusal): p.source.active.run('changed-seal')
    with pytest.raises(ValueError): service.enqueue(attempt='changed-seal')
    assert native_count(p) == 0


def test_sealing_cannot_adopt_a_previously_completed_local_occurrence(runtime,tmp_path,monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'prior/survey/retained'))
    p=example().bind(tmp_path/'prior')
    result=p.source.active.run('previous-local')
    with pytest.raises(PipelineRefusal,match='used_local_queue_not_adoptable'):
        DistributedSurveyPipeline(runtime.dsn,host=p.wrapped,generation='not-adoptable-'+uuid4().hex)
    assert p.source.active.inspect('previous-local')['artifact_ref']==result['artifact_ref']
    assert native_count(p)==1 and not (p.source.active.directory/'distributed-execution.json').exists()


def test_local_run_waiting_before_lease_rechecks_new_distributed_seal(runtime,tmp_path,monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'racing/survey/retained'))
    p=example().bind(tmp_path/'racing')
    entered,release=Event(),Event()
    original=p.source.active._guard
    def pause(action,context=None):
        if action=='run' and not entered.is_set():
            entered.set();assert release.wait(timeout=5)
        return original(action,context)
    with patch.object(p.source.active,'_guard',side_effect=pause):
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(p.source.active.run,'early-local')
            try:
                assert entered.wait(timeout=5)
                DistributedSurveyPipeline(runtime.dsn,host=p.wrapped,generation='sealed-race-'+uuid4().hex)
            finally:
                release.set()
            with pytest.raises(PipelineRefusal,match='distributed_claim_required'):
                future.result(timeout=10)
    assert native_count(p)==0


def test_coordinator_cold_restore_with_existing_local_evidence_and_no_resend(composed, runtime, tmp_path):
    # Separate owned services keep the module fixture and other tests intact.
    # Only SQL custody is restored; local survey/audit/retention are deliberately
    # still present. This is not a new full cross-store recovery claim.
    p, _, generation = composed
    tools = Path(__file__).resolve().parents[1] / 'tools/distributed-coordinator'
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name,path)
        m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
        return m
    local = load('survey_cold_runtime', tools/'local_runtime.py')
    cold = load('survey_cold_restore', tools/'restore_fixture.py')
    from ciw.distributed_queue import install_schema
    # AF_UNIX socket paths have a small fixed length; pytest node IDs are long.
    source = local.LocalRuntime(runtime.root.parent/('cold-a-'+uuid4().hex[:6]),os.environ['ODSP_DISTRIBUTED_PG_BIN'],os.environ['ODSP_DISTRIBUTED_NATS_SERVER'])
    target = local.LocalRuntime(runtime.root.parent/('cold-b-'+uuid4().hex[:6]),os.environ['ODSP_DISTRIBUTED_PG_BIN'],os.environ['ODSP_DISTRIBUTED_NATS_SERVER'])
    try:
        source.start();install_schema(source.dsn)
        with active_connection(source.dsn) as db:
            db.execute('CREATE TABLE qualification_effects(job_id text PRIMARY KEY,value integer NOT NULL)')
        service = DistributedSurveyPipeline(source.dsn, host=p.wrapped,generation=generation)
        done = service.enqueue(attempt='restore-complete');service.execute_one('worker')
        lost = service.enqueue(attempt='restore-unknown')
        claim = service.queue.claim_next('lost-worker');service.queue.begin_dispatch(claim)
        expire(source,service,lost);service.queue.recover()
        checkpoint = cold.capture(source,source.root/'coordinator.dump')
        before = service.inspect(done['job_id'])
        result = cold.restore_current(source,target,checkpoint,
            expected_archive_sha256=checkpoint.archive_sha256,expected_snapshot_sha256=checkpoint.snapshot_sha256)
        reopened = DistributedSurveyPipeline(target.dsn,host=p.wrapped,generation=generation)
        with patch.object(p.source.active,'run',side_effect=AssertionError('restored occurrence resent')):
            assert reopened.inspect(done['job_id']) == before
            assert reopened.inspect(lost['job_id'])['status'] == 'outcome_unknown'
            assert reopened.execute_one('after-restore') is None
            assert reopened.queue.recover() == []
        assert native_count(p) == 1
        if os.getenv('ODSP_SURVEY_DISTRIBUTED_CUSTODY'):
            root=Path(os.environ['ODSP_SURVEY_DISTRIBUTED_CUSTODY']+'-cold');root.mkdir(mode=0o700,exist_ok=False)
            shutil.copyfile(checkpoint.archive,root/'coordinator.dump')
            (root/'before.json').write_text(checkpoint.snapshot_json)
            (root/'after.json').write_text(cold.snapshot(target))
            (root/'result.json').write_bytes(canonical(result))
            (root/'job.json').write_bytes(canonical(reopened.inspect(done['job_id'])))
            (root/'artifact.json').write_bytes(canonical(p.source.active.inspect('restore-complete')['artifact']))
    finally:
        target.close();source.close()
