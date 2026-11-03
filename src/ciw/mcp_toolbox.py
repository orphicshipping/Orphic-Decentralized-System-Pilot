"""Server-side toolbox activation and signed workflows over canonical services.

SPDX-License-Identifier: AGPL-3.0-or-later
MCP discovery is stable: load/unload changes persisted component activation,
not tools/list. Components contain only launcher-selected existing operations.
There is no tool for code download, signer creation or registry publication.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from threading import RLock

from .agent_tools import _shape
from .audited_host import AuditedPrincipalHost
from .campaign_contracts import canonical, parse, REQUEST_LIMITS
from .context_catalog import for_principal_facade, principal_facade, source_registry
from .persistent_cache import PrivateSQLite
from .principal_policy import PrincipalOutcomeUnknown, PrincipalRefusal
from .toolbox_skills import SkillRegistry, SkillRunner, digest, ident
from .workflow_registry import WorkflowRegistry

PROFILE = 'odsp.server-toolbox.v1'


def _schema(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required, 'additionalProperties': False}


_ID = {'type': 'string', 'minLength': 1, 'maxLength': 80}
_VERSION = {'type': 'string', 'minLength': 5, 'maxLength': 11}
_HASH = {'type': 'string', 'minLength': 64, 'maxLength': 64}
_JSON = {'type': 'object'}


def _tool(description, fields, *, read=False, required=None):
    return {'description': description, 'inputSchema': _schema(fields, required),
            'annotations': {'readOnlyHint': read, 'destructiveHint': not read,
                            'idempotentHint': read, 'openWorldHint': False}}


TOOLS = {
    'ncp_toolbox_list': _tool('List host-approved components visible under current grants and their shared activation.', {}, read=True),
    'ncp_toolbox_describe': _tool('Read an exact approved component and schema digest; this grants nothing.', {'component_id': _ID}, read=True),
    'ncp_toolbox_load': _tool('Activate an approved component at its exact digest in the shared tenant toolbox.',
                            {'component_id': _ID, 'expected_digest': _HASH}),
    'ncp_toolbox_unload': _tool('Deactivate an approved component for future calls; active effects are not undone.', {'component_id': _ID}),
    'ncp_toolbox_call': _tool('Invoke one currently active component operation through independent canonical authorization and audit.',
                            {'component_id': _ID, 'operation': {'type': 'string', 'minLength': 1, 'maxLength': 128}, 'arguments': _JSON}),
    'ncp_skill_list': _tool('List currently trusted persisted workflow versions; revoked or corrupt entries refuse the page.',
                           {'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1024},
                            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 64}}, read=True, required=[]),
    'ncp_skill_describe': _tool('Verify and describe a signed workflow version against current host trust.',
                               {'workflow_id': _ID, 'version': _VERSION}, read=True),
    'ncp_skill_run': _tool('Run a signed workflow once with fresh per-step grants, admission and audit; no automatic replay.',
                          {'workflow_id': _ID, 'version': _VERSION, 'inputs': _JSON, 'attempt': _ID}),
    'ncp_skill_cancel': _tool('Persist a cooperative cancellation request; provider termination is not established.', {'attempt': _ID}),
    'ncp_skill_receipt': _tool('Read a retained workflow receipt after current identity, workflow trust and scope checks.',
                             {'attempt': _ID, 'artifact_id': {'type': 'string', 'minLength': 41, 'maxLength': 41}}, read=True,
                             required=['attempt']),
}


def profile_manifest():
    return {'profile_id': PROFILE, 'tools': [{'name': name, **deepcopy(row)} for name, row in sorted(TOOLS.items())],
            'dynamic_discovery': False, 'activation': 'shared_server_component_routing',
            'workflow_registry': 'persisted_ed25519_current_host_trust', 'authorizes_execution': False}


class _GuardedRunner(SkillRunner):
    def __init__(self, *, fresh_guard, **kwargs):
        self._fresh_guard = fresh_guard
        super().__init__(**kwargs)

    def _scope(self, *, fresh=False):
        if self._fresh_guard():
            for event in self._attempts.values():
                event.set()
        return super()._scope(fresh=fresh)

    def _invoke_step(self, invoke, name, args, scope):
        def current():
            return not self._fresh_guard()
        with self._dispatch.scope_guard(current):
            return super()._invoke_step(invoke, name, args, scope)


class MCPToolboxHost:
    """Trusted launcher composition, for the principal facade's toolbox family.

    Construct first, add this host to the management facade, then bind_dispatch
    to a separate audited canonical facade with the SAME principal and policy.
    The inner audit chain must exclude these management tools; a running outer
    admission cannot safely nest another admission on that same journal.

    components maps trusted identifiers to fixed existing canonical operation
    names. Activating one grants neither those operations nor new discovery.
    A cooperating server process can cancel a running synchronous request;
    cancellation never promises interruption of an active native provider.
    """
    strict_mcp_input = True
    instructions = ('Use only host-approved component handles and signed workflow versions. '
                    'Loading grants nothing. Unknown outcomes require inspection, not automatic retry.')

    def __init__(self, *, path, registry, policy, principal, tenant, pins, replay_identity,
                 components, admit_step=None, maximum_seconds=30, max_attempts=128):
        from .principal_policy import PrincipalPolicy
        if type(registry) is not WorkflowRegistry or not isinstance(policy, PrincipalPolicy):
            raise TypeError('trusted_toolbox_bindings_required')
        policy.check_principal(principal)
        ident(tenant)
        ident(replay_identity)
        if (type(pins) is not dict or not 1 <= len(pins) <= 16
                or any(type(k) is not str or type(v) is not str or not 1 <= len(v) <= 512 for k, v in pins.items())):
            raise ValueError('toolbox_pins_required')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 1024:
            raise ValueError('toolbox_attempt_budget')
        if admit_step is not None and not callable(admit_step):
            raise TypeError('host_step_admission_required')
        if type(maximum_seconds) not in (int, float) or not 0 < maximum_seconds <= 60:
            raise ValueError('toolbox_deadline_refused')
        self._bound_principal, self._bound_tenant = principal, tenant
        self._principal, self._tenant = principal, tenant
        self.policy, self.registry = policy, registry
        self._policy_binding = policy
        self.pins, self.replay_identity = deepcopy(pins), replay_identity
        self._admit = admit_step or (lambda _: False)
        self._seconds, self._max_attempts = maximum_seconds, max_attempts
        self._dispatch = None
        self._lock = RLock()
        self._owner = digest([tenant, principal, replay_identity])
        source = {row['name']: row for rows in source_registry().values() for row in rows}
        if type(components) is not dict or not 1 <= len(components) <= 32:
            raise ValueError('toolbox_component_budget')
        self._components = {}
        for component_id, operations in components.items():
            ident(component_id)
            if (type(operations) not in (tuple, list) or not 1 <= len(operations) <= 32
                    or any(type(n) is not str or n not in source or n in TOOLS for n in operations)
                    or len(set(operations)) != len(operations)):
                raise ValueError('toolbox_component_operations')
            self._components[component_id] = {'component_id': component_id,
                'tools': [deepcopy(source[n]) for n in sorted(operations)], 'pins': deepcopy(pins)}
        self.db = PrivateSQLite(path)
        with self.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS toolbox_components '
                       '(tenant TEXT NOT NULL, component TEXT NOT NULL, digest TEXT NOT NULL, '
                       'generation INTEGER NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)), '
                       'PRIMARY KEY(tenant,component))')
            db.execute('CREATE TABLE IF NOT EXISTS toolbox_attempts '
                       '(owner TEXT NOT NULL, attempt TEXT NOT NULL, request BLOB NOT NULL, '
                       'workflow_digest TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, '
                       'result BLOB, PRIMARY KEY(owner,attempt))')
            db.execute('CREATE TABLE IF NOT EXISTS toolbox_config '
                       '(owner TEXT PRIMARY KEY, config TEXT NOT NULL)')
            config = digest([self.pins, max_attempts, maximum_seconds])
            db.execute('INSERT OR IGNORE INTO toolbox_config VALUES (?,?)', (self._owner, config))
            if db.execute('SELECT config FROM toolbox_config WHERE owner=?', (self._owner,)).fetchone()[0] != config:
                raise ValueError('toolbox_configuration_changed')

    def bind_dispatch(self, host):
        if type(host) is not AuditedPrincipalHost:
            raise TypeError('audited_canonical_dispatch_required')
        facade = principal_facade(host)
        if (facade.principal != self._principal or facade.policy is not self.policy
                or set(TOOLS) & host._audit.chain.allowed):
            raise ValueError('separate_canonical_audit_binding_required')
        with self._lock:
            if self._dispatch is not None:
                raise ValueError('toolbox_dispatch_already_bound')
            selector = for_principal_facade(host)
            for component in self._components.values():
                component['tools'] = [selector.describe(row['name'])['tool'] for row in component['tools']]
            self._dispatch = host
            self._dispatch_binding = host
        return self

    def _authorize(self, name, arguments):
        if (self._dispatch is None or self._bound_principal != self._principal
                or self._bound_tenant != self._tenant
                or self._dispatch is not self._dispatch_binding or self.policy is not self._policy_binding
                or principal_facade(self._dispatch).principal != self._principal
                or principal_facade(self._dispatch).policy is not self._policy_binding):
            raise PrincipalRefusal()
        # The selected policy owns the fresh grant/context decision, including
        # a host-selected native policy. Never reconstruct a Python fallback.
        return self.policy.authorize_context(self._principal, name, arguments)

    def descriptions(self):
        return profile_manifest()['tools']

    def binding_scope(self):
        """Host-only identity for catalog invalidation, never an admission."""
        trust = self.registry._snapshot()[2]
        dispatch = self._dispatch
        return {'tenant': self._tenant, 'principal': self._principal,
                'pins': deepcopy(self.pins), 'replay_identity': self.replay_identity,
                'components_sha256': digest(self._components), 'trust': list(trust),
                'registry_binding': digest(str(self.registry.db.path)),
                'state_binding': digest(str(self.db.path)),
                'canonical_audit': None if dispatch is None else {
                    'chain_id': dispatch._audit.chain.chain_id,
                    'allowed': sorted(dispatch._audit.chain.allowed),
                    'journal_binding': digest(str(dispatch._audit.chain.path))}}

    def _component(self, component_id):
        ident(component_id)
        if component_id not in self._components:
            raise ValueError('component_not_approved')
        selected = deepcopy(self._components[component_id])
        view = for_principal_facade(self._dispatch)
        for row in selected['tools']:
            if view.describe(row['name'])['tool'] != row:
                raise ValueError('component_schema_or_grant_changed')
        return selected, digest(selected)

    def _state(self, component_id):
        with self.db.connect() as db:
            return db.execute('SELECT digest,generation,active FROM toolbox_components '
                              'WHERE tenant=? AND component=?', (self._tenant, component_id)).fetchone()

    def _workflow(self, workflow_id, version):
        checked = self.registry.get(self._tenant, workflow_id, version)
        payload = checked['envelope']['payload']
        if payload['pins'] != self.pins:
            raise ValueError('workflow_runtime_pins_changed')
        # The signer also pinned every exact canonical schema. Current visible
        # schemas may be narrower; they must match, never silently widen them.
        definition = payload['definition']
        current = SkillRegistry([definition], facade=self._dispatch)
        if current.describe(workflow_id, version)['skill_sha256'] != checked['skill_sha256']:
            raise ValueError('workflow_current_schema_changed')
        # Constructing the principal-visible registry can perform policy I/O.
        # Do not disclose or execute a signature checked before that boundary.
        self.registry.assert_current(checked)
        return checked, current

    def request_scope_guard(self, name, arguments):
        """Host-only guard for the outer audit's dispatch/disclosure boundaries.

        Snapshot only immutable request and workflow metadata. The facade and
        audited wrapper install this callback; MCP callers cannot supply it.
        Cancellation remains available under its own grant after revocation.
        """
        if name not in TOOLS:
            return None
        args = parse(canonical(arguments, limits=REQUEST_LIMITS), limits=REQUEST_LIMITS)
        _shape(args, TOOLS[name]['inputSchema'])
        context = self._authorize(name, args)
        if name == 'ncp_toolbox_call':
            component_id = args['component_id']
            component, checksum = self._component(component_id)
            if args['operation'] not in {row['name'] for row in component['tools']}:
                raise ValueError('operation_not_in_component')
            state = self._state(component_id)
            if state is None or state[0] != checksum or not state[2]:
                raise ValueError('component_inactive')
            def current():
                return (self._component(component_id)[1] == checksum
                        and self._authorize(name, args) == context
                        and self._state(component_id) == state)
            return current
        if name not in {'ncp_skill_list', 'ncp_skill_describe', 'ncp_skill_run', 'ncp_skill_receipt'}:
            return lambda: self._authorize(name, args) == context
        registry = self.registry
        if name == 'ncp_skill_list':
            initial = registry.list(self._tenant, offset=args.get('offset', 0), limit=args.get('limit', 16))
            trust = registry._snapshot()[2]
            validity = []
            for row in initial['items']:
                checked = registry.get(self._tenant, row['workflow_id'], row['version'])
                if (checked['workflow_sha256'] != row['workflow_sha256']
                        or (checked['trust_generation'], checked['trust_sha256']) != trust[1:]):
                    raise ValueError('workflow_list_changed')
                validity.append(checked['envelope']['payload'])
            registry._assert_current(trust)
            for payload in validity:
                registry._assert_validity(payload)
            def current():
                if self.registry is not registry or self._authorize(name, args) != context:
                    return False
                value = registry.list(self._tenant, offset=args.get('offset', 0), limit=args.get('limit', 16))
                registry._assert_current(trust)
                for payload in validity:
                    registry._assert_validity(payload)
                return value == initial
            return current
        if name == 'ncp_skill_receipt':
            request, expected, _, _ = self._attempt(args['attempt'])
            workflow_id, version = request['workflow_id'], request['version']
        else:
            workflow_id, version = args['workflow_id'], args['version']
            expected = None
        checked, _ = self._workflow(workflow_id, version)
        if expected is not None and checked['workflow_sha256'] != expected:
            raise ValueError('server_attempt_workflow_changed')
        def current():
            if self.registry is not registry or self._authorize(name, args) != context:
                return False
            value, _ = self._workflow(workflow_id, version)
            if value != checked:
                return False
            if name == 'ncp_skill_receipt':
                now, checksum, _, _ = self._attempt(args['attempt'])
                if now != request or checksum != expected:
                    return False
            # Attempt-store reads may block too. Finish with fresh trust/time.
            registry.assert_current(checked)
            return True
        return current

    def _attempt(self, attempt):
        ident(attempt)
        with self.db.connect() as db:
            row = db.execute('SELECT request,workflow_digest,cancelled,result FROM toolbox_attempts '
                             'WHERE owner=? AND attempt=?', (self._owner, attempt)).fetchone()
        if row is None:
            raise ValueError('unknown_server_attempt')
        raw, workflow_digest, cancelled, result = row
        request = parse(raw, limits=REQUEST_LIMITS)
        if request.get('attempt') != attempt or cancelled not in (0, 1):
            raise ValueError('server_attempt_corrupt')
        return request, workflow_digest, bool(cancelled), result

    def _runner(self, attempt, *, execution):
        request, expected, _, _ = self._attempt(attempt)
        checked, registry = self._workflow(request['workflow_id'], request['version'])
        if checked['workflow_sha256'] != expected:
            raise ValueError('server_attempt_workflow_changed')
        def guard():
            if execution:
                self._authorize('ncp_skill_run', request)
            current, _ = self._workflow(request['workflow_id'], request['version'])
            if current['workflow_sha256'] != expected:
                raise ValueError('workflow_changed_before_step')
            return self._attempt(attempt)[2]
        def admit(intent):
            if guard():
                return False
            approved = self._admit(deepcopy(intent)) is True
            return approved and not guard()
        return _GuardedRunner(facade=self._dispatch, registry=registry, tenant=self._tenant,
            pins=self.pins, replay_identity='server-' + digest([self.replay_identity, attempt])[:48],
            admit_step=admit, maximum_seconds=self._seconds, fresh_guard=guard)

    def _call(self, name, args, context, effect_started):
        if name == 'ncp_toolbox_list':
            items = []
            for component_id in sorted(self._components):
                try:
                    component, checksum = self._component(component_id)
                except (ValueError, KeyError):
                    continue
                state = self._state(component_id)
                items.append({'component_id': component_id, 'digest': checksum,
                              'active': bool(state and state[0] == checksum and state[2]),
                              'generation': state[1] if state else 0})
            return {'items': items, 'tools_list_changes': False}
        if name in ('ncp_toolbox_describe', 'ncp_toolbox_load', 'ncp_toolbox_unload', 'ncp_toolbox_call'):
            component_id = args['component_id']
            if name == 'ncp_toolbox_unload':
                # A management grant can stop an approved component even after
                # its underlying execution grants were revoked.
                ident(component_id)
                component = deepcopy(self._components[component_id])
                checksum = digest(component)
            else:
                component, checksum = self._component(component_id)
            if name == 'ncp_toolbox_describe':
                return {'component': component, 'digest': checksum}
            if name in ('ncp_toolbox_load', 'ncp_toolbox_unload'):
                active = name == 'ncp_toolbox_load'
                if active and args['expected_digest'] != checksum:
                    raise ValueError('component_digest_changed')
                with self.db.transaction() as db:
                    if self._authorize(name, args) != context:
                        raise PrincipalRefusal()
                    old = db.execute('SELECT generation FROM toolbox_components WHERE tenant=? AND component=?',
                                     (self._tenant, component_id)).fetchone()
                    generation = old[0] + 1 if old else 1
                    effect_started()
                    db.execute('INSERT OR REPLACE INTO toolbox_components VALUES (?,?,?,?,?)',
                               (self._tenant, component_id, checksum, generation, int(active)))
                return {'component_id': component_id, 'active': active, 'generation': generation,
                        'digest': checksum, 'tools_list_changes': False}
            operation = args['operation']
            if operation not in {row['name'] for row in component['tools']}:
                raise ValueError('operation_not_in_component')
            state = self._state(component_id)
            if state is None or state[0] != checksum or not state[2]:
                raise ValueError('component_inactive')
            def current():
                return (self._authorize(name, args) == context and self._state(component_id) == state
                        and self._component(component_id)[1] == checksum)
            from .services import invoke
            with self._dispatch.scope_guard(current):
                effect_started()
                value = invoke(operation, args['arguments'], host=self._dispatch)
            return {'component_id': component_id, 'operation': operation, 'result': value}
        if name == 'ncp_skill_list':
            return self.registry.list(self._tenant, offset=args.get('offset', 0), limit=args.get('limit', 16))
        if name == 'ncp_skill_describe':
            checked, _ = self._workflow(args['workflow_id'], args['version'])
            return checked
        if name == 'ncp_skill_run':
            ident(args['attempt'])
            checked, _ = self._workflow(args['workflow_id'], args['version'])
            raw = canonical(args, limits=REQUEST_LIMITS)
            with self.db.transaction() as db:
                if self._authorize(name, args) != context:
                    raise PrincipalRefusal()
                if db.execute('SELECT 1 FROM toolbox_attempts WHERE owner=? AND attempt=?',
                              (self._owner, args['attempt'])).fetchone():
                    raise ValueError('server_attempt_already_used')
                if db.execute('SELECT COUNT(*) FROM toolbox_attempts WHERE owner=?',
                              (self._owner,)).fetchone()[0] >= self._max_attempts:
                    raise ValueError('server_attempt_capacity')
                effect_started()
                db.execute('INSERT INTO toolbox_attempts(owner,attempt,request,workflow_digest) VALUES (?,?,?,?)',
                           (self._owner, args['attempt'], raw, checked['workflow_sha256']))
            active = self._runner(args['attempt'], execution=True)
            result = active.run(args['workflow_id'], args['version'], args['inputs'], attempt=args['attempt'], max_bytes=65536)
            with self.db.transaction() as db:
                db.execute('UPDATE toolbox_attempts SET result=? WHERE owner=? AND attempt=?',
                           (canonical(result), self._owner, args['attempt']))
            return result
        if name == 'ncp_skill_cancel':
            self._attempt(args['attempt'])
            with self.db.transaction() as db:
                if self._authorize(name, args) != context:
                    raise PrincipalRefusal()
                effect_started()
                db.execute('UPDATE toolbox_attempts SET cancelled=1 WHERE owner=? AND attempt=?',
                           (self._owner, args['attempt']))
            return {'cancel_requested': True, 'termination_established': False}
        if name == 'ncp_skill_receipt':
            active = self._runner(args['attempt'], execution=False)
            artifact = args.get('artifact_id')
            if artifact is None:
                stored = self._attempt(args['attempt'])[3]
                if stored is None:
                    return {'status': 'outcome_unknown', 'receipt_available': False,
                            'retry_authorized': False, 'attempt': args['attempt']}
                summary = parse(stored, limits=REQUEST_LIMITS)
                artifact = summary['artifact_id']
            return active.receipt(artifact, max_bytes=65536)
        raise ValueError('unknown_server_toolbox_operation')

    def call(self, name, arguments):
        started = False
        def effect_started():
            nonlocal started
            started = True
        try:
            args = parse(canonical(arguments, limits=REQUEST_LIMITS), limits=REQUEST_LIMITS)
            if name not in TOOLS:
                raise ValueError('unknown_server_toolbox_operation')
            _shape(args, TOOLS[name]['inputSchema'])
            context = self._authorize(name, args)
            guard = self.request_scope_guard(name, args)
            value = self._call(name, args, context, effect_started)
            if self._authorize(name, args) != context:
                raise PrincipalOutcomeUnknown() if started else PrincipalRefusal()
            if guard is not None and guard() is not True:
                raise PrincipalOutcomeUnknown() if started else PrincipalRefusal()
            result = {'schema': PROFILE, 'status': 'ok', 'data': value, 'authorizes_execution': False}
            if name == 'ncp_skill_run' and value.get('status') in {'outcome_unknown', 'unknown', 'pending'}:
                result.update(status='refused', error={'code': 'outcome_unknown'}, effect_state='unknown')
            # Account for the duplicated MCP text/structuredContent envelope.
            from .principal_policy import PrincipalFacade
            PrincipalFacade.mcp_result(name, result)
            return result
        except Exception:
            return {'schema': PROFILE, 'status': 'refused', 'data': None,
                    'error': {'code': 'outcome_unknown' if started else 'toolbox_refused'},
                    'effect_state': 'unknown' if started else 'not_started',
                    'authorizes_execution': False}
