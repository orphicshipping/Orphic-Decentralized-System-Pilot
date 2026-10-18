"""Inventory workflow declarations and literal test overlap without merging gates.

Runner/platform, event, environment, permissions and artifact differences remain
part of identity. Similar commands are evidence for review, never deletion rules.
"""
from collections import defaultdict
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex

import yaml


def _python_argument(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'str' and len(node.args) == 1):
        path = node.args[0]
        if (isinstance(path, ast.BinOp) and isinstance(path.op, ast.Div)
                and isinstance(path.right, ast.Constant) and isinstance(path.right.value, str)
                and re.search(r'\.py(?:::|$)', path.right.value)):
            return '{path}/' + path.right.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _python_argument(node.left)
        if left and left.startswith('-'):
            return left + '{dynamic}'
    return None


def pytest_declarations(command, shell=''):
    """Read declarations without evaluating shell/Python or guessing dynamic lists.

    Copied fixture names and pip-installed pytest are never test invocations.
    Literal Python subprocess arrays are supported; computed arrays remain open.
    """
    invocations, unresolved, python_blocks = [], [], []
    lines = command.splitlines()
    direct = []
    index = 0
    if shell.startswith('python'):
        python_blocks.append(command)
    else:
        while index < len(lines):
            line = lines[index]
            match = re.search(r'\bpython[0-9.]*\s+-\s+<<\s*[\'"]?([A-Za-z_][A-Za-z0-9_]*)', line)
            if match:
                end = index + 1
                while end < len(lines) and lines[end].strip() != match[1]:
                    end += 1
                python_blocks.append('\n'.join(lines[index + 1:end]))
                index = end + 1
            else:
                direct.append(line)
                index += 1
        joined = re.sub(r'(?:\\|`)\n\s*', ' ', '\n'.join(direct))
        for line in joined.splitlines():
            if not re.search(r'(?:\bpython[0-9.]*|\$[A-Za-z_][A-Za-z0-9_]*)\s+.*?-m\s+pytest\b|^\s*pytest\b', line):
                continue
            try:
                lexer = shlex.shlex(line, posix=True, punctuation_chars=';&|<>')
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError:
                unresolved.append({'reason': 'shell_tokenization', 'declaration': line})
                continue
            if 'pytest' not in tokens:
                continue
            prefix = tokens[:tokens.index('pytest')]
            if prefix and prefix[0] == '&':
                prefix = prefix[1:]
            if prefix and prefix[0] == 'env':
                prefix = prefix[1:]
                while prefix and (prefix[0].startswith('-') or '=' in prefix[0]):
                    prefix = prefix[2:] if prefix[0] in {'-u', '--unset'} else prefix[1:]
            while prefix and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', prefix[0]):
                prefix = prefix[1:]
            executable = prefix[0] if prefix else 'pytest'
            if not re.search(r'(?:^|[/\\])python[0-9.]*(?:exe)?$|^\$[A-Za-z_][A-Za-z0-9_]*$', executable) and executable != 'pytest':
                unresolved.append({'reason': 'unsupported_shell_prefix', 'declaration': line})
                continue
            start = tokens.index('pytest')
            tail = tokens[start + 1:]
            for stop in ('&&', '||', ';', '>', '2>'):
                if stop in tail:
                    tail = tail[:tail.index(stop)]
            invocations.append({'language': 'shell', 'argv': tokens[:start + 1] + tail,
                                'pytest_arguments': tail, 'selection_complete': not any('$' in x for x in tail if not x.startswith('-'))})
    for block in python_blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:
            unresolved.append({'reason': 'python_syntax', 'declaration': block})
            continue
        found = False
        for call in ast.walk(tree):
            if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute)
                    or not isinstance(call.func.value, ast.Name) or call.func.value.id != 'subprocess'
                    or call.func.attr not in {'run', 'check_call', 'check_output', 'Popen'} or not call.args):
                continue
            values = call.args[0]
            if not isinstance(values, (ast.List, ast.Tuple)):
                if re.search(r"['\"]pytest['\"]", block):
                    unresolved.append({'reason': 'computed_subprocess_command_in_pytest_step', 'declaration': ast.unparse(call)})
                continue
            args = [_python_argument(x) for x in values.elts]
            positions = [i for i in range(1, len(args)) if args[i - 1:i + 1] == ['-m', 'pytest']]
            if not positions:
                continue
            found = True
            start = positions[0]
            tail = args[start + 1:]
            invocations.append({'language': 'python_literal_subprocess', 'argv': args,
                                'pytest_arguments': tail, 'selection_complete': None not in tail and not any('{path}' in x for x in tail if isinstance(x, str)),
                                'cwd_declaration': ast.unparse(next((k.value for k in call.keywords if k.arg == 'cwd'), ast.Constant(value=None)))})
        if not found and re.search(r"['\"]pytest['\"]", block):
            unresolved.append({'reason': 'computed_python_invocation', 'declaration': block})
    return invocations, unresolved


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _selectors(root, arguments):
    selected, other = {}, []
    for item in arguments:
        if not isinstance(item, str) or item.startswith('-') or not re.search(r'\.py(?:::|$)', item):
            continue
        pattern, _, node = item.partition('::')
        for prefix in ('${{ github.workspace }}/', '$GITHUB_WORKSPACE/'):
            if pattern.startswith(prefix):
                pattern = pattern[len(prefix):]
        if Path(pattern).is_absolute() or '..' in pattern.replace('\\', '/').split('/'):
            other.append(item)
            continue
        if pattern.startswith('tests/'):
            matches = sorted(root.glob(pattern))
            kind = 'explicit_repository_path'
        elif '/' not in pattern and '\\' not in pattern:
            matches = sorted((root / 'tests').glob(pattern))
            kind = 'basename_reference_not_content_attestation'
        elif pattern.startswith('{path}/'):
            matches = sorted((root / 'tests').glob(Path(pattern).name))
            kind = 'computed_path_filename_correlation_not_content_attestation'
        else:
            matches, kind = [], 'outside_top_level_tests'
        if not matches:
            other.append(item)
        for path in matches:
            if path.is_file() and not path.is_symlink():
                selected[path.relative_to(root).as_posix()] = {'selector': item, 'node_selector': node or None, 'mapping': kind}
    return selected, other


