"""Independent live-boundary review: real local protocols and custody faults.

Only the synthetic loopback fixtures write observations. Production intake must
not publish, reconnect after unresolved cleanup, or launch after revocation.
"""
from pathlib import Path
import json
import shutil
import sys

import pytest

import ciw.live_intake as live_module
from ciw.adapters.subprocess import RuntimeCleanupUncertain
from ciw.live_intake import LiveBinding, TOOLS
from ciw.principal_policy import PrincipalPolicy
from test_live_intake import PYTHON, broker, certificate, host, mqtt_binding, opc_server, port, publish


def arguments(source_id='mqtt-source', *, count=1, seconds=1):
    return {'source_id': source_id, 'count': count, 'seconds': seconds}


def test_review_revocation_during_credentials_prevents_real_subscription(tmp_path, monkeypatch):
    current = ['permitted']

    def resolve_credentials(_source):
        # Secret-provider I/O completed after the host revoked this operation.
        current[0] = ''
        return {}

    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number),
                      authorize=lambda _name, _args: current[0],
                      credentials=resolve_credentials)
        result = active.call('ncp_live_read', arguments())
    assert result['status'] == 'refused'
    log = (tmp_path / 'broker.log').read_text()
    assert 'Received SUBSCRIBE' not in log, 'revoked request still reached the real broker'


@pytest.mark.parametrize('wrapping', ['direct', 'cause', 'group'])
def test_review_uncertain_process_cleanup_blocks_subsequent_launch(tmp_path, monkeypatch, wrapping):
    active = host(tmp_path, monkeypatch, mqtt_binding(port()))
    launches = []
    held = RuntimeCleanupUncertain()

    def unresolved_launch(*_args, **_kwargs):
        launches.append(True)
        if wrapping == 'direct':
            raise held
        if wrapping == 'group':
            raise ExceptionGroup('retained cleanup diagnostics', [held, OSError('cleanup')])
        raise OSError('temporary-directory cleanup') from held

    monkeypatch.setattr(live_module, '_bounded_process', unresolved_launch)
    for _ in range(2):
        result = active.call('ncp_live_read', arguments())
        assert result['status'] == 'refused'
        assert result['error']['code'] == 'outcome_unknown'
    assert len(launches) == 1, 'unresolved process custody must impose a persistent launch hold'


def test_review_package_startup_source_drift_refuses_before_execution(tmp_path, monkeypatch):
    # A small isolated copy reproduces package startup without modifying shared
    # source or the installed SDK. The trusted launcher initially binds its bytes.
    original = Path(live_module.__file__).resolve().parent
    isolated = tmp_path / 'source' / 'ciw'
    isolated.mkdir(parents=True)
    for name in ('__init__.py', 'live_intake.py', 'live_intake_worker.py', 'offline_intake.py'):
        shutil.copyfile(original / name, isolated / name)
    monkeypatch.setattr(live_module, '__file__', str(isolated / 'live_intake.py'))
    active = host(tmp_path, monkeypatch, mqtt_binding(port()))
    marker = tmp_path / 'changed-startup-executed'
    (isolated / '__init__.py').write_text(
        f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    result = active.call('ncp_live_read', arguments())
    assert result['status'] == 'refused'
    assert not marker.exists(), 'unpinned package startup executed in the protocol worker'


def test_review_sdk_drift_during_credentials_refuses_before_import(tmp_path, monkeypatch):
    extra = tmp_path / 'site-packages'
    package = extra / 'paho'
    package.mkdir(parents=True)
    startup = package / '__init__.py'
    startup.write_text('# initially bound fixture package\n')
    marker = tmp_path / 'changed-sdk-imported'
    real = PYTHON.parent.parent / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'

    def resolve_credentials(_source):
        # Simulate a dependency update that completed while credential I/O ran.
        startup.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        return {}

    active = host(tmp_path, monkeypatch, mqtt_binding(port()),
                  import_roots=(extra, real), credentials=resolve_credentials)
    result = active.call('ncp_live_read', arguments())
    assert result['status'] == 'refused'
    assert not marker.exists(), 'SDK drift during credential lookup executed before post-check'


def test_review_canonical_grant_revoked_during_credentials_prevents_subscription(tmp_path, monkeypatch):
    grant = {'principal': 'alice', 'enabled': True,
             'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
             'scientific_operations': [], 'resources': [], 'reference_scopes': [],
             'handoff_recipients': []}
    value = {'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]}

    def resolve_credentials(_source):
        # The live-source authority and canonical operation grant are distinct.
        # Source authorization staying valid cannot restore a revoked MCP grant.
        grant['operations'] = []
        value['revision'] = 'canonical-grant-revoked'
        return {}

    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number), credentials=resolve_credentials)
        facade = PrincipalPolicy(lambda: value).facade('alice', live=active)
        result = facade.call('ncp_live_read', arguments())
    assert result['status'] == 'refused'
    assert 'Received SUBSCRIBE' not in (tmp_path / 'broker.log').read_text()


