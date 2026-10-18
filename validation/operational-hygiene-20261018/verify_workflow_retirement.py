"""Prove both old workflow step sequences survive in the shared FSRT job."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

root = Path(sys.argv[1]).resolve()
base = '9e04140ff07b6699042153e80e45e8513dcafe08'
retired = '.github/workflows/simulated-fsrt.yml'
target = '.github/workflows/synthetic-fsrt-view.yml'
load = lambda raw: yaml.load(raw, Loader=yaml.BaseLoader)
old_raw = subprocess.check_output(['git', '-C', str(root), 'show', base + ':' + retired])
prior_raw = subprocess.check_output(['git', '-C', str(root), 'show', base + ':' + target])
new_raw = (root / target).read_bytes()
old, prior, new = map(load, (old_raw, prior_raw, new_raw))
canonical = lambda step: {k: v for k, v in step.items() if k != 'name'}
steps = [canonical(s) for s in new['jobs']['combined-source']['steps']]
sequences = []
for label, workflow in [('retired_simulation', old), ('retained_views', prior)]:
    job = next(iter(workflow['jobs'].values()))
    for key in ('runs-on', 'timeout-minutes'):
        assert job[key] == new['jobs']['combined-source'][key], key
    position = -1
    matched = []
    for original in job['steps']:
        same = [i for i in range(position + 1, len(steps)) if steps[i] == canonical(original)]
        assert same, (label, original.get('name'))
        position = same[0]
        matched.append(position)
    sequences.append({'source': label, 'steps': len(job['steps']), 'matched_indices': matched})
assert new['on'] == old['on']
assert new['permissions'] == old['permissions'] == prior['permissions']
assert sum('validation/simulated_fsrt_integration.py' in step.get('run', '') for step in steps) == 1
assert not (root / retired).exists()
print(json.dumps({'status': 'PASS', 'base_revision': base, 'target': target,
    'retired_workflow': retired, 'sequences': sequences,
    'native_campaign_invocations': 1, 'original_event_scope_preserved': True,
    'original_permissions_preserved': True,
    'scope': 'Declaration-preservation proof; no native Godot or hosted execution is asserted.',
    'source_sha256': {'retired_before': hashlib.sha256(old_raw).hexdigest(),
        'retained_before': hashlib.sha256(prior_raw).hexdigest(),
        'retained_after': hashlib.sha256(new_raw).hexdigest()}}, indent=2))
