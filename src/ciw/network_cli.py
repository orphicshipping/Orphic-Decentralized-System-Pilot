"""Local NET network identity/consent/release journeys, not a cloud service."""
from __future__ import annotations
import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from . import device_boundary as b
from .control_contracts import save_new
from .network_workflow import register_schemas


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    demo=sub.add_parser('demo');demo.add_argument('--output',type=Path,required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('--workspace',type=Path,required=True)
    inspect.add_argument('--workspace-sha256',required=True)
    args=parser.parse_args(argv);register_schemas()
    try:
        if args.command=='inspect':
            from .session import Session
            if args.workspace.is_symlink() or not args.workspace.is_file():raise ValueError('regular workspace required')
            with args.workspace.open('rb') as stream:raw=stream.read(64*1024*1024+1)
            if len(raw)>64*1024*1024 or sha256(raw).hexdigest()!=args.workspace_sha256:raise ValueError('workspace budget or external anchor mismatch')
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'workspace.json';path.write_bytes(raw)
                session=Session.from_workspace(path,Path(tmp)/'reopened')
                summary={'status':'HISTORICAL_ONLY','results':len(session.results),'executions':len(session.executions),
                    'active_grants_restored':False,'current_root_authority':'not_revalidated; external workspace anchor checked'}
            print(json.dumps(summary));return 0
        from .network_example import fixture,access_token,enroll
        from .network_trust import signed_document
        args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
        f=fixture(args.output);first=enroll(f)
        if first.get('payload',{}).get('status')!='completed':raise ValueError('synthetic enrollment did not complete')
        no_phone=f['host'].verify_device_packet(f['packet'])
        token=access_token(f,'data.contribution.prepare',f['consent'])
        result=f['host'].request('contribute',token,{'source_b64':base64.b64encode(f['source']).decode(),
            'consent':f['consent'],'recipient':'notation-research','purpose':'research'})
        if result.get('payload',{}).get('status')!='completed':raise ValueError('synthetic contribution did not prepare')
        export=result['payload']['result']['data']['result']['export']
        save_new(args.output/'contribution.json',export)
        raw=b'opaque synthetic model release; never executed'
        release=signed_document('release','notation-publisher',{'schema':'ciw.network-release.v1',
            'release_id':'model-demo','kind':'model','version':1,'device_family':'gauge-fixture',
            'runtime_profile':'opaque-reference-v1','artifact_id':b.raw_id(raw),'size_bytes':len(raw),
            'issued_at':f['now'],'expires':f['now']+300,'validation_record_id':None},f['keys']['publisher'])
        token2=access_token(f,'release.review',release)
        release_result=f['host'].request('release',token2,{'artifact_b64':base64.b64encode(raw).decode(),'manifest':release})
        revocation=signed_document('revocation','tenant-demo',{'schema':'ciw.network-consent-revocation.v1',
            'consent_id':'consent-demo','tenant_id':'tenant-demo','issued_at':f['now'],'reason':'customer_request'},f['keys']['consent'])
        revoke=f['host'].request('revoke',access_token(f,'data.consent.revoke',revocation),{'revocation':revocation})
        after=f['host'].request('contribute',token,{'source_b64':base64.b64encode(f['source']).decode(),
            'consent':f['consent'],'recipient':'notation-research','purpose':'research'})
        if release_result['payload']['status']!='completed' or revoke['payload']['status']!='completed' or after['payload']['status']!='refused':
            raise ValueError('release/revocation/refusal journey did not establish its expected outcomes')
        path=f['session'].save_workspace(args.output/'workspace.json')
        if token in path.read_text() or token2 in path.read_text():raise ValueError('access token escaped into retained evidence')
        summary={'status':'SYNTHETIC_LOCAL_JOURNEY_COMPLETED','enrollment':'completed','device_without_phone':no_phone,
            'contribution':'PREPARED_LOCAL_ONLY','revoked_consent_reuse':'refused','release':'SIGNATURE_AND_BYTES_REVIEWED',
            'transmitted':False,'installed':False,'training_admission':False,'actual_login_ceremony':False,
            'hardware_attestation':False,'SP1':'NOT_RUN','private_keys_exported':False,
            'workspace_sha256':sha256(path.read_bytes()).hexdigest()}
        save_new(args.output/'summary.json',summary);print(json.dumps(summary,indent=2));return 0
    except (ValueError,OSError,RuntimeError,ImportError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'REFUSE','reason':str(exc)[:400]}));return 2


if __name__=='__main__':raise SystemExit(main())
