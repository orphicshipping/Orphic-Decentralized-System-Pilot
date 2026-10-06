"""Recorded device/NET boundary journeys; no serial, radio or machine drivers."""
from __future__ import annotations
import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import struct
import tempfile
import time

from . import device_boundary as b
from .device_boundary_workflow import BoundaryHost, validate_policy, register_schemas
from .core.identities import content_identity, new_identity
from .control_contracts import save_new
from .session import Session
from .instruments import make_demo_run


def example(*, now_ns=None):
    """Ephemeral synthetic signing keys stay in memory, never in the delivery."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    device_key=Ed25519PrivateKey.generate(); net_key=Ed25519PrivateKey.generate()
    now=time.time_ns() if now_ns is None else now_ns
    cfg={"schema":b.TRANSFORM,"calibration_id":"synthetic-cal-1","gain":1000,"offset":0,"denominator":1,
         "valid_from_ns":now-10**12,"valid_until_ns":now+10**12,"count_min":0,"count_max":100_000,
         "uncertainty_bound_nm":b.rational(1000),
         "uncertainty_basis":"operator_declared_total_absolute_bound_not_covariance"}
    raw=struct.pack("<q",24982)
    payload={"schema":b.PAYLOAD,"device_id":"demo-gauge","boot_id":"demo-boot","sequence":1,
        "sample_id":"demo-sample-1","execution_id":new_identity("execution"),"frame_id":"gauge-axis",
        "clock_id":"demo-declared-clock","captured_at_ns":now-10**6,"time_uncertainty_ns":100,
        "firmware_id":b.raw_id(b"synthetic firmware identity only"),"gateware_id":None,
        "raw_b64":base64.b64encode(raw).decode(),"raw_id":b.raw_id(raw),"transform":cfg,
        "device_state":{"quantity":"length","unit":"mm","value":{"numerator":12491,"denominator":500},"frame_id":"gauge-axis"},
        "origin":"synthetic","filter_id":b.FILTER_D}
    packet=b.signed_packet(payload,device_key)
    policy={"schema":"ciw.device-boundary-policy.v1","epoch":"demo-policy-1",
        "device_id":payload["device_id"],"boot_id":payload["boot_id"],
        "device_public_key_b64":base64.b64encode(device_key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode(),
        "firmware_id":payload["firmware_id"],"gateware_id":None,"frame_id":payload["frame_id"],"clock_id":payload["clock_id"],
        "approved_transform_ids":[content_identity(cfg)],"allowed_functions":list(b.FUNCTIONS[:6]),
        "require_sp1_functions":["generate_inspection_record"],"valid_from_ns":now-10**12,"valid_until_ns":now+10**12,
        "max_sample_age_ns":10**11,"max_time_uncertainty_ns":1000,
        "specification_nm":{"specification_id":b.raw_id(b"demo 24.97 to 25.00 mm tolerance"),
                            "lower":b.rational(24970000),"upper":b.rational(25000000)},
        "procedure_id":b.raw_id(b"demo read-only inspection procedure")}
    return packet,policy,device_key,net_key


def checked(path,expected,limit=b.MAX_BYTES):
    if path.is_symlink() or not path.is_file():raise ValueError("require regular input file")
    with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError("input byte limit")
    if expected is not None and sha256(raw).hexdigest()!=expected:raise ValueError("external file anchor mismatch")
    return raw


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    demo=subs.add_parser('demo');demo.add_argument('--output',type=Path,required=True)
    run=subs.add_parser('run');run.add_argument('--packet',type=Path,required=True)
    run.add_argument('--policy',type=Path,required=True);run.add_argument('--policy-sha256',required=True)
    run.add_argument('--function',choices=b.FUNCTIONS);run.add_argument('--output',type=Path,required=True)
    run.add_argument('--sp1-binding',type=Path);run.add_argument('--sp1-binding-sha256')
    inspect=subs.add_parser('inspect');inspect.add_argument('--workspace',type=Path,required=True)
    inspect.add_argument('--workspace-sha256',required=True)
    gate=subs.add_parser('proof-gate');gate.add_argument('--output',type=Path,required=True)
    gate.add_argument('--binding',type=Path);gate.add_argument('--binding-sha256')
    gate.add_argument('--packet',type=Path);gate.add_argument('--packet-sha256')
    args=parser.parse_args(argv);register_schemas()
    try:
        if args.command=='proof-gate':
            args.output.mkdir(parents=True,exist_ok=False)
            result={'status':'NOT_RUN','reason':'explicit packet and reviewed SCR SP1 binding required',
                    'proof_generated':False,'equipment_authority':False}
            code=2
            supplied=(args.binding,args.binding_sha256,args.packet,args.packet_sha256)
            if any(item is not None for item in supplied):
                try:
                    if not all(item is not None for item in supplied):
                        raise ValueError('supply packet, packet-sha256, binding and binding-sha256 together')
                    from .device_boundary_sp1 import ExistingSP1Verifier
                    binding=b.parse(checked(args.binding,args.binding_sha256))
                    packet_raw=checked(args.packet,args.packet_sha256)
                    (args.output/'input-packet.json').write_bytes(packet_raw)
                    result['input_packet_id']=b.raw_id(packet_raw)
                    packet=b.parse(packet_raw)
                    b.keys(packet,{'schema','payload','signature','proof'})
                    if packet['schema']!=b.PACKET:raise ValueError('unsupported packet schema')
                    statement=b.statement(packet['payload'])
                    proof,runtime=ExistingSP1Verifier(binding).prove(statement,b.compute(statement))
                    packet['proof']={'schema':'ciw.device-boundary-sp1.v1',
                                     'bytes_b64':base64.b64encode(proof).decode()}
                    (args.output/'proof.bin').write_bytes(proof)
                    save_new(args.output/'packet-with-proof.json',packet)
                    result.update(status='PASS',reason=None,proof_generated=True,runtime=runtime,
                                  packet_id=b.raw_id(b.encode(packet)))
                    code=0
                except Exception as exc:
                    result.update(status='FAIL',reason=type(exc).__name__+': '+str(exc)[:1000])
                    code=1
            save_new(args.output/'gate.json',result);print(json.dumps(result));return code
        if args.command=='inspect':
            raw=checked(args.workspace,args.workspace_sha256,64*1024*1024)
            with tempfile.TemporaryDirectory() as tmp:
                target=Path(tmp)/'workspace.json';target.write_bytes(raw)
                session=Session.from_workspace(target,Path(tmp)/'readback')
                result={'status':'historical_only','executions':list(session.executions.values()),
                        'results':list(session.results.values()),'authorizes_execution':False}
            print(json.dumps(result,indent=2));return 0
        args.output.mkdir(parents=True,exist_ok=False)
        session=Session(make_demo_run(),args.output/'session')
        # The demo oscillator is explicitly a Session carrier, never gauge data.
        if args.command=='demo':
            packet,policy,_,net_key=example();raw=b.encode(packet)
            save_new(args.output/'packet.json',packet);save_new(args.output/'policy.json',policy)
            host=BoundaryHost(session,policy)
            responses=[host.request(raw),host.request(raw,'record_raw'),host.request(raw,'record_calibrated'),
                       host.request(raw,'compare_tolerance'),host.request(raw,'generate_inspection_record'),
                       host.request(raw,'machine_control')]
            review=responses[0]['payload']['result']['data']['review']
            reply=b.make_reply(review,net_key,net_id='demo-net',now_ns=time.time_ns(),ttl_ns=10**9)
            save_new(args.output/'reply.json',reply)
            from .legibility import key_fingerprint
            public=b.unb64(reply['signature']['public_key_b64'],32)
            ack=b.verify_reply(reply,trusted_keys={key_fingerprint(public):public},packet_id=b.raw_id(raw),
                device_id=policy['device_id'],policy_id=content_identity(policy),net_id='demo-net',now_ns=time.time_ns())
            result={'status':'demonstrated_recorded_boundary','responses':responses,'reply_check':ack,
                    'SP1':'NOT_RUN','physical_acquisition':False,'equipment_authority':False}
        else:
            raw=checked(args.packet,None)
            policy=validate_policy(b.parse(checked(args.policy,args.policy_sha256)))
            verifier=None
            if args.sp1_binding is not None or args.sp1_binding_sha256 is not None:
                if args.sp1_binding is None or args.sp1_binding_sha256 is None:
                    raise ValueError('supply both SP1 binding and its external SHA-256')
                from .device_boundary_sp1 import ExistingSP1Verifier
                verifier=ExistingSP1Verifier(b.parse(checked(args.sp1_binding,args.sp1_binding_sha256)))
            host=BoundaryHost(session,policy,verifier=verifier)
            result=host.request(raw,args.function)
        session.save_workspace(args.output/'workspace.json')
        save_new(args.output/'result.json',result)
        workspace_sha=sha256((args.output/'workspace.json').read_bytes()).hexdigest()
        print(json.dumps({'result':result,'workspace_sha256':workspace_sha},indent=2))
        if args.command=='run' and result.get('payload',{}).get('status')!='completed':return 2
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,ImportError,TypeError,OverflowError,RecursionError) as exc:
        print(json.dumps({'status':'REFUSE','reason':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