def test_review_actual_mqtt_partial_receipt_and_no_client_publish(tmp_path, monkeypatch):
    with broker(tmp_path) as number:
        publish(number)
        before = (tmp_path / 'broker.log').read_text()
        active = host(tmp_path, monkeypatch, mqtt_binding(number))
        result = active.call('ncp_live_read', arguments(count=2, seconds=.3))
        assert result['status'] == 'ok', result
        receipt = active.store.native(result['data']['artifact_id'])
        assert result['data']['record_count'] == 1
        assert receipt['status'] == 'deadline_partial'
        assert receipt['device_writes_performed'] is False
        assert receipt['physical_validity_established'] is False
        metadata = json.dumps(active.call('ncp_live_sources', {}))
        assert '127.0.0.1' not in metadata and 'observations/test' not in metadata
    log = (tmp_path / 'broker.log').read_text()
    assert log.count('Received SUBSCRIBE') == 1
    assert log.count('Received PUBLISH') == before.count('Received PUBLISH')


def test_review_actual_opcua_requested_deadline_retains_completed_reads(tmp_path, monkeypatch):
    with opc_server(tmp_path) as (endpoint, node_id):
        binding = LiveBinding('opc-source', 'opcua.scalar.v1', 'local_fixture', {
            'endpoint': endpoint, 'node_id': node_id, 'unit': 'degC', 'datatype': 'Double',
            'application_uri': 'urn:odsp:test', 'secure': False, 'server_certificate': None,
            'interval_seconds': 1,
        }, '2.1.0', 'test-1')
        active = host(tmp_path, monkeypatch, binding)
        result = active.call('ncp_live_read', arguments('opc-source', count=3, seconds=.8))
    assert result['status'] == 'ok', 'a normal requested deadline lost completed observations'
    receipt = active.store.native(result['data']['artifact_id'])
    assert receipt['status'] == 'deadline_partial'
    assert 0 < result['data']['record_count'] < 3
    assert all(row['interpretation']['value'] == 21.5 for row in receipt['records'])


def test_review_retained_read_rechecks_original_source_grant(tmp_path, monkeypatch):
    # The locator read grant does not itself restore revoked source access.
    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number))
        grant = {'principal': 'alice', 'enabled': True,
                 'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
                 'scientific_operations': [], 'resources': [], 'reference_scopes': [],
                 'handoff_recipients': []}
        value = {'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]}
        facade = PrincipalPolicy(lambda: value).facade('alice', live=active)
        result = facade.call('ncp_live_read', arguments())
        assert result['status'] == 'ok', result
        locator = result['data']['artifact_id']
        grant['operations'] = [row for row in grant['operations'] if row['name'] != 'ncp_live_read']
        value['revision'] = 'source-access-revoked'
        denied = facade.call('ncp_live_receipt', {'artifact_id': locator})
        assert denied['status'] == 'refused', 'retained bytes bypassed the revoked source grant'


def test_review_source_authority_revoked_during_receipt_page_withholds_bytes(tmp_path, monkeypatch):
    source_allowed = [True]

    def authorize(name, _args):
        if name == 'ncp_live_read':
            return 'source-epoch' if source_allowed[0] else ''
        return 'retained-page-epoch'

    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number), authorize=authorize)
        grant = {'principal': 'alice', 'enabled': True,
                 'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
                 'scientific_operations': [], 'resources': [], 'reference_scopes': [],
                 'handoff_recipients': []}
        policy = PrincipalPolicy(lambda: {
            'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]})
        facade = policy.facade('alice', live=active)
        result = facade.call('ncp_live_read', arguments())
        assert result['status'] == 'ok', result
    actual_page = active.store.page

    def page_during_revocation(*args, **kwargs):
        value = actual_page(*args, **kwargs)
        source_allowed[0] = False
        return value

    monkeypatch.setattr(active.store, 'page', page_during_revocation)
    result = facade.call('ncp_live_receipt', {'artifact_id': result['data']['artifact_id']})
    assert result['status'] == 'refused', 'page I/O hid a newly revoked source authority'
    assert result['data'] is None


def test_review_page_reread_cannot_substitute_another_valid_receipt(tmp_path, monkeypatch):
    # Both receipts are valid retained records for the same source and owner.
    # Page I/O must not substitute the second occurrence after the first record
    # has passed its own digest and authorization checks.
    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number))
        first = active.call('ncp_live_read', arguments())
        second = active.call('ncp_live_read', arguments())
        assert first['status'] == second['status'] == 'ok'
    original = active.store.page
    first_id, second_id = (result['data']['artifact_id'] for result in (first, second))

    def substituted_page(artifact_id, offset, limit):
        assert artifact_id == first_id
        page = original(second_id, offset, limit)
        page['artifact_id'] = first_id
        return page

    monkeypatch.setattr(active.store, 'page', substituted_page)
    result = active.call('ncp_live_receipt', {'artifact_id': first_id})
    assert result['status'] == 'refused' and result['data'] is None


