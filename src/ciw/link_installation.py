"""Reference-paired installation research through Link and the existing NET Session.

No acquisition, excitation, alignment, calibration, inversion or equipment grant.
The original mounting estimator is used unchanged. All capability decisions here
are scientific use restrictions, never evidence of physical qualification.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import platform
from threading import Lock

import numpy as np

from . import link_contract as c, link_gateway as gateway, link_workflow as workflow
from . import mount_characterization as mount
from .adapters.protocol import InstrumentManifest
from .control_plane import CapabilityRegistry, experiment
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.schemas import register_payload_validator

PLAN = 'ciw.link-installation-plan.v1'
SCHEMA = 'ciw.link-installation-study.v1'
OPERATION = 'link.characterize-installation.v1'
MAX_BYTES = 4 * 1024 * 1024
MAX_PLAN = 32768
MAX_CAPTURE_ARTIFACT = 2 * 1024 * 1024
CHUNK_BYTES = 32768
_LOCK = Lock()
_REGISTERED = False
SCOPE = {'physical_qualification': 'HELD', 'source_authentication': 'NOT_ESTABLISHED',
    'isolated_mount_transfer': 'NOT_IDENTIFIED', 'continuous_qualified_band_hz': None,
    'spectral_uncertainty': 'NOT_EVALUATED', 'inverse_correction': False,
    'hardware_access': False, 'excitation': False, 'model_update': False,
    'machine_control': False, 'part_release': False, 'training_admission': False}


def equal(a, b):
    return c.canonical_json(a) == c.canonical_json(b)


def validate_plan(p):
    c.bounded(p, MAX_PLAN)
    c.keys(p, {'schema', 'study_id', 'revision', 'installation', 'manifest_ref',
        'boot_id', 'reference_channel', 'dut_channel', 'context', 'protocol',
        'conditions', 'analysis', 'chain_evidence'})
    if p['schema'] != PLAN:
        raise ValueError('Unknown installation plan')
    c.text(p['study_id']); c.integer(p['revision'], minimum=1, maximum=2**53-1)
    for name in ('boot_id', 'reference_channel', 'dut_channel'): c.text(p[name])
    if p['reference_channel'] == p['dut_channel']:
        raise ValueError('Reference and DUT require separate channels')
    c.content_ref(p['manifest_ref'])
    i = p['installation']
    c.keys(i, {'occurrence_id', 'asset_id', 'mount_id', 'mount_revision', 'dut', 'reference',
               'fixture', 'mounting', 'reference_location', 'dut_location'})
    for name in ('occurrence_id', 'asset_id', 'mount_id', 'mount_revision', 'reference_location', 'dut_location'):
        c.text(i[name])
    for side in ('reference', 'dut'):
        c.keys(i[side], {'unit_id', 'hardware_revision'})
        c.text(i[side]['unit_id']); c.text(i[side]['hardware_revision'])
    if i['reference']['unit_id'] == i['dut']['unit_id']:
        raise ValueError('One physical unit cannot serve as its own independent reference')
    for artifact in (i['fixture'], i['mounting'], p['protocol']):
        if not c.unpack(artifact, 8192): raise ValueError('Retain nonempty fixture, mounting and protocol declarations')
    c.keys(p['context'], c.CONTEXT_FIELDS)
    for v in p['context'].values():
        if v is not None: c.text(v)
    if p['context']['asset_id'] != i['asset_id']:
        raise ValueError('Installation and acquisition asset differ')
    c.keys(p['conditions'], {'temperature_K', 'temperature_measurand', 'basis'})
    if not 1 <= c.finite(p['conditions']['temperature_K']) <= 1000:
        raise ValueError('Temperature outside research profile')
    if (p['conditions']['temperature_measurand'] not in {'mounting_surface', 'pcb', 'die', 'ambient'}
            or p['conditions']['basis'] != 'declared_not_independently_verified'):
        raise ValueError('Declare temperature location and unverified basis')
    a = p['analysis']
    c.keys(a, {'sample_rate_hz', 'block_samples', 'replicate_blocks', 'estimator', 'phase_skew_bound_s', 'reference_response', 'limits'})
    fs = c.finite(a['sample_rate_hz'])
    if not 1 <= fs <= 100000 or type(a['block_samples']) is not int or a['block_samples'] not in (16, 32):
        raise ValueError('Use complete 16/32-sample blocks inside existing Link capture bounds')
    c.integer(a['replicate_blocks'], minimum=4, maximum=8)
    if 2*a['block_samples']*a['replicate_blocks'] > c.MAX_RECORDS:
        raise ValueError('Planned paired samples exceed the unchanged Link record budget')
    if a['estimator'] != 'H1.periodic_disjoint_blocks.v1':
        raise ValueError('Unsupported estimator; no plugin loading')
    if a['phase_skew_bound_s'] is not None and not 0 <= c.finite(a['phase_skew_bound_s']) <= 1:
        raise ValueError('Invalid relative phase-skew bound')
    if a['reference_response'] not in {'UNKNOWN', 'DECLARED_MATCHED_CHAIN_RESPONSES'}:
        raise ValueError('Calibration or independent reference truth cannot be declared by status')
    if p['chain_evidence'] is not None:
        if not c.unpack(p['chain_evidence'], 8192): raise ValueError('Empty chain-response evidence')
    elif a['reference_response'] != 'UNKNOWN':
        raise ValueError('Matched-chain declaration requires its original supporting artifact')
    c.keys(a['limits'], {'input_bin_power_floor', 'minimum_coherence', 'gain_error',
                         'phase_error_deg', 'maximum_frequency_hz'})
    for key, lo, hi in [('input_bin_power_floor', 1e-24, 1e12), ('minimum_coherence', 0, 1),
        ('gain_error', 0, 1), ('phase_error_deg', 0, 180), ('maximum_frequency_hz', fs/a['block_samples'], fs/2)]:
        if not lo <= c.finite(a['limits'][key]) <= hi:
            raise ValueError('Explicit analysis limit invalid: ' + key)
    return deepcopy(p)


def pack_capture(raw):
    """Existing packed-byte objects in bounded chunks; no large JSON string."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_CAPTURE_ARTIFACT:
        raise ValueError('Capture artifact exceeds the bounded offline study size')
    return {'chunks': [c.packed(raw[i:i+CHUNK_BYTES], CHUNK_BYTES) for i in range(0, len(raw), CHUNK_BYTES)],
            'byte_count': len(raw), 'sha256': c.bytes_ref(raw)}


