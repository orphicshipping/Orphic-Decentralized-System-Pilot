"""Installed numerical provider identity; metadata discovery executes no models."""
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import platform
import re

SOURCES = ('numerical_contracts.py','numerical_runtime.py','numerical_kernels.py',
           'numerical_records.py','numerical_workflow.py','core/covariance.py')
SCOPE = 'bounded_supplied_model_computation_no_physical_qualification'


def runtime_identity():
    root = Path(__file__).parent
    return {'provider':'ciw.numerical', 'version':'1',
        'source_files_sha256': {name:sha256((root/name).read_bytes()).hexdigest() for name in SOURCES},
        'environment': {'python':platform.python_version(), 'numpy':version('numpy')},
        'scope':SCOPE}


def validate_runtime(value, *, allow_absent=False):
    """Check retained declarations without requiring the old runtime to be installed."""
    if value is None and allow_absent:
        return
    if (type(value) is not dict or set(value) != {'provider','version','source_files_sha256','environment','scope'}
            or value['provider'] != 'ciw.numerical' or value['version'] != '1' or value['scope'] != SCOPE):
        raise ValueError('Invalid numerical runtime declaration')
    hashes = value['source_files_sha256']
    if (type(hashes) is not dict or set(hashes) != set(SOURCES)
            or any(type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None for v in hashes.values())):
        raise ValueError('Missing numerical source commitments')
    env = value['environment']
    if (type(env) is not dict or set(env) != {'python','numpy'}
            or any(type(v) is not str or not 1 <= len(v) <= 128 for v in env.values())):
        raise ValueError('Invalid numerical environment declaration')
