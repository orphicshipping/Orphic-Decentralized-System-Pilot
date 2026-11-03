"""Opt-in durable audit barrier around the existing current-principal facade.

The launcher supplies the chain and independent durable anchor callbacks. An
audit admission is never an execution grant. No sink, path, identity or recovery
decision is accepted through MCP arguments. Existing unaudited hosts are unchanged.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from hashlib import sha256
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .audit_chain import (AuditChain, AuditedCall, AuditOutcomeUncertain, AuditRefusal,
                         AuditReplay, standard_result_outcome)
from .campaign_contracts import canonical, parse, REQUEST_LIMITS
from .principal_policy import PrincipalFacade, PrincipalOutcomeUnknown, PrincipalRefusal, _specs


def _digest(value, **kwargs):
    return 'sha256:' + sha256(canonical(value, **kwargs)).hexdigest()


class AuditedPrincipalHost:
    strict_mcp_input = True
    instructions = (PrincipalFacade.instructions +
        ' Admitted calls use a host-owned redacted hash chain with an independent durable anchor. '
        'Unknown custody requires host reconciliation and never implies rollback or retry permission.')

    def __init__(self, facade, *, chain, read_anchor, compare_and_set_anchor):
        if type(facade) is not PrincipalFacade or type(chain) is not AuditChain:
            raise ValueError('an existing principal facade and audit chain are required')
        known=set(_specs()) | {'resource_read'}
        if not chain.allowed <= known:
            raise ValueError('audit operations must name existing canonical services')
        self.facade=facade
        self._bound_facade, self._bound_principal = facade, facade.principal
        self._identity_refused = False
        self._scope_checks = ContextVar('audited_host_scope_checks', default=())
        self._audit=AuditedCall(chain,read_anchor=read_anchor,compare_and_set_anchor=compare_and_set_anchor,
            principal_ref='sha256:'+sha256(self._bound_principal.encode('ascii')).hexdigest(),
            result_outcome=standard_result_outcome)

    def _check_identity(self):
        """Return the constructor binding, or permanently refuse this wrapper.

        Restoring the original object/principal after an observed rebind does
        not reset this state or any audit hold. A trusted launcher must build a
        new wrapper, whose unchanged chain/anchor still governs unresolved work.
        This consistency check is not a sandbox against a local administrator.
        """
        facade = self._bound_facade
        if (self._identity_refused or getattr(self, 'facade', None) is not facade
                or type(facade) is not PrincipalFacade
                or type(getattr(facade, 'principal', None)) is not str
                or facade.principal != self._bound_principal):
            self._identity_refused = True
            raise PrincipalRefusal()
        return facade

    @contextmanager
    def scope_guard(self, check):
        """Trusted caller consistency check; never an execution grant.

        Checks are local to this synchronous invocation context and nest without
        changing the shared journal. They run after audit I/O at dispatch and
        before disclosure. No callback is accepted in a tool's arguments.
        """
        if not callable(check):
            raise TypeError('host_scope_check_required')
        token = self._scope_checks.set(self._scope_checks.get() + (check,))
        try:
            yield self
        finally:
            self._scope_checks.reset(token)

    @staticmethod
    def _check_scope(checks):
        for check in checks:
            if check() is not True:
                raise PrincipalRefusal()

    def _checked_result(self, value, effect_state, checks=()):
        try:
            facade = self._check_identity()
            with facade.policy.lock:
                self._check_identity()
                self._check_scope(checks)
        except Exception:
            # A call/read already happened. Withhold its data without pretending
            # it was prevented or undoing any terminal event already committed.
            raise AuditOutcomeUncertain(effect_state) from None
        return value

    def _discovery(self, read):
        facade = self._check_identity()
        with facade.policy.lock:
            self._check_identity()
            value = read(facade)
            self._check_identity()
            return value

    def call(self, name, arguments, *, call_id=None):
        # call_id is a trusted SDK invocation parameter. MCP transports call this
        # with exactly name/arguments; an arguments.call_id field grants nothing.
        try:
            checks = self._scope_checks.get()
            self._check_identity()
            raw=canonical({'name':name,'arguments':arguments},limits=REQUEST_LIMITS)
            # The SDK caller may retain/mutate its object while durable audit
            # I/O blocks. Hash and dispatch one bounded, detached JSON snapshot.
            request=parse(raw,limits=REQUEST_LIMITS)
            name,arguments=request['name'],request['arguments']
            request_ref='sha256:'+sha256(raw).hexdigest()
            if type(name) is not str or name not in _specs() or name not in self._audit.chain.allowed:
                raise PrincipalRefusal()
            # A profile's signer/source authority may change during journal or
            # anchor I/O after its own call has returned. Bind its additional
            # checks to this exact detached request and apply the same barriers.
            guard = self._check_identity().request_scope_guard(name, arguments)
            if guard is not None:
                checks = checks + (guard,)
            dispatched = False
            def dispatch():
                nonlocal dispatched
                try:
                    facade = self._check_identity()
                except PrincipalRefusal:
                    return PrincipalFacade.refusal()
                with facade.policy.lock:
                    # Audit callbacks and the policy lock can both block. Check
                    # again after each boundary, before using principal grants.
                    try:
                        self._check_identity()
                        self._check_scope(checks)
                    except Exception:
                        return PrincipalFacade.refusal()
                    dispatched = True
                    return self._checked_result(facade.call(name,arguments), 'unknown', checks)
            value = self._audit.invoke(operation=name,call_id=call_id or uuid4().hex,
                request_ref=request_ref,call=dispatch,result_ref=_digest)
            return self._checked_result(value, 'returned', checks) if dispatched else value
        except AuditReplay as exc:
            # Previous effects are never replayed by the journal wrapper. The
            # underlying operation's existing inspection API remains authoritative.
            state=exc.event.get('effect_state','unknown')
            return {'status':'refused','data':None,'error':{'code':'audit_call_already_recorded',
                    'effect_state':state},'authorizes_execution':False}
        except AuditOutcomeUncertain as exc:
            return {'status':'refused','data':None,'error':{
                'code':'audit_unavailable' if exc.effect_state=='not_started' else 'outcome_unknown',
                'effect_state':exc.effect_state},'authorizes_execution':False}
        except AuditRefusal:
            # Existing unresolved custody, a stale anchor or a full journal
            # refuses new work. Do not clear the hold or retry with another ID.
            return {'status':'refused','data':None,'error':{'code':'audit_unavailable'},'authorizes_execution':False}
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
            return PrincipalFacade.refusal()

    def invoke(self,name,arguments=None,*,call_id=None):
        result=self.call(name,{} if arguments is None else arguments,call_id=call_id)
        if result.get('status')!='ok':
            if (result.get('error') or {}).get('code')=='outcome_unknown':
                raise PrincipalOutcomeUnknown()
            raise PrincipalRefusal()
        return result['data']

    def descriptions(self):
        return self._discovery(lambda facade: [row for row in facade.descriptions()
                                              if row['name'] in self._audit.chain.allowed])

    def resource_descriptions(self):
        return self._discovery(lambda facade: facade.list_resources()
                               if 'resource_read' in self._audit.chain.allowed else [])

    def resource_templates(self):
        return self._discovery(lambda facade: facade.resource_templates
                               if 'resource_read' in self._audit.chain.allowed else [])

    def read_resource(self,uri):
        checks = self._scope_checks.get()
        self._check_identity()
        if 'resource_read' not in self._audit.chain.allowed:
            raise PrincipalRefusal()
        request_ref=_digest({'uri':uri},limits=REQUEST_LIMITS)
        dispatched = False
        def read():
            nonlocal dispatched
            try:
                facade = self._check_identity()
            except PrincipalRefusal:
                return PrincipalFacade.refusal()
            with facade.policy.lock:
                try:
                    self._check_identity()
                    self._check_scope(checks)
                except Exception:
                    return PrincipalFacade.refusal()
                try:
                    dispatched = True
                    data = facade.read_resource(uri)
                except PrincipalRefusal:
                    return PrincipalFacade.refusal()
                return self._checked_result({'status':'ok','data':data}, 'unknown', checks)
        value=self._audit.invoke(operation='resource_read',call_id=uuid4().hex,
            request_ref=request_ref,call=read,result_ref=_digest)
        if dispatched:
            self._checked_result(value, 'returned', checks)
        if value.get('status')!='ok':
            raise PrincipalRefusal()
        return value['data']

    @staticmethod
    def mcp_result(name,value):
        return PrincipalFacade.mcp_result(name,value)


def open_audited(facade,**bindings):
    return AuditedPrincipalHost(facade,**bindings)
