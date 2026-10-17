"""One evidence projection for MCP, CLI and the existing retained Workbench.

Offline UI embeds exact record identities. It performs display filtering only;
all comparisons and proposals are inspected as retained records.
"""
from __future__ import annotations

import base64
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import tempfile

from .agent_api import encode
from .operational_store import Records
from .operations.runner import seal


def snapshot(host):
    catalog=host.call('oe_catalog',{})
    records=[host.call('oe_inspect',{'record':r['ref']}) for r in catalog['records']]
    return {'schema':'ncp.operational-view.v1','investigation':host.spec['investigation'],
            'catalog':catalog,'records':records,'historical':True,'provider_execution':False}


def render_html(report):
    from .control_contracts import keys
    keys(report,{'schema','investigation','catalog','records','historical','provider_execution'})
    if report['schema']!='ncp.operational-view.v1' or report['historical'] is not True or report['provider_execution'] is not False:
        raise ValueError('Operational view requires a historical retained projection')
    assets=files('ciw').joinpath('web')
    template=assets.joinpath('operational.html').read_text()
    # Reuse the existing measurement evidence presentation styles.
    css=assets.joinpath('measurement_visualizer.css').read_text()+'\n'+assets.joinpath('operational.css').read_text()
    js=assets.joinpath('operational.js').read_text()
    h=lambda v:base64.b64encode(sha256(v.encode()).digest()).decode()
    csp="default-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'; img-src data:; "+f"script-src 'sha256-{h(js)}'; style-src 'sha256-{h(css)}'"
    for key,value in {'__CSP__':csp,'__STYLE__':css,'__SCRIPT__':js,'__DATA__':base64.b64encode(encode(report)).decode()}.items():
        if template.count(key)!=1:raise ValueError('Operational view asset mismatch')
        template=template.replace(key,value)
    raw=template.encode()
    if len(raw)>12*1024*1024:raise ValueError('Operational view exceeds 12 MiB')
    return raw


def attach_workbench(host, workspace, output, *, recorded_at):
    """Attach the same projection through the existing typed-data Workbench API."""
    from .session import Session
    from .data_sources import retain_record
    from .control_contracts import artifact
    report=snapshot(host);raw=encode(report)
    with tempfile.TemporaryDirectory(prefix='ncp-operational-workbench-') as tmp:
        session=Session.from_workspace(workspace,output_dir=Path(tmp)/'reopened')
        native=artifact(raw,media_type='application/vnd.notation.operational-view+json',producer='ciw.operational_view',
            experiment_id=report['investigation'],metadata={'payload':RecordsPayload(raw),
                'investigation':report['investigation'],'physical_validation':'not_established'})
        native['created_at']=recorded_at;native=seal(native)
        envelope=retain_record(native,dataset_id='operational/'+report['investigation'],revision=1,parent_source_id=None,
                               recorded_at=recorded_at,data_family='artifact')
        descriptor=session.workbench.add_source({'kind':'data-record','label':'Historical operational investigation',
            'bytes_b64':base64.b64encode(encode(envelope)).decode()})
        # The workspace source and scientific results retain their existing identities.
        stage=Path(tmp)/'attached.json';session.save_workspace(stage)
        with Path(output).open('xb') as stream:stream.write(stage.read_bytes())
    return {'status':'RETAINED_IN_WORKBENCH','investigation':report['investigation'],'source':descriptor,
            'new_executions':0,'provider_execution':False}


def RecordsPayload(raw):
    from . import link_contract as c
    return {'sha256':c.bytes_ref(raw),'byte_count':len(raw),
            'chunks':[c.packed(raw[i:i+24576],24576) for i in range(0,len(raw),24576)]}
