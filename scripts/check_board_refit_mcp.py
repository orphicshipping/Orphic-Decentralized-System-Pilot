#!/usr/bin/env python3
"""Installed Board acceptance through both supported SDK protocols.

Use the retained source archive's test journey against an isolated installed
package. This harness grants only its explicit synthetic demonstration profile.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import runpy
import subprocess
import sys

import anyio
import ciw
from ciw.board_refit_cli import demo, open_host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    module = Path(ciw.__file__).resolve()
    if not sys.flags.isolated or not module.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError('Require python -I and a noneditable installed package')
    helper = Path(__file__).resolve().parents[1] / 'tests' / 'test_board_refit_mcp.py'
    exercise = runpy.run_path(str(helper))['exercise_board_refit_journey']
    poison = root / 'untrusted-working-directory'
    (poison/'ciw').mkdir(parents=True)
    (poison/'ciw'/'__init__.py').write_text("raise RuntimeError('Ambient package must not be imported')\n")
    receipts = []
    for modern, protocol in ((False,'2025-11-25'), (True,'2026-07-28')):
        selected = root/protocol
        config = demo(selected)
        config_path = selected/'mcp-config.json'
        original = json.loads(config_path.read_text())
        server = original['mcpServers']['ncp-board-refit']
        assert server['args'][:2] == ['-I','-m'] and 'PYTHONPATH' not in server.get('env',{})
        server.update(cwd=str(poison),env={'PYTHONPATH':str(poison)})
        config_path.write_text(json.dumps(original,indent=2))
        receipt = anyio.run(exercise, Path(config['profile']), modern)
        with open_host(config['profile']) as host:
            before = host.call('ncp_board_status', {})
            baseline = host._baseline()
            candidate = host._candidate_bundle(receipt['candidate_ref'])
            counts = {'baseline_executions':len(baseline['workspace']['executions']),
                      'candidate_executions':len(candidate['workspace']['executions'])}
        command = [sys.executable,'-I','-m','ciw.net','refit','status','--profile',config['profile']]
        process = subprocess.run(command,capture_output=True,text=True,timeout=30,cwd=poison)
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout) == before
        with open_host(config['profile']) as host:
            assert host.call('ncp_board_status', {}) == before
        receipt.update(protocol=protocol,inspection_counts=counts,cli_command=command,
                       cli_status=json.loads(process.stdout),ambient_import_refused=True)
        (selected/'journey.json').write_text(json.dumps(receipt,indent=2))
        receipts.append({'protocol':protocol,'candidate_ref':receipt['candidate_ref'],
            'calls':len(receipt['calls']),'process_sessions':receipt['process_sessions'],
            'receipt_sha256':sha256((selected/'journey.json').read_bytes()).hexdigest()})
    result = {'schema':'ncp.installed-board-pair-acceptance.v1','status':'PASS',
        'module':str(module),'python':sys.version,'helper_sha256':sha256(helper.read_bytes()).hexdigest(),
        'journeys':receipts,'physical_qualification':'not_established','candidate_acceptance':False}
    (root/'report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
