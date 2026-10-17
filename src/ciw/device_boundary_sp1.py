"""Optional SP1 path through the existing SCR proving/registered-guest boundary.

No substitute verifier, mock mode, network prover or registry mutation. A new
guest must be built and independently qualified using execution.build before
its ELF is registered. Until then SP1-required capabilities remain closed.
"""
from __future__ import annotations
import base64
from hashlib import sha256
from pathlib import Path
import re

from . import device_boundary as b
from .adapters.subprocess import PinnedSubprocessAdapter, _json

_DRIVER = r'''
import base64, hashlib, json, os, pathlib, sys, tempfile
os.environ["SP1_PROVER"]="cpu"
os.environ.pop("NETWORK_PRIVATE_KEY",None)
os.environ.pop("SP1_PRIVATE_KEY",None)
root, host, guest, action = sys.argv[1:]
sys.path.insert(0, root)
from execution.specification import ExecutionSpecification
from execution.engine import ExecutionResult
from execution.commitments import OUTPUT_TAG, COMPUTATION_TAG, commit_hex, canonical_u32
from execution.proving import verify_existing_proof, prove_and_verify_result
request=json.loads(sys.stdin.buffer.read())
program=base64.b64decode(request['program'],validate=True)
input_bytes=base64.b64decode(request['input'],validate=True)
output=base64.b64decode(request['output'],validate=True)
spec=ExecutionSpecification(program,b'',input_bytes)
output_id=commit_hex(OUTPUT_TAG,[output])
computation=commit_hex(COMPUTATION_TAG,[bytes.fromhex(spec.program_identity()),bytes.fromhex(spec.input_identity()),bytes.fromhex(output_id),canonical_u32(0)])
result=ExecutionResult(spec,spec.identity(),spec.program_identity(),spec.input_identity(),0,'completed',0,output,output_id,computation,None)
with tempfile.TemporaryDirectory() as tmp:
    proof=pathlib.Path(tmp)/'proof.bin'
    if action=='prove':
        prove_and_verify_result(result,spec,proof,pathlib.Path(host),pathlib.Path(guest),prove_timeout=1800)
    else:
        proof.write_bytes(base64.b64decode(request['proof'],validate=True))
    if not 0<proof.stat().st_size<=8*1024*1024:raise ValueError('proof byte budget exceeded')
    checked=verify_existing_proof(result,spec,proof,pathlib.Path(host),pathlib.Path(guest))
    if checked.get('outcome')!='verified' or checked.get('backend')!='sp1-cpu v6.1.0':
        raise ValueError('fresh registered SP1 verification did not succeed')
    response={'verification':checked,'proof_sha256':hashlib.sha256(proof.read_bytes()).hexdigest(),
              'input_sha256':hashlib.sha256(input_bytes).hexdigest(),'output_sha256':hashlib.sha256(output).hexdigest()}
    if action=='prove':response['proof_b64']=base64.b64encode(proof.read_bytes()).decode()
print(json.dumps(response,allow_nan=False))
'''


class ExistingSP1Verifier:
    """Paths and reviewed digests come only from trusted host setup."""
    def __init__(self,binding):
        b.keys(binding,{'schema','scr_root','scr_revision','source_root','python','python_sha256','python_version',
                        'dependencies','host','host_sha256','guest','guest_sha256'})
        if binding['schema']!='ciw.device-boundary-sp1-binding.v1':raise ValueError('unsupported SP1 binding')
        for name in ('scr_root','python','host','guest'):
            if not Path(binding[name]).is_absolute():raise ValueError('explicit absolute runtime paths required')
        for name in ('python_sha256','host_sha256','guest_sha256'):
            if type(binding[name]) is not str or not re.fullmatch(r'[a-f0-9]{64}',binding[name]):
                raise ValueError('reviewed file SHA-256 required')
        if binding['source_root'] not in ('.','instruments/execution/compute-runtime'):
            raise ValueError('select standalone SCR or the existing consolidated Compute Runtime source root')
        self.binding=b.parse(b.encode(binding))
        self._binding_id=b.raw_id(b.encode(self.binding))
        self._files()
        self.adapter=PinnedSubprocessAdapter(binding['scr_root'],binding['scr_revision'],'execution.proving',
            source_root=binding['source_root'],python_executable=binding['python'],timeout_seconds=1900,max_output_bytes=12*1024*1024,
            expected_python_sha256=binding['python_sha256'],expected_python_version=binding['python_version'],
            expected_dependencies=binding['dependencies'])

    def _files(self):
        if b.raw_id(b.encode(self.binding))!=self._binding_id:
            raise ValueError('SP1 host configuration mutated after binding')
        for name in ('host','guest'):
            path=Path(self.binding[name])
            if path.is_symlink() or not path.is_file():raise ValueError('missing or symlinked SP1 '+name)
            h=sha256(); total=0
            with path.open('rb') as stream:
                while chunk:=stream.read(1024*1024):
                    total+=len(chunk)
                    if total>256*1024*1024:raise ValueError('SP1 binary byte limit')
                    h.update(chunk)
            if total==0 or h.hexdigest()!=self.binding[name+'_sha256']:raise ValueError('SP1 binary drift')

    def _call(self,input_bytes,output_bytes,proof,action):
        if b.compute(input_bytes)!=output_bytes:raise ValueError('invalid affine candidate before SP1')
        self._files();before=self.adapter.runtime_identity()
        request={'program':base64.b64encode(b.PROGRAM).decode(),'input':base64.b64encode(input_bytes).decode(),
                 'output':base64.b64encode(output_bytes).decode(),'proof':base64.b64encode(proof).decode()}
        code,raw=self.adapter._run(_DRIVER,[str(self.adapter.source_root),self.binding['host'],self.binding['guest'],action],b.encode(request))
        self._files()
        if self.adapter.runtime_identity()!=before:raise ValueError('SP1 environment drift')
        if code:raise ValueError('registered SCR SP1 prover/verifier refused or unavailable')
        result=_json(raw)
        required={'verification','proof_sha256','input_sha256','output_sha256'}|({'proof_b64'} if action=='prove' else set())
        b.keys(result,required)
        if result['input_sha256']!=sha256(input_bytes).hexdigest() or result['output_sha256']!=sha256(output_bytes).hexdigest():
            raise ValueError('SP1 statement mismatch')
        proof_raw=b.unb64(result['proof_b64'],8*1024*1024) if action=='prove' else proof
        if result['proof_sha256']!=sha256(proof_raw).hexdigest():raise ValueError('SP1 proof identity mismatch')
        if result['verification'].get('outcome')!='verified' or result['verification'].get('backend')!='sp1-cpu v6.1.0':
            raise ValueError('not a fresh SP1 verification')
        runtime={'schema':'ciw.device-boundary-sp1-runtime.v1','binding_id':b.raw_id(b.encode(self.binding)),
                 'scr_runtime':before,'host_sha256':self.binding['host_sha256'],'guest_sha256':self.binding['guest_sha256'],
                 'proof_sha256':result['proof_sha256'],'verification':result['verification'],
                 'source_to_binary_attestation':'registered_guest_plus_operator_binding_not_hardware_attestation'}
        return proof_raw,runtime

    def verify(self,input_bytes,output_bytes,proof):
        if type(proof) is not bytes or not 0<len(proof)<=8*1024*1024:raise ValueError('proof byte limit')
        return self._call(input_bytes,output_bytes,proof,'verify')[1]

    def prove(self,input_bytes,output_bytes):
        return self._call(input_bytes,output_bytes,b'','prove')
