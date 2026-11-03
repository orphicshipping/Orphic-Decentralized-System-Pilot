"""Run existing integration contracts with host-selected native policy.

This qualification harness changes policy construction only, in its own Python
process. It disables the original Python decision methods. The stdio child uses
the same selection before calling the unmodified principal_mcp launcher. It does
not change repository launch defaults or register a request-selectable backend.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--stdio', action='store_true')
    args, forwarded = parser.parse_known_args()
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    from ciw import principal_policy as policy_module
    from ciw.rust_principal_policy import RustPrincipalPolicy
    from ciw.process_policy import ProcessPolicy, executable_sha256
    process = ProcessPolicy(environment={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
        wall_seconds=3, cpu_seconds=3, memory_bytes=256 * 1024 * 1024,
        file_bytes=1048576, max_input_bytes=2097152, max_output_bytes=1048576,
        expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))
    counts = Counter()
    class SelectedNativePolicy(RustPrincipalPolicy):
        def __init__(self, reader):
            super().__init__(reader, binary=args.binary, expected_sha256=args.expected_sha256,
                             process_policy=process)
            counts['instances'] += 1

        def _decision(self, operation, **kwargs):
            counts['decision:' + operation] += 1
            return super()._decision(operation, **kwargs)

    def no_python(*unused, **kwargs):
        counts['forbidden_python_fallback'] += 1
        raise AssertionError('Python principal authorization is disabled in native qualification')

    # Keep inheritance/type compatibility, but make every Python decision fail.
    for name in ('snapshot', 'check_principal', 'authorize', 'authorize_context', 'authorize_resource', 'context'):
        setattr(policy_module.PrincipalPolicy, name, no_python)
    policy_module.PrincipalPolicy._grant = staticmethod(no_python)
    for name in ('validate_policy', '_request', '_rule', '_direct_science'):
        setattr(policy_module, name, no_python)
    policy_module.PrincipalPolicy = SelectedNativePolicy
    if args.stdio:
        from ciw.principal_mcp import main as serve
        return serve(forwarded)

    import mcp
    original_parameters = mcp.StdioServerParameters
    class NativeParameters(original_parameters):
        def __init__(self, **values):
            parameters = values.get('args', [])
            if parameters[:2] == ['-m', 'ciw.principal_mcp']:
                values['args'] = [str(Path(__file__).resolve()), '--binary', str(args.binary),
                    '--expected-sha256', args.expected_sha256, '--stdio', '--', *parameters[2:]]
            super().__init__(**values)
    mcp.StdioServerParameters = NativeParameters
    import pytest
    status = pytest.main(forwarded)
    if counts['instances'] == 0 or counts['forbidden_python_fallback']:
        status = 1
    if args.report:
        args.report.write_text(json.dumps({'schema': 'ciw.native-policy-existing-contracts.v1',
            'binary_sha256': args.expected_sha256, 'pytest_exit': int(status), 'activity': dict(counts),
            'source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'python_policy_decisions_disabled': True, 'hosted_qualification': False}, indent=2, sort_keys=True) + '\n')
    return int(status)


if __name__ == '__main__':
    raise SystemExit(main())
