"""Principal-bound operational metrology over existing native scientific hosts.

This module coordinates fixed retained-data jobs; it implements no domain solver,
calibration correction, acquisition, physical acceptance or device command.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import sys
from threading import RLock
from types import MappingProxyType

from .agent_api import encode, parse
from .agent_tools import _shape
from .campaign_contracts import canonical, REQUEST_LIMITS, text, integer
from .link_store import Journal, safe_path
from .operational_store import Records
from .principal_policy import PrincipalPolicy, PrincipalRefusal, owner_for

PROFILE = 'ciw.metrology-operations.v1'
AUTHORITY = {'physical_qualification': 'not_established', 'calibration_authenticity': 'not_established',
             'device_contact': False, 'actuation': 'not_permitted', 'state_admission': 'not_performed',
             'review_grants_execution': False}
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}\Z')
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
_S = {'type': 'string', 'minLength': 1, 'maxLength': 64}
_R = {'type': 'string', 'pattern': r'^sha256:[0-9a-f]{64}$', 'minLength': 71, 'maxLength': 71}


def _spec(description, properties, required, *, read=True):
    return {'description': description, 'inputSchema': {'type': 'object', 'properties': properties,
            'required': list(required), 'additionalProperties': False}, 'outputSchema': {'type': 'object'},
            'annotations': {'readOnlyHint': read, 'destructiveHint': False,
                            'idempotentHint': True, 'openWorldHint': False}}


TOOLS = {
    'ncp_metrology_catalog': _spec('Inspect fixed retained-data jobs and immutable plan digests. Discovery grants no execution.', {}, ()),
    'ncp_metrology_run': _spec('Run one exact host-bound metrology plan through its existing native AgentHost. Retry returns its durable outcome; an interrupted claim never reruns.',
        {'job': _S, 'plan_ref': _R, 'attempt': _S}, ('job', 'plan_ref', 'attempt'), read=False),
    'ncp_metrology_status': _spec('Inspect an owned durable attempt and separate review records. Does not execute a provider.',
        {'job': _S, 'attempt': _S}, ('job', 'attempt')),
    'ncp_metrology_read': _spec('Read bounded original bytes of one owned plan, source, calibration inspection or retained native outcome. Base64 is transport encoding only.',
        {'job': _S, 'attempt': _S, 'artifact': {'type': 'string', 'enum': ['plan', 'source', 'calibration',
         'outcome', 'workspace', 'graph-run', 'experiment', 'finding', 'review']}, 'review_id': _S,
         'offset': {'type': 'integer', 'minimum': 0, 'maximum': 8388608},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 4096}}, ('job', 'artifact')),
    'ncp_metrology_review': _spec('Append a review of an exact retained outcome. Review cannot edit numerical results, approve calibration, accept equipment or authorize another run.',
        {'job': _S, 'attempt': _S, 'outcome_ref': _R, 'review_id': _S,
         'disposition': {'type': 'string', 'enum': ['reviewed', 'requires_followup']},
         'note': {'type': 'string', 'minLength': 1, 'maxLength': 2048}},
        ('job', 'attempt', 'outcome_ref', 'review_id', 'disposition', 'note'), read=False),
}


def profile_manifest():
    return {'profile_id': PROFILE, 'host_factory': 'ciw.metrology_operations.MetrologyHost',
            'engines': ['scalar', 'calibrated-window', 'residual-monitor', 'bim-clearance'],
            'qualification': 'retained_data_numerical_only', 'authorizes_execution': False,
            'authority': deepcopy(AUTHORITY),
            'tools': [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()]}


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError('invalid_metrology_identifier')
    return value


def _ref(raw):
    return 'sha256:' + sha256(raw).hexdigest()


class MetrologyJob:
    """Trusted launcher binding; models cannot construct or modify jobs."""

    def __init__(self, host, source, graph, *, kind):
        from .operational_agent import OperationalHost
        from .metrology_clearance import ClearanceHost
        if kind == 'operational' and type(host) is OperationalHost:
            self.agent = host.agent
            self._profile_ref = _ref(host.profile.read_bytes())
        elif kind == 'bim-clearance' and type(host) is ClearanceHost:
            self.agent = host
            self._profile_ref = None
        else:
            raise TypeError('existing_native_metrology_host_required')
        self.host, self.kind, self.source, self.graph = host, kind, _id(source), _id(graph)
        self._source = self.agent._get(source)
        self._graph = self.agent._get(graph)
        self._host_policy = self.agent._host_policy
        self._source_digest, self._graph_digest = _ref(encode(self._source)), _ref(encode(self._graph))
        self.operations = tuple(sorted(self._operations()))
        self.raw, self.calibration, self.engine = self._source_description()
        self.description = {'schema': 'ciw.metrology-plan.v1', 'engine': self.engine,
            'source_ref': _ref(self.raw), 'source_artifact_ref': self._source_digest,
            'source_evidence_id': self._source['evidence_id'], 'graph_ref': self._graph_digest,
            'host_policy_ref': self._host_policy, 'profile_ref': self._profile_ref,
            'calibration_ref': _ref(encode(self.calibration)), 'scientific_operations': list(self.operations),
            'authority': deepcopy(AUTHORITY)}
        self.plan_ref = _ref(encode(self.description))

    @classmethod
    def operational(cls, host, model):
        return cls(host, model, model + '-graph', kind='operational')

    @classmethod
    def clearance(cls, host, *, source='source', graph='baseline'):
        return cls(host, source, graph, kind='bim-clearance')

    def _operations(self):
        operations = {n['operation_id'] for n in self._graph['nodes']}
        if self.kind == 'bim-clearance':
            from .metrology_clearance import graph
            if self._graph != graph():
                raise ValueError('fixed_clearance_graph_required')
            return operations
        from . import operational_engineering as eng
        raw, metadata = eng.source_request(self._source)
        engine = metadata['engine']
        from .control_plane import experiment
        from .core.identities import content_identity
        expected = experiment('operational-' + self.source, model_id=content_identity(metadata),
            parent_checkpoint=metadata['binding_ref'], nodes=[
                {'node_id': 'assessment', 'operation_id': eng.ASSESS, 'parameters': {}, 'inputs': {}, 'depends_on': []},
                {'node_id': 'verification', 'operation_id': eng.VERIFY, 'parameters': {},
                 'inputs': {'assessment': {'node_id': 'assessment', 'port': 'result'}}, 'depends_on': []}])
        if self._graph != expected:
            raise ValueError('fixed_operational_graph_required')
        if engine == 'scalar':
            from .link_study import OPS
            operations.update(OPS)
        elif engine == 'calibrated-window':
            from .calibrated_window import OPERATIONS, COMPOSITION
            operations.update(op for _, op in OPERATIONS)
            operations.add(COMPOSITION['calibration_operation_id'])
        elif engine == 'residual-monitor':
            from .residual_monitor import OPERATION
            operations.add(OPERATION)
        else:
            # These paths are the existing native metrology engines; other
            # operational analysis remains available through its original host.
            raise ValueError('unsupported_metrology_engine')
        return operations

    def _source_description(self):
        if self.kind == 'bim-clearance':
            from .metrology_clearance import source_bytes, inspection
            raw = source_bytes(self._source)
            selected = inspection(raw)
            return raw, {'schema': 'ciw.metrology-calibration-context.v1',
                'declaration': selected['calibration'], 'pose': selected['pose'],
                'interpretation': 'native clearance applicability and uncertainty gates remain authoritative',
                'authority': deepcopy(AUTHORITY)}, 'bim-clearance'
        from .operational_engineering import source_request
        raw, metadata = source_request(self._source)
        engine = metadata['engine']
        value = parse(raw)
        context = {'schema': 'ciw.metrology-calibration-context.v1', 'engine': engine,
                   'authority': deepcopy(AUTHORITY)}
        if engine == 'scalar':
            from .calibration_inspection import inspect_certificate
            from .link_contract import unpack
            declaration = value['calibration']['declaration']
            context.update(declaration=value['calibration'],
                certificate_inspection=None if declaration is None else inspect_certificate(unpack(declaration['certificate'], 32768)),
                interpretation='DCC structural inspection does not authorize correction; native scalar applicability stays independent')
        elif engine == 'calibrated-window':
            context.update(declaration=value['calibration_profile'],
                           interpretation='native calibrated-window full joint covariance and source validity gates')
        else:
            context.update(upstream_refs=sorted(value['upstreams']),
                           interpretation='native monitor binds and validates exact calibrated upstream windows')
        return raw, context, engine

    def current(self):
        if self.kind == 'operational':
            self.host._check('inspect')
            if _ref(self.host.profile.read_bytes()) != self._profile_ref:
                raise ValueError('metrology_profile_changed')
        if (self.agent._host_policy != self._host_policy or
                _ref(encode(self.agent._get(self.source))) != self._source_digest or
                _ref(encode(self.agent._get(self.graph))) != self._graph_digest):
            raise ValueError('metrology_plan_changed')

    def provider_status(self):
        if self.kind == 'operational':
            return self.host.model_adapters[self.source]['provider_status']
        from .metrology_clearance import OP
        return ('operator_bound_pin_check_at_dispatch' if
                parse(self.agent._catalog)['operations'][OP]['bound'] else 'provider_unavailable')

    def interpretation(self, artifacts, finding):
        if finding is not None:
            return finding['data']['status']
        if self.kind == 'bim-clearance' and 'workspace' in artifacts:
            from .metrology_clearance import OP
            rows = [row for row in parse(artifacts['workspace'])['results'] if row['operation_id'] == OP]
            if len(rows) == 1:
                native = rows[0]['data']['native']
                return native['posterior']['verdict'] if native['posterior'] is not None else native['status']
        return None

    def execute(self, attempt):
        self.current()
        response = self.host.call('net_execute', {'source': self.source, 'graph': self.graph, 'attempt': attempt})
        artifacts = {name: encode(self.agent._get(binding['artifact_id']))
                     for name, binding in response.get('artifacts', {}).items()}
        finding = None
        if self.kind == 'operational' and response.get('status') == 'completed':
            finding = self.host.call('oe_model_support', {'attempt': attempt})
            artifacts['finding'] = encode(finding)
        return response, artifacts, finding


class MetrologyHost:
    """Fixed principal/tenant/jobs with durable local review and retry identities."""
    strict_mcp_input = True
    instructions = ('Inspect fixed metrology plans, then run an exact plan digest. All work uses retained '
                    'sources. Numerical completion is not physical acceptance. Review never grants authority.')

    def __init__(self, *, policy, principal, tenant, jobs, journal, create=False):
        if not isinstance(policy, PrincipalPolicy) or type(jobs) is not dict or not 1 <= len(jobs) <= 8:
            raise TypeError('trusted_policy_and_fixed_jobs_required')
        owner_for(principal); _id(tenant)
        if any(_id(name) != name or type(job) is not MetrologyJob for name, job in jobs.items()):
            raise TypeError('native_metrology_jobs_required')
        if len({id(job.agent) for job in jobs.values()}) != len(jobs):
            raise ValueError('one_job_per_native_host_required')
        policy.check_principal(principal)
        self.policy, self._bound_principal, self._bound_tenant = policy, principal, tenant
        self.jobs = MappingProxyType(dict(jobs)); self._lock = RLock()
        self.journal = safe_path(journal)
        identity = {'schema': PROFILE, 'owner': owner_for(principal), 'tenant': tenant,
                    'jobs': {name: job.plan_ref for name, job in sorted(jobs.items())},
                    'native_storage': {name: _ref(str(job.agent._output).encode())
                                       for name, job in sorted(jobs.items())}}
        self._identity = identity
        with Journal(self.journal, create=create) as database:
            self._journal_id = database.store_id
            records = Records(database)
            records._immutable('metrology-host', identity)
            for job in jobs.values():
                job.current()
                records.put(job.description); records.put_bytes(job.raw); records.put(job.calibration)

    @staticmethod
    def descriptions():
        return profile_manifest()['tools']

    def binding_scope(self):
        """Catalog identity only; never disclose operator paths or runtime details."""
        with self._records():
            pass
        for job in self.jobs.values():
            job.current()
        return {'profile': PROFILE, 'identity_ref': _ref(encode(self._identity)),
            'journal_binding_ref': _ref(encode([str(self.journal), self._journal_id])),
            'jobs': {name: {'plan_ref': job.plan_ref,
                'source_ref': job.description['source_ref'],
                'runtime_catalog_ref': _ref(job.agent._catalog),
                'native_storage_ref': _ref(str(job.agent._output).encode())}
                for name, job in sorted(self.jobs.items())}}

    @contextmanager
    def _records(self):
        with Journal(self.journal) as database:
            if database.store_id != self._journal_id or database.state('metrology-host')['value'] != self._identity:
                raise ValueError('metrology_owner_binding_changed')
            yield Records(database)

    def _authorized(self, name, args, *, execute=False):
        grant = self.policy.authorize(self._bound_principal, name, args)
        if 'job' in args:
            job = self.jobs[_id(args['job'])]
            job.current()
            if execute and not set(job.operations) <= set(grant['scientific_operations']):
                raise PrincipalRefusal()
            if execute and job.provider_status() == 'provider_unavailable':
                raise ValueError('metrology_provider_unavailable')
        return grant

    def call(self, name, arguments):
        canonical({'name': name, 'arguments': arguments}, limits=REQUEST_LIMITS)
        if name not in TOOLS:
            raise ValueError('unknown_metrology_operation')
        _shape(arguments, TOOLS[name]['inputSchema'])
        with self.policy.lock, self._lock:
            self._authorized(name, arguments, execute=name == 'ncp_metrology_run')
            result = getattr(self, name.removeprefix('ncp_metrology_'))(**deepcopy(arguments))
            # A policy file may change outside the in-process admission lock.
            self._authorized(name, arguments)
            envelope = {'schema': 'ncp.control-response.v1', 'status': 'ok', 'data': result, 'error': None}
            if name == 'ncp_metrology_run' and result.get('status') == 'outcome_unknown':
                # Existing workflow/audit callers classify the standard outer
                # envelope, not arbitrary nested domain results. Preserve the
                # durable intent for status inspection without reporting success.
                envelope.update(status='refused', data=None, error={'code': 'outcome_unknown'})
            from .principal_policy import PrincipalFacade
            PrincipalFacade.mcp_result(name, envelope)
            return envelope

    def catalog(self):
        rows = []
        for name, job in sorted(self.jobs.items()):
            try:
                self._authorized('ncp_metrology_run', {'job': name, 'plan_ref': job.plan_ref, 'attempt': 'discovery'}, execute=True)
            except (ValueError, KeyError, PermissionError):
                executable = False
            else:
                executable = True
            capabilities = job.agent.capabilities()
            operations = capabilities['catalog']['operations']
            graph_operations = {node['operation_id'] for node in job._graph['nodes']}
            executable = executable and capabilities['output_enabled'] and all(
                operations[op]['bound'] and operations[op]['agent_execution_enabled'] for op in graph_operations)
            executable = executable and capabilities['execution_budget']['used'] < capabilities['execution_budget']['maximum']
            rows.append({'job': name, 'plan_ref': job.plan_ref, 'engine': job.engine,
                         'scientific_operations': list(job.operations), 'currently_executable': executable,
                         'provider_status': job.provider_status()})
        return {'profile': PROFILE, 'jobs': rows, 'authority': deepcopy(AUTHORITY)}

    @staticmethod
    def _key(job, attempt):
        return sha256(encode([_id(job), _id(attempt)])).hexdigest()

    def _state(self, records, key):
        index = records.journal.state('metrology-attempts')
        state = None if index is None else index['value']['attempts'].get(key)
        if state is not None:
            if (set(state) != {'job', 'attempt', 'plan_ref', 'state', 'intent_ref', 'outcome_ref'}
                    or state['job'] not in self.jobs or self._key(state['job'], state['attempt']) != key
                    or state['plan_ref'] != self.jobs[state['job']].plan_ref
                    or state['state'] not in ('claimed', 'finished')
                    or (state['state'] == 'claimed') != (state['outcome_ref'] is None)):
                raise ValueError('metrology_attempt_binding_mismatch')
            intent = records.get(state['intent_ref'])
            if (intent.get('schema') != 'ciw.metrology-intent.v1'
                    or any(intent.get(k) != state[k] for k in ('job', 'attempt', 'plan_ref'))
                    or intent.get('tenant') != self._bound_tenant
                    or intent.get('context', {}).get('principal') != self._bound_principal):
                raise ValueError('metrology_intent_binding_mismatch')
        return state

    @staticmethod
    def _outcome(records, state):
        if state['outcome_ref'] is None:
            return None
        outcome = records.get(state['outcome_ref'])
        if (outcome.get('schema') != 'ciw.metrology-outcome.v1'
                or any(outcome.get(k) != state[k] for k in ('job', 'attempt', 'plan_ref', 'intent_ref'))):
            raise ValueError('metrology_outcome_binding_mismatch')
        return outcome

    def _change(self, records, key, value, *, new):
        # Existing Journal compare-and-set provides interprocess claims. Never
        # substitute a Python lock for the durable unique-attempt decision.
        for _ in range(8):
            old = records.journal.state('metrology-attempts')
            values = {} if old is None else deepcopy(old['value']['attempts'])
            if new and key in values:
                return False
            if new and len(values) >= 64:
                raise ValueError('metrology_attempt_capacity')
            if not new and (key not in values or values[key]['state'] != 'claimed'):
                raise ValueError('metrology_attempt_changed')
            values[key] = value
            try:
                records.journal.compare_and_set_state('metrology-attempts', 0 if old is None else old['revision'], {'attempts': values})
                return True
            except ValueError as error:
                if str(error) != 'Stale simulation/configuration revision':
                    raise
        raise ValueError('metrology_claim_contention')

    def run(self, job, plan_ref, attempt):
        selected = self.jobs[job]
        if plan_ref != selected.plan_ref:
            raise ValueError('metrology_plan_digest_mismatch')
        key = self._key(job, attempt)
        with self._records() as records:
            if self._state(records, key) is not None:
                return self._summary(records, key, reused=True)
            intent = {'schema': 'ciw.metrology-intent.v1', 'job': job, 'attempt': attempt,
                      'plan_ref': plan_ref, 'context': self.policy.context(self._bound_principal),
                      'tenant': self._bound_tenant, 'authority': deepcopy(AUTHORITY)}
            state = {'job': job, 'attempt': attempt, 'plan_ref': plan_ref, 'state': 'claimed',
                     'intent_ref': records.put(intent), 'outcome_ref': None}
            if not self._change(records, key, state, new=True):
                return self._summary(records, key, reused=True)
        dispatched = False
        try:
            self._authorized('ncp_metrology_run', {'job': job, 'plan_ref': plan_ref, 'attempt': attempt}, execute=True)
            dispatched = True
            response, artifacts, finding = selected.execute('metrology-' + key[:40])
            status = response.get('status', 'outcome_unknown')
            if response.get('execution_outcome') == 'unknown':
                status = 'outcome_unknown'
            outcome = {'schema': 'ciw.metrology-outcome.v1', 'job': job, 'attempt': attempt, 'plan_ref': plan_ref,
                'intent_ref': state['intent_ref'], 'status': status, 'native_response': response,
                'interpretation': selected.interpretation(artifacts, finding),
                'artifacts': {}, 'authority': deepcopy(AUTHORITY)}
            with self._records() as records:
                outcome['artifacts'] = {name: records.put_bytes(raw) for name, raw in artifacts.items()}
                outcome_ref = records.put(outcome)
                self._change(records, key, {**state, 'state': 'finished', 'outcome_ref': outcome_ref}, new=False)
                return self._summary(records, key, reused=False)
        except Exception:
            # If post-dispatch retention fails, the claimed intent is sufficient
            # to prohibit a replay. Never relabel it completed or overwrite it.
            if not dispatched:
                with self._records() as records:
                    outcome = {'schema': 'ciw.metrology-outcome.v1', 'job': job, 'attempt': attempt,
                        'plan_ref': plan_ref, 'intent_ref': state['intent_ref'], 'status': 'refused',
                        'native_response': None, 'interpretation': None, 'artifacts': {}, 'authority': deepcopy(AUTHORITY)}
                    self._change(records, key, {**state, 'state': 'finished', 'outcome_ref': records.put(outcome)}, new=False)
            with self._records() as records:
                return self._summary(records, key, reused=False)

    def _summary(self, records, key, *, reused):
        state = self._state(records, key)
        if state is None:
            raise ValueError('unknown_metrology_attempt')
        outcome = self._outcome(records, state)
        reviews = records.journal.state('metrology-reviews:' + key)
        return {**state, 'status': 'outcome_unknown' if outcome is None else outcome['status'],
            'interpretation': None if outcome is None else outcome['interpretation'],
            'execution_ids': [] if outcome is None or outcome['native_response'] is None else outcome['native_response'].get('execution_ids', []),
            'result_ids': [] if outcome is None or outcome['native_response'] is None else outcome['native_response'].get('result_ids', []),
            'reused_response': reused, 'reviews': [] if reviews is None else deepcopy(reviews['value']['records']),
            'authority': deepcopy(AUTHORITY)}

    def status(self, job, attempt):
        with self._records() as records:
            return self._summary(records, self._key(job, attempt), reused=True)

    def read(self, job, artifact, attempt=None, review_id=None, offset=0, limit=4096):
        integer(offset, 0, 8388608); integer(limit, 1, 4096)
        selected = self.jobs[job]
        with self._records() as records:
            references = {'plan': selected.plan_ref, 'source': _ref(selected.raw), 'calibration': _ref(encode(selected.calibration))}
            if artifact not in references:
                state = self._state(records, self._key(job, attempt))
                if state is None or state['outcome_ref'] is None:
                    raise ValueError('metrology_outcome_not_retained')
                outcome = self._outcome(records, state)
                references.update(outcome['artifacts'], outcome=state['outcome_ref'])
                if artifact == 'review':
                    _id(review_id)
                    reviews = records.journal.state('metrology-reviews:' + self._key(job, attempt))
                    matches = [] if reviews is None else [v for v in reviews['value']['records'] if v['review_id'] == review_id]
                    if len(matches) != 1:
                        raise ValueError('unknown_metrology_review')
                    references['review'] = matches[0]['record']
            reference = references[artifact]
            raw = records.bytes(reference)
        if offset > len(raw):
            raise ValueError('metrology_offset_outside_artifact')
        part = raw[offset:offset + limit]
        return {'artifact': artifact, 'sha256': reference, 'byte_count': len(raw), 'offset': offset,
                'bytes_b64': base64.b64encode(part).decode('ascii'),
                'next_offset': offset + len(part) if offset + len(part) < len(raw) else None,
                'authority': deepcopy(AUTHORITY)}

    def review(self, job, attempt, outcome_ref, review_id, disposition, note):
        key = self._key(job, attempt); _id(review_id); text(note, 2048)
        with self._records() as records:
            state = self._state(records, key)
            if state is None or state['outcome_ref'] is None or outcome_ref != state['outcome_ref']:
                raise ValueError('metrology_review_requires_exact_retained_outcome')
            self._outcome(records, state)
            value = {'schema': 'ciw.metrology-review.v1', 'job': job, 'attempt': attempt,
                'outcome_ref': outcome_ref, 'plan_ref': state['plan_ref'], 'review_id': review_id,
                'disposition': disposition, 'note': note, 'context': self.policy.context(self._bound_principal),
                'tenant': self._bound_tenant, 'authority': deepcopy(AUTHORITY)}
            reference = records.put(value)
            for _ in range(8):
                old = records.journal.state('metrology-reviews:' + key)
                rows = [] if old is None else deepcopy(old['value']['records'])
                existing = [row for row in rows if row['review_id'] == review_id]
                if existing:
                    if existing[0]['record'] != reference:
                        raise ValueError('metrology_review_identity_conflict')
                    break
                if len(rows) >= 16:
                    raise ValueError('metrology_review_capacity')
                rows.append({'review_id': review_id, 'record': reference})
                try:
                    records.journal.compare_and_set_state('metrology-reviews:' + key,
                        0 if old is None else old['revision'], {'records': rows})
                    break
                except ValueError as error:
                    if str(error) != 'Stale simulation/configuration revision':
                        raise
            else:
                raise ValueError('metrology_review_contention')
        return {'record': reference, 'review': value, 'authority': deepcopy(AUTHORITY)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--initialize', action='store_true', help='Explicit create-only journal and native output initialization')
    parser.add_argument('--companion-policies', type=Path,
                        help='Host-only bounded companion isolation policy file')
    parser.add_argument('--tool', choices=tuple(TOOLS))
    parser.add_argument('--arguments', default='{}')
    args = parser.parse_args(argv)
    try:
        from .campaign_contracts import closed_object
        from .companion_policy_config import load_companion_policies, policy_set
        from .principal_policy import _read_policy
        config = _read_policy(safe_path(args.config))
        closed_object(config, {'schema', 'principal', 'tenant', 'policy', 'journal', 'jobs'})
        if config['schema'] != PROFILE or type(config['jobs']) is not dict or not 1 <= len(config['jobs']) <= 8:
            raise ValueError('invalid_metrology_launch_configuration')
        for field in ('policy', 'journal'):
            if not Path(config[field]).is_absolute():
                raise ValueError('absolute_operator_path_required')
        policy = PrincipalPolicy.from_file(safe_path(config['policy']))
        companion_policies = load_companion_policies(args.companion_policies)
        operational_policies = {name: value for name, value in companion_policies.items()
                                if name in {'calibrated-window', 'residual-monitor'}}
        with ExitStack() as stack:
            jobs = {}
            for name, value in config['jobs'].items():
                closed_object(value, {'kind', 'profile'}, {'model', 'provider', 'revision'})
                if not Path(value['profile']).is_absolute():
                    raise ValueError('absolute_operator_profile_required')
                if value['kind'] == 'operational' and set(value) == {'kind', 'profile', 'model'}:
                    from .operational_agent import OperationalHost
                    native = stack.enter_context(OperationalHost(value['profile'], resume=not args.initialize,
                                                                 process_policies=operational_policies))
                    jobs[name] = MetrologyJob.operational(native, value['model'])
                elif value['kind'] == 'bim-clearance' and set(value) == {'kind', 'profile', 'provider', 'revision'}:
                    from .metrology_clearance import Provider
                    from .agent_mcp import from_profile
                    if not Path(value['provider']).is_absolute():
                        raise ValueError('absolute_operator_provider_required')
                    selected = policy_set(companion_policies, 'bim-clearance')
                    provider = Provider(safe_path(value['provider']), value['revision'],
                                        process_policy=None if selected is None else selected['cse'])
                    native = stack.enter_context(from_profile(safe_path(value['profile']), instrument='bim-clearance',
                        clearance_provider=provider, resume=not args.initialize))
                    jobs[name] = MetrologyJob.clearance(native)
                else:
                    raise ValueError('unsupported_metrology_launch_binding')
            host = MetrologyHost(policy=policy, principal=config['principal'], tenant=config['tenant'],
                jobs=jobs, journal=config['journal'], create=args.initialize)
            facade = policy.facade(config['principal'], metrology=host)
            if args.tool:
                print(encode(facade.call(args.tool, parse(args.arguments.encode()))).decode())
            else:
                from .mcp_server import serve_sdk
                serve_sdk(facade, name='odsp-metrology')
        return 0
    except Exception:
        print('metrology_request_refused', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
