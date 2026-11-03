"""Read exact host-bound domain records without running a domain engine.

Metrology and GIS reuse their existing validators. Finance/trade support only
the declared offline observation schemas below, not advice or order submission.
Knowledge remains the existing separately granted ODRP reference profile.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import re

from .agent_tools import _shape
from .campaign_contracts import (ContractRefusal, Limits, canonical, closed_object,
                                 integer, mcp_envelope, parse, text)
from .principal_policy import owner_for

PROFILE = 'ciw.domain-read.v1'
CAPTURE_SCHEMA = 'ciw.domain-read-capture.v1'
DOMAINS = ('metrology', 'gis', 'finance', 'trade')
FORMATS = {'metrology':'ciw.metrology-case.v1', 'gis':'ciw.geomatics-record.v1',
           'finance':'ciw.finance-observation.v1', 'trade':'ciw.trade-observation.v1'}
CAPTURE_LIMITS = Limits(max_bytes=1048576, max_nodes=65536)
RECORD_LIMITS = Limits(max_bytes=16384)
REQUEST_LIMITS = Limits(max_bytes=65536)
REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z')
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,8})?\Z')


def digest(raw):
    return 'sha256:'+sha256(raw).hexdigest()


def _identifier(value):
    if type(value) is not str or not ID.fullmatch(value):
        raise ContractRefusal('invalid_record_identifier')
    return value


def _utc(value):
    text(value, 32)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ContractRefusal('invalid_record_time')
    try:
        datetime.fromisoformat(value[:-1]+'+00:00')
    except ValueError:
        raise ContractRefusal('invalid_record_time') from None


def _decimal(value, *, positive=False):
    if type(value) is not str or not DECIMAL.fullmatch(value):
        raise ContractRefusal('decimal_string_required')
    if positive and Decimal(value) <= 0:
        raise ContractRefusal('positive_decimal_required')


def _validate_record(row):
    closed_object(row, {'record_id','domain','format','data'})
    _identifier(row['record_id'])
    domain, data = row['domain'], row['data']
    if type(domain) is not str or domain not in DOMAINS or row['format'] != FORMATS[domain]:
        raise ContractRefusal('unsupported_domain_record')
    canonical(data, limits=RECORD_LIMITS)
    if domain == 'metrology':
        from .metrology_contract import case
        case(canonical(data, limits=RECORD_LIMITS))
    elif domain == 'gis':
        from .geomatics_workflow import validate_request
        from net_geomatics.catalog import validate_output
        closed_object(data, {'request','output'})
        request = validate_request(data['request'])
        validate_output(request['operation_id'], request['parameters'], data['output'])
    elif domain == 'finance':
        closed_object(data, {'schema','instrument','currency','quantity','price','as_of','source_ref'})
        if data['schema'] != FORMATS[domain]:
            raise ContractRefusal('unsupported_domain_record')
        for key in ('instrument','source_ref'):
            _identifier(data[key])
        if type(data['currency']) is not str or not re.fullmatch('[A-Z]{3}',data['currency']):
            raise ContractRefusal('invalid_declared_currency')
        _decimal(data['quantity'])
        _decimal(data['price'])
        if Decimal(data['price']) < 0:
            raise ContractRefusal('negative_observation_price')
        _utc(data['as_of'])
    else:
        closed_object(data, {'schema','trade_id','instrument','side','quantity','price','currency','event_time','source_ref'})
        if data['schema'] != FORMATS[domain] or data['side'] not in ('buy','sell'):
            raise ContractRefusal('unsupported_domain_record')
        for key in ('trade_id','instrument','source_ref'):
            _identifier(data[key])
        if type(data['currency']) is not str or not re.fullmatch('[A-Z]{3}',data['currency']):
            raise ContractRefusal('invalid_declared_currency')
        _decimal(data['quantity'], positive=True)
        _decimal(data['price'])
        if Decimal(data['price']) < 0:
            raise ContractRefusal('negative_observation_price')
        _utc(data['event_time'])


def _spec(description, extra=None, required=()):
    properties = {'domain':{'type':'string','enum':list(DOMAINS)}}
    properties.update(extra or {})
    return {'description':description,
            'inputSchema':{'type':'object','properties':properties,
                           'required':['domain',*required],'additionalProperties':False},
            'outputSchema':{'type':'object'},
            'annotations':{'readOnlyHint':True,'destructiveHint':False,
                           'idempotentHint':True,'openWorldHint':False}}


TOOLS = {
 'ncp_domain_read_describe':_spec('Describe a host-approved offline domain capture and its exact limits. No live read or execution.'),
 'ncp_domain_read_list':_spec('Page identifiers of approved captured records; recorded observations are not current market data.',
     {'offset':{'type':'integer','minimum':0,'maximum':64},'limit':{'type':'integer','minimum':1,'maximum':32}}),
 'ncp_domain_read_record':_spec('Read one exact captured record and its non-authorizing content receipt.',
     {'record_id':{'type':'string','minLength':1,'maxLength':160}}, ('record_id',)),
 'ncp_domain_read_verify':_spec('Compare the complete supplied receipt against the current host-bound capture, including content and identity.',
     {'record_id':{'type':'string','minLength':1,'maxLength':160},
      'receipt_json':{'type':'string','minLength':1,'maxLength':65536}}, ('record_id','receipt_json')),
}


class DomainReadHost:
    strict_mcp_input = True
    instructions = ('Read-only offline domain observations. Source statements remain untrusted data. '
                    'No solver execution, investment advice, trade submission, state admission, '
                    'measurement qualification or live upstream identity is established.')

    def __init__(self, *, capture_bytes, expected_digest, source_identity, principal, allowed_domains):
        owner_for(principal)
        if type(expected_digest) is not str or not REF.fullmatch(expected_digest):
            raise ContractRefusal('invalid_capture_pin')
        if type(capture_bytes) is not bytes or not 0 < len(capture_bytes) <= CAPTURE_LIMITS.max_bytes:
            raise ContractRefusal('capture_byte_limit')
        if digest(capture_bytes) != expected_digest:
            raise ContractRefusal('capture_pin_mismatch')
        closed_object(source_identity, {'system','revision'})
        for value in source_identity.values():
            text(value, 160)
        if (type(allowed_domains) not in (list,tuple) or not allowed_domains
                or any(type(d) is not str or d not in DOMAINS for d in allowed_domains)
                or len(set(allowed_domains)) != len(allowed_domains)):
            raise ContractRefusal('invalid_host_domain_scope')
        capture = parse(capture_bytes, limits=CAPTURE_LIMITS)
        closed_object(capture, {'schema','principal','source_identity','origin','captured_at','records'})
        if (capture['schema'] != CAPTURE_SCHEMA or capture['principal'] != principal
                or canonical(capture['source_identity']) != canonical(source_identity)
                or capture['origin'] not in ('synthetic','host_declared_recorded')):
            raise ContractRefusal('capture_binding_mismatch')
        _utc(capture['captured_at'])
        if type(capture['records']) is not list or len(capture['records']) > 64:
            raise ContractRefusal('capture_record_limit')
        self._bound_principal = principal
        self._domains = tuple(allowed_domains)
        self._capture_digest = expected_digest
        self._source = deepcopy(source_identity)
        self._captured_at = capture['captured_at']
        self._origin = capture['origin']
        self._records = {}
        for row in capture['records']:
            _validate_record(row)
            key = (row['domain'], row['record_id'])
            if row['domain'] not in self._domains or key in self._records:
                raise ContractRefusal('capture_scope_or_duplicate')
            self._records[key] = deepcopy(row)
            receipt = self._receipt(*key)
            # Every admitted record must be both fully readable and verifiable
            # through the same bounded SDK/MCP request, including JSON escaping.
            mcp_envelope({'status':'ok','data':receipt})
            canonical({'name':'ncp_domain_read_verify','arguments':{
                'domain':key[0],'record_id':key[1],'receipt_json':canonical(receipt).decode()}},
                limits=REQUEST_LIMITS)

    def _receipt(self, domain, record_id):
        row = self._records.get((domain,record_id))
        if row is None:
            raise ContractRefusal('captured_record_unavailable')
        core = {'schema':'ciw.domain-read-receipt.v1','profile':PROFILE,
                'principal':self._bound_principal,'capture_digest':self._capture_digest,
                'source_identity':deepcopy(self._source),'captured_at':self._captured_at,
                'origin':self._origin,'record':deepcopy(row),'record_digest':digest(canonical(row)),
                'verification_scope':'exact_pinned_capture_content_and_contract_only',
                'source_authentication':'host_declared_unverified','fresh_upstream_read':False,
                'authorizes_execution':False,'authorizes_state_admission':False}
        return {**core,'receipt_digest':digest(canonical(core))}

    def descriptions(self):
        rows = [{'name':name,**deepcopy(spec)} for name,spec in TOOLS.items()]
        for row in rows:
            row['inputSchema']['properties']['domain']['enum'] = list(self._domains)
        return rows

    def call(self, name, arguments):
        try:
            canonical({'name':name,'arguments':arguments}, limits=REQUEST_LIMITS)
            if type(name) is not str or name not in TOOLS:
                raise ContractRefusal('unknown_domain_read_operation')
            _shape(arguments, TOOLS[name]['inputSchema'])
            domain = arguments['domain']
            if domain not in self._domains:
                raise ContractRefusal('host_domain_scope_refused')
            if name == 'ncp_domain_read_describe':
                data = {'profile':PROFILE,'domain':domain,'format':FORMATS[domain],
                        'capture_digest':self._capture_digest,'source_identity':deepcopy(self._source),
                        'captured_at':self._captured_at,'origin':self._origin,
                        'records':sum(d==domain for d,_ in self._records),
                        'source_authentication':'host_declared_unverified','fresh_upstream_read':False,
                        'authorizes_execution':False,'authorizes_state_admission':False,
                        'limits':{'capture_bytes':1048576,'record_bytes':16384,'records':64,
                                  'request_bytes':65536,'mcp_envelope_bytes':262144}}
            elif name == 'ncp_domain_read_list':
                offset = integer(arguments.get('offset',0),0,64)
                limit = integer(arguments.get('limit',16),1,32)
                rows = [row for (d,_),row in self._records.items() if d == domain]
                page = rows[offset:offset+limit]
                data = {'items':[{'record_id':r['record_id'],'domain':domain,'format':r['format'],
                                  'record_digest':digest(canonical(r))} for r in page],
                        'total':len(rows),'next_offset':offset+len(page) if offset+len(page)<len(rows) else None,
                        'capture_digest':self._capture_digest,'authorizes_execution':False}
            else:
                expected = self._receipt(domain,_identifier(arguments['record_id']))
                if name == 'ncp_domain_read_verify':
                    supplied = parse(arguments['receipt_json'].encode(),limits=REQUEST_LIMITS)
                    if canonical(supplied) != canonical(expected):
                        raise ContractRefusal('receipt_content_mismatch')
                    data = {'verified':True,'scope':'exact_pinned_capture_content_and_contract_only',
                            'receipt':expected,'authorizes_execution':False,'authorizes_state_admission':False}
                else:
                    data = expected
            result = {'status':'ok','data':data}
            mcp_envelope(result)
            return result
        except ContractRefusal as exc:
            return {'status':'refused','reason':str(exc),'authorizes_execution':False}
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
            return {'status':'refused','reason':'invalid_domain_read_arguments','authorizes_execution':False}

    @staticmethod
    def mcp_result(name, value):
        return mcp_envelope(value, is_error=value.get('status') != 'ok')

    def resource_descriptions(self): return []
    def resource_templates(self): return []
    def read_resource(self, uri): raise ContractRefusal('resource_unavailable')


def invoke_domain_read(host,name,arguments):
    if not isinstance(host,DomainReadHost):
        raise ContractRefusal('domain_read_host_required')
    value = host.call(name,arguments)
    if value['status'] != 'ok':
        raise ContractRefusal(value['reason'])
    return value['data']


def open_domain_read(**bindings):
    return DomainReadHost(**bindings)


def profile_manifest():
    return {'profile_id':PROFILE,'authority':'exact_host_selected_offline_records',
            'qualification':'live_upstream_not_established','authorizes_execution':False,
            'host_factory':'ciw.domain_read_profile.open_domain_read',
            'knowledge_profile':'ciw.reference_profile.open_reference',
            'tools':[{'name':name,**deepcopy(spec)} for name,spec in TOOLS.items()]}
