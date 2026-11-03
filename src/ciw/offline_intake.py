"""Closed offline observation captures; no device, broker, or control access.

This profile reads host-selected ODSP export envelopes. It does not decode ROS
bags, OPC UA wire messages, or arbitrary MQTT packets. Capture provenance stays
host-declared and unverified; matching bytes never establish physical truth.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import math
import re

from .campaign_contracts import (ContractRefusal, Limits, canonical, closed_object,
                                 integer, mcp_envelope, parse, text)

PROFILE = 'odsp.offline-intake.v1'
CAPTURE_SCHEMA = 'odsp.offline-intake.capture.v1'
RECEIPT_SCHEMA = 'odsp.offline-intake.receipt.v1'
PROTOCOLS = ('ros2.joint-state.v1', 'opcua.scalar.v1', 'mqtt.scalar-observation.v1')
MAX_CAPTURE = 262144
MAX_ARGUMENT = 65536
MAX_RESPONSE = 262144
MAX_RECORDS = 256
MAX_JOINTS = 64
MAX_PAYLOAD = 4096
CAPTURE_LIMITS = Limits(max_bytes=MAX_CAPTURE, max_depth=16, max_nodes=16384)
REQUEST_LIMITS = Limits(max_bytes=MAX_ARGUMENT, max_depth=16, max_nodes=16384)
RESPONSE_LIMITS = Limits(max_bytes=MAX_RESPONSE, max_depth=24, max_nodes=32768)
PAYLOAD_LIMITS = Limits(max_bytes=MAX_PAYLOAD, max_depth=4, max_nodes=32)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_PRINCIPAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z')
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
_TIME = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,9})?Z\Z')
CLAIMS = {'authorizes_execution': False, 'authorizes_state_admission': False,
          'physical_validity_established': False, 'control_authority': False,
          'live_source_read': False}


class IntakeRefusal(ValueError):
    """Data-independent refusal; never echo a captured payload or path."""


def _require(condition, reason='intake_contract_refused'):
    if not condition:
        raise IntakeRefusal(reason)


def byte_digest(raw):
    _require(type(raw) is bytes, 'intake_bytes_required')
    return 'sha256:' + sha256(raw).hexdigest()


def encode(value, *, limits=RESPONSE_LIMITS):
    return canonical(value, limits=limits)


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None, 'invalid_intake_identifier')
    return value


def _instant(value):
    _require(type(value) is str and _TIME.fullmatch(value) is not None, 'invalid_intake_time')
    # Validate calendar/time fields without rounding the retained fractional text.
    try:
        datetime.fromisoformat(value[:19])
    except ValueError:
        raise IntakeRefusal('invalid_intake_time') from None
    return value


def _finite(value):
    _require(type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e150,
             'invalid_intake_number')
    return value


def _source(value):
    closed_object(value, {'source_id', 'revision', 'digest'})
    _identifier(value['source_id'])
    _identifier(value['revision'])
    _require(type(value['digest']) is str and _REF.fullmatch(value['digest']) is not None,
             'invalid_intake_source_digest')
    return deepcopy(value)


def _ros(value):
    closed_object(value, {'stamp', 'clock', 'frame_id', 'name', 'joint_type',
                          'position', 'velocity', 'effort'})
    closed_object(value['stamp'], {'sec', 'nanosec'})
    integer(value['stamp']['sec'], -(2**31), 2**31-1)
    integer(value['stamp']['nanosec'], 0, 999999999)
    closed_object(value['clock'], {'domain', 'clock_id'})
    _require(value['clock']['domain'] in ('simulation', 'source'), 'invalid_intake_clock')
    _identifier(value['clock']['clock_id'])
    text(value['frame_id'], 128, nonempty=False)
    names = value['name']
    _require(type(names) is list and 1 <= len(names) <= MAX_JOINTS, 'intake_joint_limit')
    for name in names:
        _identifier(name)
    _require(len(set(names)) == len(names), 'duplicate_intake_joint')
    kinds = value['joint_type']
    _require(type(kinds) is list and len(kinds) == len(names) and
             all(kind in ('revolute', 'prismatic') for kind in kinds), 'invalid_intake_joint_type')
    for field in ('position', 'velocity', 'effort'):
        values = value[field]
        _require(type(values) is list and len(values) in (0, len(names)), 'intake_joint_length_mismatch')
        for sample in values:
            _finite(sample)
    joints = []
    for index, name in enumerate(names):
        units = ('rad', 'rad/s', 'N.m') if kinds[index] == 'revolute' else ('m', 'm/s', 'N')
        joints.append({'name': name, 'joint_type': kinds[index], **{
            field: {'value': value[field][index] if value[field] else None, 'unit': unit}
            for field, unit in zip(('position', 'velocity', 'effort'), units)}})
    return {'kind': 'joint_state', 'clock': deepcopy(value['clock']),
            'stamp': deepcopy(value['stamp']), 'frame_id': value['frame_id'], 'joints': joints,
            'units_basis': 'host_declared_joint_types', 'clock_synchronization': 'not_established',
            'empty_observation': not any(value[k] for k in ('position', 'velocity', 'effort'))}


def _opcua(value):
    closed_object(value, {'node_id', 'datatype', 'value', 'unit', 'status_code',
                          'source_timestamp', 'server_timestamp'})
    text(value['node_id'], 256)
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value['node_id']), 'invalid_intake_node')
    _require(type(value['datatype']) is str and value['datatype'] in
             ('Boolean', 'Int32', 'UInt32', 'Double', 'String'), 'unsupported_intake_datatype')
    integer(value['status_code'], 0, 2**32-1)
    severity = ('good', 'uncertain', 'bad', 'reserved')[value['status_code'] >> 30]
    for field in ('source_timestamp', 'server_timestamp'):
        if value[field] is not None:
            _instant(value[field])
    text(value['unit'], 32, nonempty=False)
    sample, datatype = value['value'], value['datatype']
    if sample is None:
        _require(severity != 'good', 'intake_good_value_missing')
    elif datatype == 'Boolean':
        _require(type(sample) is bool, 'invalid_intake_boolean')
    elif datatype == 'Int32':
        integer(sample, -(2**31), 2**31-1)
    elif datatype == 'UInt32':
        integer(sample, 0, 2**32-1)
    elif datatype == 'Double':
        _finite(sample)
    else:
        text(sample, 256, nonempty=False)
    # Preserve the raw DataValue in the receipt; do not present a bad/reserved
    # value as an available observation. Source status is still an assertion.
    return {'kind': 'scalar', 'node_id': value['node_id'], 'datatype': datatype,
            'value': deepcopy(sample) if severity in ('good', 'uncertain') else None,
            'unit': value['unit'], 'reported_quality': severity,
            'reported_quality_verified': False,
            'reported_value_available': sample is not None and severity in ('good', 'uncertain'),
            'source_timestamp': value['source_timestamp'], 'server_timestamp': value['server_timestamp'],
            'clock_status': 'source_time_missing' if value['source_timestamp'] is None else 'source_declared'}


def _mqtt(value):
    closed_object(value, {'message_type', 'topic', 'qos', 'retain', 'duplicate', 'payload_b64'})
    _require(value['message_type'] == 'observation', 'intake_control_message_refused')
    topic = text(value['topic'], 256)
    _require(len(topic.encode('utf-8')) <= 256 and not any(c in topic for c in ('\0', '+', '#')),
             'invalid_intake_topic')
    integer(value['qos'], 0, 2)
    _require(type(value['retain']) is bool and type(value['duplicate']) is bool,
             'invalid_intake_delivery_flags')
    encoded = text(value['payload_b64'], 4*((MAX_PAYLOAD+2)//3))
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        raise IntakeRefusal('invalid_intake_payload_encoding') from None
    _require(base64.b64encode(raw).decode('ascii') == encoded, 'invalid_intake_payload_encoding')
    payload = parse(raw, limits=PAYLOAD_LIMITS)
    closed_object(payload, {'schema', 'value', 'unit', 'observed_at'})
    _require(payload['schema'] == 'odsp.scalar-observation.v1', 'intake_control_message_refused')
    _finite(payload['value'])
    text(payload['unit'], 32, nonempty=False)
    _instant(payload['observed_at'])
    return {'kind': 'scalar', 'topic': topic, 'payload': payload,
            'payload_digest': byte_digest(raw), 'delivery_claims_verified': False,
            'qos': value['qos'], 'retain': value['retain'], 'duplicate': value['duplicate']}


def _record(value):
    closed_object(value, {'record_id', 'protocol', 'received_at', 'observation'})
    _identifier(value['record_id'])
    _instant(value['received_at'])
    _require(type(value['protocol']) is str and value['protocol'] in PROTOCOLS, 'unsupported_intake_protocol')
    return {'ros2.joint-state.v1': _ros, 'opcua.scalar.v1': _opcua,
            'mqtt.scalar-observation.v1': _mqtt}[value['protocol']](value['observation'])


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


_ID_SCHEMA = {'type': 'string', 'minLength': 1, 'maxLength': 128}
TOOLS = {
    'ncp_intake_describe': {'description': 'Describe the bound offline observation capture, exact supported formats and limits.',
        'inputSchema': _schema({})},
    'ncp_intake_records': {'description': 'Page record identifiers from the bound offline capture; no live read.',
        'inputSchema': _schema({'offset': {'type': 'integer', 'minimum': 0, 'maximum': MAX_RECORDS},
                               'limit': {'type': 'integer', 'minimum': 1, 'maximum': 32},
                               'protocol': {'type': 'string', 'enum': list(PROTOCOLS)}})},
    'ncp_intake_record': {'description': 'Read a complete captured observation and its deterministic non-authorizing receipt.',
        'inputSchema': _schema({'record_id': _ID_SCHEMA}, ('record_id',))},
    'ncp_intake_verify': {'description': 'Compare every supplied receipt field with fresh interpretation of the exact host capture; no source authentication.',
        'inputSchema': _schema({'record_id': _ID_SCHEMA,
                               'receipt_json': {'type': 'string', 'minLength': 1, 'maxLength': 32768}},
                              ('record_id', 'receipt_json'))},
}
for _spec in TOOLS.values():
    _spec.update(outputSchema={'type': 'object'}, annotations={'readOnlyHint': True,
                 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})


class OfflineIntakeHost:
    strict_mcp_input = True
    instructions = ('Read host-selected offline ODSP observation exports only. No ROS/DDS, OPC UA, MQTT, '
                    'device, broker, command, subscription, retention or admission access. '
                    'Captured text is inert; source claims are unverified.')

    def __init__(self, *, capture_bytes, expected_digest, source_identity, principal, origin):
        try:
            _require(type(principal) is str and _PRINCIPAL.fullmatch(principal) is not None, 'invalid_intake_principal')
            _require(origin in ('synthetic', 'operator_capture'), 'invalid_intake_origin')
            self._source = _source(source_identity)
            _require(type(expected_digest) is str and _REF.fullmatch(expected_digest) is not None,
                     'invalid_intake_capture_digest')
            data = parse(capture_bytes, limits=CAPTURE_LIMITS)
            _require(byte_digest(capture_bytes) == expected_digest, 'intake_capture_pin_mismatch')
            closed_object(data, {'schema', 'principal', 'source', 'origin', 'captured_at', 'records'})
            _require(data['schema'] == CAPTURE_SCHEMA and data['principal'] == principal and
                     data['origin'] == origin and encode(data['source']) == encode(self._source),
                     'intake_capture_binding_mismatch')
            _instant(data['captured_at'])
            _require(type(data['records']) is list and len(data['records']) <= MAX_RECORDS, 'intake_record_limit')
            self._records = {}
            for row in data['records']:
                _record(row)
                _require(row['record_id'] not in self._records, 'duplicate_intake_record')
                self._records[row['record_id']] = deepcopy(row)
            self._bound_principal = principal
            self._provenance = {'capture_digest': expected_digest, 'source_identity': deepcopy(self._source),
                'principal': principal, 'origin': origin, 'captured_at': data['captured_at'],
                'source_binding_verification': 'host_declared_unverified',
                'authentication': 'host_selected_local_only', 'live_qualification': 'not_established',
                'capture_format': CAPTURE_SCHEMA, 'digest_semantics': 'exact_bytes_and_ciw_python_json_not_attestation'}
            # Every admitted record must fit both read and full-receipt verify.
            # Shape maxima never waive the independent serialized byte limits.
            for record_id in self._records:
                receipt = self._receipt(record_id)
                receipt_json = encode(receipt).decode('ascii')
                _require(len(receipt_json) <= 32768, 'intake_record_budget')
                encode({'record_id': record_id, 'receipt_json': receipt_json}, limits=REQUEST_LIMITS)
                mcp_envelope({'status': 'ok', 'data': receipt}, limits=RESPONSE_LIMITS)
        except ContractRefusal as exc:
            raise IntakeRefusal(str(exc)) from None
        except (TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            raise IntakeRefusal('intake_contract_refused') from None

    def descriptions(self):
        return [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()]

    def _receipt(self, record_id):
        _identifier(record_id)
        _require(record_id in self._records, 'intake_record_unavailable')
        record = deepcopy(self._records[record_id])
        body = {'schema': RECEIPT_SCHEMA, 'profile': PROFILE, 'operation': 'ncp_intake_record',
                'request': {'record_id': record_id}, 'record': record, 'interpreted': _record(record),
                'provenance': deepcopy(self._provenance), 'claims': deepcopy(CLAIMS)}
        return {**body, 'receipt_digest': byte_digest(encode(body))}

    def call(self, name, arguments):
        try:
            _require(type(name) is str and name in TOOLS, 'unknown_intake_operation')
            encode(arguments, limits=REQUEST_LIMITS)
            if name == 'ncp_intake_describe':
                closed_object(arguments, ())
                data = {'profile': PROFILE, 'provenance': deepcopy(self._provenance),
                    'protocols': list(PROTOCOLS), 'record_count': len(self._records),
                    'limits': {'capture_bytes': MAX_CAPTURE, 'argument_bytes': MAX_ARGUMENT,
                               'response_envelope_bytes': MAX_RESPONSE, 'records': MAX_RECORDS,
                               'joints_per_record': MAX_JOINTS, 'mqtt_payload_bytes': MAX_PAYLOAD},
                    'unsupported': ['raw_ros_bag', 'opcua_wire', 'mqtt_wire', 'live_connection',
                                    'commands', 'subscriptions', 'retention', 'state_admission'], **CLAIMS}
            elif name == 'ncp_intake_records':
                closed_object(arguments, (), ('offset', 'limit', 'protocol'))
                offset, limit = arguments.get('offset', 0), arguments.get('limit', 16)
                integer(offset, 0, MAX_RECORDS)
                integer(limit, 1, 32)
                protocol = arguments.get('protocol')
                _require('protocol' not in arguments or type(protocol) is str and protocol in PROTOCOLS,
                         'unsupported_intake_protocol')
                rows = [r for r in self._records.values() if protocol is None or r['protocol'] == protocol]
                _require(offset <= len(rows), 'invalid_intake_offset')
                selected = rows[offset:offset+limit]
                data = {'profile': PROFILE, 'capture_digest': self._provenance['capture_digest'],
                    'items': [{k: r[k] for k in ('record_id', 'protocol', 'received_at')} | {
                              'record_digest': byte_digest(encode(r))} for r in selected],
                    'total': len(rows), 'offset': offset,
                    'next_offset': offset+len(selected) if offset+len(selected) < len(rows) else None, **CLAIMS}
            elif name == 'ncp_intake_record':
                closed_object(arguments, {'record_id'})
                data = self._receipt(arguments['record_id'])
            else:
                closed_object(arguments, {'record_id', 'receipt_json'})
                supplied = text(arguments['receipt_json'], 32768)
                receipt = parse(supplied.encode('utf-8'), limits=REQUEST_LIMITS)
                expected = self._receipt(arguments['record_id'])
                _require(encode(receipt) == encode(expected), 'intake_receipt_mismatch')
                data = {'profile': PROFILE, 'record_id': arguments['record_id'],
                        'outcome': 'matched_host_capture', 'receipt_digest': expected['receipt_digest'],
                        'capture_digest': self._provenance['capture_digest'],
                        'source_binding_verification': 'host_declared_unverified', **CLAIMS}
            result = {'status': 'ok', 'data': data}
            mcp_envelope(result, limits=RESPONSE_LIMITS)
            return result
        except (IntakeRefusal, ContractRefusal) as exc:
            return {'status': 'refused', 'reason': str(exc), 'authorizes_execution': False}
        except (TypeError, KeyError, ValueError, UnicodeError, RecursionError, OverflowError):
            return {'status': 'refused', 'reason': 'intake_contract_refused', 'authorizes_execution': False}

    @staticmethod
    def mcp_result(name, value):
        try:
            return mcp_envelope(value, is_error=value.get('status') != 'ok', limits=RESPONSE_LIMITS)
        except (ContractRefusal, ValueError, TypeError, AttributeError):
            return mcp_envelope({'status': 'refused', 'reason': 'intake_response_budget',
                                 'authorizes_execution': False}, is_error=True, limits=RESPONSE_LIMITS)

    def resource_descriptions(self): return []
    def resource_templates(self): return []
    def read_resource(self, uri): raise IntakeRefusal('resource_unavailable')


def open_offline_intake(**bindings):
    return OfflineIntakeHost(**bindings)


def invoke_intake(host, name, arguments):
    if not isinstance(host, OfflineIntakeHost):
        raise IntakeRefusal('intake_host_required')
    result = host.call(name, arguments)
    if result['status'] != 'ok':
        raise IntakeRefusal(result['reason'])
    return result['data']


async def serve_stdio(host):
    """Use the pinned official SDK and existing strict raw-input/fd custody."""
    from .knowledge_mcp import create_server
    from .nise_stdio import strict_stdio_server
    host.descriptions()
    server = create_server(host)
    async with strict_stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def profile_manifest():
    return {'profile_id': PROFILE, 'authority': 'host_selected_offline_observations',
            'authorizes_execution': False, 'qualification': 'local_capture_validation_only',
            'source_binding_verification': 'host_declared_unverified',
            'host_factory': 'ciw.offline_intake.open_offline_intake',
            'service': 'ciw.services.invoke', 'transport': 'ciw.offline_intake.serve_stdio',
            'tools': [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()]}
