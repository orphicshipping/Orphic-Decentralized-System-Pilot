"""Configure and serve the bounded GaugeLink uncertainty refit through MCP or CLI."""
from pathlib import Path
import argparse
import json
import sys

from .board_refit_cli import safe_path, _binding, _client_config
from .control_contracts import load, save_new, keys, text
from .core.identities import content_identity
from .operations.runner import seal, check_seal
from .link_refit import GaugeLinkRefitProfile, plan_for, runtime_identity

SCHEMA = 'ciw.gaugelink-refit-profile.v1'


def _read(path):
    path = safe_path(path, existing=True)
    descriptor = load(path)
    keys(descriptor, {'schema','record_digest','investigation_ref','bundle','plan','board',
                      'runtime','grant_path','grant_ref'})
    check_seal(descriptor)
    if descriptor['schema'] != SCHEMA or descriptor['runtime'] != runtime_identity():
        raise ValueError('Profile or provider identity changed; create a separately reviewed profile')
    text(descriptor['investigation_ref'])
    values = {}
    for name in ('bundle','plan','board'):
        keys(descriptor[name], {'sha256'})
        values[name] = load(safe_path(path.parent / (name + '.json'), existing=True),
                            expected_sha256=descriptor[name]['sha256'])
    profile = GaugeLinkRefitProfile(values['bundle'], values['plan'])
    if values['board'] != profile.board():
        raise ValueError('Board differs from the selected GaugeLink profile')
    if not Path(descriptor['grant_path']).is_absolute():
        raise ValueError('Operator grant path must be absolute')
    grant = load(safe_path(descriptor['grant_path'], existing=True))
    if content_identity(grant) != descriptor['grant_ref']:
        raise ValueError('Grant changed or revoked')
    return path, descriptor, profile, values['board'], grant


def open_host(path):
    from .board_refit import BoardRefitHost
    path, descriptor, profile, board, grant = _read(path)
    def authority_check():
        # Re-read bounded input pins too; clients cannot refresh a selected source.
        for name in ('bundle','plan','board'):
            load(safe_path(path.parent / (name + '.json'), existing=True),
                 expected_sha256=descriptor[name]['sha256'])
        current = load(safe_path(descriptor['grant_path'], existing=True))
        if content_identity(current) != descriptor['grant_ref']:
            raise PermissionError('Operator grant changed or revoked')
        if load(safe_path(path, existing=True)) != descriptor:
            raise PermissionError('Selected profile changed')
    runtime = safe_path(path.parent / 'runtime')
    return BoardRefitHost(board, runtime, source=profile.source, grant=grant,
        authority_check=authority_check, resume=runtime.exists(),
        investigation_ref=descriptor['investigation_ref'], execution_profile=profile)


def prepare(bundle_path, output, instance_id, objective):
    """Prepare exact Board/source bindings for an operator to issue a grant."""
    bundle = load(safe_path(bundle_path, existing=True))
    plan = plan_for(bundle, instance_id=instance_id, max_standard_uncertainty=objective)
    profile = GaugeLinkRefitProfile(bundle, plan)
    output = safe_path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('bundle',bundle), ('plan',plan), ('board',profile.board()), ('source',profile.source)]:
        save_new(output / (name + '.json'), value)
    return {'status':'prepared', 'board_ref':profile.board()['record_digest'],
            'source_evidence_id':profile.source['evidence_id'], 'source_ref':content_identity(profile.source),
            'providers_executed':False, 'grant_created':False}


