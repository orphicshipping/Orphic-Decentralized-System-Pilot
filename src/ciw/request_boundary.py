"""AGPL-3.0-or-later. Host-owned opt-in comparison; Python results only."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from . import agent_api, agent_mcp, control_contracts, session, request_boundary_contracts, request_boundary_transport
from .adapters import subprocess as runtime
from .adapters.subprocess import _guard_cleanup, RuntimeCleanupUncertain
from .request_boundary_contracts import classify, wire_output, SCHEMA
from .request_boundary_transport import _exchange, executable_snapshot, _close_owned, ShadowFailure

SOURCE_SHA256 = {
    'agent_api': '3ec9ae6f41e6618b6680f610ba39c06634639b17d3dffa86ab63a67f0898dd7e',
    'agent_mcp': '564d51f4ea3e74b3ab56916e130d6efaaaa1e2ec3c374a2f174feb072a9643e3',
    'control_contracts': 'abbf4350e784efbc580318739a57e8e1a0a5e8f1d5dd8edb2b7b6a7385528a00',
    'session': '214d00cdf5e29b099dfbbe0237955b9dc632d3faddd2ec7601fd965e1d9cd25b',
    'request_boundary_contracts': '4ad61d781d8994730eafd68efcfc9e7f0bfd41344c48bb503a97ccb8fedfc6f1',
    'request_boundary_transport': '496035258257e9d250f91a59ab519d5fea0151922363a27fa3adbf070db7131b',
    'subprocess': '39c7ef37aa14ac6c9445351d782d3e1740844d693082f61a9f9c080e9761cfd6',
}
MAX_INPUT = 8*1024*1024

def _cancelled(error, inherited):
    pending, seen = [error], set()
    while pending:
        item = pending.pop()
        if item is inherited or id(item) in seen:
            continue
        seen.add(id(item))
        if not isinstance(item, (Exception, BaseExceptionGroup)):
            return item
        pending.extend(x for x in (item.__context__, item.__cause__) if x is not None)
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
    return None

def digest_file(path):
    if (not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ShadowFailure('file_binding')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ShadowFailure('file_binding')
        digest=sha256(); count=0
        while block := os.read(fd, min(65536, MAX_INPUT+1-count)):
            count += len(block)
            if count>MAX_INPUT: raise ShadowFailure('file_budget')
            digest.update(block)
        return digest.hexdigest()
    finally:
        _close_owned(fd)  # Never retry an ambiguous close.

class RequestBoundary:
    def __init__(self, *, enabled=False, binary=None, binary_sha256=None, timeout=1.0):
        self.enabled=enabled;self.binary=binary;self.binary_sha256=binary_sha256;self.timeout=timeout
        self.last_observation=None
        self._cleanup_uncertain=None
        self._last_failure=None

    @_guard_cleanup
    def _run(self, raw):
        with executable_snapshot(self.binary, self.binary_sha256) as executable:
            return _exchange(executable, 'boundary-v1', raw, self.timeout)

    @_guard_cleanup
    def _digest(self, path):
        return digest_file(path)

    def prefix(self, raw, seen=frozenset()):
        # Snapshot is immutable. No ID is consumed and no tool can be dispatched.
        self.last_observation={'status':'pending','execution_authorized':False}
        frozen=frozenset(seen)
        expected,message=classify(raw,frozen)
        if expected['code']==-32700:
            self.last_observation={'status':'python_only','reason':'raw_validation','execution_authorized':False}
            return expected
        self._observe(expected,dict(schema=SCHEMA,operation='request_prefix',message=message,
            seen=[{'kind':'int' if kind is int else 'str','value':value} for kind,value in frozen]))
        return expected

    def wire_output(self, encoded_bytes):
        self.last_observation={'status':'pending','execution_authorized':False}
        expected=wire_output(encoded_bytes)
        self._observe(expected,dict(schema=SCHEMA,operation='wire_output',encoded_bytes=encoded_bytes))
        return expected

    def _observe(self, expected, contract):
        if self.enabled is not True:
            self.last_observation={'status':'disabled','execution_authorized':False};return
        if self._cleanup_uncertain is not None:
            self.last_observation={'status':'held','reason':'cleanup_uncertain','execution_authorized':False};return
        inherited=sys.exception()
        try:
            if (not isinstance(self.binary,Path) or not self.binary.is_absolute()
                    or type(self.binary_sha256) is not str
                    or re.fullmatch('[0-9a-f]{64}',self.binary_sha256) is None
                    or type(self.timeout) not in (int,float) or not 0<self.timeout<=2):
                raise ShadowFailure('configuration')
            for module in (agent_api,agent_mcp,control_contracts,session,request_boundary_contracts,request_boundary_transport,runtime):
                if self._digest(Path(module.__file__))!=SOURCE_SHA256[module.__name__.rsplit('.',1)[-1]]:
                    raise ShadowFailure('source_binding')
            if self._digest(self.binary)!=self.binary_sha256: raise ShadowFailure('binary_binding')
            raw=json.dumps(contract,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
            if len(raw)>MAX_INPUT: raise ShadowFailure('input_budget')
            output=self._run(raw)
            reply=session.loads_json(output.decode('utf-8'))
            if type(reply) is not dict or reply.get('schema')!='odsp.request-boundary.reply.v1':
                raise ShadowFailure('reply_version')
            if reply.get('status')=='unsupported': raise ShadowFailure('rust_unsupported')
            if set(reply)!={'schema','status','result'} or reply['status']!='ok':raise ShadowFailure('reply_shape')
            # Compare serialized result tokens: Python bool/int equality must not
            # permit true to impersonate an integer in a malformed child reply.
            same=json.dumps(reply['result'],sort_keys=True,allow_nan=False,separators=(',',':'))==json.dumps(expected,sort_keys=True,separators=(',',':'))
            self.last_observation={'status':'matched' if same else 'mismatch',
                                   'reason':'exact_result' if same else 'result_fields',
                                   'execution_authorized':False}
        except BaseException as exc:
            # Keep cancellation and cleanup diagnostics on the host binding.
            # Only the redacted status is presentation data, never a grant.
            self._last_failure=exc
            if not isinstance(exc, Exception):
                self.last_observation={'status':'interrupted','reason':'observation_interrupted','execution_authorized':False}
                raise
            held=isinstance(exc,RuntimeCleanupUncertain) or self._cleanup_uncertain is not None
            reason='cleanup_uncertain' if held else str(exc) if isinstance(exc,ShadowFailure) else 'observer_error'
            self.last_observation={'status':'held' if held else 'fallback','reason':reason,'execution_authorized':False}
            cancellation=_cancelled(exc,inherited)
            if cancellation is not None:
                # Ordinary diagnostic fallback must not swallow an interrupted
                # request just because its cleanup also raised an Exception.
                raise BaseExceptionGroup('Shadow cancellation and cleanup failure',
                                         [cancellation, exc]) from exc
