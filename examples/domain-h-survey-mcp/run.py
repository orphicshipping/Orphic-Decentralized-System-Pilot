"""Synthetic operator launcher for the canonical, audited survey MCP profile.

No hosted listener, credentials, external source, device, SQL input or approval
endpoint. Every invocation requires a new private synthetic fixture directory.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from ciw.audit_chain import AuditChain, empty_anchor
from ciw.audited_host import AuditedPrincipalHost
from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import content_ref
from ciw.persistent_cache import PrivateSQLite
from ciw.services import invoke
from ciw.survey_analysis_pipeline import validate_artifact
from ciw.survey_pipeline_profile import SurveyPipelineHost, TOOLS


class LocalAnchor:
    """Separate local SQLite CAS head; not a remote trust or NSL anchor."""
    def __init__(self, path):
        self.db = PrivateSQLite(path)
        with self.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pipeline_anchor(singleton INTEGER PRIMARY KEY,value BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO pipeline_anchor VALUES(1,?)',
                       (canonical(empty_anchor('synthetic-survey-service')),))

    def read(self):
        with self.db.connect() as db:
            return parse(db.execute('SELECT value FROM pipeline_anchor WHERE singleton=1').fetchone()[0])

    def cas(self, before, after):
        with self.db.transaction() as db:
            return db.execute('UPDATE pipeline_anchor SET value=? WHERE singleton=1 AND value=?',
                              (canonical(after), canonical(before))).rowcount == 1


def source_module():
    path = Path(__file__).resolve().parents[1] / 'domain-h-survey-analysis/run.py'
    spec = importlib.util.spec_from_file_location('canonical_pipeline_synthetic_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bind(root):
    root.mkdir(mode=0o700, exist_ok=False)
    p = source_module().prepare(root / 'survey')
    # Only this new synthetic in-memory policy is extended. No existing host
    # settings or saved policy are changed or loaded as current authority.
    p.state['policy']['principals'][0]['operations'] = [{'name': name, 'constraints': {}} for name in TOOLS]
    state = {'decision': True, 'seen': []}
    def admission(intent, context):
        state['seen'].append(intent['operation'])
        return state['decision']
    host = SurveyPipelineHost(p.active, admit=admission)
    facade = p.policy.facade('survey-operator', pipeline=host)
    audit = root / 'audit'
    audit.mkdir(mode=0o700)
    anchor = LocalAnchor(audit / 'anchor.sqlite')
    chain = AuditChain(audit / 'calls.jsonl', chain_id='synthetic-survey-service', allowed_operations=TOOLS)
    wrapped = AuditedPrincipalHost(facade, chain=chain, read_anchor=anchor.read,
                                   compare_and_set_anchor=anchor.cas)
    return SimpleNamespace(root=root, source=p, profile=host, facade=facade, wrapped=wrapped,
                           anchor=anchor, chain=chain, state=state)


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def demonstrate(root):
    p = bind(root)
    catalog = invoke('ncp_pipeline_catalog', {}, host=p.wrapped)
    args = {'plan_ref': catalog['plan_ref'], 'attempt': 'canonical-survey-1'}
    first = invoke('ncp_pipeline_run', args, host=p.wrapped)
    inspected = invoke('ncp_pipeline_inspect', args, host=p.wrapped)
    replay = invoke('ncp_pipeline_run', args, host=p.wrapped)
    if first['artifact_ref'] != inspected['artifact_ref'] or first['artifact_ref'] != replay['artifact_ref']:
        raise ValueError('canonical_pipeline_replay_changed')
    assembled, pages, offset = bytearray(), [], 0
    while True:
        page = invoke('ncp_pipeline_read', {**args, 'offset': offset, 'limit': 8192}, host=p.wrapped)
        assembled.extend(base64.b64decode(page['chunk_b64'], validate=True))
        pages.append({k: page[k] for k in ('artifact_ref', 'byte_size', 'offset', 'next_offset')})
        offset = page['next_offset']
        if offset is None:
            break
    if content_ref(bytes(assembled)) != first['artifact_ref']:
        raise ValueError('canonical_page_assembly_pin_mismatch')
    artifact = validate_artifact(parse(bytes(assembled)), expected_ref=first['artifact_ref'])
    events = p.chain.verify(p.anchor.read())
    write_new(root / 'assembled-analysis.json', artifact)
    write_new(root / 'audit-verification.json', {'head': p.anchor.read(), 'records': events,
        'scope': 'local_redacted_chain_separate_sqlite_cas_not_nsl_or_authenticated_time'})
    receipt = {'schema': 'odsp.synthetic-canonical-survey-mcp.v1', 'status': 'held',
        'catalog': catalog, 'result': first, 'same_attempt_replay_equal': first == replay,
        'inspection_same_artifact': inspected['artifact_ref'] == first['artifact_ref'],
        'artifact_ref': first['artifact_ref'], 'pages': pages, 'audit_events': len(events),
        'policy_settings_changed': False, 'hosted_qualified': False,
        'nsl_admission': 'not_verified', 'authenticated_time': False}
    write_new(root / 'journey.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--stdio', action='store_true', help='Serve the existing strict MCP stdio adapter')
    args = parser.parse_args()
    root = args.output.absolute()
    if args.stdio:
        from ciw.knowledge_mcp import serve_stdio
        asyncio.run(serve_stdio(bind(root).wrapped))
    else:
        print(json.dumps(demonstrate(root), sort_keys=True))


if __name__ == '__main__':
    main()
