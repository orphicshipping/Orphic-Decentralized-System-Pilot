"""Inventory workflow declarations and literal test overlap without merging gates.

Runner/platform, event, environment, permissions and artifact differences remain
part of identity. Similar commands are evidence for review, never deletion rules.
"""
from collections import defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml


def audit(root):
    root = Path(root)
    policy = json.loads((root / 'release/qualification-policy.json').read_text())
    required = {x['path'] for x in policy['required_workflows']}
    workflows = []
    wf_groups = defaultdict(list)
    job_groups = defaultdict(list)
    test_modules = defaultdict(set)
    test_steps = defaultdict(list)
    for path in sorted((root / '.github/workflows').iterdir()):
        if path.suffix not in {'.yml', '.yaml'}:
            continue
        raw = path.read_bytes()
        value = yaml.load(raw, Loader=yaml.BaseLoader)
        if not isinstance(value, dict) or not isinstance(value.get('jobs'), dict):
            raise ValueError('Invalid workflow: ' + str(path))
        relative = path.relative_to(root).as_posix()
        canonical = {k: v for k, v in value.items() if k != 'name'}
        wf_groups[json.dumps(canonical, sort_keys=True)].append(relative)
        context = {k: v for k, v in value.items() if k not in {'name', 'jobs'}}
        jobs = []
        for name, job in value['jobs'].items():
            identity = relative + ':' + name
            declared = {'context': context, 'job': {k: v for k, v in job.items() if k != 'name'}}
            job_groups[json.dumps(declared, sort_keys=True)].append(identity)
            for step in job.get('steps', []):
                command = step.get('run', '')
                if 'pytest' not in command:
                    continue
                test_steps[command].append(identity)
                for module in re.findall(r'tests/test_[A-Za-z0-9_*/?\-]+\.py', command):
                    test_modules[module].add(identity)
            jobs.append({'id': name, 'runs_on': job.get('runs-on'),
                         'matrix': job.get('strategy', {}).get('matrix'),
                         'uses': job.get('uses')})
        workflows.append({'path': relative, 'name': value.get('name'), 'sha256': hashlib.sha256(raw).hexdigest(),
                          'events': list(value.get('on', {})), 'jobs': jobs, 'release_required': relative in required})
    return {'schema': 'ncp.workflow-inventory.v1', 'workflow_files': len(workflows),
            'top_level_jobs': sum(len(x['jobs']) for x in workflows),
            'release_required_workflows': len(required),
            'exact_workflow_duplicates_ignoring_display_name': [x for x in wf_groups.values() if len(x) > 1],
            'exact_job_duplicates_with_inherited_context': [x for x in job_groups.values() if len(x) > 1],
            'repeated_literal_pytest_commands': [{'command': k, 'jobs': v} for k, v in test_steps.items() if len(v) > 1],
            'literal_test_overlap': [{'module_pattern': k, 'jobs': sorted(v)} for k, v in sorted(test_modules.items()) if len(v) > 1],
            'overlap_scope': 'Literal pytest module patterns only; source, installed, native and platform runs retain separate verification identities.',
            'platform_policy': 'Different runners/matrices remain distinct qualification lanes even when their commands match.',
            'workflows': workflows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    value = audit(args.repository)
    text = json.dumps(value, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
        print(f"{value['workflow_files']} workflows; {value['top_level_jobs']} jobs; {len(value['exact_workflow_duplicates_ignoring_display_name'])} exact duplicate groups")
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
