"""Canonical principal-bound MCP surface for one fixed retained survey pipeline.

All source, model, code, policy and retention selection belongs to the launcher.
No request supplies authority, SQL, a path, endpoint or executable. Outputs stay
held; local custody and scientific grants do not admit physical or NSL state.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from pathlib import Path
import base64
import re

from .agent_tools import _shape
from .campaign_contracts import REQUEST_LIMITS, canonical, mcp_envelope, parse
from .data_instruments import content_ref, version_ref
from .survey_analysis_pipeline import CLAIMS, PIPELINE_ID, SurveyAnalysisPipeline

PROFILE = 'ciw.survey-pipeline.v1'
_REF = {'type': 'string', 'pattern': '^sha256:[0-9a-f]{64}$', 'minLength': 71, 'maxLength': 71}
_ATTEMPT = {'type': 'string', 'minLength': 1, 'maxLength': 80}
_ARGS = {'plan_ref': _REF, 'attempt': _ATTEMPT}


def _spec(description, properties=None, required=(), *, read=True):
    return {'description': description,
        'inputSchema': {'type': 'object', 'properties': properties or {},
                        'required': list(required), 'additionalProperties': False},
        'outputSchema': {'type': 'object'},
        'annotations': {'readOnlyHint': read, 'destructiveHint': False,
                        'idempotentHint': True, 'openWorldHint': False}}


TOOLS = {
    'ncp_pipeline_catalog': _spec('Describe the one host-bound fixed survey plan. Discovery grants no execution or evidence admission.'),
    'ncp_pipeline_run': _spec('Execute or reopen the exact host-bound survey plan under a durable attempt. No SQL, provider, input or authority selection; outputs remain held.',
                            _ARGS, tuple(_ARGS), read=False),
    'ncp_pipeline_inspect': _spec('Inspect one existing attempt under current grants and source holds. Never resume or retry uncertain work.',
                                _ARGS, tuple(_ARGS)),
    'ncp_pipeline_read': _spec('Read a bounded page of the exact held artifact for an existing attempt. Current source/authority checks apply to every page.',
        {**_ARGS, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 131072},
         'limit': {'type': 'integer', 'minimum': 128, 'maximum': 16384}}, (*_ARGS, 'offset', 'limit')),
}


class PipelineProfileRefusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise PipelineProfileRefusal(reason)


class SurveyPipelineHost:
    """Trusted launcher constructor; both admissions remain mandatory.

    admit(detached_intent, current_context) must return exact True anew. The
    underlying pipeline's own admission, scientific grants, source checks and
    durable unknown-outcome hold are independently enforced.
    """
    strict_mcp_input = True
    instructions = 'One principal-bound fixed survey pipeline. Outputs are held observations, never physical state, approval or authority.'

    def __init__(self, pipeline, *, admit):
        require(type(pipeline) is SurveyAnalysisPipeline and callable(admit), 'bound_pipeline_and_admission_required')
        self.pipeline, self.policy, self._admit = pipeline, pipeline.policy, admit
        self._bound_principal, self._bound_tenant = pipeline.survey.ledger.principal, pipeline.survey.ledger.tenant
        self._objects = (pipeline, pipeline.policy, admit, self._bound_principal, self._bound_tenant)
        self._plan_ref, self._code_ref = pipeline.plan_ref, content_ref(Path(__file__).read_bytes())
        self._interface_ref = version_ref(TOOLS)
        self._check_bindings()

    def _check_bindings(self):
        require((self.pipeline, self.policy, self._admit, self._bound_principal, self._bound_tenant) == self._objects
                and self.pipeline.policy is self.policy
                and self.pipeline.survey.ledger.principal == self._bound_principal
                and self.pipeline.survey.ledger.tenant == self._bound_tenant
                and self.pipeline.plan_ref == self._plan_ref
                and content_ref(Path(__file__).read_bytes()) == self._code_ref
                and version_ref(TOOLS) == self._interface_ref, 'pipeline_profile_binding_changed')
        self.pipeline._check_bindings()

    def _request(self, name, arguments):
        require(type(name) is str and name in TOOLS, 'unknown_pipeline_service')
        arguments = parse(canonical(arguments, limits=REQUEST_LIMITS), limits=REQUEST_LIMITS)
        _shape(arguments, TOOLS[name]['inputSchema'])
        if name != 'ncp_pipeline_catalog':
            require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', arguments['attempt']) is not None,
                    'pipeline_attempt_identifier_refused')
            require(arguments['plan_ref'] == self._plan_ref, 'pipeline_plan_pin_refused')
        return arguments

    def request_scope_guard(self, name, arguments):
        arguments = self._request(name, arguments)
        self._check_bindings()
        context = self.policy.authorize_context(self._bound_principal, name, arguments)
        intent = {'operation': name, 'arguments': arguments, 'plan_ref': self._plan_ref,
                  'profile': PROFILE, 'implementation_ref': self._code_ref, 'interface_ref': self._interface_ref}

        def check():
            self._check_bindings()
            self.policy.validate_context(context)
            self.policy.authorize(self._bound_principal, name, arguments)
            try:
                allowed = self._admit(deepcopy(intent), deepcopy(context)) is True
            except Exception:
                allowed = False
            require(allowed, 'pipeline_profile_current_admission_refused')
            self._check_bindings()
            self.policy.validate_context(context)
            if name != 'ncp_pipeline_catalog':
                self.pipeline._guard('run' if name == 'ncp_pipeline_run' else 'read', context)
            self.policy.authorize(self._bound_principal, name, arguments)
            self.policy.validate_context(context)
            self._check_bindings()
            return True
        check()
        return check

    @staticmethod
    def _summary(result):
        summary = {k: deepcopy(result[k]) for k in ('status', 'job_id', 'attempt', 'automatic_retry', 'claims')}
        if 'artifact' in result:
            artifact = result['artifact']
            summary.update({k: result[k] for k in ('artifact_id', 'artifact_ref', 'reproducible_value_ref')})
            summary.update(review=artifact['review'], input={k: artifact['input'][k]
                for k in ('source_id', 'source_ref', 'capture_ref', 'dataset_ref', 'schema_ref', 'model_ref')},
                output_dataset_ref=artifact['transform']['dataset_ref'],
                statistics=artifact['analysis']['value']['statistics'],
                uncertainty=artifact['analysis']['value']['uncertainty'],
                implementation_ref=version_ref(artifact['pipeline']['implementation']))
        return summary

    def call(self, name, arguments):
        started = False
        try:
            arguments = self._request(name, arguments)
            guard = self.request_scope_guard(name, arguments)
            if name == 'ncp_pipeline_catalog':
                data = {'profile': PROFILE, 'pipeline_id': PIPELINE_ID, 'plan_ref': self._plan_ref,
                    'definition': deepcopy(self.pipeline._plan['definition']),
                    'implementation_ref': version_ref(self.pipeline._plan['implementation']),
                    'input_dataset_ref': self.pipeline._request['dataset_ref'],
                    'scope': 'one_host_bound_local_plan', 'readiness': 'not_asserted_by_discovery',
                    'claims': deepcopy(CLAIMS)}
            else:
                guard()
                if name == 'ncp_pipeline_run':
                    started = True  # Local custody/publication may now occur.
                    result = self.pipeline.run(arguments['attempt'])
                    require(result['status'] == 'held', 'pipeline_attempt_not_completed')
                else:
                    result = self.pipeline.inspect(arguments['attempt'])
                guard()
                if name == 'ncp_pipeline_read':
                    require('artifact' in result, 'pipeline_artifact_unavailable')
                    raw = canonical(result['artifact'])
                    offset, limit = arguments['offset'], arguments['limit']
                    require(offset <= len(raw), 'pipeline_artifact_offset_refused')
                    chunk = raw[offset:offset + limit]
                    data = {'status': 'held', 'artifact_ref': result['artifact_ref'], 'byte_size': len(raw),
                        'offset': offset, 'next_offset': offset + len(chunk) if offset + len(chunk) < len(raw) else None,
                        'chunk_b64': base64.b64encode(chunk).decode(), 'claims': deepcopy(CLAIMS)}
                else:
                    data = self._summary(result)
            data['service'] = {'profile_id': PROFILE, 'implementation_ref': self._code_ref,
                               'interface_ref': self._interface_ref}
            reply = {'status': 'ok', 'data': data, 'authorizes_execution': False}
            require(len(canonical(reply)) <= 65536, 'pipeline_service_output_budget')
            mcp_envelope(reply)
            guard()
            return reply
        except Exception:
            # A failed run may already have retained a result. Requests cannot
            # choose a new identity to undo custody; inspect the same attempt.
            return {'status': 'refused', 'data': None,
                'error': {'code': 'outcome_unknown' if started else 'pipeline_request_or_current_scope_refused'},
                'effect_state': 'unknown' if started else 'not_started', 'authorizes_execution': False}

    def descriptions(self):
        self._check_bindings()
        return [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()]


def profile_manifest():
    return {'profile_id': PROFILE, 'tools': [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()],
        'scope': 'principal_bound_fixed_local_survey_pipeline', 'authority': deepcopy(CLAIMS),
        'limits': {'pipelines': 1, 'attempts': 16, 'page_bytes': 16384, 'reply_bytes': 65536},
        'qualification': 'requires_local_service_tests_no_hosted_or_physical_inference'}
