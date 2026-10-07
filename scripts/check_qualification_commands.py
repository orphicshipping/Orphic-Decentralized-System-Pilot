"""Exercise installed reference/timing CLIs with retained command receipts.

Run with isolated Python (-I) and a fresh output directory. This is a synthetic
software acceptance campaign; it creates no physical or SP1 qualification.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


async def run_command(destination, report, name, arguments, expected=0, *, summary=False, timeout_s=30):
    """Retain the attempted command and incremental output even when launch/wait fails."""
    argv = [sys.executable, '-I', '-B', '-u', '-m', 'ciw.net', *arguments]
    receipt = {'name': name, 'arguments': list(arguments), 'command': argv,
               'exit': None, 'expected_exit': expected, 'summary': summary,
               'timeout_s': timeout_s, 'status': 'attempted',
               'stdout_file': 'commands/' + name + '.stdout',
               'stderr_file': 'commands/' + name + '.stderr'}
    report['commands'].append(receipt)
    process = None
    phase = 'open_logs'
    try:
        with (destination / receipt['stdout_file']).open('xb') as stdout, \
                (destination / receipt['stderr_file']).open('xb') as stderr:
            phase = 'spawn'
            process = await asyncio.create_subprocess_exec(*argv, cwd=destination, stdout=stdout, stderr=stderr)
            receipt['status'] = 'running'
            phase = 'wait'
            try:
                await asyncio.wait_for(process.wait(), timeout_s)
            except TimeoutError as exc:
                raise TimeoutError(f'{name}: exceeded the {timeout_s:g}-second command deadline') from exc
        receipt['exit'] = process.returncode
        phase = 'check_exit'
        require(process.returncode == expected, f'{name}: expected exit {expected}, observed {process.returncode}')
        phase = 'decode_output'
        text = (destination / receipt['stdout_file']).read_text(encoding='utf-8')
        result = text if summary else json.loads(text) if text else None
        receipt['status'] = 'completed'
        return result
    except BaseException as exc:
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass  # Child exited between the returncode check and kill.
                await process.wait()
            receipt['exit'] = process.returncode
        receipt['status'] = 'timeout' if isinstance(exc, TimeoutError) else 'failed'
        receipt['failure'] = {'phase': phase, 'type': type(exc).__name__,
                              'detail': str(exc) or f'{name}: interrupted by {type(exc).__name__}'}
        raise

async def exercise(destination: Path, report: dict):
    from ciw import reference_comparison, transport_timing
    from ciw.instruments import make_demo_run
    from ciw.operations.runner import seal
    from ciw.server import WorkbenchServer
    from ciw.session import Session
    from websockets.asyncio.server import serve

    report['module_sha256'] = {Path(module.__file__).name: sha256(Path(module.__file__).read_bytes()).hexdigest()
                              for module in (reference_comparison, transport_timing)}

    async def command(name, arguments, expected=0, *, summary=False):
        return await run_command(destination, report, name, arguments, expected, summary=summary)

    for case, expected in [('pass', 0), ('fail', 2), ('unknown', 3)]:
        await command('demo-' + case, ['reference-compare', 'demo', '--case', case,
                                      '--output-dir', case], expected)
        await command('verify-' + case, ['reference-compare', 'verify', case + '/report.json'], expected)
        text = await command('summary-' + case, ['reference-compare', 'verify',
                             case + '/report.json', '--summary'], expected, summary=True)
        status = {'pass': 'PASS', 'fail': 'FAIL', 'unknown': 'INDETERMINATE'}[case]
        require(text.startswith(status + ':'), 'Comparison summary lost the leading disposition')
    before = (destination / 'pass/report.json').read_bytes()
    await command('preserve-report', ['reference-compare', 'run', 'pass/request.json',
                                     '--output', 'pass/report.json'], 1)
    require((destination / 'pass/report.json').read_bytes() == before, 'Existing report changed')
    bad = json.loads(before)
    bad['authority']['physical_qualification'] = 'established'
    seal(bad)
    (destination / 'tampered-comparison.json').write_text(json.dumps(bad), encoding='utf-8')
    await command('reject-resealed-comparison', ['reference-compare', 'verify', 'tampered-comparison.json'], 1)
    session = Session(make_demo_run(), destination / 'session')
    snapshot = deepcopy(session.snapshot())
    bridge = WorkbenchServer(session)
    async with serve(bridge.handler, '127.0.0.1', 0) as listener:
        url = 'ws://127.0.0.1:' + str(listener.sockets[0].getsockname()[1])
        await command('timing-live', ['timing', 'run', '--url', url, '--samples', '3',
                                     '--timeout', '3', '--output', 'timing.json'])
        text = await command('timing-budget', ['timing', 'run', '--url', url, '--samples', '1',
            '--timeout', '3', '--budget-ms', '0.000001', '--output', 'timing-budget.json', '--summary'],
            1, summary=True)
        require('observed_budget_exceeded' in text, 'Timing summary lost the budget breach')
    require(session.snapshot() == snapshot, 'Read-only timing mutated the session')
    timing = await command('timing-offline-inspect', ['timing', 'inspect', 'timing.json'])
    require(timing['summary']['successful'] == 3 and timing['hard_real_time_qualified'] is False,
            'Timing campaign scope or count changed')
    await command('timing-offline-summary', ['timing', 'inspect', 'timing.json', '--summary'], summary=True)
    bad = deepcopy(timing)
    bad['summary']['failed'] = 99
    bad['record_digest'] = transport_timing._digest(bad)
    (destination / 'tampered-timing.json').write_text(json.dumps(bad), encoding='utf-8')
    await command('timing-reject-resealed-summary', ['timing', 'inspect', 'tampered-timing.json'], 2)

    async def silent(socket):
        await socket.wait_closed()

    async with serve(silent, '127.0.0.1', 0) as listener:
        url = 'ws://127.0.0.1:' + str(listener.sockets[0].getsockname()[1])
        await command('timing-retain-timeout', ['timing', 'run', '--url', url, '--samples', '1',
            '--timeout', '0.05', '--output', 'timing-timeout.json'], 1)
    timeout = json.loads((destination / 'timing-timeout.json').read_text(encoding='utf-8'))
    require(timeout['summary']['timeout_count'] == 1 and timeout['summary']['latency_ms'] is None,
            'Failed timing sample was hidden')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='Fresh directory for reports and command receipts')
    args = parser.parse_args(argv)
    destination = args.output_dir.resolve()
    report = {'schema': 'ciw.qualification-command-checks.v1', 'status': 'FAIL', 'commands': [],
              'physical_qualification': 'not_established', 'hard_real_time_qualified': False,
              'new_sp1_proof_execution': 'not_performed'}
    created = False
    try:
        require(sys.flags.isolated, 'Use isolated Python: python -I scripts/check_qualification_commands.py ...')
        require(not os.path.lexists(destination), 'Use a fresh output directory')
        distribution = importlib.metadata.distribution('computational-instrumentation-workbench')
        spec = importlib.util.find_spec('ciw')
        origin = Path(spec.origin).resolve() if spec and spec.origin else None
        source_root = Path(__file__).resolve().parents[1]
        package = Path(distribution.locate_file('ciw')).resolve()
        require(origin == package / '__init__.py', 'Imported module is not the installed distribution')
        require(package != source_root and source_root not in package.parents,
                'Installed acceptance must run outside the source checkout')
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        require(not direct.get('dir_info', {}).get('editable', False), 'Editable installs cannot pass this gate')
        report['installation'] = {'package': str(package), 'version': distribution.version,
                                  'python': sys.version, 'isolated': True, 'direct_url': direct}
        destination.mkdir(parents=True)
        created = True
        (destination / 'commands').mkdir()
        asyncio.run(exercise(destination, report))
        report['status'] = 'PASS'
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError) as exc:
        report['failure'] = {'type': type(exc).__name__, 'detail': str(exc)}
    if created:
        (destination / 'qualification.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'commands': len(report['commands']),
                      'qualification': str(destination / 'qualification.json') if created else None,
                      'failure': report.get('failure')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