def unpack_capture(value):
    c.keys(value, {'chunks', 'byte_count', 'sha256'})
    c.integer(value['byte_count'], minimum=1, maximum=MAX_CAPTURE_ARTIFACT)
    parts = value['chunks']
    if type(parts) is not list or not 1 <= len(parts) <= MAX_CAPTURE_ARTIFACT//CHUNK_BYTES:
        raise ValueError('Invalid bounded capture chunk count')
    raw = b''.join(c.unpack(p, CHUNK_BYTES) for p in parts)
    if not equal(pack_capture(raw), value):
        raise ValueError('Capture chunk order, lengths or exact identity changed')
    return raw


def prepare(plan_raw, capture_raw, *, expected_plan_ref, expected_capture_ref):
    """Freeze externally selected bytes. Does not claim when a plan was authored."""
    c.content_ref(expected_plan_ref); c.content_ref(expected_capture_ref)
    if c.bytes_ref(plan_raw) != expected_plan_ref or c.bytes_ref(capture_raw) != expected_capture_ref:
        raise ValueError('Plan or capture differs from external selection')
    validate_plan(c.loads(plan_raw, MAX_PLAN))
    # Retain bounded invalid captures too; Session will record a normal refusal.
    if type(c.loads(capture_raw, c.MAX_CAPTURE_BYTES)) is not dict:
        raise ValueError('Capture must be a bounded JSON object')
    r = {'schema': SCHEMA, 'plan': c.packed(plan_raw, MAX_PLAN),
         'capture': pack_capture(capture_raw)}
    c.bounded(r, MAX_BYTES)
    return r


