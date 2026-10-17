"""Retained alarm review and the existing Link supervisory command boundary.

Acknowledgment is review context, never an equipment reset. Configuration plans
reuse Link's validated revision/authority contracts. The installed DeviceRuntime
supports synthetic configuration only and continues to refuse physical dispatch.
"""
from __future__ import annotations

from copy import deepcopy
import time

from . import link_contract as c
from .agent_api import identifier, parse
from .control_contracts import keys, number
from .core.identities import content_identity
from .link_runtime import prepare_configuration, DeviceRuntime, Authority
from .operational_context import config_diff
from .operational_store import Records


def validate(spec, sources):
    keys(spec,{'alarms','configuration_candidates'})
    if type(spec['alarms']) is not dict or type(spec['configuration_candidates']) is not dict:
        raise ValueError('Supervisory rules and candidates must be operator-bound objects')
    if len(spec['alarms'])>16 or len(spec['configuration_candidates'])>16:raise ValueError('Supervisory profile exceeds bound')
    for name,rule in spec['alarms'].items():
        identifier(name);keys(rule,{'source','channel','clock','boot','high','max_age','semantics'})
        if rule['source'] not in sources:raise ValueError('Alarm source not admitted')
        if number(rule['max_age'])<0:raise ValueError('Alarm max age must be nonnegative')
        number(rule['high'])
        if rule['semantics']!='engineering_review_only':raise ValueError('Alarm does not replace local equipment protection')
    for name,candidate in spec['configuration_candidates'].items():
        identifier(name);keys(candidate,{'source','new_configuration','expiry_duration_s','required_proposal_template'})
        if candidate['source'] not in sources or not 0<number(candidate['expiry_duration_s'])<=3600:
            raise ValueError('Invalid supervisory configuration candidate')
    c.bounded(spec,32768)


def alarm_evaluate(host, *, query, rule, as_of):
    identifier(rule);number(as_of)
    policy=host.investigation['supervision']['alarms'].get(rule)
    if policy is None:raise PermissionError('Alarm rule not granted')
    with host._records() as records:
        q=host._owned(records,query)
        if q['schema']!='ncp.operational-query.v1':raise ValueError('Alarm requires a retained interval query')
        selection=q['selection']
        if q['source']!=policy['source'] or any(selection[k]!=policy[k] for k in ('channel','clock','boot')):
            raise ValueError('Alarm source/quantity/clock/boot differs from policy')
        # Reuse exact full-interval selection rather than a display page.
        raw,binding=host._source(records,q['source'])
        from .operational_context import query as project
        selected=[]
        for offset in range(0,256,32):
            page=project(raw,binding,**{k:selection[k] for k in ('clock','boot','channel','start','end')},offset=offset,limit=32)
            selected+=page['rows']
            if page['next_offset'] is None:break
        latest=max(selected,key=lambda r:r['time']) if selected else None
        u=binding['context']['time_uncertainty']
        validity='missing' if latest is None else latest['quality']
        age=None if latest is None else as_of-latest['time']
        stale=age is None or u is None or age<0 or age+u>policy['max_age']
        value=None if latest is None else latest['value']
        status='UNKNOWN' if stale or validity!='valid' or value is None else 'ACTIVE' if value>=policy['high'] else 'INACTIVE'
        v={'schema':'ncp.operational-alarm.v1','rule':rule,'policy_ref':content_identity(policy),'query':query,
            'status':status,'sample_validity':validity,'stale':stale,'age_in_source_clock':age,'as_of':as_of,
            'observation':None if latest is None else latest['id'],'source_time':None if latest is None else latest['time'],
            'value':value,'threshold':policy['high'],'local_protection':'independent; unchanged','acknowledged':False,
            'scope':'engineering_review_only; historical selection','connectivity':binding['context']['connectivity']}
        return host._retain(records,v)


def alarm_acknowledge(host, *, alarm, note):
    with host._records() as records:
        v=host._owned(records,alarm)
        if v['schema']!='ncp.operational-alarm.v1':raise ValueError('Alarm acknowledgment requires an exact retained alarm')
        return host._retain(records,{'schema':'ncp.operational-alarm-ack.v1','alarm':alarm,'note':note,
            'scope':'operator_review_context_only','equipment_alarm_reset':False,'alarm_condition_changed':False})


def alarm_inspect(host, *, alarm):
    with host._records() as records:
        v=host._owned(records,alarm)
        if v['schema']!='ncp.operational-alarm.v1':raise ValueError('Unknown alarm record')
        acknowledgments=[]
        for item in records.catalog('ncp.operational-alarm-ack.v1'):
            ack=records.get(item['ref'])
            if ack.get('investigation')==host.spec['investigation'] and ack['alarm']==alarm:
                acknowledgments.append({'ref':item['ref'],'data':ack})
        return {'status':'RETAINED','record':alarm,'data':v,'acknowledgments':acknowledgments,
                'new_executions':0,'device_contact':False}


