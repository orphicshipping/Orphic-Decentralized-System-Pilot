"""Fixed Blender candidate annotation recipe; stdlib-only until explicitly executed.

Run under operator-selected Blender --background --disable-autoexec --python ...
with explicit source/view/asset hashes. Opens an existing reviewed .blend copy;
never imports STEP or generates authoritative geometry. No NET acceptance claim.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
MAX_FILE = 2 * 1024 * 1024


def read_pinned(path, expected):
    path = Path(path).absolute()
    if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
        raise ValueError('Require operator-held SHA-256')
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('Require an ordinary reviewed input file')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE or 'sha256:' + hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Input bytes differ or exceed bounded profile')
    return raw


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def annotations(raw, asset_ref):
    if len(raw) > MAX_FILE or not isinstance(asset_ref, str) or not DIGEST.fullmatch(asset_ref):
        raise ValueError('Invalid pinned visual input')
    view = json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if type(view) is not dict or view.get('schema') != 'ciw.sensor-view.v1':
        raise ValueError('Expected existing retained view export')
    if view.get('mode') not in ('REPLAY', 'SIMULATION', 'COUNTERFACTUAL'):
        raise ValueError('Candidate annotations cannot claim LIVE')
    if type(view.get('claims')) is not dict or view['claims'].get('equipment_control') is not False:
        raise ValueError('No equipment-control authority')
    for key in ('evidence_id', 'source_snapshot_ref'):
        if not isinstance(view.get(key), str) or not DIGEST.fullmatch(view[key]): raise ValueError('Missing source identity')
    if type(view.get('bindings')) is not list or len(view['bindings']) > 128:
        raise ValueError('Binding budget')
    output = {}
    for item in view['bindings']:
        if item.get('asset_ref') != asset_ref: continue
        node = item['binding']['node']
        if type(node) is not str or not node or node.startswith('/') or any(c in node for c in ('\\', ':', '\x00')) or any(p in ('', '.', '..') for p in node.split('/')):
            raise ValueError('Invalid explicit node path')
        if node in output: raise ValueError('Ambiguous visual binding')
        output[node] = {'notations_evidence_id':view['evidence_id'],
                        'notations_snapshot_ref':view['source_snapshot_ref'],
                        'notations_asset_ref':asset_ref, 'notations_mode':view['mode'],
                        'notations_authority':'representation_only'}
    if not output: raise ValueError('No bindings for the operator-selected asset')
    return deepcopy(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'view', 'output', 'report'): parser.add_argument('--'+name, type=Path, required=True)
    for name in ('source-ref', 'view-ref', 'asset-ref'): parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    source_bytes = read_pinned(args.source, args.source_ref)
    view_bytes = read_pinned(args.view, args.view_ref)
    properties = annotations(view_bytes, args.asset_ref)
    for path in (args.output, args.report):
        if path.exists() or any(p.is_symlink() for p in (path.absolute(), *path.absolute().parents)):
            raise ValueError('Candidate/report outputs must be new nonlinked paths')
    if args.output.absolute() == args.report.absolute(): raise ValueError('Distinct output paths required')
    import bpy  # Only the explicitly selected native Blender executes this recipe.
    bpy.ops.wm.open_mainfile(filepath=str(args.source.resolve()), load_ui=False, use_scripts=False)
    mapped, missing = [], []
    def object_path(obj):
        names = [obj.name]; current = obj.parent
        while current is not None:
            names.append(current.name); current = current.parent
        return '/'.join(reversed(names))
    objects = {object_path(obj):obj for obj in bpy.data.objects}
    for node, attrs in properties.items():
        if node not in objects: missing.append(node); continue
        for key, value in attrs.items(): objects[node][key] = value
        mapped.append(node)
    # Do not overwrite the authored source; this is a new reviewable .blend.
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.absolute()), copy=True)
    if read_pinned(args.source, args.source_ref) != source_bytes or read_pinned(args.view, args.view_ref) != view_bytes:
        raise ValueError('Original input drift')
    if args.output.stat().st_size > MAX_FILE: raise ValueError('Candidate exceeds this bounded retention profile')
    raw = args.output.read_bytes()
    report = {'schema':'ciw.blender-sensor-candidate.v1','source_ref':args.source_ref,'view_ref':args.view_ref,
              'asset_ref':args.asset_ref,'candidate_ref':'sha256:'+hashlib.sha256(raw).hexdigest(),
              'blender_version':'.'.join(map(str,bpy.app.version)),
              'recipe_ref':'sha256:'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'mapped':mapped,'missing':missing,
              'geometry_equivalence':'not_established','manufacturing_approval':False,'equipment_control':False,
              'scope':'candidate metadata annotation only; no geometry edits or glTF re-export'}
    with args.report.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    return report


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
