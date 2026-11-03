#!/usr/bin/env python3
"""Run bounded local campaign tests sequentially and preserve exact evidence.

This is a source qualification runner, not a deployment, full scientific-suite,
installed-wheel, namespace-containment or live-provider attestation. Skipped
prerequisites remain partial and make the aggregate non-passing.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ciw.adapters.subprocess import _bounded_process

GROUPS={
 'contracts':('test_campaign_contracts.py','test_campaign_receipts.py','test_locked_sbom.py'),
 'read_profiles':('test_offline_intake.py','test_offline_intake_policy.py','test_domain_read_profile.py',
                  'test_proof_registry.py','test_proof_profile.py'),
 'custody':('test_process_policy.py','test_campaign_process_cleanup.py','test_audit_chain.py',
            'test_campaign_audited_host.py','test_store_queue.py','test_campaign_coordinator.py',
            'test_request_boundary_custody.py'),
 'integration':('test_principal_policy.py','test_knowledge_mcp.py','test_response_policy.py',
                'test_direct_science_policy.py','test_control_annotations.py','test_read_adapter_boundaries.py',
                'test_campaign_shared_profiles.py','test_campaign_client_metadata.py','test_request_boundary.py'),
 'native':('test_campaign_native_policy.py','test_recompiler_profile.py','test_schematic_profile.py',
           'test_clocksync_profile.py','test_nise_profile.py','test_pinned_profile_policy.py',
           'test_schematic_policy.py','test_clocksync_policy.py','test_nise_policy.py'),
}


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True,timeout=30).strip()


def stamp():return datetime.now(timezone.utc).isoformat()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--group',action='append',choices=tuple(GROUPS))
    args=parser.parse_args(argv)
    destination=args.output.absolute()
    destination.mkdir(parents=True,exist_ok=False)
    versions={name:importlib.metadata.version(name) for name in ('pytest','mcp','numpy','cryptography')}
    # This runner names the qualified dependency contract, not whatever happens
    # to import locally. No install, fallback, download or repin is performed.
    expected={'pytest':'9.0.2','mcp':'2.3.0','numpy':'2.4.3','cryptography':'46.0.0'}
    if versions!=expected:
        raise ValueError('qualification dependency versions differ from the current locked contract')
    receipt={'schema':'ciw.tool-programme-qualification.v1','started_at':stamp(),
        'source_commit':git('rev-parse','HEAD'),'source_tree':git('rev-parse','HEAD^{tree}'),
        'working_tree_clean':not bool(git('status','--porcelain','--untracked-files=all')),
        'python':platform.python_version(),'executable':sys.executable,'dependencies':versions,
        'scope':'local source tests only','authorizes_execution':False,'phases':[]}
    for name in args.group or GROUPS:
        report=destination/(name+'.xml')
        command=[sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider',
                 '--junitxml='+str(report),*['tests/'+file for file in GROUPS[name]]]
        phase={'name':name,'command':command,'started_at':stamp(),'wall_limit_seconds':900,
               'output_limit_bytes':1048576,'counts':None,'status':'failed'}
        try:
            code,output=_bounded_process(command,cwd=ROOT,timeout=900,limit=1048576)
            phase['return_code']=code
            (destination/(name+'.log')).write_bytes(output)
            phase['log_sha256']=sha256(output).hexdigest()
            if report.is_file():
                tree=ET.parse(report).getroot()
                suites=[tree] if tree.tag=='testsuite' else tree.findall('testsuite')
                counts={key:sum(int(s.get(key,'0')) for s in suites) for key in ('tests','failures','errors','skipped')}
                phase['counts']=counts
                phase['junit_sha256']=sha256(report.read_bytes()).hexdigest()
                if code==0 and counts['tests']>0 and counts['failures']==counts['errors']==0:
                    phase['status']='partial' if counts['skipped'] else 'passed'
        except BaseException as exc:
            # No raw exception text: launch input, host paths and provider
            # stderr stay in bounded private evidence, not status summaries.
            phase['exception_type']=type(exc).__name__
            phase['finished_at']=stamp();receipt['phases'].append(phase)
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):
                receipt['status']='interrupted';receipt['finished_at']=stamp()
                (destination/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
                raise
        else:
            phase['finished_at']=stamp();receipt['phases'].append(phase)
        print(json.dumps({'phase':name,'status':phase['status'],'counts':phase['counts']}),flush=True)
    receipt['status']='passed' if all(p['status']=='passed' for p in receipt['phases']) else 'incomplete'
    receipt['finished_at']=stamp()
    (destination/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    return 0 if receipt['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