def command_prepare(host, *, proposal, candidate):
    identifier(candidate)
    binding=host.investigation['supervision']['configuration_candidates'].get(candidate)
    if binding is None:raise PermissionError('Unknown operator configuration candidate')
    with host._records() as records:
        p=host._owned(records,proposal)
        if p['schema']!='ncp.operational-proposal.v1' or p['template']!=binding['required_proposal_template']:
            raise ValueError('Command requires the specified evidence-linked proposal')
        if host.inspect_record(proposal)['applicability']!='BOUND_TO_FROZEN_INVESTIGATION':raise ValueError('Proposal is stale')
        raw,context=host._source(records,binding['source'])
        if context['family']!='link-capture':raise ValueError('Configuration adapter requires an exact Link capture')
        capture=parse(raw)
        device=capture['manifest']
        plan=prepare_configuration(device,binding['new_configuration'],expires_ns=time.time_ns()+int(binding['expiry_duration_s']*1e9))
        # This validates native structure and revision; it does not approve a scientific claim.
        validation={'schema':'ncp.supervisory-command-validation.v1','plan_ref':plan['plan_ref'],
            'status':'PASS','scope':'native_Link_configuration_structure_revision_and_frozen_proposal',
            'physical_qualification':'not_established','equipment_permission':'not_granted'}
        validation_ref=records.put(validation)
        return host._retain(records,{'schema':'ncp.supervisory-command.v1','proposal':proposal,'candidate':candidate,
            'source':binding['source'],'binding_ref':host.investigation['sources'][binding['source']]['binding'],
            'plan':plan,'validation':validation_ref,'approval':'not_granted','dispatch':'not_performed',
            'supported_effect':'simulated_configuration_only','physical_dispatch':'unavailable'})


def approve_command(profile, command, *, expected_ref, approval_ref, principal, expires_ns):
    """Explicit local operator act; never an MCP tool or a profile/grant edit."""
    from .agent_api import parse
    from .link_store import Journal,safe_path
    spec=parse(profile.read_bytes())
    c.content_ref(expected_ref);c.text(approval_ref);identifier(principal);c.integer(expires_ns)
    if command!=expected_ref:raise ValueError('Approval must bind the exact reviewed command')
    with Journal(safe_path(profile.parent/spec['journal'])) as db:
        records=Records(db)
        revoked=db.state('oe-revoked:'+spec['investigation'])
        if revoked is not None and revoked['value'].get('revoked') is True:raise PermissionError('Investigation revoked')
        v=records.get(command)
        if v.get('investigation')!=spec['investigation'] or v['schema']!='ncp.supervisory-command.v1':raise PermissionError('Wrong command/investigation')
        if not time.time_ns()<expires_ns<=v['plan']['expires_ns']:raise ValueError('Approval expiration exceeds command applicability')
        value={'schema':'ncp.supervisory-command-approval.v1','command':command,'expected_ref':expected_ref,
            'principal':principal,'approval_ref':approval_ref,'expires_ns':expires_ns,'scope':'simulated_configuration_only'}
        ref=records.put(value);records._immutable('oe-command-approval:'+command,{'approval':ref})
        return {'status':'APPROVED_SIMULATED_CONFIGURATION','command':command,'approval':ref,'physical_permission':'not_granted'}


def command_dispatch(host, *, command, attempt):
    identifier(attempt)
    with host._records() as records:
        v=host._owned(records,command)
        if v['schema']!='ncp.supervisory-command.v1':raise ValueError('Require an exact prepared supervisory command')
        if host.inspect_record(v['proposal'])['applicability']!='BOUND_TO_FROZEN_INVESTIGATION':raise ValueError('Proposal is stale')
        approval=records.journal.state('oe-command-approval:'+command)
        if approval is None:raise PermissionError('Exact command lacks operator approval')
        approved=records.get(approval['value']['approval'])
        now=time.time_ns()
        if now>=approved['expires_ns'] or approved['command']!=command:raise PermissionError('Approval expired or mismatched')
        raw,binding=host._source(records,v['source'])
        from .agent_api import parse
        from .link_gateway import validate_capture
        capture=parse(raw);native=validate_capture(capture)
        if not native['observations'] or any(r['origin']!='synthetic' for r in native['observations']):
            return {'status':'refused','reason':'physical_configuration_dispatch_unavailable','command':command,'device_contact':False}
        key='oe-command-attempt:'+content_identity([host.spec['investigation'],attempt])
        old=records.journal.state(key)
        if old is not None:
            if old['value']['command']!=command:raise ValueError('Attempt belongs to a different command')
            return {**old['value'],'reused_response':True}
        claimed={'schema':'ncp.supervisory-command-attempt.v1','investigation':host.spec['investigation'],
            'command':command,'attempt':attempt,'status':'incomplete','outcome':'unknown','automatic_replay':False}
        records.journal.compare_and_set_state(key,0,claimed)
        try:
            runtime=DeviceRuntime(capture['manifest'],records.journal,boot_id=capture['boot_id'],origin='synthetic')
            authority=Authority(approved['principal'],runtime.device['device_id'],frozenset({'CONFIGURE'}),approved['expires_ns'])
            replacement,review=runtime.configure_simulated(v['plan'],authority,now_ns=now)
            result={**claimed,'status':'completed','outcome':'simulated_configuration_applied','review':review,
                'new_boot':replacement.boot_id,'approval':approval['value']['approval'],'physical_dispatch':'not_performed'}
        except Exception as exc:
            result={**claimed,'status':'failed','outcome':'unknown_after_dispatch','reason':type(exc).__name__+': '+str(exc)[:256]}
        receipt=records.put(result)
        current=records.journal.state(key)
        records.journal.compare_and_set_state(key,current['revision'],{**result,'receipt':receipt})
        return {**result,'receipt':receipt,'reused_response':False}
