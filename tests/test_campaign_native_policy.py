"""Opt-in process policy reaches every selected native launch and replay identity."""
from copy import deepcopy
import os
from pathlib import Path
import runpy
import sys

import pytest

from ciw import clocksync_profile, nise_profile, recompiler_profile, schematic_profile
from ciw.process_policy import ProcessPolicy, executable_sha256


def selected():
    return ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'),
                         max_output_bytes=4*1024*1024)


def checkout(variable):
    raw=os.environ.get(variable)
    if not raw:
        pytest.skip(variable+' must name the existing approved native checkout')
    return Path(raw).absolute()


def fixture(filename, name):
    # Existing independently stated numerical/graph fixtures, shipped with the
    # installed test suite; never a tool-selected module or input code path.
    return deepcopy(runpy.run_path(str(Path(__file__).with_name(filename)))[name])


def test_nise_policy_reaches_source_probe_compile_and_replay():
    root=checkout('CIW_NISE_CHECKOUT'); python=os.environ.get('CIW_NISE_PYTHON')
    if not python: pytest.skip('CIW_NISE_PYTHON must select the existing approved interpreter')
    policy=selected()
    request={'execution_id':'policy:nise:1',
             'catalog':nise_profile.parse((root/'examples/bearing_catalog.json').read_bytes()),
             'query':nise_profile.parse((root/'examples/bearing_query.json').read_bytes())}
    with nise_profile.NiseHost(checkout=root,python_executable=python,principal='policy-owner',process_policy=policy) as host:
        receipt=nise_profile.invoke_nise(host,'ncp_nise_compile',{'request':request})
        assert receipt['runtime']['process_policy_ref']==policy.identity()['policy_sha256']
        assert nise_profile.invoke_nise(host,'ncp_nise_verify',{'receipt':receipt})['outcome']=='verified'
        altered=deepcopy(receipt); altered['runtime']['process_policy_ref']='sha256:'+'0'*64
        altered['receipt_digest']=nise_profile._receipt_digest(altered)
        refused=nise_profile.invoke_nise(host,'ncp_nise_verify',{'receipt':altered})
        assert refused['outcome']=='runtime_mismatch' and refused['canonical_receipt_match'] is False


def test_clock_policy_preserves_numeric_result_and_binds_replay():
    policy=selected(); request=fixture('test_clocksync_profile.py','INPUT')
    with clocksync_profile.ClockSyncHost(checkout=checkout('CIW_CLOCKSYNC_CHECKOUT'),
            python_executable=sys.executable,principal='policy-owner',process_policy=policy) as host:
        receipt=clocksync_profile.invoke_clocksync(host,'ncp_clocksync_reconcile',{'request':request})
        assert receipt['runtime']['process_policy_ref']==policy.identity()['policy_sha256']
        assert receipt['numerical_result']['event_time_delta']==6.5
        assert receipt['numerical_result']['variance']==22.75
        value=clocksync_profile.invoke_clocksync(host,'ncp_clocksync_replay',{'receipt':receipt})
        assert value['outcome']=='verified'
        forged=deepcopy(receipt);forged['runtime']['process_policy_ref']='sha256:'+'0'*64
        forged['receipt_digest']=clocksync_profile._receipt_digest(forged)
        refused=clocksync_profile.invoke_clocksync(host,'ncp_clocksync_replay',{'receipt':forged})
        assert refused['outcome']=='runtime_mismatch' and refused['canonical_receipt_match'] is False


def test_recompiler_selected_policy_is_reported_without_changing_kernel_receipt():
    policy=selected(); request=fixture('test_recompiler_profile.py','INPUT')
    host=recompiler_profile.RecompilerHost(checkout=checkout('CIW_NSR_CHECKOUT'),python_executable=sys.executable,
            principal='policy-owner',process_policy=policy)
    try:
        value=recompiler_profile.invoke_recompiler(host,'ncp_recompiler_reconcile',{'request':request})
        assert value['runtime']['process_policy_ref']==policy.identity()['policy_sha256']
        receipt=value['receipt']
        replay=recompiler_profile.invoke_recompiler(host,'ncp_recompiler_verify_receipt',{'receipt':receipt})
        assert replay['canonical_receipt_match'] is True
        assert replay['runtime']['process_policy_ref']==policy.identity()['policy_sha256']
    finally:
        host._lease_directory.cleanup()


def test_sra_policy_survives_full_bundle_and_occurrence_replay():
    policy=selected(); request=fixture('test_schematic_profile.py','SOURCE')
    with schematic_profile.SchematicHost(checkout=checkout('CIW_SRA_CHECKOUT'),python=sys.executable,
            principal='policy-owner',process_policy=policy) as host:
        value=schematic_profile.invoke_schematic(host,'ncp_schematic_assess',{'request':request})
        receipt=value['receipt']
        assert receipt['bundle']['runtimes']['sra']['process_policy_ref']==policy.identity()['policy_sha256']
        replay=schematic_profile.invoke_schematic(host,'ncp_schematic_verify_receipt',{'receipt':receipt})
        assert replay['outcome']=='verified' and replay['numerical_match'] is True
        assert replay['reproduction_receipt']['bundle']['session_id']!=receipt['bundle']['session_id']
        forged=deepcopy(receipt);forged['bundle']['runtimes']['sra']['process_policy_ref']='sha256:'+'0'*64
        forged['receipt_digest']=schematic_profile._receipt_digest(forged)
        assert host.call('ncp_schematic_verify_receipt',{'receipt':forged})['status']=='refused'