@pytest.mark.parametrize('revocation', ['source_authority', 'canonical_source_grant'])
def test_review_outer_terminal_audit_revocation_withholds_live_receipt(tmp_path, monkeypatch, revocation):
    from test_mcp_toolbox import audited, local_client
    source_allowed = [True]

    def authorize(name, _args):
        if name == 'ncp_live_read':
            return 'source-epoch' if source_allowed[0] else ''
        return 'retained-page-epoch'

    grant = {'principal': 'alice', 'enabled': True,
             'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
             'scientific_operations': [], 'resources': [], 'reference_scopes': [],
             'handoff_recipients': []}
    value = {'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]}
    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number), authorize=authorize)
        facade = PrincipalPolicy(lambda: value).facade('alice', live=active)
        first = facade.call('ncp_live_read', arguments())
        assert first['status'] == 'ok', first
    outer, anchor = audited(facade, tmp_path, 'live-outer-review', TOOLS)
    original = outer._audit.compare_and_set_anchor

    def terminal(before, after):
        committed = original(before, after)
        if after['sequence'] == 2:
            if revocation == 'source_authority':
                source_allowed[0] = False
            else:
                grant['operations'] = [row for row in grant['operations'] if row['name'] != 'ncp_live_read']
                value['revision'] = 'source-access-revoked'
        return committed

    monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', terminal)
    result = local_client(outer).call('ncp_live_receipt', {'artifact_id': first['data']['artifact_id']})
    assert result['status'] == 'refused' and result['data'] is None, result
    # The committed terminal record remains evidence of the completed read.
    assert anchor.read()['sequence'] == 2
    assert outer._audit.chain.verify(anchor.read())[-1]['event']['phase'] == 'succeeded'


@pytest.mark.parametrize('connack', [False, True])
def test_review_unacknowledged_mqtt_handshake_cannot_complete_observation_window(tmp_path, monkeypatch, connack):
    import socket
    from threading import Event, Thread
    # A real TCP peer accepts the client's CONNECT but never completes the
    # requested MQTT subscription. TCP success alone is no MQTT read session.
    stop = Event()
    packets, failures = [], []
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(15)
    number = listener.getsockname()[1]

    def unacknowledged_peer():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(.1)
                first = True
                while not stop.is_set():
                    try:
                        raw = connection.recv(8192)
                    except TimeoutError:
                        continue
                    if not raw:
                        break
                    packets.append(raw)
                    if first and connack:
                        # Successful MQTTv5 CONNACK, deliberately no SUBACK.
                        connection.sendall(b'\x20\x03\x00\x00\x00')
                    first = False
        except Exception as exc:
            failures.append(type(exc).__name__)

    thread = Thread(target=unacknowledged_peer, daemon=True)
    thread.start()
    try:
        active = host(tmp_path, monkeypatch, mqtt_binding(number))
        result = active.call('ncp_live_read', arguments(seconds=.2))
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=5)
    assert not thread.is_alive() and not failures
    assert packets and packets[0][0] >> 4 == 1, packets
    if connack:
        assert any(packet[0] >> 4 == 8 for packet in packets), packets
    assert result['status'] == 'refused' and result['data'] is None, result


