"""Synthetic paired acquisition fixture through the existing Link runtime/journals."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np

from . import link_contract as c, link_installation as inst
from .link_profiles import example_manifest
from .link_store import Journal, safe_path
from .link_runtime import DeviceRuntime
from .link_gateway import capture
from .control_contracts import save_new


def fixture(*, compliant=False, occurrence='synthetic.installation.1'):
    fs, n, blocks = 1024, 32, 4
    device = example_manifest('ConditionLink')
    device['device_id'] = 'synthetic.paired-acquisition-host'
    device['channels'] = [{'id': name, 'transducer_id': 'synthetic.'+side+'.unit.1',
        'quantity': 'acceleration', 'unit': inst.mount.UNIT, 'frame': inst.mount.FRAME,
        'encoding': 'notation-ascii-scalar.v1'} for name, side in [('REF', 'reference'), ('DUT', 'dut')]]
    device['configuration'].update(enabled_channels=['REF', 'DUT'],
        sample_rate_hz={'REF': fs, 'DUT': fs}, ranges={'REF': [-100, 100], 'DUT': [-100, 100]})
    device['clock'].update(tick_hz=fs, timestamp_event='sensor_sample')
    device = c.manifest(device)
    context = dict.fromkeys(c.CONTEXT_FIELDS); context['asset_id'] = 'synthetic.bench'
    plan = {'schema': inst.PLAN, 'study_id': 'synthetic.paired-installation-study', 'revision': 1,
        'installation': {'occurrence_id': occurrence, 'asset_id': 'synthetic.bench', 'mount_id': 'synthetic.mount',
            'mount_revision': 'compliant-model.1' if compliant else 'unity-model.1',
            'dut': {'unit_id': 'synthetic.dut.unit.1', 'hardware_revision': 'synthetic.no-board'},
            'reference': {'unit_id': 'synthetic.reference.unit.1', 'hardware_revision': 'synthetic.no-reference-sensor'},
            'fixture': c.packed(b'Synthetic periodic linear fixture; NOT a physical test.'),
            'mounting': c.packed(b'Synthetic compliant fixture' if compliant else b'Synthetic unity fixture'),
            'reference_location': 'declared structure-side x-axis', 'dut_location': 'declared sensor-side x-axis'},
        'manifest_ref': c.content_identity(device), 'boot_id': 'synthetic.boot.'+c.content_identity(occurrence)[7:23],
        'reference_channel': 'REF', 'dut_channel': 'DUT', 'context': context,
        'protocol': c.packed(b'Four complete periodic blocks; no real excitation. Synthetic point limits only.'),
        'conditions': {'temperature_K': 293.15, 'temperature_measurand': 'mounting_surface',
                       'basis': 'declared_not_independently_verified'},
        'analysis': {'sample_rate_hz': fs, 'block_samples': n, 'replicate_blocks': blocks, 'estimator': 'H1.periodic_disjoint_blocks.v1',
            'phase_skew_bound_s': 0.0, 'reference_response': 'DECLARED_MATCHED_CHAIN_RESPONSES',
            'limits': {'input_bin_power_floor': 1e-12, 'minimum_coherence': 0.95, 'gain_error': 0.05,
                       'phase_error_deg': 5.0, 'maximum_frequency_hz': 480.0}},
        'chain_evidence': c.packed(b'Synthetic identical readout-chain definitions; not a calibration certificate.')}
    raw_plan = (json.dumps(plan, indent=2, allow_nan=False)+'\n').encode()
    inst.validate_plan(plan)
    # This is an authored LTI fixture, not a solver of the Notation PCB.
    rng = np.random.default_rng(68193); x = rng.normal(size=(blocks, n))
    r = np.fft.rfftfreq(n, 1/fs)/384.0; zeta = 0.12
    h = (1+2j*zeta*r)/(1-r*r+2j*zeta*r) if compliant else np.ones(n//2+1, dtype=complex)
    h[-1] = h[-1].real
    y = np.fft.irfft(np.fft.rfft(x, axis=1)*h, n=n, axis=1)
    records = []
    for i, (ref, dut) in enumerate(zip(x.ravel(), y.ravel())):
        for name, value in [('REF', ref), ('DUT', dut)]:
            records.append(c.make_observation(device, boot_id=plan['boot_id'], sequence=len(records),
                channel_id=name, raw=f'{value:.17f} {inst.mount.UNIT}\r\n'.encode(),
                tick=i, origin='synthetic', context=context, procedure_ref=c.bytes_ref(raw_plan)))
    return device, plan, raw_plan, records


def demo(output, *, compliant=False):
    from . import link_installation_io as io
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    device, plan, raw_plan, records = fixture(compliant=compliant)
    with (root/'plan.json').open('xb') as f: f.write(raw_plan)
    with Journal(root/'outbox.sqlite', create=True) as edge, Journal(root/'ingress.sqlite', create=True) as net:
        runtime = DeviceRuntime(device, edge, boot_id=plan['boot_id'], origin='synthetic')
        for record in records:
            actual = runtime.emit(record['channel_id'], c.unpack(record['raw']), tick=record['device_tick'],
                context=record['context'], procedure_ref=record['procedure_ref'])['record']
            if not inst.equal(actual, record): raise ValueError('Synthetic producer differs from authored record')
        delivery = edge.sync_to(net, manifests=[device])
        retry = edge.sync_to(net, manifests=[device])
        captured = capture(net, expected_manifest=device, boot_id=plan['boot_id'])
    raw_capture = (json.dumps(captured, indent=2, allow_nan=False)+'\n').encode()
    with (root/'capture.json').open('xb') as f: f.write(raw_capture)
    request = inst.prepare(raw_plan, raw_capture, expected_plan_ref=c.bytes_ref(raw_plan),
                           expected_capture_ref=c.bytes_ref(raw_capture))
    result = io.run(request, root/'investigation')
    page = io.report(root/'investigation', root/'installation.html', expected_request_ref=result['request_ref'])
    response = (result['result'] or {}).get('data', {}).get('response') or {}
    summary = {'schema': 'ciw.link-installation-demo.v1', 'status': result['status'], 'origin': 'synthetic',
        'request_ref': result['request_ref'], 'evidence_id': result['evidence_id'],
        'execution_id': result['execution_id'], 'result_id': result['result_id'],
        'delivery': {'new_acknowledgements': delivery['new_acknowledgements'],
                     'historical_acknowledgements': retry['existing_acknowledgements'],
                     'scope': 'existing_local_unsigned_sync_not_remote_authentication'},
        'passing_sampled_frequencies_hz': response.get('passing_sampled_frequencies_hz', []),
        'sampled_points': len(response.get('frequencies', [])), 'report': page, 'scope': dict(inst.SCOPE)}
    save_new(root/'summary.json', summary)
    return summary