def configure(prepared, grant_path, investigation_ref):
    prepared = safe_path(prepared)
    text(investigation_ref)
    bindings = {name: _binding(prepared / (name + '.json'))[0] for name in ('bundle','plan','board')}
    grant_path = safe_path(grant_path, existing=True)
    descriptor = seal({'schema':SCHEMA, 'investigation_ref':investigation_ref, **bindings,
        'runtime':runtime_identity(), 'grant_path':str(grant_path), 'grant_ref':content_identity(load(grant_path))})
    path = prepared / 'profile.json'
    save_new(path, descriptor)
    with open_host(path) as host:
        host.descriptions()
    config = _client_config(path, module='ciw.link_refit_cli', name='ncp-gaugelink-refit')
    save_new(prepared / 'mcp-config.json', config)
    return {'status':'configured', 'profile':str(path), 'mcp_config':str(prepared / 'mcp-config.json'),
            'providers_executed':False, 'activated':False, 'transport':'stdio'}


def demo(output):
    # Only this explicit synthetic demonstration command creates an example grant.
    from . import link_study_io, link_measurements
    from .link_study_cli import example
    output = safe_path(output)
    output.mkdir(parents=True, exist_ok=False)
    link_study_io.run(example(), output / 'synthetic-study')
    save_new(output / 'measurement.json', link_measurements.build(output / 'synthetic-study'))
    prepared = output / 'profile'
    state = prepare(output / 'measurement.json', prepared, 'synthetic.station', .03)
    grant = {'schema':'ciw.board-refit-grant.v1', 'grant_id':'synthetic-station-uncertainty-demo',
        **{key:state[key] for key in ('board_ref','source_evidence_id','source_ref')}, 'allow_baseline':True,
        'targets':[{'node_id':'hypothesis','parameter':'offset_scale'}], 'max_candidate_runs':4,'max_previews':16}
    save_new(output / 'grant.json', grant)
    value = configure(prepared, output / 'grant.json', 'demo:synthetic-station-refit')
    save_new(prepared / 'example-preview.json', {'base_board_ref':state['board_ref'],
        'node_id':'hypothesis','parameter':'offset_scale','replacement':.25})
    return {**value, 'synthetic':True, 'synthetic_source_created':True,
            'providers_executed':True, 'refit_providers_executed':False,
            'acquisition_performed':False, 'physical_qualification':'not_established'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ncp gaugelink-refit', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepared = commands.add_parser('prepare', help='Bind retained evidence and a fixed objective; creates no grant')
    prepared.add_argument('--bundle', type=Path, required=True)
    prepared.add_argument('--output-dir', type=Path, required=True)
    prepared.add_argument('--instance-id', required=True)
    prepared.add_argument('--max-standard-uncertainty', type=float, required=True)
    configured = commands.add_parser('configure', help='Bind a pre-existing operator grant')
    configured.add_argument('--prepared', type=Path, required=True)
    configured.add_argument('--grant', type=Path, required=True)
    configured.add_argument('--investigation-ref', required=True)
    demonstration = commands.add_parser('demo', help='Explicitly create a synthetic Station fixture and example grant')
    demonstration.add_argument('--output-dir', type=Path, required=True)
    for command in ('serve','status','call'):
        selected = commands.add_parser(command)
        selected.add_argument('--profile', type=Path, required=True)
        if command == 'call':
            selected.add_argument('--tool', required=True)
            selected.add_argument('--arguments', type=Path, required=True, help='JSON argument file for the same MCP domain tools')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            value = prepare(args.bundle, args.output_dir, args.instance_id, args.max_standard_uncertainty)
        elif args.command == 'configure':
            value = configure(args.prepared, args.grant, args.investigation_ref)
        elif args.command == 'demo':
            value = demo(args.output_dir)
        else:
            with open_host(args.profile) as host:
                if args.command == 'serve':
                    import anyio
                    from .board_refit_mcp import serve_stdio
                    anyio.run(serve_stdio, host)
                    return 0
                value = host.call('ncp_board_status', {}) if args.command == 'status' else host.call(
                    args.tool, load(safe_path(args.arguments, existing=True)))
        print(json.dumps(value, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError) as exc:
        print(json.dumps({'status':'refused', 'reason':str(exc)[:300]}), file=sys.stderr)
        return 2
    except ImportError:
        print(json.dumps({'status':'refused','reason':'Install the board-mcp optional dependency'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