@pytest.mark.parametrize('protocol', ['mqtt', 'opcua'])
def test_review_actual_secure_channel_rejects_wrong_server_certificate(tmp_path, monkeypatch, protocol):
    server_root, client_root = tmp_path / 'server', tmp_path / 'client'
    server_root.mkdir()
    client_root.mkdir()
    server_tls, client_tls = certificate(server_root), certificate(client_root)
    if protocol == 'mqtt':
        with broker(server_root, tls=server_tls) as number:
            active = host(tmp_path, monkeypatch, mqtt_binding(number, tls=True, ca_file=str(client_tls[0])))
            result = active.call('ncp_live_read', arguments(seconds=2))
        assert 'Received SUBSCRIBE' not in (server_root / 'broker.log').read_text()
    else:
        with opc_server(server_root, tls=server_tls) as (endpoint, node_id):
            binding = LiveBinding('opc-source', 'opcua.scalar.v1', 'local_fixture', {
                'endpoint': endpoint, 'node_id': node_id, 'unit': 'degC', 'datatype': 'Double',
                'application_uri': 'urn:odsp:test', 'secure': True,
                'server_certificate': str(client_tls[0]), 'interval_seconds': 0,
            }, '2.1.0', 'test-1')
            active = host(tmp_path, monkeypatch, binding, credentials=lambda _source: {
                'certificate': str(client_tls[0]), 'private_key': str(client_tls[1])})
            result = active.call('ncp_live_read', arguments('opc-source', seconds=2))
    assert result['status'] == 'refused' and result['data'] is None, result
    assert str(tmp_path) not in json.dumps(result)


def test_review_acknowledged_empty_mqtt_window_retains_no_fabricated_observations(tmp_path, monkeypatch):
    with broker(tmp_path) as number:
        # No fixture publisher seeds the topic. A completed subscription may
        # truthfully observe no messages during the caller's requested window.
        active = host(tmp_path, monkeypatch, mqtt_binding(number))
        result = active.call('ncp_live_read', arguments(count=2, seconds=.3))
    assert result['status'] == 'ok', result
    receipt = active.store.native(result['data']['artifact_id'])
    assert receipt['records'] == [] and receipt['status'] == 'deadline_partial'
    assert result['data']['record_count'] == 0
    assert 'Received SUBSCRIBE' in (tmp_path / 'broker.log').read_text()


@pytest.mark.parametrize('revocation', ['source_authority', 'canonical_source_grant'])
def test_review_outer_terminal_audit_revocation_withholds_new_live_read(tmp_path, monkeypatch, revocation):
    from test_mcp_toolbox import audited, local_client
    source_allowed = [True]
    grant = {'principal': 'alice', 'enabled': True,
             'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
             'scientific_operations': [], 'resources': [], 'reference_scopes': [],
             'handoff_recipients': []}
    value = {'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]}
    with broker(tmp_path) as number:
        publish(number)
        active = host(tmp_path, monkeypatch, mqtt_binding(number),
                      authorize=lambda _name, _args: 'source-epoch' if source_allowed[0] else '')
        facade = PrincipalPolicy(lambda: value).facade('alice', live=active)
        outer, anchor = audited(facade, tmp_path, 'live-read-review', TOOLS)
        original = outer._audit.compare_and_set_anchor

        def terminal(before, after):
            committed = original(before, after)
            if after['sequence'] == 2:
                if revocation == 'source_authority':
                    source_allowed[0] = False
                else:
                    grant['operations'] = []
                    value['revision'] = 'source-access-revoked'
            return committed

        monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', terminal)
        result = local_client(outer).call('ncp_live_read', arguments())
    assert result['status'] == 'refused' and result['data'] is None, result
    assert result['error']['code'] == 'outcome_unknown'
    # A completed connection and retained record are not falsely rolled back.
    assert (tmp_path / 'broker.log').read_text().count('Received SUBSCRIBE') == 1
    assert len(list((tmp_path / 'retained').rglob('retained-*.json'))) == 1
    assert anchor.read()['sequence'] == 2
    assert outer._audit.chain.verify(anchor.read())[-1]['event']['phase'] == 'succeeded'

