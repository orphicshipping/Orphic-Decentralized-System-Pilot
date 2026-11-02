"""Retain a post-build local image identity and an actual installed-byte check.

This command uses only the local Docker daemon and --network none. It does not
pull, tag, publish, start a service or provide deployment authorization.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def record(image, context, output, base_image):
    if output.exists() or output.is_symlink():
        raise ValueError('image receipt output must be new')
    identity = json.loads((context / 'identity.json').read_text())
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', image], check=True, capture_output=True, text=True, timeout=30).stdout)[0]
    # Run the resolved immutable local ID, never a mutable tag after inspection.
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev', '--entrypoint', 'python', info['Id'], '-I', '-B', '-c',
               "import json; from ciw.private_release import verify_release; print(json.dumps(verify_release('/srv/ncp/wheel/release-manifest.json'),sort_keys=True))"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    report = {'schema': 'odsp.private-image-receipt.v1', 'status': 'failed', 'identity': identity,
              'image_id': info['Id'], 'repository_digests': info.get('RepoDigests', []),
              'base_image': base_image, 'manifest_sha256': hashlib.sha256((context / 'wheel/release-manifest.json').read_bytes()).hexdigest(),
              'container_exit_code': result.returncode, 'publication': 'not_performed', 'deployment': 'not_performed',
              'image_reproducibility': 'not_established; immutable local content identity recorded after build'}
    try:
        observed = json.loads(result.stdout)
        if result.returncode != 0 or observed != identity:
            raise ValueError('container release identity did not match selected build')
        report.update(status='passed', installed_identity=observed)
    except (ValueError, json.JSONDecodeError) as error:
        report['error'] = str(error)
        report['stderr'] = result.stderr[-4000:]
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--context', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--base-image', required=True)
    args = parser.parse_args()
    try:
        report = record(args.image, args.context, args.output, args.base_image)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'status':'refused', 'reason':str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
