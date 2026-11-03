"""Host-bound ROS2/OPC UA/MQTT observation connections and durable read receipts.

No adapter publishes measurements, writes a node, calls a device method or sends
motion/control. The process policy bounds network SDKs, but is not a network or
filesystem sandbox. Physical identity, calibration and timing need separate
qualification even when the connection is authenticated.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
import base64
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import os
from pathlib import Path
import re
import sys
import tempfile
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4

from .adapters.subprocess import _bounded_process, _guard_cleanup
from .agent_tools import _shape
from .campaign_contracts import canonical, parse, REQUEST_LIMITS, OUTPUT_LIMITS as RESPONSE_LIMITS
from .offline_intake import _record, _identifier, _instant, PROTOCOLS
from .process_policy import ProcessPolicy
from .retention import Store

PROFILE='odsp.live-intake.v1'
_BOOTSTRAP=('import json,sys;sys.path.extend(json.loads(sys.argv[2]));'
            'sys.path.insert(0,sys.argv[1]);from ciw.live_intake_worker import entrypoint;entrypoint()')


def _check(condition, reason='live_binding_refused'):
    if not condition:
        raise ValueError(reason)


def _hash(raw):
    return 'sha256:'+sha256(raw).hexdigest()


def _file_hash(path):
    path=Path(path)
    _check(path.is_absolute() and not path.is_symlink() and path.is_file(), 'live_file_binding_refused')
    with path.open('rb') as stream:
        raw=stream.read(64*1024*1024+1)
    _check(len(raw)<=64*1024*1024, 'live_file_budget')
    return sha256(raw).hexdigest()


def _import_identity(roots):
    """Fingerprint every installed byte before any SDK/site startup can run.

    The initial snapshot is a trusted launcher's local binding, not an attestation
    that an arbitrary installation is safe. A fresh bytecode prefix and -S keep
    unlisted .pth/sitecustomize and old source caches from running at startup.
    """
    identities=[];count=total=0
    for root in roots:
        entries={}
        for path in sorted(root.rglob('*')):
            _check(not path.is_symlink(),'live_import_symlink')
            if path.is_dir():
                continue
            _check(path.is_file(),'live_import_file_type')
            count+=1;total+=path.stat().st_size
            _check(count<=20000 and total<=512*1024*1024,'live_import_budget')
            entries[path.relative_to(root).as_posix()]=_file_hash(path)
        _check(entries,'live_empty_import_root')
        # This is a local file inventory, not the protocol's limited JSON tree.
        import json
        identities.append(sha256(json.dumps(entries,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    return identities


def _settings(protocol, settings, origin):
    value=parse(canonical(settings, limits=REQUEST_LIMITS), limits=REQUEST_LIMITS)
    _check(origin in ('local_fixture','live'), 'live_origin_required')
    if protocol=='mqtt.scalar-observation.v1':
        _check(set(value)=={'host','port','topic','qos','tls','ca_file'})
        _check(type(value['host']) is str and 0<len(value['host'])<=253 and
               not any(c in value['host'] for c in '/@\\\x00'))
        _check(type(value['port']) is int and 1<=value['port']<=65535)
        _check(type(value['topic']) is str and 0<len(value['topic'].encode())<=256 and
               not any(c in value['topic'] for c in '\x00+#'))
        _check(type(value['qos']) is int and value['qos'] in (0,1,2) and type(value['tls']) is bool)
        _check(value['ca_file'] is None or Path(value['ca_file']).is_absolute())
        hostname=value['host']
        _check(origin!='live' or value['tls'], 'live_mqtt_requires_tls')
    elif protocol=='opcua.scalar.v1':
        _check(set(value)=={'endpoint','node_id','unit','datatype','application_uri','secure',
                           'server_certificate','interval_seconds'})
        endpoint=urlsplit(value['endpoint'])
        _check(endpoint.scheme=='opc.tcp' and endpoint.hostname and endpoint.port and
               not endpoint.username and not endpoint.password and not endpoint.query and not endpoint.fragment)
        _check(type(value['node_id']) is str and 0<len(value['node_id'])<=256 and
               type(value['unit']) is str and len(value['unit'])<=32)
        _check(value['datatype'] in ('Boolean','Int32','UInt32','Double','String'))
        _identifier(value['application_uri'])
        _check(type(value['interval_seconds']) in (int,float) and 0<=value['interval_seconds']<=1)
        _check(type(value['secure']) is bool and (origin!='live' or value['secure']), 'live_opcua_requires_security')
        _check(not value['secure'] or (type(value['server_certificate']) is str and
                                     Path(value['server_certificate']).is_absolute()))
        hostname=endpoint.hostname
    else:
        _check(protocol=='ros2.joint-state.v1')
        _check(set(value)=={'topic','domain_id','joints','clock','reliable','secure','security_keystore'})
        _check(type(value['topic']) is str and re.fullmatch(r'/[A-Za-z_][A-Za-z0-9_/]{0,127}',value['topic']))
        _check(type(value['domain_id']) is int and 0<=value['domain_id']<=232)
        _check(type(value['joints']) is dict and 1<=len(value['joints'])<=64 and
               all(kind in ('revolute','prismatic') for kind in value['joints'].values()))
        for name in value['joints']:
            _identifier(name)
        _check(type(value['clock']) is dict and set(value['clock'])=={'domain','clock_id'} and
               value['clock']['domain'] in ('simulation','source'))
        _identifier(value['clock']['clock_id'])
        _check(type(value['reliable']) is bool and type(value['secure']) is bool)
        _check(origin!='live' or value['secure'], 'live_ros2_requires_security')
        _check(not value['secure'] or (type(value['security_keystore']) is str and
                                     Path(value['security_keystore']).is_absolute()))
        hostname=None
    if origin=='local_fixture' and hostname is not None:
        _check(ipaddress.ip_address(hostname).is_loopback, 'fixture_endpoint_must_be_loopback_literal')
    return value


@dataclass(frozen=True)
class LiveBinding:
    source_id: str
    protocol: str
    origin: str
    settings: dict
    sdk_version: str
    credential_epoch: str

    def __post_init__(self):
        _identifier(self.source_id);_identifier(self.credential_epoch)
        _check(self.protocol in PROTOCOLS)
        _check(type(self.sdk_version) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',self.sdk_version))
        value=_settings(self.protocol,self.settings,self.origin)
        object.__setattr__(self,'settings',MappingProxyType(value))


def _schema(properties,required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


TOOLS={
 'ncp_live_sources':{'description':'Describe host-bound read-only observation connections; this does not connect.',
                     'inputSchema':_schema({})},
 'ncp_live_read':{'description':'Read a finite set of observations from an approved connection and retain its receipt; never actuates.',
     'inputSchema':_schema({'source_id':{'type':'string','minLength':1,'maxLength':128},
                           'count':{'type':'integer','minimum':1,'maximum':32},
                           'seconds':{'type':'number','minimum':0.05,'maximum':10}},('source_id','count','seconds'))},
 'ncp_live_receipt':{'description':'Read a page of a retained observation receipt without reconnecting.',
     'inputSchema':_schema({'artifact_id':{'type':'string','minLength':41,'maxLength':41},
                           'offset':{'type':'integer','minimum':0,'maximum':262144},
                           'limit':{'type':'integer','minimum':1,'maximum':16384}},('artifact_id',))},
}
for _name,_spec in TOOLS.items():
    _read=_name!='ncp_live_read'
    _spec['annotations']={'readOnlyHint':_read,'destructiveHint':False,'idempotentHint':_read,
                          'openWorldHint':not _read}


def profile_manifest():
    return {'profile_id':PROFILE,'tools':[{'name':n,**deepcopy(s)} for n,s in sorted(TOOLS.items())],
            'protocols':list(PROTOCOLS),'device_writes':False,'physical_qualification':'not_established',
            'authorizes_execution':False}


class LiveIntakeHost:
    strict_mcp_input=True
    instructions='Read explicitly bound observation sources only. No device writes, control or inferred calibration.'

    def __init__(self,*,principal,tenant,bindings,python,python_sha256,process_policy,
                 authorize,credentials=None,import_roots=None):
        _identifier(principal);_identifier(tenant)
        _check(type(bindings) in (list,tuple) and 1<=len(bindings)<=16 and
               all(type(b) is LiveBinding for b in bindings))
        _check(len({b.source_id for b in bindings})==len(bindings))
        _check(type(process_policy) is ProcessPolicy and process_policy.mode=='limits' and
               process_policy.wall_seconds<=15 and process_policy.memory_bytes<=1024**3 and
               process_policy.max_output_bytes<=262144 and process_policy.max_input_bytes<=65536)
        _check(callable(authorize) and (credentials is None or callable(credentials)))
        self._bound_principal,self._bound_tenant=principal,tenant
        self._principal,self._tenant=principal,tenant
        self._bindings={b.source_id:b for b in bindings}
        self._python=Path(python).absolute()
        _check(_file_hash(self._python.resolve())==python_sha256,'live_python_pin_mismatch')
        self._python_hash=python_sha256
        if import_roots is None:
            import_roots=(self._python.parent.parent/'lib'/
                          f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages',)
        _check(type(import_roots) in (list,tuple) and 1<=len(import_roots)<=8,'live_import_roots_required')
        roots=tuple(Path(p) for p in import_roots)
        _check(len(set(roots))==len(roots),'live_import_roots_duplicate')
        for root in roots:
            _check(root.is_absolute() and root.is_dir() and root.resolve()==root and
                   root.name in ('site-packages','dist-packages'),'live_import_root_refused')
        self._import_roots=roots
        self._import_hashes=_import_identity(roots)
        self._policy=process_policy
        for binding in bindings:
            if binding.protocol=='ros2.joint-state.v1' and binding.origin=='local_fixture':
                _check(process_policy.environment.get('ROS_AUTOMATIC_DISCOVERY_RANGE')=='LOCALHOST' and
                       process_policy.environment.get('ROS_LOCALHOST_ONLY')=='1',
                       'fixture_ros_discovery_must_be_localhost')
        self._authorize=authorize
        self._principal_policy=None
        self._cleanup_uncertain=None
        self._credentials=credentials or (lambda _source:{})
        self._source=Path(__file__).resolve().parent
        self._files={n:_file_hash(self._source/n) for n in ('__init__.py','live_intake.py','live_intake_worker.py','offline_intake.py')}
        self._public_files={}
        for binding in bindings:
            for field in ('ca_file','server_certificate'):
                path=binding.settings.get(field)
                if path:
                    self._public_files[str(path)]=_file_hash(path)
        self._config=_hash(canonical(self._public_bindings(),limits=RESPONSE_LIMITS))
        self.store=Store('live-intake:'+sha256(canonical([tenant,principal])).hexdigest(),evict=False)

    def _public_bindings(self):
        # Endpoints and local paths are hashed, never disclosed in MCP metadata.
        return [{'source_id':b.source_id,'protocol':b.protocol,'origin':b.origin,
                 'sdk_version':b.sdk_version,'credential_epoch':b.credential_epoch,
                 'settings_digest':_hash(canonical(dict(b.settings),limits=REQUEST_LIMITS))}
                for b in self._bindings.values()]

    def binding_scope(self):
        return {'bindings':self._public_bindings(),'process_policy':self._policy.identity(),
                'source_sha256':deepcopy(self._files),'python_sha256':self._python_hash,
                'import_roots_sha256':list(self._import_hashes),
                'import_policy':'explicit-roots-no-site-startup-fresh-bytecode-v1',
                'certificate_digests':sorted(self._public_files.values())}

    def descriptions(self):
        return profile_manifest()['tools']

    def _bind_principal_policy(self,policy,principal):
        """The canonical facade binds its actual policy once, in trusted code."""
        from .principal_policy import PrincipalPolicy
        _check(isinstance(policy,PrincipalPolicy) and principal==self._principal,
               'live_principal_policy_refused')
        _check(self._principal_policy is None or self._principal_policy is policy,
               'live_principal_policy_rebind_refused')
        self._principal_policy=policy

    def _current(self,name,args):
        _check((self._bound_principal,self._bound_tenant)==(self._principal,self._tenant),'live_identity_changed')
        _check(_hash(canonical(self._public_bindings(),limits=RESPONSE_LIMITS))==self._config,'live_binding_changed')
        epoch=self._authorize(name,deepcopy(args))
        _check(type(epoch) is str and 0<len(epoch)<=128,'live_read_not_authorized')
        if self._principal_policy is not None:
            # One coherent snapshot binds authorization and identity. The
            # callback epoch cannot stand in for current canonical grants.
            with self._principal_policy.lock:
                context=self._principal_policy.authorize_context(self._principal,name,args)
                epoch=_hash(canonical([epoch,context],limits=REQUEST_LIMITS))
        return epoch

    def _runtime_pins(self):
        _check(_file_hash(self._python.resolve())==self._python_hash,'live_python_pin_changed')
        _check(_import_identity(self._import_roots)==self._import_hashes,'live_sdk_files_changed')
        for name,digest in self._files.items():
            _check(_file_hash(self._source/name)==digest,'live_worker_source_changed')
        for path,digest in self._public_files.items():
            _check(_file_hash(path)==digest,'live_certificate_changed')

    def request_scope_guard(self,name,arguments):
        """Keep original source authority current across an outer audit barrier."""
        _check(name in TOOLS)
        args=parse(canonical(arguments,limits=REQUEST_LIMITS),limits=REQUEST_LIMITS)
        _shape(args,TOOLS[name]['inputSchema'])
        epoch=self._current(name,args)
        source_request=None
        source_epoch=None
        if name=='ncp_live_receipt':
            receipt=self.store.native(args['artifact_id'])
            check=deepcopy(receipt);digest=check.pop('receipt_digest')
            _check(_hash(canonical(check,limits=RESPONSE_LIMITS))==digest,'live_receipt_tampered')
            _check(receipt['principal']==self._principal and receipt['tenant']==self._tenant and
                   receipt['binding_digest']==self._config,'live_receipt_scope_changed')
            source_request=deepcopy(receipt['request'])
            source_epoch=self._current('ncp_live_read',source_request)

        def current():
            _check(self._current(name,args)==epoch,'live_authorization_changed')
            if source_request is not None:
                _check(self._current('ncp_live_read',source_request)==source_epoch,
                       'live_source_authorization_changed')
            return True

        return current

    @_guard_cleanup
    def _read(self,args,epoch):
        binding=self._bindings[args['source_id']]
        self._runtime_pins()
        secrets=self._credentials(binding.source_id)
        _check(type(secrets) is dict and set(secrets)<={'username','password','certificate','private_key'})
        _check(all(type(v) is str and 0<len(v)<=4096 for v in secrets.values()))
        request={'protocol':binding.protocol,'settings':dict(binding.settings),'secrets':secrets,
                 'count':args['count'],'seconds':args['seconds'],'sdk_version':binding.sdk_version}
        # Credential resolution is host I/O. It can overlap policy revocation
        # or runtime changes, so neither earlier check authorizes a later launch.
        self._runtime_pins()
        with tempfile.TemporaryDirectory(prefix='odsp-live-read-') as folder:
            import json
            _check(self._current('ncp_live_read',args)==epoch,'live_authorization_changed')
            code,raw=_bounded_process([str(self._python),'-B','-I','-S','-X',
                'pycache_prefix='+str(Path(folder)/'bytecode'),'-c',_BOOTSTRAP,str(self._source.parent),
                json.dumps([str(root) for root in self._import_roots])],
                cwd=Path(folder),timeout=args['seconds']+3,limit=262144,
                stdin=canonical(request,limits=REQUEST_LIMITS),process_policy=self._policy)
        _check(code==0,'live_protocol_read_refused')
        self._runtime_pins()
        value=parse(raw,limits=RESPONSE_LIMITS)
        _check(set(value)=={'schema','sdk','sdk_version','records','completed_at'} and
               value['schema']=='odsp.live-intake.worker.v1' and value['sdk_version']==binding.sdk_version and
               value['sdk']=={'mqtt.scalar-observation.v1':'paho-mqtt','opcua.scalar.v1':'asyncua',
                              'ros2.joint-state.v1':'rclpy'}[binding.protocol])
        _instant(value['completed_at'])
        _check(type(value['records']) is list and len(value['records'])<=args['count'])
        occurrence=uuid4().hex
        records=[]
        for index,row in enumerate(value['records']):
            allowed={'received_at','observation'} | ({'native_datatype'} if binding.protocol=='opcua.scalar.v1' else set())
            _check(type(row) is dict and set(row)==allowed)
            record={'record_id':occurrence+'-'+str(index),'protocol':binding.protocol,
                    'received_at':row['received_at'],'observation':row['observation']}
            normalized=_record(record)
            transport={'native_datatype':row['native_datatype']} if 'native_datatype' in row else {}
            records.append({'record':record,'interpretation':normalized,'transport':transport})
        receipt={'schema':'odsp.live-intake.receipt.v1','occurrence':occurrence,
            'principal':self._principal,'tenant':self._tenant,'source_id':binding.source_id,
            'request':deepcopy(args),
            'binding_digest':self._config,'origin':binding.origin,'protocol':binding.protocol,
            'runtime':self.binding_scope(),'sdk':value['sdk'],'sdk_version':value['sdk_version'],
            'completed_at':value['completed_at'],'records':records,
            'status':'requested_count_reached' if len(records)==args['count'] else 'deadline_partial',
            'connection_read_performed':True,'device_writes_performed':False,
            'physical_validity_established':False,'authorizes_execution':False,
            'source_clock_synchronization':'not_established'}
        receipt['receipt_digest']=_hash(canonical(receipt,limits=RESPONSE_LIMITS))
        canonical(receipt,limits=RESPONSE_LIMITS)
        artifact=self.store.keep(receipt,kind='live-observation',catalog={'source_id':binding.source_id})
        return {'artifact_id':artifact,'receipt_digest':receipt['receipt_digest'],
                'record_count':len(records),'status':receipt['status'],'origin':binding.origin,
                'connection_read_performed':True,'physical_validity_established':False}

    def call(self,name,arguments):
        begun=False
        source_request=None
        source_epoch=None
        try:
            _check(name in TOOLS)
            args=parse(canonical(arguments,limits=REQUEST_LIMITS),limits=REQUEST_LIMITS)
            _shape(args,TOOLS[name]['inputSchema'])
            epoch=self._current(name,args)
            if name=='ncp_live_sources':
                result={'sources':self._public_bindings(),'connected':False}
            elif name=='ncp_live_read':
                _check(args['source_id'] in self._bindings,'live_source_not_bound')
                begun=True
                result=self._read(args,epoch)
            else:
                _check(re.fullmatch(r'retained-[0-9a-f]{32}',args['artifact_id']) is not None)
                receipt=self.store.native(args['artifact_id'])
                check=deepcopy(receipt);digest=check.pop('receipt_digest')
                _check(_hash(canonical(check,limits=RESPONSE_LIMITS))==digest,'live_receipt_tampered')
                _check(receipt['principal']==self._principal and receipt['tenant']==self._tenant and
                       receipt['binding_digest']==self._config,'live_receipt_scope_changed')
                source_request=receipt['request']
                source_epoch=self._current('ncp_live_read',source_request)
                result=self.store.page(args['artifact_id'],args.get('offset',0),args.get('limit',4096))
                # Store.page performs another read. Disclose only the exact
                # detached receipt whose digest, identity and scope were checked.
                raw=canonical(receipt,limits=RESPONSE_LIMITS)
                offset=args.get('offset',0);chunk=raw[offset:offset+args.get('limit',4096)]
                next_offset=offset+len(chunk)
                _check(result=={'schema':'ncp.retained-page.v1','artifact_id':args['artifact_id'],
                    'byte_size':len(raw),'digest':_hash(raw),'offset':offset,
                    'next_offset':None if next_offset>=len(raw) else next_offset,
                    'chunk_b64':base64.b64encode(chunk).decode('ascii'),'authorizes_execution':False},
                    'live_receipt_page_changed')
            _check(self._current(name,args)==epoch,'live_authorization_changed')
            if source_request is not None:
                _check(self._current('ncp_live_read',source_request)==source_epoch,
                       'live_source_authorization_changed')
            response={'schema':PROFILE,'status':'ok','data':result,'authorizes_execution':False}
            from .principal_policy import PrincipalFacade
            PrincipalFacade.mcp_result(name,response)
            return response
        except Exception:
            return {'schema':PROFILE,'status':'refused','data':None,
                    'error':{'code':'outcome_unknown' if begun else 'live_read_refused'},
                    'authorizes_execution':False}
