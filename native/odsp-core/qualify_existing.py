"""Run the existing numerical suite with native execution and Python computation disabled.

Qualification helper only: file hashes measured here record the tested inputs;
they do not approve a binary for production or create a deployment binding.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--lapack-directory', type=Path, required=True)
    parser.add_argument('--junit', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if not args.binary.is_absolute() or not args.lapack_directory.is_absolute():
        parser.error('Explicit absolute native input paths are required')
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root/'src'), str(root/'tests')]
    import pytest
    from ciw.process_policy import ProcessPolicy, executable_sha256
    from ciw.rust_numerical import RustNumericalBackend
    import ciw.numerical_workflow as workflow
    libraries = {path: sha256(path.read_bytes()).hexdigest()
                 for prefix in ('libscipy_openblas64_', 'libgfortran-', 'libquadmath-')
                 for path in args.lapack_directory.glob(prefix+'*')}
    backend = RustNumericalBackend(args.binary, sha256(args.binary.read_bytes()).hexdigest(),
        process_policy=ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'),
            wall_seconds=10, cpu_seconds=10, memory_bytes=256*1024*1024,
            max_input_bytes=512*1024, max_output_bytes=2*1024*1024, file_bytes=0),
        lapack_libraries=libraries)
    counts = {}

    def execute(operation, run, parameters):
        counts[operation] = counts.get(operation, 0)+1
        return backend.execute(operation, run, parameters)

    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, 'execute', side_effect=execute))
        stack.enter_context(patch.object(workflow, 'runtime_identity', side_effect=backend.runtime_identity))
        stack.enter_context(patch('ciw.numerical_kernels.calculate',
            side_effect=AssertionError('Python numerical computation is disabled for native qualification')))
        code = pytest.main(['-q', str(root/'tests/test_builtin_numerical.py'), '--junitxml='+str(args.junit)])
    receipt = {'schema': 'odsp.native-numerical-local-qualification.v1', 'exit_code': int(code),
        'selected_backend': backend.runtime_identity(), 'execute_attempts_by_operation': counts,
        'hosted_qualified': False, 'physical_qualified': False, 'formal_proof_established': False}
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2)+'\n')
    print(json.dumps({'exit_code': int(code), 'execute_attempts_by_operation': counts,
                     'binary_sha256': backend.expected_sha256, 'receipt': str(args.receipt)}, sort_keys=True))
    return int(code)


if __name__ == '__main__':
    raise SystemExit(main())