def unpack_request(r):
    c.bounded(r, MAX_BYTES); c.keys(r, {'schema', 'plan', 'capture'})
    if r['schema'] != SCHEMA: raise ValueError('Unsupported installation investigation')
    pr = c.unpack(r['plan'], MAX_PLAN); cr = unpack_capture(r['capture'])
    return validate_plan(c.loads(pr, MAX_PLAN)), c.loads(cr, c.MAX_CAPTURE_BYTES), pr, cr


def analyze(r):
    p, capture, plan_raw, capture_raw = unpack_request(r)
    retained = gateway.validate_capture(capture)
    device = capture['manifest']; a = p['analysis']; inst = p['installation']
    plan_ref = c.bytes_ref(plan_raw)
    if c.content_identity(device) != p['manifest_ref'] or capture['boot_id'] != p['boot_id']:
        raise ValueError('Plan belongs to another device configuration or acquisition boot')
    if device['clock']['timestamp_event'] != 'sensor_sample':
        raise ValueError('Host/report receipt time is not sensor sample time')
    hz = device['clock']['tick_hz']
    if hz is None: raise ValueError('Unknown sample-clock rate')
    dt = Fraction(hz) / Fraction(str(a['sample_rate_hz']))
    if dt.denominator != 1 or dt < 1:
        raise ValueError('Sampling period must be an exact positive device-tick interval')
    names = [p['reference_channel'], p['dut_channel']]
    if len(device['channels']) != 2 or set(device['configuration']['enabled_channels']) != set(names):
        raise ValueError('Select exactly the paired channels; no implicit subset')
    for side, name in zip(('reference', 'dut'), names):
        ch = c.channel_for(device, name)
        if (ch['transducer_id'] != inst[side]['unit_id'] or ch['quantity'] != 'acceleration'
                or ch['unit'] != mount.UNIT or ch['frame'] != mount.FRAME
                or ch['encoding'] != 'notation-ascii-scalar.v1'
                or device['configuration']['sample_rate_hz'][name] != a['sample_rate_hz']):
            raise ValueError('Reference/DUT identity, units, frame, grammar or declared rate mismatch')
    observations = retained['observations']
    if retained['events'] or retained['projection']['gaps']:
        raise ValueError('Events/gaps require review; no subset FFT or automatic repair')
    if any(row['procedure_ref'] != plan_ref or not equal(row['context'], p['context']) for row in observations):
        raise ValueError('Acquisition does not bind the exact original plan/installation context')
    grouped = [[row for row in observations if row['channel_id'] == name] for name in names]
    n = len(grouped[0]); block = a['block_samples']
    if n != len(grouped[1]) or n != block*a['replicate_blocks']:
        raise ValueError('Require equal complete paired replicate blocks; no crop or padding')
    ticks = [[row['device_tick'] for row in rows] for rows in grouped]
    if any(x is None for rows in ticks for x in rows) or ticks[0] != ticks[1]:
        raise ValueError('Paired sample ticks disagree; no alignment or interpolation')
    if any(t != ticks[0][0]+i*int(dt) for i,t in enumerate(ticks[0])):
        raise ValueError('Sample-clock gap or nonuniform acquisition')
    if any(row['stage'] != 'reported' or row['value'] is None for row in observations):
        raise ValueError('Require original complete reported acceleration values')
    flags = [{'measurement_id': row['measurement_id'], 'flags': deepcopy(row['quality_flags'])}
             for row in observations if row['quality_flags']]
    base = {'schema': 'ciw.link-installation-response.v1', 'plan_ref': plan_ref,
        'capture_bytes_ref': c.bytes_ref(capture_raw), 'manifest_ref': p['manifest_ref'],
        'installation': deepcopy(inst), 'analysis_criteria': deepcopy(a),
        'conditions': deepcopy(p['conditions']), 'origin': observations[0]['origin'],
        'sample_count_per_channel': n, 'source_quality_flags': flags,
        'input_joint_covariance_ref': None if capture['joint_covariance'] is None else capture['joint_covariance']['covariance_id'],
        'uncertainty_use': 'Original marginal/joint records retained; H1 spectral uncertainty NOT propagated',
        'scope': deepcopy(SCOPE)}
    if flags:
        return {**base, 'status': 'SOURCE_REVIEW_REQUIRED', 'response': None,
                'analysis_input_ref': None, 'projection_ref': retained['projection']['native_stream']['record_digest']}
    projection = retained['projection']
    if projection['withheld'] or len(projection['mappings']) != len(observations):
        raise ValueError('All original records must project; no unmarked missing sample')
    byref = {v['record_digest']: v for v in projection['native_stream']['observations']}
    byid = {m['measurement_id']: byref[m['observation_ref']] for m in projection['mappings']}
    paired = {'schema': mount.SCHEMA, 'test_id': p['study_id'], 'mount_id': inst['occurrence_id'],
        'mount_revision': inst['mount_revision'], 'temperature_K': p['conditions']['temperature_K'],
        **{k: deepcopy(v) for k,v in a.items() if k != 'replicate_blocks'}, 'source_quality_flags': [],
        'reference': [byid[v['measurement_id']] for v in grouped[0]],
        'dut': [byid[v['measurement_id']] for v in grouped[1]]}
    pair_ref = c.content_identity(paired)
    # The nested estimator remains unchanged. Its execution fields are null; the
    # enclosing genuine Session result supplies the computational occurrence.
    response = mount.analyze(paired, input_ref=pair_ref)
    return {**base, 'status': 'RETAINED_RESPONSE_PREVIEW', 'response': response,
            'analysis_input_ref': pair_ref, 'projection_ref': projection['native_stream']['record_digest']}


