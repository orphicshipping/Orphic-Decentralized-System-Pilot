"""Boundaries plus actual installed principal-MCP journeys; synthetic data only."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tarfile
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest
import adapter


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, raw, kind in entries:
            item = tarfile.TarInfo(name)
            item.type, item.size = kind, len(raw)
            stream.addfile(item, io.BytesIO(raw))
    return base64.b64encode(data.getvalue()).decode('ascii')


def mock_bridge(result=None):
    bridge = AsyncMock()
    bridge.request.return_value = [] if result is None else result
    return bridge


@pytest.mark.parametrize('mutation', [
    lambda b: b.update(extra=True),
    lambda b: b.update(action='shell'),
    lambda b: b.update(payload={'path': '/private/path'}),
    lambda b: b['identity'].update(owner='principal:' + '0' * 64),
    lambda b: b['identity'].update(grants=['all']),
    lambda b: b['transaction'].update(owner_id='other-owner'),
    lambda b: b['transaction'].update(generation='historical'),
    lambda b: b['transaction'].update(epoch=True),
    lambda b: b['transaction'].update(id='../state'),
    lambda b: b['transaction']['release'].update(source_commit='0' * 40),
])
def test_untrusted_envelope_refuses_before_state_mutation(host, mutation):
    body = host.body()
    mutation(body)
    async def run():
        async with host.client() as client:
            result = await client.post('/transaction', json=body)
        assert result.status_code in {400, 403, 409}
        assert not adapter.STATE.exists()
    asyncio.run(run())


@pytest.mark.parametrize('raw', [
    b'{"action":"tools","action":"call"}', b'{"nested":{"a":1,"a":2}}',
    b'{"value":NaN}', b'{"value":1e500}', b'{"value":"\\ud800"}', b'\xff', b'[]',
])
def test_malformed_json_is_bounded_and_never_restored(host, raw):
    async def run():
        async with host.client() as client:
            result = await client.post('/transaction', content=raw, headers={'content-type': 'application/json'})
        assert result.status_code == 400
        assert not adapter.STATE.exists()
    asyncio.run(run())


def test_arguments_json_duplicate_keys_refuse_before_restore(host):
    body = host.body('call', {'name': 'ncp_plan_template', 'arguments_json': '{"baseline":"a","baseline":"b"}'})
    async def run():
        async with host.client() as client:
            result = await client.post('/transaction', json=body)
        assert result.status_code == 400
        assert not adapter.STATE.exists()
    asyncio.run(run())


def test_full_request_utf8_bytes_are_bounded(host, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_REQUEST', 100)
    async def run():
        async with host.client() as client:
            result = await client.post('/transaction', content=('{"x":"' + '😀' * 30 + '"}').encode(),
                                       headers={'content-type': 'application/json'})
        assert result.status_code == 413
        assert not adapter.STATE.exists()
    asyncio.run(run())


@pytest.mark.parametrize('kind,name', [
    (tarfile.REGTYPE, '../escape'), (tarfile.REGTYPE, '/absolute'),
    (tarfile.SYMTYPE, 'link'), (tarfile.LNKTYPE, 'hardlink'),
    (tarfile.FIFOTYPE, 'pipe'), (tarfile.REGTYPE, 'a\\b'),
])
def test_archive_unsafe_entries_never_replace_an_existing_workspace(host, kind, name):
    old = adapter.STATE / 'transactions' / 'old' / 'state'
    old.mkdir(parents=True)
    (old / 'sentinel').write_text('old live writer')
    body = host.body()
    encoded_state = archive([(name, b'', kind)])
    with pytest.raises(adapter.Refused):
        adapter.restore(encoded_state, body=body, config=host.config)
    assert (old / 'sentinel').read_text() == 'old live writer'


def test_duplicate_members_and_compression_expansion_refuse(host, monkeypatch):
    for entries in ([('same', b'', tarfile.REGTYPE)] * 2,
                    [('large', b'0' * 1025, tarfile.REGTYPE)]):
        monkeypatch.setattr(adapter, 'MAX_ARCHIVE', 1024)
        with pytest.raises(adapter.Refused):
            adapter.restore(archive(entries), body=host.body(), config=host.config)


def test_member_count_is_bounded_before_extract(host, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_MEMBERS', 1)
    with pytest.raises(adapter.Refused, match='expanded_checkpoint'):
        adapter.restore(archive([('one', b'', tarfile.REGTYPE), ('two', b'', tarfile.REGTYPE)]),
                        body=host.body(), config=host.config)


def test_checkpoint_refuses_compressible_expansion_and_symlink(host, monkeypatch, tmp_path):
    root = tmp_path / 'snapshot'
    root.mkdir()
    (root / 'large').write_bytes(b'0' * 1025)
    monkeypatch.setattr(adapter, 'MAX_ARCHIVE', 1024)
    with pytest.raises(adapter.Refused, match='expanded_checkpoint'):
        adapter.checkpoint(root)
    (root / 'large').unlink()
    (root / 'link').symlink_to('/etc/passwd')
    with pytest.raises(adapter.Refused, match='unsafe_checkpoint'):
        adapter.checkpoint(root)


def test_reusing_a_workspace_never_deletes_the_old_writer(host):
    body = host.body()
    destination = adapter.workspace_path(body)
    destination.mkdir(parents=True)
    (destination / 'sentinel').write_text('retain')
    async def run():
        with pytest.raises(adapter.Refused, match='reuse'):
            await adapter.execute(body, host.config, 'alpha', asyncio.Event())
    asyncio.run(run())
    assert (destination / 'sentinel').read_text() == 'retain'


def test_readiness_is_read_only_and_fence_is_explicit(host):
    async def run():
        async with host.client() as client:
            result = await client.get('/health')
            assert result.status_code == 200
            assert result.json()['live'] and result.json()['ready']
            assert not adapter.STATE.exists()
            adapter.mark_fence(host.body(), 'unknown')
            result = await client.get('/health')
            assert result.status_code == 503
            assert result.json()['live'] and result.json()['fenced'] and not result.json()['ready']
    asyncio.run(run())


def test_release_readiness_failure_discloses_no_paths(host, monkeypatch):
    def invalid():
        raise adapter.Refused('host_configuration_refused', 503)
    monkeypatch.setattr(adapter, 'configuration', invalid)
    async def run():
        async with host.client() as client:
            value = await client.get('/health')
        assert value.status_code == 503
        assert value.json()['release'] is None
        assert str(host.root) not in value.text
    asyncio.run(run())


def test_primary_and_cleanup_failure_contexts_survive_without_checkpoint(host, monkeypatch):
    bridge = mock_bridge()
    primary = adapter.ConsoleError('invalid_server_response', 'sensitive argument')
    cleanup = RuntimeError('private path')
    bridge.request.side_effect = primary
    bridge.close.side_effect = cleanup
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
    body = host.body()
    async def run():
        with pytest.raises(adapter.Uncertain) as failure:
            await adapter.execute(body, host.config, 'alpha', asyncio.Event())
        assert failure.value.primary is primary
        assert failure.value.cleanup is cleanup
        assert failure.value.__cause__ is primary
        assert adapter.workspace_path(body).is_dir()
        assert adapter.fence_path(body['identity']).exists()
        journal = (adapter.principal_path(adapter.workspace_path(body), body['identity']) / 'operator.json').read_text()
        assert 'sensitive' not in journal and 'private path' not in journal
    asyncio.run(run())


def test_checkpoint_publication_failure_never_acknowledges(host, monkeypatch):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    def fail(_):
        raise OSError('private publication path')
    monkeypatch.setattr(adapter, 'checkpoint', fail)
    async def run():
        result = await host.request()
        assert result.status_code == 503
        assert result.json() == {'error': {'code': 'outcome_unknown'}}
        assert host.state is None
        assert any((adapter.STATE / 'fences').iterdir())
    asyncio.run(run())


def test_whole_response_budget_includes_raw_json_and_state(host, monkeypatch):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge(['😀' * 40]))
    monkeypatch.setattr(adapter, 'MAX_RESPONSE', 256)
    async def run():
        result = await host.request()
        assert result.status_code == 503
        assert result.json() == {'error': {'code': 'outcome_unknown'}}
        assert host.state is None
    asyncio.run(run())


def test_cancellation_retains_the_live_actor_and_fences_followup(host, monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        bridge = mock_bridge()
        async def waiting(*_):
            entered.set()
            await release.wait()
            return []
        bridge.request.side_effect = waiting
        monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
        request = asyncio.create_task(host.request())
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert adapter.ACTIVE
        assert not bridge.close.called
        assert adapter.LOCAL_FENCES
        release.set()
        await asyncio.gather(*tuple(adapter.ACTIVE), return_exceptions=True)
        followup = await host.request()
        assert followup.status_code == 409
        assert followup.json()['error']['code'] == 'outcome_unknown'
    asyncio.run(run())


def test_audit_sink_failure_prevents_provider_start(host, monkeypatch):
    bridge = mock_bridge()
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
    def fail(*_):
        raise OSError('secret sink path')
    monkeypatch.setattr(adapter, 'audit', fail)
    async def run():
        result = await host.request()
        assert result.status_code == 503
        assert not bridge.open.called
        assert adapter.LOCAL_FENCES
    asyncio.run(run())


def test_queue_has_a_bound_and_refuses_before_restore(host, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_INFLIGHT', 1)
    monkeypatch.setattr(adapter, 'INFLIGHT', 1)
    async def run():
        result = await host.request()
        assert result.status_code == 429
        assert not adapter.STATE.exists()
    asyncio.run(run())


def test_audit_capacity_refuses_without_dropping_old_events(host, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    async def run():
        result = await host.request()
        assert result.status_code == 200
    asyncio.run(run())
    root = tmp_path / 'full-journal'
    root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(host.state)), mode='r:gz') as stream:
        stream.extractall(root, filter='data')
    path = next((root / 'principals').glob('*/operator.json'))
    journal = json.loads(path.read_text())
    journal['events'] = [journal['events'][0]] * (adapter.MAX_EVENTS - 2)
    path.write_text(json.dumps(journal))
    before = path.read_bytes()
    with pytest.raises(adapter.Refused, match='audit_capacity_exhausted'):
        adapter.restore(adapter.checkpoint(root), body=host.body(), config=host.config)
    assert path.read_bytes() == before


def test_unknown_workspaces_are_never_evicted_for_capacity(host, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_WORKSPACES', 1)
    old = adapter.STATE / 'transactions' / 'old' / 'state'
    old.mkdir(parents=True)
    (old / 'evidence').write_bytes(b'retain unknown effects')
    with pytest.raises(adapter.Refused, match='workspace_capacity_exhausted'):
        adapter.restore(None, body=host.body(), config=host.config)
    assert (old / 'evidence').read_bytes() == b'retain unknown effects'


def test_effect_unknown_from_shared_facade_is_not_checkpointed(host, monkeypatch):
    result = {'isError': True, 'structuredContent': {'schema': 'ncp.control-response.v1',
              'status': 'refused', 'data': None, 'error': {'code': 'outcome_unknown'}}}
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge(result))
    async def run():
        reply = await host.request('call', {'name': 'ncp_domain_run', 'arguments': {'domain': 'sequence', 'example': True}, 'confirmed': True})
        assert reply.status_code == 503
        assert 'state' not in reply.json()
        assert adapter.LOCAL_FENCES
    asyncio.run(run())


def test_timeout_retains_actor_until_effects_are_resolved_without_ack(host, monkeypatch):
    monkeypatch.setattr(adapter, 'TRANSACTION_SECONDS', .02)
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        bridge = mock_bridge()
        async def waiting(*_):
            entered.set()
            await finish.wait()
            return []
        bridge.request.side_effect = waiting
        monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
        reply = await host.request()
        assert entered.is_set()
        assert reply.status_code == 503 and 'state' not in reply.json()
        assert not bridge.close.called
        assert adapter.ACTIVE
        finish.set()
        await asyncio.gather(*tuple(adapter.ACTIVE), return_exceptions=True)
        assert bridge.close.called
        assert adapter.LOCAL_FENCES
    asyncio.run(run())


def test_http_disconnect_is_an_unknown_outcome_without_cancelling_provider(host, monkeypatch):
    from starlette.requests import Request
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        bridge = mock_bridge()
        async def waiting(*_):
            entered.set()
            await finish.wait()
            return []
        bridge.request.side_effect = waiting
        monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
        messages = asyncio.Queue()
        messages.put_nowait({'type': 'http.request', 'body': adapter.encoded(host.body()), 'more_body': False})
        request = Request({'type': 'http', 'method': 'POST', 'path': '/transaction',
                           'headers': [(b'content-type', b'application/json')]}, messages.get)
        handling = asyncio.create_task(adapter.transact(request))
        await entered.wait()
        messages.put_nowait({'type': 'http.disconnect'})
        reply = await handling
        assert reply.status_code == 503
        assert json.loads(reply.body) == {'error': {'code': 'outcome_unknown'}}
        assert not bridge.close.called
        finish.set()
        await asyncio.gather(*tuple(adapter.ACTIVE), return_exceptions=True)
        assert adapter.LOCAL_FENCES
    asyncio.run(run())


def test_returned_state_survives_worker_publication_failure_until_exact_ack(host, monkeypatch):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    async def run():
        body = host.body()
        async with host.client() as client:
            first = await client.post('/transaction', json=body)
        assert first.status_code == 200
        value = first.json()
        root = adapter.workspace_path(body)
        receipt = adapter.returned_path(body['transaction']['id'])
        assert root.exists() and receipt.exists()
        assert not adapter.LOCAL_FENCES
        # No subsequent request means no durable Worker acknowledgement: a
        # publication failure cannot make this local evidence disappear.
        assert (root / 'deployment-generation.json').exists()
        async with host.client() as client:
            health = await client.get('/health')
        assert health.json()['ready']
        host.state, host.base_commit = value['state'], body['transaction']['id']
        next_body = host.body()
        with pytest.raises(adapter.Refused, match='acknowledgement'):
            adapter.acknowledge_returned({**next_body, 'state': value['state'] + 'AAAA'}, host.config)
        assert root.exists() and receipt.exists()
        second = await host.request()
        assert second.status_code == 200
        assert not root.exists() and not receipt.exists()
        assert len(list((adapter.STATE / 'returned').iterdir())) == 1
    asyncio.run(run())


def test_acknowledged_cleanup_failure_preserves_receipt_and_starts_no_operation(host, monkeypatch):
    bridge = mock_bridge()
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
    async def run():
        first = await host.request()
        assert first.status_code == 200
        prior = host.base_commit
        root = adapter.STATE / 'transactions' / prior
        receipt = adapter.returned_path(prior)
        real_remove = adapter.shutil.rmtree
        def fail_after_state(path, *args, **kwargs):
            if path == root:
                real_remove(root / 'state')
                raise OSError('synthetic parent-directory cleanup failure')
            return real_remove(path, *args, **kwargs)
        monkeypatch.setattr(adapter.shutil, 'rmtree', fail_after_state)
        bridge.reset_mock()
        second = await host.request()
        assert second.status_code == 503
        assert second.json() == {'error': {'code': 'working_state_refused'}}
        assert not bridge.open.called
        assert receipt.exists()
        assert not adapter.LOCAL_FENCES
        assert not (root / 'state').exists()
    asyncio.run(run())


class TestInstalledMCP:
    """Each test starts real isolated installed principal-MCP subprocesses."""

    def test_all_seven_operations_reopen_and_principal_isolation(self, host):
        import ciw
        assert Path(ciw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        async def run():
            template = await host.call('ncp_plan_template', {'baseline': 'baseline'})
            plan = await host.call('ncp_plan_create', {'source': 'source', 'baseline': 'baseline', 'graph': template['graph']})
            digest = {'plan_id': plan['plan_id'], 'expected_digest': plan['digest']}
            await host.call('ncp_plan_admit', digest)
            done = await host.call('ncp_run_submit', {**digest, 'attempt': 'synthetic-adapter-acceptance'})
            assert done['state'] == 'completed'
            assert len(done['result']['execution_ids']) == 7
            assert await host.call('ncp_run_get', {'run_id': done['run_id']}) == done
            inventory = await host.request('runs')
            assert inventory.json()['result']['data']['items'][0]['run_id'] == done['run_id']
            denied = await host.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': done['run_id']}}, principal='beta')
            assert denied.status_code == 200
            assert denied.json()['result']['data']['isError']
            empty = await host.request('runs', principal='beta')
            assert empty.json()['result']['data']['items'] == []
            assert await host.call('ncp_run_get', {'run_id': done['run_id']}) == done
            assert len(list((adapter.STATE / 'transactions').iterdir())) == 1
            assert adapter.returned_path(host.base_commit).exists()
        asyncio.run(run())

    def test_policy_revocation_and_reduced_grants_recheck_before_restore(self, host):
        async def run():
            first = await host.request()
            assert first.status_code == 200, first.text
            old_context = host.policy.context('alpha')
            host.policy_data['revision'] = 'fixture-2'
            host.policy_data['principals'][0]['operations'] = []
            host.write_policy()
            body = host.body('call', {'name': 'ncp_capabilities_list', 'arguments': {}})
            async with host.client() as client:
                reduced = await client.post('/transaction', json=body)
                body['identity'] = old_context
                stale = await client.post('/transaction', json=body)
            assert reduced.status_code == stale.status_code == 403
            host.policy_data['principals'][0]['enabled'] = False
            host.write_policy()
            async with host.client() as client:
                revoked = await client.post('/transaction', json=body)
            assert revoked.status_code == 403
            assert len(list((adapter.STATE / 'transactions').iterdir())) == 1
        asyncio.run(run())

    def test_failed_worker_stays_alive_and_locked_while_new_restore_is_quarantined(self, host, monkeypatch):
        from ciw.agent_recovery import RecoveryStore
        real_bridge = adapter.PrincipalBridge
        primary = adapter.ConsoleError('invalid_server_response', 'synthetic primary fault')
        cleanup = RuntimeError('synthetic cleanup fault')
        alive = []

        class FaultAfterRealMCP(real_bridge):
            async def _dispatch(self, action, payload):
                await super()._dispatch(action, payload)
                raise primary

            async def close(self):
                alive.append(self)
                raise cleanup

        async def run():
            first = await host.request()
            assert first.status_code == 200, first.text
            template = await host.call('ncp_plan_template', {'baseline': 'baseline'})
            body = host.body()
            monkeypatch.setattr(adapter, 'PrincipalBridge', FaultAfterRealMCP)
            try:
                with pytest.raises(adapter.Uncertain) as failure:
                    await adapter.execute(body, host.config, 'alpha', asyncio.Event())
                assert failure.value.primary.code == 'invalid_server_response'
                assert failure.value.cleanup is cleanup
                assert alive and not alive[0].task.done()
                old = adapter.workspace_path(body)
                old_partition = adapter.principal_path(old, body['identity'])
                with pytest.raises(ValueError, match='active writer'):
                    RecoveryStore(old_partition / 'profile/agent-output/control-state', resume=True)
                # Restore uses a new epoch/new path. An old still-live child
                # cannot reach that new directory through its bound profile.
                new_body = host.body()
                new_body['transaction']['epoch'] = 2
                adapter.restore(host.state, body=new_body, config=host.config)
                new = adapter.workspace_path(new_body)
                before = {p.relative_to(new).as_posix(): sha256(p.read_bytes()).hexdigest()
                          for p in new.rglob('*') if p.is_file()}
                old_before = {p.relative_to(old).as_posix(): sha256(p.read_bytes()).hexdigest()
                              for p in old.rglob('*') if p.is_file()}
                assert old != new
                with pytest.raises(adapter.Refused, match='reuse'):
                    adapter.restore(host.state, body=body, config=host.config)
                # The old child performs an actual retained-state write after
                # the new restore. Its response fault preserves uncertainty,
                # and its fixed old pathname cannot modify the new snapshot.
                with pytest.raises(adapter.ConsoleError):
                    await alive[0].request('call', {'name': 'ncp_plan_create', 'confirmed': True,
                        'arguments': {'source': 'source', 'baseline': 'baseline', 'graph': template['graph']}})
                after = {p.relative_to(new).as_posix(): sha256(p.read_bytes()).hexdigest()
                         for p in new.rglob('*') if p.is_file()}
                old_after = {p.relative_to(old).as_posix(): sha256(p.read_bytes()).hexdigest()
                             for p in old.rglob('*') if p.is_file()}
                assert after == before
                assert old_after != old_before
                with pytest.raises(ValueError, match='active writer'):
                    RecoveryStore(old_partition / 'profile/agent-output/control-state', resume=True)
            finally:
                # Release the actual child only after both lock/quarantine
                # assertions. This cleanup uses the real inherited method.
                for bridge in alive:
                    await real_bridge.close(bridge)
        asyncio.run(run())
