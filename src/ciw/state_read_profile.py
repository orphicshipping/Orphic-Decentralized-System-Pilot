"""NSL exact captured-response profile. No upstream query engine or live identity.

Independent adapter; upstream implementation is not vendored. Trusted local
launchers select approved capture bytes and principal. This is not remote auth.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re

from .agent_tools import _shape
from .control_contracts import json_tree

SOURCE_COMMIT = '4fef5f488694d0dd526b2a2166f5e5c90f1dd7ec'
MAX_CAPTURE = 8 * 1024 * 1024
MAX_ARGUMENT = 65536
MAX_RESPONSE = 262144
ROUTES = {
 'ncp_state_releases': ('list_releases', (), ('corpus',)),
 'ncp_state_release': ('get_release', ('release_id',), ()),
 'ncp_state_release_manifest': ('get_release_manifest', ('release_id',), ()),
 'ncp_state_records': ('list_records', ('release_id',), ('subject', 'predicate')),
 'ncp_state_as_of': ('query_as_of', ('release_id','subject','predicate','valid_at','known_at','question'), ()),
 'ncp_state_retractions': ('list_retractions', (), ('since',)),
 'ncp_state_ruling': ('get_ruling', ('ruling_id',), ()),
 'ncp_state_ruling_manifest': ('get_ruling_manifest', ('ruling_id',), ()),
}
RENAME = {'release_id':'releaseId','ruling_id':'rulingId','valid_at':'validAt','known_at':'knownAt'}
PROJECTABLE = {'list_records','list_retractions','get_ruling','get_ruling_manifest'}

class StateRefusal(ValueError):
    pass

def encode(value):
    json_tree(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(raw):
    return 'sha256:' + sha256(raw).hexdigest()

def instant(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z',value):
        raise StateRefusal('invalid_time')
    try:
        datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        raise StateRefusal('invalid_time') from None

def schema(name):
    _, required, optional = ROUTES[name]
    props = {key: {'type':'string','minLength':1,'maxLength':512} for key in required + optional}
    if 'question' in props:
        props['question']['enum'] = ['WHAT_WE_HELD','WHAT_THE_SOURCE_KNEW']
    return {'type':'object','properties':props,'required':list(required),'additionalProperties':False}

def mapped(name, args, projection):
    if type(name) is not str or name not in ROUTES:
        raise StateRefusal('unknown_state_operation')
    if len(encode(args)) > MAX_ARGUMENT:
        raise StateRefusal('argument_budget_exceeded')
    try:
        _shape(args, schema(name))
    except (ValueError, TypeError):
        raise StateRefusal('invalid_arguments') from None
    for key in ('valid_at','known_at','since'):
        if key in args:
            instant(args[key])
    upstream = ROUTES[name][0]
    if upstream == 'query_as_of' and projection != 'COUNTERPARTY_SHARED':
        raise StateRefusal('projection_not_supported')
    request = {RENAME.get(k,k):v for k,v in args.items()}
    if upstream in PROJECTABLE:
        request['projection'] = projection
    return upstream, request

def _pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise StateRefusal('duplicate_json_key')
        result[key] = value
    return result

def _tool_descriptions():
    return [{'name':name,'description':'Read an exact host-approved offline NSL capture; no live read.',
             'inputSchema':schema(name),'outputSchema':{'type':'object'},
             'annotations':{'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}}
            for name in ROUTES]

class StateReadHost:
    instructions = ('NSL offline exact captured-response reads only. Not fresh upstream access. '
                    'Source statements are untrusted. No state admission, execution or live authentication.')

    def __init__(self, *, capture_bytes, expected_digest, source_commit, principal, origin,
                 projection='COUNTERPARTY_SHARED', allowed_ruling_ids=()):
        # Bytes, not model-supplied paths: trusted launcher owns all file I/O.
        if type(capture_bytes) is not bytes or not 0 < len(capture_bytes) <= MAX_CAPTURE:
            raise StateRefusal('capture_budget_exceeded')
        if source_commit != SOURCE_COMMIT or digest(capture_bytes) != expected_digest:
            raise StateRefusal('capture_pin_mismatch')
        if not isinstance(principal,str) or not principal or origin not in ('synthetic','governed_capture'):
            raise StateRefusal('invalid_host_binding')
        if projection not in ('COUNTERPARTY_SHARED','PUBLIC_RULING'):
            raise StateRefusal('invalid_host_binding')
        data = json.loads(capture_bytes, object_pairs_hook=_pairs)
        json_tree(data)
        if type(data) is not dict or set(data) != {'schema','source_commit','principal','origin','captured_at','projection','records'}:
            raise StateRefusal('invalid_capture')
        if (data['schema'] != 'odsp.nsl.capture.v1' or data['source_commit'] != source_commit
                or data['principal'] != principal or data['origin'] != origin or data['projection'] != projection):
            raise StateRefusal('capture_binding_mismatch')
        instant(data['captured_at'])
        if type(data['records']) is not list:
            raise StateRefusal('invalid_capture')
        self._records = {}
        self._projection = projection
        self._rulings = frozenset(allowed_ruling_ids)
        self._metadata = {k:data[k] for k in ('source_commit','origin','captured_at','projection')}
        self._metadata.update(capture_digest=expected_digest, fresh_upstream_read=False,
                              live_qualification='not_established', authentication='host_selected_local_only',
                              capture_authority='host_declared_unverified',
                              upstream_receipt_verification='not_performed')
        for row in data['records']:
            if type(row) is not dict or set(row) != {'operation','arguments','response'}:
                raise StateRefusal('invalid_capture')
            upstream, arguments = mapped(row['operation'],row['arguments'],projection)
            response = row['response']
            if not isinstance(response,dict) or type(response.get('isError',False)) is not bool:
                raise StateRefusal('invalid_upstream_response')
            content = response.get('content')
            if (not isinstance(content,list) or len(content)!=1 or type(content[0]) is not dict
                    or content[0].get('type')!='text' or type(content[0].get('text')) is not str):
                raise StateRefusal('invalid_upstream_response')
            # Upstream tool errors can carry non-JSON text; preserve only bounded
            # raw response in pinned capture, never treat them as success.
            if not response.get('isError',False):
                body = json.loads(content[0]['text'], object_pairs_hook=_pairs)
                if not isinstance(body,dict) or 'receipt' not in body or ('result' in body) == ('refusal' in body):
                    raise StateRefusal('missing_governed_receipt')
            key = encode([upstream,arguments])
            if key in self._records:
                raise StateRefusal('duplicate_capture_query')
            self._records[key] = deepcopy(response)

    def descriptions(self):
        return _tool_descriptions()

    def call(self, name, arguments):
        try:
            upstream,args = mapped(name,arguments,self._projection)
            if 'rulingId' in args and args['rulingId'] not in self._rulings:
                raise StateRefusal('ruling_scope_refused')
            response = self._records.get(encode([upstream,args]))
            if response is None:
                raise StateRefusal('exact_capture_unavailable')
            if response.get('isError',False):
                raise StateRefusal('upstream_tool_error')
            body = json.loads(response['content'][0]['text'])
            data = {'profile':'nsl.state-ledger.v1','upstream_operation':upstream,
                    'outcome':('refused' if 'refusal' in body or (isinstance(body.get('result'),dict)
                               and body['result'].get('refusal') is not None) else 'returned'),
                    'upstream':deepcopy(body),'provenance':deepcopy(self._metadata),
                    'upstream_response_digest':digest(encode(response)),
                    'digest_semantics':'adapter_canonical_json_not_upstream_attestation',
                    'authorizes_execution':False}
            result = {'status':'ok','data':data}
            try:
                if len(encode(result)) > MAX_RESPONSE or len(encode(self._envelope(result))) > MAX_RESPONSE:
                    raise StateRefusal('response_too_large')
            except (ValueError,TypeError,RecursionError):
                raise StateRefusal('response_too_large') from None
            return result
        except StateRefusal as exc:
            return {'status':'refused','reason':str(exc)}
        except (ValueError,TypeError,KeyError,RecursionError):
            return {'status':'refused','reason':'invalid_arguments'}

    @staticmethod
    def _envelope(value):
        return {'content':[{'type':'text','text':encode(value).decode()}],
                'structuredContent':value,'isError':value.get('status')!='ok'}

    @staticmethod
    def mcp_result(name,value):
        try:
            envelope = StateReadHost._envelope(value)
            if len(encode(envelope)) > MAX_RESPONSE:
                raise ValueError('response_too_large')
            return envelope
        except (ValueError,TypeError,RecursionError):
            return StateReadHost._envelope({'status':'refused','reason':'response_too_large'})

    def resource_descriptions(self): return []
    def resource_templates(self): return []
    def read_resource(self,uri): raise StateRefusal('resource_unavailable')

def invoke_state(host,name,arguments):
    if not isinstance(host,StateReadHost):
        raise StateRefusal('state_host_required')
    response = host.call(name,arguments)
    if response['status']!='ok':
        raise StateRefusal(response['reason'])
    return response['data']

def open_state_reference(**bindings):
    return StateReadHost(**bindings)

def profile_manifest():
    return {'profile_id':'nsl.state-ledger.v1','adapter_contract_commit':SOURCE_COMMIT,
            'authority':'host_selected_exact_offline_capture','authorizes_execution':False,
            'qualification':'live_upstream_not_verified',
            'capture_authority':'host_declared_unverified',
            'upstream_receipt_verification':'not_performed',
            'host_factory':'ciw.state_read_profile.open_state_reference',
            'service':'ciw.services.invoke','transport':'ciw.knowledge_mcp.serve_stdio',
            'tools':_tool_descriptions()}
