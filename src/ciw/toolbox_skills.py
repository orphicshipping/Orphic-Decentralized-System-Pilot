"""Host-versioned client recipes over existing principal-bound canonical services.

SPDX-License-Identifier: AGPL-3.0-or-later
No server tool registration, arbitrary code, provider binding or ledger admission.
"""
from copy import deepcopy
from hashlib import sha256
import re
from threading import Event, RLock
from time import monotonic

from .agent_tools import _shape
from .context_catalog import for_principal_facade, principal_facade, source_registry
from .context_selector import MAX_OUTPUT, serialize
from .control_contracts import json_tree, save_new
from .principal_policy import PrincipalOutcomeUnknown, PrincipalRefusal
from .retention import RetainedFailure, Store, _owner_dir, _transaction
from .toolbox_custody import DirectorySyncOwner


def digest(value):
    return sha256(serialize(value)).hexdigest()


def bounded(value, maximum=65536):
    json_tree(value)
    if len(serialize(value)) > maximum:
        raise ValueError('skill_byte_budget')
    return deepcopy(value)


def ident(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', value) is None:
        raise ValueError('invalid_skill_identifier')
    return value


def output(value, maximum):
    if type(maximum) is not int or not 128 <= maximum <= MAX_OUTPUT:
        raise ValueError('invalid_output_budget')
    return bounded(value, maximum)


def _step_outcome(tool, arguments, result):
    """Classify service outcome metadata, never text inside source/record payloads.

    An accepted asynchronous submission is pending, not a completed workflow.
    A lost coordinator worker may have had effects even when its record says
    failed. Inspection of such a record cannot authorize a dependent dispatch.
    """
    status = result.get('status')
    if (result.get('execution_outcome') == 'unknown'
            or status in ('unknown', 'outcome_unknown')):
        return 'outcome_unknown'
    if tool in ('ncp_run_submit', 'ncp_run_get', 'ncp_run_cancel'):
        state = result.get('state')
        if state in ('queued', 'running'):
            return 'pending'
        if state == 'unknown' or state not in ('completed', 'failed', 'cancelled'):
            return 'outcome_unknown'
        if state != 'completed':
            return state
    if tool == 'ncp_coordinate' and arguments.get('action') in ('submit', 'recall'):
        if result.get('error') == 'worker_lost':
            return 'outcome_unknown'
        if status not in ('completed', 'running', 'failed', 'cancelled'):
            return 'outcome_unknown'
    if status in ('running', 'queued', 'pending'):
        return 'pending'
    if status in ('refused', 'failed', 'cancelled', 'incomplete'):
        return status
    return 'completed'


class SkillRegistry:
    """Construct only in trusted host code; no model-facing registration/write API."""
    def __init__(self, definitions, *, facade=None):
        if type(definitions) not in (list, tuple) or not 1 <= len(definitions) <= 16:
            raise ValueError('skill_registry_budget')
        definitions = bounded(list(definitions), 16 * 16384)
        tools = {r['name']: r for rows in source_registry().values() for r in rows}
        if facade is not None:
            # One coherent authorized snapshot, including exact host-narrowed
            # schemas. This binds a recipe; it does not admit any step.
            names = set()
            for definition in definitions:
                if type(definition) is not dict or type(definition.get('steps')) is not list:
                    raise ValueError('invalid_skill_definition')
                for step in definition['steps']:
                    if type(step) is not dict or type(step.get('tool')) is not str or step['tool'] not in tools:
                        raise ValueError('unknown_skill_service')
                    names.add(step['tool'])
            selected = for_principal_facade(facade).select(sorted(names))
            tools = {row['name']: row for row in selected['tools']}
        self._definitions = {}
        for definition in definitions:
            spec = bounded(definition, 16384)
            if set(spec) != {'schema', 'skill_id', 'version', 'description', 'inputSchema', 'steps'}:
                raise ValueError('invalid_skill_definition')
            if spec['schema'] != 'ncp.client-skill.v1' or type(spec['inputSchema']) is not dict:
                raise ValueError('invalid_skill_definition')
            ident(spec['skill_id'])
            if type(spec['version']) is not str or re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', spec['version']) is None:
                raise ValueError('invalid_skill_version')
            if type(spec['description']) is not str or len(spec['description']) > 512:
                raise ValueError('invalid_skill_description')
            if type(spec['steps']) is not list or not 1 <= len(spec['steps']) <= 8:
                raise ValueError('skill_step_budget')
            prior = set()
            for step in spec['steps']:
                if set(step) != {'step_id', 'tool', 'arguments'} or step['tool'] not in tools:
                    raise ValueError('unknown_skill_service')
                name = ident(step['step_id'])
                if name in prior or type(step['arguments']) is not dict or len(step['arguments']) > 32:
                    raise ValueError('invalid_skill_step')
                for binding in step['arguments'].values():
                    if type(binding) is not dict:
                        raise ValueError('invalid_skill_binding')
                    if set(binding) == {'literal'}:
                        pass
                    elif set(binding) == {'input'}:
                        ident(binding['input'])
                    elif set(binding) == {'step', 'path'}:
                        if binding['step'] not in prior or type(binding['path']) is not list or len(binding['path']) > 12:
                            raise ValueError('invalid_skill_dependency')
                        for key in binding['path']:
                            if type(key) is str:
                                ident(key)
                            elif type(key) is not int or not 0 <= key <= 1024:
                                raise ValueError('invalid_skill_selector')
                    else:
                        raise ValueError('invalid_skill_binding')
                step['schema_sha256'] = digest(tools[step['tool']])
                prior.add(name)
            key = (spec['skill_id'], spec['version'])
            if key in self._definitions:
                raise ValueError('duplicate_skill_version')
            self._definitions[key] = (serialize(spec), digest(spec))

    def describe(self, skill_id, version, *, max_bytes=MAX_OUTPUT):
        try:
            raw, identity = self._definitions[(ident(skill_id), version)]
        except (KeyError, TypeError):
            raise ValueError('unknown_skill_version') from None
        import json
        return output({'definition': json.loads(raw), 'skill_sha256': identity,
                       'authorizes_execution': False}, max_bytes)

    def list(self, *, offset=0, limit=8, max_bytes=MAX_OUTPUT):
        if type(offset) is not int or not 0 <= offset <= 16 or type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError('invalid_skill_page')
        keys = sorted(self._definitions)
        if offset > len(keys):
            raise ValueError('invalid_skill_page')
        rows = [{'skill_id': key[0], 'version': key[1], 'skill_sha256': self._definitions[key][1]}
                for key in keys[offset:offset + limit]]
        end = offset + len(rows)
        return output({'items': rows, 'next_offset': end if end < len(keys) else None,
                       'authorizes_execution': False}, max_bytes)

    def load(self, skill_id, version, selector, *, max_bytes=MAX_OUTPUT):
        """Load chooses model context only; never admits or invokes a recipe."""
        spec = self.describe(skill_id, version)['definition']
        return selector.select(sorted({s['tool'] for s in spec['steps']}), max_bytes=max_bytes)


class SkillRunner:
    """Synchronous, bounded, cooperative client workflow; one instance per client.

    Constructor arguments are host-only. admit_step defaults to deny and must be
    called anew for every step. services.invoke independently applies current
    canonical policy and profile scope. No execution/result cache is used here.
    """
    def __init__(self, *, facade, registry, tenant, pins, replay_identity,
                 admit_step=None, maximum_seconds=30):
        authority, facade = facade, principal_facade(facade)
        if not isinstance(registry, SkillRegistry):
            raise TypeError('principal_facade_and_registry_required')
        for value in (tenant, replay_identity):
            ident(value)
        if type(pins) is not dict or not pins or len(pins) > 16 or any(
                type(k) is not str or type(v) is not str or not 1 <= len(v) <= 512 for k, v in pins.items()):
            raise ValueError('host_pins_required')
        if type(maximum_seconds) not in (int, float) or not 0 < maximum_seconds <= 60:
            raise ValueError('invalid_skill_deadline')
        if admit_step is not None and not callable(admit_step):
            raise TypeError('host_admission_required')
        for host in facade.hosts.values():
            if host is not None and hasattr(host, '_bound_tenant') and host._bound_tenant != tenant:
                raise ValueError('host_tenant_mismatch')
        self.facade, self.registry = facade, registry
        self._dispatch = authority
        self._principal = facade.principal
        self._selector = for_principal_facade(authority)
        self._tenant, self._pins, self._replay = tenant, bounded(pins), replay_identity
        self._admit = admit_step or (lambda _: False)
        self._seconds = maximum_seconds
        self._store = Store('toolbox-skill:' + digest([tenant, facade.principal, replay_identity]), evict=False)
        self._lock, self._attempts = RLock(), {}
        self._directory_sync = DirectorySyncOwner()

    def _scope(self, *, fresh=False):
        if self.facade.principal != self._principal or principal_facade(self._dispatch) is not self.facade:
            raise ValueError('skill_owner_binding_changed')
        for host in self.facade.hosts.values():
            if host is not None and (
                    hasattr(host, '_bound_tenant') and host._bound_tenant != self._tenant or
                    hasattr(host, '_bound_principal') and host._bound_principal != self.facade.principal):
                raise ValueError('skill_host_binding_changed')
        # Dispatch guards already hold the policy lock. A fresh selector avoids
        # reversing the ordinary selector-lock -> policy-lock order across threads.
        selector = for_principal_facade(self._dispatch) if fresh else self._selector
        return digest([selector.categories()['catalog_id'], self._tenant, self._pins, self._replay])

    def _invoke_step(self, invoke, name, args, scope):
        from .audited_host import AuditedPrincipalHost
        def matches():
            return self._scope(fresh=True) == scope
        if isinstance(self._dispatch, AuditedPrincipalHost):
            # The wrapper checks at actual dispatch, after durable audit I/O,
            # and again after terminal audit I/O before returning data.
            with self._dispatch.scope_guard(matches):
                return invoke(name, args, host=self._dispatch)
        with self.facade.policy.lock:
            if not matches():
                raise PrincipalRefusal()
            result = invoke(name, args, host=self._dispatch)
            try:
                unchanged = matches()
            except Exception:
                raise PrincipalOutcomeUnknown() from None
            if not unchanged:
                raise PrincipalOutcomeUnknown()
            return result

    def _keep(self, record):
        value = bounded(record)
        value['receipt_sha256'] = digest(value)
        value = bounded(value)
        return self._store.keep(value, kind='toolbox-skill', catalog={
            'schema': 'ncp.client-skill-audit.v1', 'authorizes_execution': False})

    def cancel(self, attempt):
        self._scope()  # A canceled principal cannot retrieve attempt existence.
        with self._lock:
            state = self._attempts.get(ident(attempt))
            if state is None:
                raise ValueError('unknown_skill_attempt')
            state.set()
        return {'cancel_requested': True, 'termination_established': False, 'authorizes_execution': False}

    def receipt(self, artifact_id, *, max_bytes=MAX_OUTPUT):
        scope = self._scope()
        value = self._store.native(artifact_id)
        claimed = value.pop('receipt_sha256', None)
        if claimed != digest(value):
            raise ValueError('skill_receipt_digest_mismatch')
        value['receipt_sha256'] = claimed
        # Retention I/O can block while the host changes policy or captures.
        # Recheck before disclosure; a saved locator never carries permission.
        with self.facade.policy.lock:
            if value.get('scope_sha256') != scope or self._scope(fresh=True) != scope:
                raise ValueError('skill_receipt_scope_changed')
            return output({'artifact_id': artifact_id, 'receipt': value, 'authorizes_execution': False}, max_bytes)

    @staticmethod
    def _arguments(step, inputs, results):
        args = {}
        for name, binding in step['arguments'].items():
            if 'literal' in binding:
                value = binding['literal']
            elif 'input' in binding:
                value = inputs[binding['input']]
            else:
                value = results[binding['step']]
                for key in binding['path']:
                    # Missing/ambiguous evidence never becomes a guessed handle.
                    if type(key) is int and type(value) is not list or type(key) is str and type(value) is not dict:
                        raise ValueError('skill_dependency_shape')
                    value = value[key]
            args[name] = deepcopy(value)
        return bounded(args)

    def run(self, skill_id, version, inputs, *, attempt, max_bytes=MAX_OUTPUT):
        from .services import invoke
        self._directory_sync.check()
        # Reserve the largest terminal summary before any side effects.
        output({'status': 'outcome_unknown', 'artifact_id': 'retained-' + 'f' * 32,
                'steps_completed': 8, 'authorizes_execution': False}, max_bytes)
        specrow = self.registry.describe(skill_id, version)
        spec, scope = specrow['definition'], self._scope()
        inputs = bounded(inputs)
        _shape(inputs, spec['inputSchema'])
        with self._lock:
            ident(attempt)
            if attempt in self._attempts or len(self._attempts) >= 4:
                raise ValueError('skill_attempt_used_or_capacity_reached')
            cancel = self._attempts[attempt] = Event()
        # Create-only replay guard is skill metadata, never a permission cache.
        # A recreated client cannot replay an old attempt after a process crash.
        directory = _owner_dir(self._store.owner)
        with _transaction(directory):
            marker = directory / ('toolbox-attempt-' + digest(attempt) + '.json')
            if marker.exists() or len(list(directory.glob('toolbox-attempt-*.json'))) >= 4:
                raise ValueError('skill_attempt_used_or_capacity_reached')
            save_new(marker, {'schema': 'ncp.client-skill-attempt.v1',
                             'request_sha256': digest([specrow['skill_sha256'], inputs, scope])})
            self._directory_sync.sync(directory)
        record = {'schema': 'ncp.client-skill-audit.v1', 'skill_sha256': specrow['skill_sha256'],
                  'attempt': attempt, 'scope_sha256': scope, 'input_sha256': digest(inputs),
                  'status': 'started', 'steps': [], 'authorizes_execution': False,
                  'evidence_admission': False, 'freshness_or_truth_established': False}
        artifact = self._keep(record)  # Refuse before dispatch if retention unavailable.
        results, deadline = {}, monotonic() + self._seconds
        for step in spec['steps']:
            if cancel.is_set() or monotonic() >= deadline:
                record['status'] = 'cancelled' if cancel.is_set() else 'timed_out'
                break
            dispatched = False
            try:
                if self._scope() != scope:
                    raise ValueError('skill_scope_changed')
                description = self._selector.describe(step['tool'])
                if description['schema_sha256'] != step['schema_sha256']:
                    raise ValueError('skill_schema_changed')
                args = self._arguments(step, inputs, results)
                _shape(args, description['tool']['inputSchema'])
                admission = {'skill_sha256': specrow['skill_sha256'], 'attempt': attempt,
                             'step_id': step['step_id'], 'tool': step['tool'],
                             'schema_sha256': step['schema_sha256'], 'scope_sha256': scope,
                             'arguments_sha256': digest(args), 'arguments': args}
                if self._admit(deepcopy(admission)) is not True:
                    raise ValueError('step_not_admitted')
                if cancel.is_set() or monotonic() >= deadline:
                    record['status'] = 'cancelled' if cancel.is_set() else 'timed_out'
                    break
                if self._scope() != scope:
                    raise ValueError('skill_scope_changed')
                intent = {**record, 'status': 'step_pending', 'admission': admission}
                artifact = self._keep(intent)
                # Retention and refresh may block: revalidate after their I/O,
                # then linearize stop/dispatch with cancel() under the same lock.
                if self._scope() != scope:
                    raise ValueError('skill_scope_changed')
                with self._lock:
                    if cancel.is_set() or monotonic() >= deadline:
                        record['status'] = 'cancelled' if cancel.is_set() else 'timed_out'
                        break
                    dispatched = True
                result = bounded(self._invoke_step(invoke, step['tool'], args, scope))
                outcome = _step_outcome(step['tool'], args, result)
                result_artifact = self._keep({**intent, 'status': 'step_' + outcome, 'result': result,
                                              'result_sha256': digest(result)})
                if outcome != 'completed':
                    record['status'] = outcome
                    record['halted_step'] = {'step_id': step['step_id'], 'artifact_id': result_artifact,
                                             'result_sha256': digest(result)}
                    artifact = result_artifact
                    break
                results[step['step_id']] = result
                record['steps'].append({'step_id': step['step_id'], 'artifact_id': result_artifact,
                                         'result_sha256': digest(result)})
            except PrincipalRefusal:
                record['status'] = 'refused'
                break
            except (PrincipalOutcomeUnknown, RetainedFailure) as exc:
                record['status'] = 'outcome_unknown' if isinstance(exc, PrincipalOutcomeUnknown) or str(exc) == 'outcome_unknown' else 'refused'
                if isinstance(exc, RetainedFailure):
                    record['upstream_failure_artifact'] = exc.artifact_id
                break
            except Exception:
                record['status'] = 'outcome_unknown' if dispatched else 'refused'
                break
        else:
            record['status'] = 'completed'
        try:
            artifact = self._keep(record)
        except Exception:
            # Existing pending receipt survives. Never auto-retry a possible effect.
            record['status'] = 'outcome_unknown'
        return output({'status': record['status'], 'artifact_id': artifact,
                       'steps_completed': len(record['steps']), 'authorizes_execution': False}, max_bytes)
