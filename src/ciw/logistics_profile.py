"""Read-only facade over OLM's governed inspect_retained; no domain engine.
Source contract: ORPHIC-LOGISTICS-MCP 217a09016116123a5be95544b870e990c895e142.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, json, re, subprocess, uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from .retention import Store

PIN = '217a09016116123a5be95544b870e990c895e142'
PROTOCOL = '2025-06-18'
MAX_INPUT = 16384
MAX_UPSTREAM = 6 * 1024 * 1024
MAX_OUTPUT = 262144
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')
KINDS = frozenset(('workflow','acquisition','compute','observation','catalog','state','twin','package'))
ROLES = {'catalog':'pilot:compute-read','compute':'pilot:compute-read',
         'observation':'pilot:observation-read','state':'pilot:state-read','twin':'pilot:twin-read'}
# Fixed, independently authored serializer: ECMAScript JSON numeric semantics and
# UTF-16 key ordering. No request can supply code or select the executable.
CANON = """let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{let c=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(c).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+c(v[k])).join(',')+'}';process.stdout.write(c(JSON.parse(s)));});"""

def raw(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()

def canonical_digest(body, node):
    if len(body) > MAX_UPSTREAM: raise ValueError('upstream_response_too_large')
    result = subprocess.run([node, '-e', CANON], input=body, capture_output=True, timeout=10, check=True)
    return hashlib.sha256(result.stdout).hexdigest()

def exact(value, required, optional=()):
    if type(value) is not dict or set(value)-set(required)-set(optional) or not set(required)<=set(value):
        raise ValueError('invalid_arguments')

def identifier(value):
    if type(value) is not str or not ID.fullmatch(value): raise ValueError('invalid_identifier')
    return value

def selector(value):
    if type(value) is not dict: raise ValueError('invalid_selector')
    kind=value.get('kind')
    fields={'acquisition':['planId'], 'compute':['jobId','include'],
            'observation':['observationId','observationDigest'], 'catalog':[],
            'state':['entityId','validAt','knownAt'], 'twin':['projectionId']}
    if kind=='workflow':
        mode=value.get('mode')
        if mode not in ('plan','run'): raise ValueError('invalid_selector')
        needed=['mode','planId']+(['runId'] if mode=='run' else [])
    elif kind=='package':
        if value.get('mode')!='manifest': raise ValueError('invalid_selector')
        needed=['mode','packageId','version']
    elif kind in fields: needed=fields[kind]
    else: raise ValueError('invalid_selector')
    exact(value,['kind']+needed)
    for key in needed:
        v=value[key]
        if key=='include':
            if v not in ('status','result'): raise ValueError('invalid_selector')
        elif key=='observationDigest':
            if type(v) is not str or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',v): raise ValueError('invalid_digest')
        elif key in ('validAt','knownAt'):
            if type(v) is not str or not 1<=len(v)<=256 or any(ord(c)<32 or ord(c)==127 for c in v): raise ValueError('invalid_time')
        else: identifier(v)
    return dict(value)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('upstream_redirect_refused')

class HTTPSReader:
    """Host-configured endpoint only. No retries or credential fallback."""
    def __init__(self,endpoint,token,tenant):
        u=urlsplit(endpoint)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or u.query or u.path!='/api/pilot/mcp': raise ValueError('invalid_host_endpoint')
        if any(not isinstance(v,str) or not v or '\r' in v or '\n' in v for v in (token,tenant)): raise ValueError('invalid_host_identity')
        self.endpoint,self.token,self.tenant=endpoint,token,tenant
    def __call__(self,body):
        req=Request(self.endpoint,data=body,headers={'Authorization':'Bearer '+self.token,'x-tenant-id':self.tenant,'x-read-purpose':'read-surface:mcp','mcp-protocol-version':PROTOCOL,'Content-Type':'application/json'},method='POST')
        try: response=build_opener(NoRedirect()).open(req,timeout=20)
        except HTTPError as exc: response=exc
        with response:
            data=response.read(MAX_UPSTREAM+1)
            if len(data)>MAX_UPSTREAM: raise ValueError('upstream_response_too_large')
            return response.status,dict(response.headers),data

class LogisticsHost:
    instructions='ODSP OLM retained reads only. No submission or execution authority.'
    def __init__(self, *, transport, principal, tenant, roles, allowed_kinds, node, source_commit=PIN, synthetic=False):
        if source_commit!=PIN or not principal or not tenant or not node.startswith('/'):
            raise ValueError('invalid_host_binding')
        if not set(allowed_kinds)<=KINDS: raise ValueError('invalid_host_scope')
        self.transport,self.node,self.roles=transport,node,frozenset(roles)
        self._bound_principal, self._bound_tenant = principal, tenant
        self.allowed=frozenset(allowed_kinds)
        self.synthetic=bool(synthetic)
        # Tenant and principal cannot share retained artifacts by collision.
        self.store=Store('olm:'+hashlib.sha256(raw([tenant,principal])).hexdigest())
    def descriptions(self): return [{'name':n,**deepcopy(v)} for n,v in TOOLS.items()]
    def _require_scope(self, source):
        kind = source['kind']
        if kind not in self.allowed or 'pilot:agent-read' not in self.roles or (kind in ROLES and ROLES[kind] not in self.roles):
            raise ValueError('host_scope_refused')
    def call(self,name,args):
        try: return {'status':'ok','data':self.invoke(name,args)}
        except (ValueError, OSError, subprocess.SubprocessError, TypeError, KeyError, UnicodeError):
            # Do not expose upstream bodies, tokens or transport exception text.
            return {'status':'refused','reason':'logistics_read_refused','authorizes_execution':False}
    def invoke(self,name,args):
        if type(args) is not dict or len(raw(args))>MAX_INPUT: raise ValueError('invalid_arguments')
        if name=='ncp_logistics_retained_read':
            exact(args,['artifact_id'],['offset','limit'])
            saved=self.store.native(args['artifact_id'])
            if saved.get('profile')!='olm.logistics.v1' or saved.get('adapter_contract_commit')!=PIN:
                raise ValueError('retained_profile_pin_mismatch')
            self._require_scope(selector(saved.get('source')))
            return self.store.page(args['artifact_id'],args.get('offset',0),args.get('limit',32768))
        if name=='ncp_logistics_catalog': exact(args,[]); source={'kind':'catalog'}
        elif name=='ncp_logistics_describe':
            exact(args,['capability_id'],['version']); identifier(args['capability_id'])
            if 'version' in args: identifier(args['version'])
            source={'kind':'catalog'}
        elif name=='ncp_logistics_job':
            exact(args,['job_id','include']); source={'kind':'compute','jobId':args['job_id'],'include':args['include']}
        elif name=='ncp_logistics_inspect': exact(args,['source']); source=args['source']
        else: raise ValueError('unknown_logistics_operation')
        source=selector(source); kind=source['kind']
        self._require_scope(source)
        request={'jsonrpc':'2.0','id':uuid.uuid4().hex,'method':'tools/call','params':{'name':'inspect_retained','arguments':{'source':source}}}
        body=raw(request)
        if len(body)>MAX_INPUT: raise ValueError('request_too_large')
        status,headers,body=self.transport(body)
        if type(body) is not bytes or len(body)>MAX_UPSTREAM: raise ValueError('upstream_response_too_large')
        headers={k.lower():v for k,v in headers.items()}
        digest=canonical_digest(body,self.node)
        if headers.get('x-response-canonical-digest')!=digest: raise ValueError('upstream_digest_mismatch')
        def unique(pairs):
            out={}
            for key,value in pairs:
                if key in out: raise ValueError('duplicate_upstream_key')
                out[key]=value
            return out
        result=json.loads(body,object_pairs_hook=unique,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non_json_number')))
        if status!=200 or type(result) is not dict or result.get('jsonrpc')!='2.0' or result.get('id')!=request['id'] or 'error' in result: raise ValueError('upstream_refusal')
        whole_rpc=result
        result=result.get('result')
        if type(result) is not dict or result.get('isError') is not False or type(result.get('structuredContent')) is not dict: raise ValueError('upstream_refusal')
        if headers.get('mcp-protocol-version') != PROTOCOL: raise ValueError('upstream_protocol_mismatch')
        for header in ('x-source-read-receipt-hash','x-read-receipt-hash','x-result-digest'):
            if not re.fullmatch(r'[a-f0-9]{64}',headers.get(header,'')): raise ValueError('upstream_receipt_missing')
        data=result['structuredContent']
        projection=None
        if name=='ncp_logistics_describe':
            catalog=data.get('capabilities')
            if type(catalog) is not dict or catalog.get('contract')!='capability.catalog.v1' or catalog.get('executionAuthorized') is not False or type(catalog.get('definitions')) is not list or type(catalog.get('implementations')) is not list: raise ValueError('invalid_catalog')
            definitions=[d for d in catalog['definitions'] if isinstance(d,dict) and d.get('id')==args['capability_id'] and ('version' not in args or d.get('version')==args['version'])]
            if not definitions: raise ValueError('capability_not_found')
            pairs={(d['id'],d['version']) for d in definitions}
            projection={'definitions':definitions,'implementations':[i for i in catalog['implementations'] if isinstance(i,dict) and (i.get('capabilityId'),i.get('capabilityVersion')) in pairs]}
        receipt={k:headers[k] for k in ('x-source-read-receipt-hash','x-read-receipt-hash','x-result-digest','x-response-canonical-digest') if k in headers}
        bundle={'profile':'olm.logistics.v1','adapter_contract_commit':PIN,'upstream_revision_verified':False,'source':source,'whole_rpc':whole_rpc,'receipts':receipt,'whole_rpc_digest_verified':True,'synthetic':self.synthetic,'authorizes_execution':False}
        if projection is not None:
            bundle['projection']=projection; bundle['projection_digest']=hashlib.sha256(raw(projection)).hexdigest()
        if len(raw(bundle))>3_800_000: raise ValueError('retention_capacity_exceeded_4mb_owner_budget')
        artifact=self.store.keep(bundle,kind='olm-retained-read',catalog={'profile':'olm.logistics.v1'})
        out={'artifact_id':artifact,'source_rpc_digest':digest,'synthetic':self.synthetic,'authorizes_execution':False,'page_operation':'ncp_logistics_retained_read'}
        if len(raw(bundle))<=65536: out['bundle']=bundle
        if len(raw(out))>MAX_OUTPUT: raise ValueError('output_budget')
        return out
    def mcp_result(self,name,value):
        return {'content':[{'type':'text','text':json.dumps(value)}],'structuredContent':value,'isError':value['status']!='ok'}

def invoke_logistics(host,name,arguments):
    if not isinstance(host,LogisticsHost): raise ValueError('logistics_host_required')
    response = host.call(name,arguments)
    if response['status'] != 'ok':
        raise ValueError(response['reason'])
    return response['data']

def schema(properties,required): return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
S={'type':'string','minLength':1,'maxLength':256}
TOOLS={
'ncp_logistics_catalog':{'description':'Read governed retained capability catalog; no execution.', 'inputSchema':schema({},[])},
'ncp_logistics_describe':{'description':'Read all matching catalog versions, or exact version.', 'inputSchema':schema({'capability_id':S,'version':S},['capability_id'])},
'ncp_logistics_job':{'description':'Read existing job only; never submits.', 'inputSchema':schema({'job_id':S,'include':{'enum':['status','result']}},['job_id','include'])},
'ncp_logistics_inspect':{'description':'Read one exact permitted retained selector.', 'inputSchema':schema({'source':{'type':'object'}},['source'])},
'ncp_logistics_retained_read':{'description':'Page immutable host-owned result bytes.', 'inputSchema':schema({'artifact_id':S,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':32768}},['artifact_id'])}}

# Discovery schema mirrors the same closed selectors enforced at execution.
_selector_schemas=[]
for _kind,_fields in [('workflow',{'mode':{'const':'plan'},'planId':S}),('workflow',{'mode':{'const':'run'},'planId':S,'runId':S}),('acquisition',{'planId':S}),('compute',{'jobId':S,'include':{'enum':['status','result']}}),('observation',{'observationId':S,'observationDigest':{'type':'string','pattern':'^sha256:[a-fA-F0-9]{64}$'}}),('catalog',{}),('state',{'entityId':S,'validAt':S,'knownAt':S}),('twin',{'projectionId':S}),('package',{'mode':{'const':'manifest'},'packageId':S,'version':S})]:
    _props={'kind':{'const':_kind},**_fields}
    _selector_schemas.append(schema(_props,list(_props)))
TOOLS['ncp_logistics_inspect']['inputSchema']['properties']['source']={'oneOf':_selector_schemas}
for _name,_spec in TOOLS.items():
    _spec['outputSchema'] = {'type':'object'}
    _spec['annotations'] = {'readOnlyHint':True,'destructiveHint':False,
                            'idempotentHint':_name=='ncp_logistics_retained_read',
                            'openWorldHint':_name!='ncp_logistics_retained_read'}

def open_logistics(*,endpoint,token,tenant,principal,roles,allowed_kinds,node,source_commit=PIN):
    """Trusted launcher only; does not authenticate/upgrade declared roles itself."""
    return LogisticsHost(transport=HTTPSReader(endpoint,token,tenant),tenant=tenant,
        principal=principal,roles=roles,allowed_kinds=allowed_kinds,node=node,
        source_commit=source_commit)

def profile_manifest():
    return {'profile_id':'olm.logistics.v1','adapter_contract_commit':PIN,
            'authority':'upstream_governed_retained_reads','authorizes_execution':False,
            'upstream_revision_verified':False,'qualification':'live_upstream_not_verified',
            'host_factory':'ciw.logistics_profile.open_logistics',
            'service':'ciw.services.invoke','transport':'ciw.logistics_mcp.serve',
            'upstream_identity':'host_configured_then_upstream_authenticated',
            'receipt_hashes':'preserved_upstream_claims_not_independently_attested',
            'maximum_bundle_bytes':3_800_000,'owner_retention_bytes':4_000_000,
            'tools':[{'name':n,**deepcopy(v)} for n,v in TOOLS.items()]}