def test_lane_review(root, workflows):
    """Retain each declared invocation's context and conservative merge disposition."""
    root = Path(root)
    invocations, unresolved, modules = [], [], defaultdict(list)
    for relative, value in workflows:
        for job_id, job in value['jobs'].items():
            lane = relative + ':' + job_id
            steps = job.get('steps', [])
            artifact_contracts = [s for s in steps if 'upload-artifact@' in s.get('uses', '')]
            for step_index, step in enumerate(steps):
                command = step.get('run', '')
                found, opaque = pytest_declarations(command, step.get('shell', ''))
                for row in opaque:
                    unresolved.append({'lane': lane, 'step': step_index, **row})
                for invocation_index, invocation in enumerate(found):
                    identity = lane + f':step-{step_index}:pytest-{invocation_index}'
                    selected, other = _selectors(root, invocation['pytest_arguments'])
                    prefix = [{k: v for k, v in s.items() if k != 'name'} for s in steps[:step_index]]
                    compute_args = []
                    skip = False
                    for item in invocation['argv']:
                        if skip:
                            skip = False
                            continue
                        if item in {'--junitxml', '--junit-xml'}:
                            skip = True
                        elif not isinstance(item, str) or not item.startswith(('--junitxml=', '--junit-xml=')):
                            compute_args.append(item)
                    dimensions = {
                        'platform_matrix': {'runs_on': job.get('runs-on'), 'strategy': job.get('strategy')},
                        'permissions': {'workflow': value.get('permissions', 'repository_default_undeclared'), 'job': job.get('permissions')},
                        'environment': {'workflow': value.get('env'), 'job': job.get('env'), 'step': step.get('env')},
                        'routing': {'events': value.get('on'), 'job_if': job.get('if'), 'step_if': step.get('if')},
                        'defaults_shell_cwd': {'workflow': value.get('defaults'), 'job': job.get('defaults'), 'step_shell': step.get('shell'), 'step_cwd': step.get('working-directory'), 'python_cwd': invocation.get('cwd_declaration')},
                        'provider_and_package_setup': prefix,
                        'pytest_collection_and_flags': compute_args,
                        'step_commands_and_import_checks': command,
                        'evidence_contracts': artifact_contracts}
                    row = {'id': identity, 'lane': lane, 'step': step_index,
                        'step_name': step.get('name'), **invocation, 'selected_modules': selected,
                        'other_file_selectors': other, 'dimensions': dimensions,
                        'dimension_sha256': {k: _identity(v) for k, v in dimensions.items()},
                        'execution': 'not_performed_by_static_review'}
                    row['collection_scope'] = ('explicit_file_selectors' if selected or other else 'default_or_computed_collection')
                    row['origin_declarations'] = {
                        'isolated_python_flag': '-I' in invocation['argv'],
                        'installed_package_assertion_in_step': 'site-packages' in command,
                        'wheel_install_in_step_or_prefix': any('pip' in s.get('run', '') and re.search(r'dist/|dist\)|wheel', s.get('run', '')) for s in [*steps[:step_index], step]),
                        'content_identity_verified_by_this_review': False}
                    invocations.append(row)
                    for module in selected:
                        modules[module].append(row)
    overlap = []
    for module, occurrences in sorted(modules.items()):
        if len({x['lane'].split(':')[0] for x in occurrences}) < 2:
            continue
        differing = [key for key in occurrences[0]['dimensions'] if len({x['dimension_sha256'][key] for x in occurrences}) > 1]
        complete = all(x['selection_complete'] for x in occurrences)
        disposition = 'retain_distinct_declared_contexts' if differing else 'manual_review_identical_declarations'
        if not complete:
            disposition = 'retain_incomplete_selection_requires_review'
        overlap.append({'module': module, 'workflow_count': len({x['lane'].split(':')[0] for x in occurrences}),
            'module_source_sha256': hashlib.sha256((root / module).read_bytes()).hexdigest(),
            'invocation_ids': [x['id'] for x in occurrences], 'differing_dimensions': differing,
            'selection_complete': complete, 'disposition': disposition,
            'reason': 'A repeated module name does not equate its collection, provider, import, platform or evidence context. No execution is removed by this report.'})
    return {'scope': 'Direct shell and literal Python-subprocess pytest declarations. Broad/default collections and opaque computed arrays are not expanded or silently treated as duplicates. This is declaration analysis, not proof that a lane is necessary or executed.',
            'invocations': invocations, 'unresolved_declarations': unresolved,
            'incomplete_invocation_ids': [x['id'] for x in invocations if not x['selection_complete']],
            'default_or_computed_invocation_ids': [x['id'] for x in invocations if x['collection_scope'] == 'default_or_computed_collection'],
            'overlapping_modules': overlap, 'overlapping_module_count': len(overlap),
            'automatic_removals': [], 'qualification': 'not_established_by_static_review'}


def audit(root):
    root = Path(root)
    policy = json.loads((root / 'release/qualification-policy.json').read_text())
    required = {x['path'] for x in policy['required_workflows']}
    workflows = []
    wf_groups = defaultdict(list)
    job_groups = defaultdict(list)
    test_modules = defaultdict(set)
    test_steps = defaultdict(list)
    declarations = []
    for path in sorted((root / '.github/workflows').iterdir()):
        if path.suffix not in {'.yml', '.yaml'}:
            continue
        raw = path.read_bytes()
        value = yaml.load(raw, Loader=yaml.BaseLoader)
        if not isinstance(value, dict) or not isinstance(value.get('jobs'), dict):
            raise ValueError('Invalid workflow: ' + str(path))
        relative = path.relative_to(root).as_posix()
        declarations.append((relative, value))
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
            'overlap_scope': 'Legacy lexical mentions within steps containing pytest, including copied fixtures; these are not actual invocation identities. Use test_lane_review for declared invocations.',
            'test_lane_review': test_lane_review(root, declarations),
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
