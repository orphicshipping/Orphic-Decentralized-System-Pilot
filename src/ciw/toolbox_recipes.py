"""Fixed client recipes composed solely of existing supported canonical calls.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from .toolbox_skills import SkillRegistry


def builtin_skills(*, facade=None):
    text = {'type': 'string', 'minLength': 1, 'maxLength': 512}
    def schema(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    return SkillRegistry([
        {'schema': 'ncp.client-skill.v1', 'skill_id': 'reference-citation', 'version': '1.0.0',
         'description': 'Resolve one exact source and retain its first matching evidence passage; no truth or admission claim.',
         'inputSchema': schema({'source_id': text, 'query': text}),
         'steps': [
             {'step_id': 'resolve', 'tool': 'ncp_knowledge_resolve', 'arguments': {'source_id': {'input': 'source_id'}}},
             {'step_id': 'search', 'tool': 'ncp_evidence_search', 'arguments': {'query': {'input': 'query'},
              'record_id': {'step': 'resolve', 'path': ['record', 'record_id']}, 'limit': {'literal': 1}}},
             {'step_id': 'passage', 'tool': 'ncp_evidence_span', 'arguments': {
              'span_id': {'step': 'search', 'path': ['items', 0, 'span_id']}}}]},
        {'schema': 'ncp.client-skill.v1', 'skill_id': 'cross-profile-provenance', 'version': '1.0.0',
         'description': 'Retain independent reference and logistics snapshots together; no inferred semantic relationship.',
         'inputSchema': schema({}),
         'steps': [
             {'step_id': 'reference', 'tool': 'ncp_knowledge_overview', 'arguments': {}},
             {'step_id': 'logistics', 'tool': 'ncp_logistics_catalog', 'arguments': {}},
             {'step_id': 'readback', 'tool': 'ncp_logistics_retained_read', 'arguments': {
              'artifact_id': {'step': 'logistics', 'path': ['artifact_id']}, 'limit': {'literal': 4096}}}]}
    ], facade=facade)
