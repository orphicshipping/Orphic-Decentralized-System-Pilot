"""Original-byte continuity and fail-closed recovery; synthetic instruments only."""
from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from ciw import link_contract as c, link_gateway as gateway, link_protocol as p
from ciw import link_inspection_cli as ui, link_study_io as study_io
from ciw.link_profiles import example_manifest, example_observations
from ciw.link_runtime import DeviceRuntime
from ciw.link_store import Journal
from ciw.link_study_cli import example

KEY = b'synthetic-test-key-do-not-deploy!!'


def wire_sequence(*, key=None):
    device = example_manifest('GaugeLink')
    values = example_observations(device)
    wires = [p.encode('manifest', c.bounded(device), key=key)]
    wires.extend(p.encode('observation', c.bounded(value), key=key) for value in values[:2])
    return device, wires


def damaged(wire, kind):
    value = bytearray(wire)
    if kind == 'crc':
        value[p.HEADER.size + 4] ^= 1
    elif kind == 'header':
        value[0] ^= 1
    elif kind == 'mac':
        value[-1] ^= 1
    elif kind == 'truncated':
        del value[-3:]
    else:
        raise AssertionError(kind)
    return bytes(value)


@pytest.mark.parametrize('kind', ['crc', 'header', 'truncated', 'mac'])
@pytest.mark.parametrize('split', ['all', 'frames', 'header', 'one_before', 'middle', 'one_byte', 'chunk_97'])
def test_bad_tail_never_discards_verified_prefix(tmp_path, kind, split):
    device, wires = wire_sequence(key=KEY)
    invalid = damaged(wires[2], kind)
    raw = wires[0] + wires[1] + invalid
    cuts = {'all': [raw], 'frames': [wires[0], wires[1], invalid],
            'header': [raw[:p.HEADER.size], raw[p.HEADER.size:]],
            'one_before': [raw[:len(wires[0])-1], raw[len(wires[0])-1:]],
            'middle': [raw[:len(raw)//2], raw[len(raw)//2:]],
            'one_byte': [raw[i:i+1] for i in range(len(raw))],
            'chunk_97': [raw[i:i+97] for i in range(0, len(raw), 97)]}
    with Journal(tmp_path/'ingress.sqlite', create=True) as store:
        with pytest.raises(ValueError):
            gateway.receive(cuts[split], store, expected_manifest=device, key=KEY, require_auth=True)
        assert [r['wire'] for r in store.rows()] == wires[:2]
        assert store.db.execute('SELECT count(*) FROM acks').fetchone()[0] == 0
        for row in store.rows():
            assert row['authentication_at_capture'] == 'shared_key_possession_verified'
    with Journal(tmp_path/'ingress.sqlite') as reopened:
        assert [r['wire'] for r in reopened.rows()] == wires[:2]
        cap = gateway.capture(reopened, expected_manifest=device, boot_id='synthetic.boot.1')
        result = gateway.validate_capture(cap)
        assert len(result['observations']) == 1
        assert result['source_authentication'] == 'not_established'
        assert result['authentication_recheck'] == 'not_reverified'


@pytest.mark.parametrize('bad_kind', ['wrong_manifest', 'semantic', 'unsigned', 'changed_retry'])
def test_semantic_auth_and_retry_refusals_preserve_earlier_frames(tmp_path, bad_kind):
    device, wires = wire_sequence(key=KEY)
    if bad_kind == 'wrong_manifest':
        other = deepcopy(device); other['device_id'] = 'not-the-bound-device'
        bad = p.encode('manifest', c.bounded(other), key=KEY)
    elif bad_kind == 'semantic':
        bad = p.encode('observation', b'{"schema":"unrecognized"}', key=KEY)
    elif bad_kind == 'unsigned':
        bad = p.encode('observation', c.bounded(example_observations(device)[1]))
    else:
        observation = example_observations(device)[0]
        observation['context']['part_id'] = 'different.part'
        bad = p.encode('observation', c.bounded(observation), key=KEY)
    with Journal(tmp_path/'j', create=True) as store:
        with pytest.raises(ValueError):
            gateway.receive([wires[0]+wires[1]+bad], store, expected_manifest=device, key=KEY, require_auth=True)
        assert [r['wire'] for r in store.rows()] == wires[:2]


@pytest.mark.parametrize('stop_after', [1, 2])
def test_buffer_full_preserves_only_the_committed_prefix(tmp_path, stop_after):
    device, wires = wire_sequence()
    with Journal(tmp_path/'j', create=True, capacity_messages=stop_after) as store:
        with pytest.raises(ValueError, match='buffer full'):
            gateway.receive([b''.join(wires)], store, expected_manifest=device)
        assert [r['wire'] for r in store.rows()] == wires[:stop_after]
        assert store.db.execute('SELECT count(*) FROM acks').fetchone()[0] == 0


@pytest.mark.parametrize('mode', ['rows', 'capture', 'runtime'])
@pytest.mark.parametrize('field,value', [('manifest_ref', 'sha256:'+'0'*64),
                                          ('message_key', 'observation:changed-index'),
                                          ('authentication', 'shared_key_possession_verified')])
def test_corrupt_index_cannot_hide_or_reidentify_an_observation(tmp_path, mode, field, value):
    device = example_manifest('GaugeLink')
    with Journal(tmp_path/'j', create=True) as store:
        runtime = DeviceRuntime(device, store, boot_id='same.boot')
        runtime.emit('CH0', b'24.982 mm\n')
        runtime.emit('CH0', b'24.983 mm\n')
        original = store.db.execute('SELECT wire FROM messages ORDER BY ordinal').fetchall()
        key = store.rows()[-1]['message_key']
        # Deliberate local index corruption. Never relabel or repair these bytes.
        store.db.execute(f'UPDATE messages SET {field}=? WHERE message_key=?', (value,key))
        with pytest.raises(ValueError):
            if mode == 'rows':
                store.rows()
            elif mode == 'capture':
                gateway.capture(store, expected_manifest=device, boot_id=runtime.boot_id)
            else:
                DeviceRuntime(device, store, boot_id=runtime.boot_id)
        assert store.db.execute('SELECT wire FROM messages ORDER BY ordinal').fetchall() == original


def test_valid_multiple_manifests_remain_selectable(tmp_path):
    device = example_manifest('GaugeLink')
    other = example_manifest('ThermalLink')
    with Journal(tmp_path/'j', create=True) as store:
        first = DeviceRuntime(device, store, boot_id='gauge')
        first.emit('CH0', b'24.982 mm\n')
        # No decode assumption about the second profile is needed by the journal.
        second = DeviceRuntime(other, store, boot_id='thermal')
        second.emit(other['channels'][0]['id'], b'300 K\n')
        assert len(store.rows()) == 4
        cap = gateway.capture(store, expected_manifest=device, boot_id='gauge')
        assert len(cap['frames']) == 1
        assert cap['boot_id'] == 'gauge'


@pytest.mark.parametrize('reopen', [False, True])
def test_orphan_acknowledgement_refuses_even_when_sqlite_integrity_passes(tmp_path, reopen):
    path = tmp_path/'j'
    with Journal(path, create=True) as store:
        store.db.execute('PRAGMA foreign_keys=OFF')
        store.db.execute('INSERT INTO acks VALUES (?,?,?,?)', ('absent', 'receiver', 'sha256:'+'0'*64, b'{}'))
        assert store.db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert store.db.execute('PRAGMA foreign_key_check').fetchall()
        if not reopen:
            with pytest.raises(ValueError, match='foreign-key'):
                store.rows()
    if reopen:
        with pytest.raises(ValueError, match='foreign-key'):
            Journal(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute('SELECT count(*) FROM acks').fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize('damage', ['missing_manifest', 'reordered_manifest'])
def test_observation_requires_its_original_prior_manifest(tmp_path, damage):
    device, wires = wire_sequence()
    with Journal(tmp_path/'j', create=True) as store:
        gateway.receive(wires, store, expected_manifest=device)
        if damage == 'missing_manifest':
            store.db.execute("DELETE FROM messages WHERE message_key LIKE 'manifest:%'")
        else:
            store.db.execute("UPDATE messages SET ordinal=99 WHERE message_key LIKE 'manifest:%'")
        with pytest.raises(ValueError):
            store.rows()


@pytest.mark.parametrize('origin', ['synthetic', 'recorded'])
def test_failed_decoding_does_not_erase_declared_source_origin(tmp_path, origin):
    request = example()
    device = request['capture']['manifest']
    from ciw.link_store import decode_bound
    for index, item in enumerate(request['capture']['frames']):
        frame, record, _, _ = decode_bound(c.unpack(item['wire'], p.MAX_FRAME), device, offline=True)
        if frame.kind == 'observation':
            record['origin'] = origin
            if index == 0:
                record['raw'] = c.packed(b'not-a-number N\r\n')
            item['wire'] = c.packed(p.encode(frame.kind, c.bounded(record)), p.MAX_FRAME)
    request['calibration']['joint_covariance'] = None
    root = tmp_path/'preview'
    result = ui.run(request, root)
    assert result['status'] == 'INCOMPLETE'
    assert result['origin'] == origin
    assert result['qualification'] == 'HELD'
    assert result['reading']['value'] is None
    assert result['checks']['arithmetic'] == 'NOT_RUN'
    assert origin.upper() in (root/'inspection.html').read_text()
    assert json.loads((root/'request.json').read_bytes()) == request
    assert study_io.inspect(root)['origin'] == origin


@pytest.mark.parametrize('exception_type', [ValueError, RuntimeError, KeyboardInterrupt])
def test_sink_failure_poisoning_never_processes_later_frames(exception_type):
    _, wires = wire_sequence()
    accepted = []
    decoder = p.Decoder()
    def sink(frame):
        accepted.append(frame.wire)
        raise exception_type('explicit local commit failure')
    with pytest.raises(exception_type):
        decoder.feed(b''.join(wires), on_frame=sink)
    assert accepted == wires[:1]
    assert decoder.failed and not decoder.buffer
    with pytest.raises(ValueError):
        decoder.feed(wires[1], on_frame=lambda _: pytest.fail('poisoned decoder reused'))
    with pytest.raises(ValueError):
        decoder.finish()


def test_callback_observes_verified_wire_before_a_later_crc_refusal():
    _, wires = wire_sequence(key=KEY)
    decoder = p.Decoder(key=KEY, require_auth=True)
    accepted = []
    with pytest.raises(ValueError, match='CRC'):
        decoder.feed(wires[0] + damaged(wires[1], 'crc'), on_frame=accepted.append)
    assert len(accepted) == 1 and accepted[0].wire == wires[0]
    assert accepted[0].authentication == 'shared_key_possession_verified'


def test_existing_batch_api_remains_identical_on_valid_frames():
    _, wires = wire_sequence(key=KEY)
    first, second = p.Decoder(key=KEY), p.Decoder(key=KEY)
    expected = first.feed(b''.join(wires))
    accepted = []
    actual = second.feed(b''.join(wires), on_frame=accepted.append)
    first.finish(); second.finish()
    assert expected == actual == accepted
    assert [frame.wire for frame in expected] == wires


@pytest.mark.parametrize('callback', [True, 1, 'store', {}])
def test_wire_cannot_supply_a_callback(callback):
    _, wires = wire_sequence()
    decoder = p.Decoder()
    with pytest.raises(ValueError, match='callable'):
        decoder.feed(wires[0], on_frame=callback)
    assert decoder.failed and not decoder.buffer


def test_journal_rows_are_one_snapshot_across_a_concurrent_writer(tmp_path, monkeypatch):
    device = example_manifest('GaugeLink')
    path = tmp_path/'j'
    with Journal(path, create=True) as store:
        DeviceRuntime(device, store, boot_id='boot').emit('CH0', b'24.982 mm\n')
        with Journal(path) as other:
            writer = DeviceRuntime(device, other, boot_id='boot')
            original = store._bounded_rows
            def concurrent_commit():
                count_and_size = original()
                writer.emit('CH0', b'24.983 mm\n')
                return count_and_size
            monkeypatch.setattr(store, '_bounded_rows', concurrent_commit)
            snapshot = store.rows()
            assert len(snapshot) == 2  # Manifest + first sample, not a mixed snapshot.
            assert not store.db.in_transaction
            monkeypatch.setattr(store, '_bounded_rows', original)
            assert len(store.rows()) == 3


def test_snapshot_does_not_end_a_callers_existing_transaction(tmp_path):
    device = example_manifest('GaugeLink')
    with Journal(tmp_path/'j', create=True) as store:
        DeviceRuntime(device, store, boot_id='boot').emit('CH0', b'24.982 mm\n')
        with pytest.raises(RuntimeError, match='intentional rollback'):
            with store.transaction():
                store.db.execute('INSERT INTO state VALUES(?,?,?)', ('test.local.state',1,b'{}'))
                assert len(store.rows()) == 2
                assert store.db.in_transaction
                raise RuntimeError('intentional rollback')
        assert store.state('test.local.state') is None
        assert not store.db.in_transaction


def test_failed_snapshot_releases_only_its_owned_read_transaction(tmp_path):
    device = example_manifest('GaugeLink')
    with Journal(tmp_path/'j', create=True) as store:
        runtime = DeviceRuntime(device, store, boot_id='boot')
        runtime.emit('CH0', b'24.982 mm\n')
        store.db.execute("UPDATE messages SET message_key='bad' WHERE message_key LIKE 'observation:%'")
        with pytest.raises(ValueError):
            store.rows()
        assert not store.db.in_transaction
        with store.transaction():
            with pytest.raises(ValueError):
                store.rows()
            assert store.db.in_transaction


def test_journal_prefix_can_resume_only_by_explicit_retry_after_refusal(tmp_path):
    device, wires = wire_sequence(key=KEY)
    path = tmp_path/'j'
    with Journal(path, create=True) as store:
        with pytest.raises(ValueError):
            gateway.receive([wires[0]+wires[1]+damaged(wires[2], 'mac')], store,
                            expected_manifest=device, key=KEY, require_auth=True)
        original = store.rows()
    # New explicit intake retries source bytes; it is not a hidden reconnect.
    with Journal(path) as store:
        gateway.receive(wires, store, expected_manifest=device, key=KEY, require_auth=True)
        assert store.rows()[:2] == original
        assert [r['wire'] for r in store.rows()] == wires
        assert len(store.rows()) == 3


def test_an_invalid_first_frame_never_creates_a_successful_observation(tmp_path):
    device, wires = wire_sequence()
    with Journal(tmp_path/'j', create=True) as store:
        with pytest.raises(ValueError):
            gateway.receive([damaged(wires[0], 'header')+wires[1]], store, expected_manifest=device)
        assert store.rows() == []


def test_corrupt_delivery_retry_reaches_same_existing_scalar_graph(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from ciw import link_delivery as delivery, link_study as study
    device = example_manifest('GaugeLink')
    device['channels'][0].update(quantity='force', unit='N', encoding='opaque.v1')
    device['clock'].update(id='link.host.monotonic_ns', tick_hz=1_000_000_000)
    with Journal(tmp_path/'edge', create=True) as edge, Journal(tmp_path/'net', create=True) as receiver:
        runtime = DeviceRuntime(device, edge, boot_id='synthetic.recovery', key=KEY)
        for index, raw in enumerate([b'1.000 N\r\n', b'2.000 N\r\n', b'3.000 N\r\n']):
            runtime.emit('CH0', raw, tick=1000+index*100)
        original = edge.rows()
        broken_transport = b''.join(r['wire'] for r in original[:-1])+damaged(original[-1]['wire'], 'crc')
        with pytest.raises(ValueError, match='CRC'):
            gateway.receive([broken_transport], receiver, expected_manifest=device, key=KEY, require_auth=True)
        prefix = receiver.rows()
        assert [r['wire'] for r in prefix] == [r['wire'] for r in original[:3]]
        assert not edge.db.execute('SELECT 1 FROM acks').fetchall()
        # Explicit host retry uses the original source, not reconstructed readings.
        node_key = Ed25519PrivateKey.generate()
        public = node_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        binding = delivery.DeliveryBinding('synthetic.tenant', 'synthetic.node', device['device_id'],
            c.content_identity(device), edge.store_id, receiver.store_id, 'synthetic.epoch',
            public, 999, 2_000_000_000)
        summary = delivery.sync_signed(edge, receiver, device=device, binding=binding,
            signing_key=node_key, now_ns=1_000_000_000, key=KEY)
        assert summary['new_acknowledgements'] == 4
        assert receiver.rows()[:3] == prefix
        assert edge.rows() == original
        cap = gateway.capture(receiver, expected_manifest=device, boot_id=runtime.boot_id)
    request = {'schema': study.SCHEMA, 'capture': cap, 'channel_id': 'CH0',
        'calibration': {'gain':1.0, 'offset':0.0, 'input_range':[-10,10],
                        'declaration':None, 'joint_covariance':None},
        'model': {'reference_kind':'declared_exact', 'expected_mean':2.0, 'residual_limit':0.01}}
    out = tmp_path/'inspection'
    result = ui.run(request, out)
    assert result['reading']['value'] == 2.0 and result['qualification'] == 'HELD'
    assert result['checks']['arithmetic'] == 'PASS'
    retained = study_io.inspect(out)
    assert c.unpack(retained['results'][study.OPS[0]]['data']['stream'], 8192) == b'1.000 N\r\n2.000 N\r\n3.000 N\r\n'
    replay = study_io.replay(out, tmp_path/'replay')
    assert replay['evidence_id'] == retained['evidence_id']
    assert set(replay['executions']).isdisjoint(retained['executions'])
    assert study_io.export(out, tmp_path/'native')['status'] == 'EXPORTED'
