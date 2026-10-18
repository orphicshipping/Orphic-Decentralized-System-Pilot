"""Generate/check packaged discovery data from existing source declarations."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'src/ciw/integration-sources.json'


def source_id(name):
    return re.sub(r'[^a-z0-9]+', '-', re.sub(r'\s*\(`[^`]+`\)', '', name).lower()).strip('-')


def generate(root=ROOT):
    manifest = root / 'instruments/manifest.json'
    catalog = root / 'docs/SYSTEMS_CATALOG.md'
    raw = manifest.read_bytes()
    declarations = json.loads(raw)
    text = catalog.read_text()
    systems = []
    section = text.split('## Surrounding systems\n', 1)[1].split('## External tool boundary inventory', 1)[0]
    for line in section.splitlines():
        match = re.fullmatch(r'\| \[([^]]+)\]\((https://github.com/[^)]+)\) \| (.*?) \| (.*?) \|', line)
        if match:
            name, repository, role, status = match.groups()
            systems.append({'id': source_id(name), 'name': name, 'source_readme': repository,
                            'role': role, 'declared_boundary': status})
    external = []
    section = text.split('## External tool boundary inventory\n', 1)[1].split('## The collapse rule', 1)[0]
    for line in section.splitlines():
        match = re.fullmatch(r'\| ([^|]+) \| (.*?) \|', line)
        if match and match[1] != 'External tool' and not re.fullmatch(r'[- :]+', match[1]):
            external.append({'id': source_id(match[1]), 'name': match[1], 'declared_boundary': match[2],
                             'documentation': 'docs/SYSTEMS_CATALOG.md'})
    for name in ('ModelingToolkit', 'RxInfer', 'OpenFOAM', 'FreeCAD', 'TensorFlow Lattice', 'FPGA toolchains'):
        if (name.removesuffix('s') if name == 'FPGA toolchains' else name) not in text:
            raise ValueError('Candidate boundary is missing from the authoritative catalog: ' + name)
        external.append({'id': source_id(name), 'name': name, 'declared_boundary': 'candidate_provider',
                         'documentation': 'docs/SYSTEMS_CATALOG.md'})
    for name in ('OpenTelemetry', 'OpenObserve'):
        external.append({'id': source_id(name), 'name': name, 'declared_boundary': 'planning_contract_only',
                         'documentation': 'docs/OPERATIONAL_OBSERVABILITY.md'})
    external.append({'id': 'gaugelink', 'name': 'Notations GaugeLink',
                     'declared_boundary': 'independent_acquisition_contract; no implicit physical qualification or command grant',
                     'documentation': 'README.md#instruments-and-gaugelink'})
    if len(declarations['modules']) != 21 or len(systems) != 26:
        raise ValueError('Review changed source/catalog scope before regenerating integration discovery data')
    sources = [manifest, catalog, *sorted((root / 'src/ciw').glob('*runtime*.json'))]
    return {'schema': 'ciw.integration-sources.v1',
            'source_files': {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in sources},
            'modules': declarations['modules'], 'systems': systems, 'external': external}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    data = (json.dumps(generate(), indent=2, ensure_ascii=False) + '\n').encode()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != data:
            parser.exit(1, 'Packaged integration declarations are stale; run scripts/update_integration_sources.py\n')
    else:
        OUTPUT.write_bytes(data)
    print('Integration source declarations match the manifest and catalog.')


if __name__ == '__main__':
    main()
