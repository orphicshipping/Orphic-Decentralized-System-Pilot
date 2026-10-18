"""Read-only source-to-workflow navigation over existing installed authorities.

This module neither installs providers nor restores execution bindings. Source
imports, profile runtime pins, host grants and qualification are separate facts.
"""
from copy import deepcopy
from hashlib import sha256
from importlib import resources

from .agent_api import parse

SECTIONS = ('imports', 'systems', 'external')
QUALIFICATION = 'not_established_by_discovery'


def _data():
    raw = resources.files('ciw').joinpath('integration-sources.json').read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('Integration declarations exceed their byte budget')
    value = parse(raw)
    if value.get('schema') != 'ciw.integration-sources.v1':
        raise ValueError('Unsupported installed integration declarations')
    return value, 'sha256:' + sha256(raw).hexdigest()


def summary():
    value, identity = _data()
    return {'schema': 'ciw.integration-catalog.v1', 'catalog_ref': identity,
            'counts': {'imports': len(value['modules']), 'systems': len(value['systems']), 'external': len(value['external'])},
            'source_files': value['source_files'], 'authorizes_execution': False,
            'qualification': QUALIFICATION, 'binding_source': 'explicit_host_configuration_only'}


def _rows(section):
    if type(section) is not str or section not in SECTIONS:
        raise ValueError('Select imports, systems or external')
    value, identity = _data()
    if section == 'imports':
        from .operator_provision import catalog
        workflows = catalog()['workflows']
        rows = []
        for module in value['modules']:
            connections = []
            instruments = []
            for workflow in workflows:
                pin = workflow['providers'].get(module['role'])
                if pin is not None:
                    connections.append({'workflow': workflow['workflow'], 'operation_id': workflow['operation_id'],
                                        'runtime_pin': pin, 'external_bindings': workflow['external_bindings'],
                                        'requires_upstream_bundle': workflow['requires_upstream_bundle'],
                                        'setup_command': 'net provision --monorepo ROOT --workflow ' + workflow['workflow'] + ' --plan'})
            if module['role'] == 'cse':
                from .metrology_clearance import OP, NATIVE_TREE
                if module['import_tree'] != NATIVE_TREE:
                    raise ValueError('Imported CSE tree differs from the registered clearance profile')
                instruments.append({'instrument': 'bim-clearance', 'operation_id': OP,
                    'source_revision': module['import_revision'], 'required_source_tree': NATIVE_TREE,
                    'revision_selection': 'explicit_host_revision_and_registered_tree_check',
                    'execution_entry': 'ncp control serve --instrument bim-clearance --profile PROFILE --provider-checkout ABSOLUTE --provider-revision FULL_SHA',
                    'qualification': QUALIFICATION})
            elif module['role'] == 'fsrt':
                from .leakage_native import PIN, SOURCE_TREE
                instruments.append({'instrument': 'leakage', 'source_revision': PIN,
                    'required_source_tree': SOURCE_TREE, 'revision_selection': 'fixed_registered_profile_pin',
                    'execution_entry': 'ncp control serve --instrument leakage --profile PROFILE --provider-checkout ABSOLUTE',
                    'qualification': QUALIFICATION})
            rows.append({**module, 'workflow_connections': connections,
                         'instrument_connections': instruments,
                         'default_activation': False, 'binding_status': 'explicit_host_binding_required' if connections else 'independent_client_or_source_boundary',
                         'runtime_revision_scope': 'module_default_declaration; workflow pins may differ'})
    else:
        rows = value[section]
    return sorted([{**deepcopy(row), 'authorizes_execution': False, 'qualification': QUALIFICATION}
                   for row in rows], key=lambda row: row['id']), identity


def listing(section='imports', *, offset=0, limit=16):
    if type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError('Require bounded integer pagination')
    rows, identity = _rows(section)
    selected = rows[offset:offset + limit]
    end = offset + len(selected)
    return {'section': section, 'catalog_ref': identity, 'items': selected, 'total': len(rows),
            'next_offset': end if end < len(rows) else None,
            'authorizes_execution': False, 'qualification': QUALIFICATION}


def describe(section, source_id):
    if type(source_id) is not str or not 1 <= len(source_id) <= 160:
        raise ValueError('Require an exact catalog source ID')
    rows, identity = _rows(section)
    matches = [row for row in rows if row['id'] == source_id]
    if len(matches) != 1:
        raise ValueError('Unknown integration source ID')
    return {'section': section, 'catalog_ref': identity, 'item': matches[0],
            'authorizes_execution': False, 'qualification': QUALIFICATION}