def make_source(request, input_ref):
    c.bounded(request, MAX_BYTES); c.content_ref(input_ref)
    if type(request) is not dict: raise ValueError('Study request must be an object')
    source = workflow.make_source({}, input_ref)
    source['instrument'] = 'link-installation-study.v1'
    source['metadata']['manifest']['instrument_id'] = source['instrument']
    source['metadata']['manifest']['supported_operations'] = [OPERATION]
    source['metadata']['installation_study'] = deepcopy(request)
    source['evidence_id'] = evidence_id(source)
    return source


def source_request(source):
    validate_run_structure(source); validate_evidence_identity(source)
    r = source['metadata']['installation_study']
    if not equal(source, make_source(r, source['metadata']['input_bytes_ref'])):
        raise ValueError('Retained installation source differs')
    return r


def execute(source, parameters):
    c.keys(parameters, set())
    return analyze(source_request(source))


def validator(op, data, source, params, selection):
    if op != OPERATION or selection['channel'] != workflow.CHANNEL or selection['interval_s'] != [0.0, 1.0]:
        raise ValueError('Use the complete installation declaration')
    if not equal(data, execute(source, params)):
        raise ValueError('Installation result differs from original evidence and bounded estimator')


def runtime_identity():
    modules = (c, gateway, workflow, mount)
    raw = Path(__file__).read_bytes() + b''.join(Path(m.__file__).read_bytes() for m in modules)
    return {'provider': 'ciw.link_installation', 'version': '1', 'code_sha256': sha256(raw).hexdigest(),
            'python': platform.python_version(), 'numpy': np.__version__,
            'scope': 'retained_reference_paired_installation_preview'}


def register():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(OPERATION, validator, role='backend'); _REGISTERED = True


def registry(*, bind=True):
    register()
    reg = CapabilityRegistry()
    manifest = InstrumentManifest(instrument_id='link-installation-study.v1', role='retained_instrument_capture',
        units={workflow.CHANNEL: '1'}, frames=(workflow.FRAME,), sampling={'kind': 'one_declaration'},
        supported_operations=(OPERATION,), calibration_requirements={'scope': 'physical_qualification_HELD'})
    reg.advertise(manifest, runtime=runtime_identity(), capabilities={OPERATION: ['reference_paired_installation']})
    if bind: reg.bind(Operation(OPERATION, 'backend', execute, runtime_identity))
    return reg


def graph():
    return experiment('link-installation-study', model_id='link-installation-study.v1', nodes=[
        {'node_id': 'response', 'operation_id': OPERATION, 'parameters': {}, 'depends_on': [], 'inputs': {}}])
