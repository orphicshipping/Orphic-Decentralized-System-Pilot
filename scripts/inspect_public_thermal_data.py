"""Re-fetch/inspect author-hosted research data; never emit a NET measured source.

Requires numpy and scipy. Optional isolated dependency path:
  python scripts/inspect_public_thermal_data.py --output-dir research-data --fetch
Use --fetch to recover missing original files from the pinned upstream commit.
Only original bytes are downloaded; no upstream code is executed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request

COMMIT = '194919b94b45f66309475bc88aa8e8826eb603bb'
REPO = 'robert-richardson/EKF-Battery-Impedance-Temperature'
PREFIX = 'Temperature Estimation using Impedance with Matlab - Github/'
FILES = {
    'Temperature_data.mat': PREFIX + 'Data/MainScriptData/Temperature_data.mat',
    'Voltage_current_data.mat': PREFIX + 'Data/MainScriptData/Voltage_current_data.mat',
    'Impedance_data.mat': PREFIX + 'Data/MainScriptData/Impedance_data.mat',
    'MainScript.m': PREFIX + 'MainScript.m',
    'LICENSE.txt': 'LICENSE.txt',
    'README.md': 'README.md',
}
EXPECTED = {
    'Temperature_data.mat': '2e2d650d0426ecb780cd3941884885dc4d55a389cd3e77adfae8f3e96b90d7df',
    'Voltage_current_data.mat': '5f83c6b4fb7e498e2abb9c508d3839ece049ff84a2895eea44742b2d9c1e44af',
    'Impedance_data.mat': 'af666e6dd935a588f1bce979a9b900398ca29f28d3834f27ac34c90e416d1b51',
    'MainScript.m': '327f5392805b4313a515bde035485aa867908b65014df255878013c696cb231a',
    'LICENSE.txt': 'e32c16b7c636bd65e0eb3963fb8571804bb69bd76bbc81ce2620d33fd7ce69a6',
    'README.md': '616b0eee35bcbeeA71902151e16dad4c40fb729cbc8d57460e6435c815c50090'.lower(),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--dependencies', type=Path)
    args = parser.parse_args()
    if args.dependencies:
        sys.path.insert(0, str(args.dependencies.resolve()))
    import numpy as np
    import scipy
    from scipy.io import loadmat

    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, path in FILES.items():
        url = f'https://raw.githubusercontent.com/{REPO}/{COMMIT}/' + urllib.parse.quote(path)
        local = destination / filename
        if args.fetch and not local.exists():
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError('Public source exceeds bounded download size')
            if hashlib.sha256(raw).hexdigest() != EXPECTED[filename]:
                raise ValueError('Upstream bytes differ: ' + filename)
            local.write_bytes(raw)
        raw = local.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != EXPECTED[filename]:
            raise ValueError('Local source bytes differ: ' + filename)
        entry = {'file': filename, 'url': url, 'bytes': len(raw), 'sha256': actual}
        if filename.endswith('.mat'):
            arrays = {}
            for name, value in loadmat(local).items():
                if name.startswith('__'):
                    continue
                delta = np.diff(value[:, 0])
                arrays[name] = {
                    'shape': list(value.shape),
                    'dtype': str(value.dtype),
                    'nonfinite_count_by_column': (~np.isfinite(value)).sum(axis=0).tolist(),
                    'min_by_column': np.nanmin(value, axis=0).tolist(),
                    'max_by_column': np.nanmax(value, axis=0).tolist(),
                    'time_strictly_increasing': bool(np.all(delta > 0)),
                    'time_step_min_median_max_s': np.quantile(delta, [0, .5, 1]).tolist(),
                    'equally_spaced_within_1e-9_s': bool(np.allclose(delta, delta[0], atol=1e-9, rtol=0)),
                }
            entry['arrays'] = arrays
        records.append(entry)
    report = {
        'schema': 'net.external-thermal-research-inventory.v1',
        'status': 'REAL_MEASUREMENTS_RETRIEVED_NOT_PILOT_QUALIFIED',
        'repository': REPO,
        'revision': COMMIT,
        'inspected_at_utc': datetime.now(timezone.utc).isoformat(),
        'runtime': {'python': sys.version, 'numpy': np.__version__, 'scipy': scipy.__version__},
        'files': records,
        'transformation': 'None. Byte hashing and array inspection only; no interpolation, calibration, fitting or NET source generation.',
        'assessment_file': 'docs/data/public-thermal-candidate.json',
    }
    (destination / 'inventory.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'files_verified': len(records), 'inventory': str(destination / 'inventory.json')}))


if __name__ == '__main__':
    main()
