"""Prepare, inspect, collect and serve personal operational investigations."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from . import link_contract as c
from .agent_api import encode, parse, identifier
from .control_contracts import keys, save_new
from .control_plane import experiment
from .core.identities import content_identity
from .link_store import Journal, safe_path
from .operational_store import Records
from . import operational_context as ctx
from . import operational_engineering as eng


def prepare(directory, *, investigation_id, sources, models=None, templates=None, grants=None, journal=None, supervision=None, providers=None):
    """Trusted operator admission. New arrivals make a new immutable generation.

    sources are exact bytes plus connective declarations, never MCP arguments.
    Existing Journal schemas and source contracts are left unchanged.
    """
    identifier(investigation_id)
    root=safe_path(directory); root.mkdir(parents=True,exist_ok=True)
    if type(sources) is not dict or not 1<=len(sources)<=16: raise ValueError('Admit 1..16 source bindings')
    compiled={}
    for alias, item in sources.items():
        identifier(alias); 
        if set(item)-{'raw','family','context','configurations','parent_receipts'} or not {'raw','family','context','configurations'}<=item.keys(): raise ValueError('Invalid source admission fields')
        binding=ctx.make_binding(item['raw'],item['family'],item['context'],item['configurations'],item.get('parent_receipts',[]))
        compiled[alias]=(item['raw'],binding)
    path=safe_path(journal or root/'operational.sqlite')
    with Journal(path,create=not path.exists()) as db:
        records=Records(db); source_refs={}; engines={}
        for alias,(raw,binding) in compiled.items():
            for ref in binding['parent_receipts']:
                segment=records.get(ref)
                if segment.get('schema')!='ncp.operational-segment.v1' or segment['source_ref']!=ctx.reference(raw): raise ValueError('Source admission receipt differs')
            source_refs[alias]={'raw':records.put_bytes(raw),'binding':records.put(binding)}
        for alias, model in (models or {}).items():
            identifier(alias); keys(model, {'source','engine','policy','request'})
            selected=source_refs[model['source']]
            raw=compiled[model['source']][0] if model['request'] is None else model['request']
            run=eng.make_source(raw,engine=model['engine'],binding_ref=selected['binding'],policy=model['policy'])
            if model['engine']=='thermal' and raw!=compiled[model['source']][0]: raise ValueError('Thermal source substitution refused')
            if model['engine']=='reference' and encode(parse(raw)['right'])!=compiled[model['source']][0]:
                raise ValueError('Reference observation substitution refused')
            if model['engine'] in {'scalar','calibrated-window','residual-monitor'}:
                from .operational_native import binds
                if not binds(raw,model['engine'],compiled[model['source']][0]): raise ValueError('Native source substitution refused')
            graph=experiment('operational-'+alias,model_id=content_identity(run['metadata']['operational']),
                parent_checkpoint=selected['binding'],nodes=[{'node_id':'assessment','operation_id':eng.ASSESS,
                    'parameters':{},'inputs':{},'depends_on':[]},
                    {'node_id':'verification','operation_id':eng.VERIFY,'parameters':{},
                     'inputs':{'assessment':{'node_id':'assessment','port':'result'}},'depends_on':[]}])
            engines[alias]={'source':model['source'],'run':records.put(run),'graph':records.put(graph)}
        inv={'schema':'ncp.operational-investigation.v1','id':investigation_id,'sources':source_refs,'engines':engines,
            'templates':deepcopy(templates or {}), 'supervision':deepcopy(supervision or {'alarms':{},'configuration_candidates':{}}), 'admission':'operator_selected_closed_source_snapshots',
            'authority':deepcopy(ctx.AUTHORITY)}
        ref=records.put(inv)
        key='oe-current:'+investigation_id; previous=db.state(key)
        db.compare_and_set_state(key,0 if previous is None else previous['revision'],{'investigation':ref})
    suffix=ref[7:23]
    profile={'schema':'ncp.operational-host.v1','journal':os.path.relpath(path,root),'investigation':ref,
        'execution_output':'execution-'+suffix,'grants':grants if grants is not None else ['inspect','derive','numerical','prepare','annotate']}
    if providers is not None: profile['providers']=deepcopy(providers)
    profile_path=root/('profile-'+suffix+'.json')
    if profile_path.exists():
        if parse(profile_path.read_bytes())!=profile: raise ValueError('Existing immutable host profile differs')
    else: save_new(profile_path,profile)
    config={'mcpServers':{'ncp-operational':{'command':sys.executable,'args':['-I','-m','ciw.net','operational','serve',
        '--profile',str(profile_path.absolute()),'--sdk','--resume']}}}
    # --resume is only used after the one explicit initialization below in the guide.
    config_path=root/('mcp-'+suffix+'.json')
    if config_path.exists() and parse(config_path.read_bytes())!=config:
        # Keep earlier generated launch files as evidence; create the corrected
        # isolated launch with an explicit content suffix rather than reuse it.
        config_path=root/('mcp-'+suffix+'-'+content_identity(config)[7:15]+'.json')
    if not config_path.exists(): save_new(config_path,config)
    return profile_path


def admit_segment(directory, *, journal, segment, family, context, configurations, investigation_id, models=None, templates=None):
    with Journal(journal) as db:
        records=Records(db);receipt=records.get(segment)
        if receipt.get('schema')!='ncp.operational-segment.v1': raise ValueError('Closed retained segment receipt required')
        raw=records.bytes(receipt['source_ref'])
    return prepare(directory,investigation_id=investigation_id,journal=journal,
        sources={'segment':{'raw':raw,'family':family,'context':context,'configurations':configurations,'parent_receipts':[segment]}},
        models=models,templates=templates)


def read_source(path):
    with safe_path(path).open('rb') as stream:raw=stream.read(ctx.MAX_SOURCE+1)
    if not 0<len(raw)<=ctx.MAX_SOURCE:raise ValueError('Source exceeds bounded segment budget')
    return raw


def default_context(**changes):
    value={'producer':'fixture','acquisition_host':'fixture-host','boot':'boot-1','clock':'fixture.clock',
        'time_uncertainty':0.0,'receipt_clock':'host.unix.ns','origin':'test_fixture',
        'calibration':{'status':'unknown'},'completeness':'closed_with_limits','connectivity':'unknown',
        'gaps':[],'material_process':{}}
    return {**value,**changes}


def configuration(name, value, *, effective=(0.0,0.0), clock='fixture.clock',boot='boot-1'):
    return {'id':name,'revision':'1','raw':c.packed(encode(value),16384),'clock':clock,'boot':boot,
        'effective':list(effective),'basis':'operator_declared','observed_fields':[]}


def default_template():
    return {'kind':'retained_analysis','operation':eng.ASSESS,'settings':{'selection':'same_frozen_recording'},
        'alternatives':['model/input discrepancy','measurement/calibration discrepancy'],
        'distinguishing_outcomes':['independent reference resolves sensor disagreement','input timing explains residual'],
        'controlled':['model and fixed decision policy'],'observed':['paired residuals','reference quality'],
        'confounders':['shared calibration','unresolved clock mapping'],
        'procedure':['review independent calibration and clock evidence','admit a new source binding if evidence changes',
                     'request explicit authorized numerical execution with a fresh attempt'],
        'prerequisites':['independent reference evidence','complete total joint covariance for definitive support'],
        'required_grants':['inspect','derive','numerical'], 'limits':{'max_executions':1,'device_commands':0,'gpu_cutoff_c':80},
        'decision_rule':'fixed operator comparison policy before evaluation',
        'prediction_limit':'No quantitative information gain or unique causal identification is predicted.'}


def example(directory, *, case='pass'):
    from .reference_comparison import example_request
    request=example_request(case)
    raw=encode(request['right'])
    return prepare(directory,investigation_id='personal-example',sources={'recorded':{
        'raw':raw,'family':'observations','context':default_context(clock='synthetic.clock',origin='synthetic'),
        'configurations':[configuration('configuration-1',{'power':10,'fan':1},clock='synthetic.clock')]}},
        models={'assessment':{'source':'recorded','engine':'reference','policy':{},'request':encode(request)}},
        templates={'resolve':default_template()})


def _host(args):
    from .operational_agent import OperationalHost
    from .companion_policy_config import load_companion_policies
    policies = load_companion_policies(getattr(args, 'companion_policies', None))
    return OperationalHost(args.profile, resume=args.resume,
        process_policies={name: value for name, value in policies.items()
                          if name in {'calibrated-window', 'residual-monitor'}})


def main(argv=None):
    parser=argparse.ArgumentParser(prog='net operational',description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    demo=sub.add_parser('example');demo.add_argument('--output-dir',required=True,type=Path);demo.add_argument('--case',choices=['pass','fail','unknown'],default='pass')
    admit=sub.add_parser('admit');admit.add_argument('--manifest',required=True,type=Path);admit.add_argument('--output-dir',required=True,type=Path)
    for name in ('serve','init','call','view','export','attach-workbench'):
        action=sub.add_parser(name);action.add_argument('--profile',required=True,type=Path);action.add_argument('--resume',action='store_true')
        action.add_argument('--companion-policies', type=Path,
                            help='Host-only bounded companion policy file; required for native companions')
        if name=='serve':action.add_argument('--sdk',action='store_true')
        elif name=='call':action.add_argument('tool');action.add_argument('--arguments',default='{}')
        elif name in ('view','export'):action.add_argument('--output',required=True,type=Path)
        elif name=='attach-workbench':
            action.add_argument('--workspace',required=True,type=Path);action.add_argument('--output',required=True,type=Path)
            action.add_argument('--recorded-at',required=True)
    collect=sub.add_parser('collect');collect.add_argument('--manifest',required=True,type=Path);collect.add_argument('--restart',action='store_true')
    for name in ('collector-state','collector-stop','collector-recover'):
        action=sub.add_parser(name);action.add_argument('--journal',required=True,type=Path);action.add_argument('--id',required=True)
    approve=sub.add_parser('approve-command');approve.add_argument('--profile',required=True,type=Path);approve.add_argument('--command',required=True);approve.add_argument('--expected-ref',required=True);approve.add_argument('--approval-ref',required=True);approve.add_argument('--principal',required=True);approve.add_argument('--expires-ns',required=True,type=int)
    revoke=sub.add_parser('revoke');revoke.add_argument('--profile',required=True,type=Path)
    http=sub.add_parser('serve-http');http.add_argument('--config',required=True,type=Path);http.add_argument('--port',type=int,default=18767)
    args=parser.parse_args(argv)
    try:
        if args.action=='example': result={'status':'PREPARED_SYNTHETIC','profile':str(example(args.output_dir,case=args.case))}
        elif args.action=='admit':
            spec=parse(args.manifest.read_bytes())
            keys({k:v for k,v in spec.items() if k!='providers'},{'schema','id','sources','models','templates','grants','supervision'})
            if spec['schema']!='ncp.operational-admission.v1':raise ValueError('Unknown admission schema')
            sources={}
            for alias,item in spec['sources'].items():
                keys(item,{'path','family','context','configurations'})
                raw=read_source(args.manifest.parent/item['path'])
                sources[alias]={'raw':raw,**{k:item[k] for k in ('family','context','configurations')}}
            models=deepcopy(spec['models'])
            for m in models.values():
                if m['request'] is not None:m['request']=safe_path(args.manifest.parent/m['request']).read_bytes()
            result={'status':'ADMITTED','profile':str(prepare(args.output_dir,investigation_id=spec['id'],sources=sources,
                models=models,templates=spec['templates'],grants=spec['grants'],supervision=spec['supervision'],
                providers={engine:{role:str(safe_path(args.manifest.parent/path).resolve()) for role,path in bindings.items()}
                    for engine,bindings in spec.get('providers',{}).items()} if 'providers' in spec else None))}
        elif args.action.startswith('collector-'):
            from .operational_collection import Collector
            with Journal(args.journal) as db:
                collector=Collector(db,args.id)
                result={'collector-state':collector.state,'collector-stop':collector.request_stop,'collector-recover':collector.recover}[args.action]()
        elif args.action=='collect':
            from .operational_collection import Collector, nvml_segments, link_segments
            spec=parse(args.manifest.read_bytes())
            keys(spec,{'schema','journal','id','adapter','binding','max_segments','max_bytes','cadence_s'})
            if spec['schema']!='ncp.operational-collector-profile.v1':raise ValueError('Unknown collector profile')
            path=safe_path(args.manifest.parent/spec['journal'])
            with Journal(path,create=not path.exists()) as db:
                collector=Collector(db,spec['id'],max_segments=spec['max_segments'],max_bytes=spec['max_bytes'],cadence_s=spec['cadence_s'])
                binding=spec['binding']
                if spec['adapter']=='nvml':
                    keys(binding,{'device_index','host','boot','count'})
                    iterator=nvml_segments(**binding)
                elif spec['adapter']=='retained-files':
                    keys(binding,{'paths'})
                    if type(binding['paths']) is not list or len(binding['paths'])>64:raise ValueError('Bounded source files required')
                    iterator=(read_source(args.manifest.parent/p) for p in binding['paths'])
                elif spec['adapter']=='link-receive':
                    keys({k:v for k,v in binding.items() if k!='segment_records'},{'paths','manifest','boot_id'})
                    if type(binding['paths']) is not list or not 1<=len(binding['paths'])<=64:
                        raise ValueError('Link receive requires 1..64 operator-bound input files')
                    device=parse(safe_path(args.manifest.parent/binding['manifest']).read_bytes())
                    iterator=link_segments((read_source(args.manifest.parent/p) for p in binding['paths']),
                        db,expected_manifest=device,boot_id=binding['boot_id'],max_records=binding.get('segment_records',1))
                else:raise ValueError('Unsupported collector adapter; no data-selected imports')
                result=collector.run(lambda index:next(iterator),source_identity={'adapter':spec['adapter'],'binding':binding},restart=args.restart)
        elif args.action=='approve-command':
            from .operational_supervision import approve_command
            result=approve_command(args.profile,args.command,expected_ref=args.expected_ref,approval_ref=args.approval_ref,principal=args.principal,expires_ns=args.expires_ns)
        elif args.action=='revoke':
            spec=parse(args.profile.read_bytes())
            with Journal(safe_path(args.profile.parent/spec['journal'])) as db:
                key='oe-revoked:'+spec['investigation'];old=db.state(key)
                db.compare_and_set_state(key,0 if old is None else old['revision'],{'revoked':True})
            result={'status':'REVOKED','already_dispatched_work':'not_undone'}
        elif args.action=='serve-http':
            from .operational_http import main as http_main
            return http_main(['--config',str(args.config),'--port',str(args.port)])
        else:
            with _host(args) as host:
                if args.action=='serve':
                    if args.sdk:
                        from .mcp_server import serve_sdk
                        return serve_sdk(host,name='ncp-operational')
                    from .agent_mcp import serve
                    return serve(host,sys.stdin.buffer,sys.stdout.buffer)
                if args.action=='init':result=host.call('oe_catalog',{})
                elif args.action=='call': result=host.call(args.tool,parse(args.arguments.encode()))
                elif args.action in ('view','export'):
                    from .operational_view import snapshot, render_html
                    report=snapshot(host)
                    with args.output.open('xb') as out:out.write(render_html(report) if args.action=='view' else encode(report))
                    result={'status':'EXPORTED','investigation':host.spec['investigation'],'output':str(args.output)}
                else:
                    from .operational_view import attach_workbench
                    result=attach_workbench(host,args.workspace,args.output,recorded_at=args.recorded_at)
        print(json.dumps(result,indent=2,allow_nan=False));return 0
    except (ValueError,PermissionError,OSError,KeyError,TypeError,RuntimeError) as exc:
        print(json.dumps({'status':'refused','reason':str(exc)[:1000]}),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
