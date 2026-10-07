"""Read-only projections of the existing selected-corpus sidecars.

Copyright (c) 2026 Notation Systems. SPDX-License-Identifier: AGPL-3.0-or-later
No Session, provider, scientific assertion adjudicator, network, or execution owner.
Original selection records are retained verbatim. Runtime indexes are derived.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any

from .control_contracts import bytes_ref, content_ref, json_tree, keys
from .core.identities import content_identity
from .session import loads_json

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_SOURCES = 512
MAX_SPANS = 10000
MAX_SPAN_BYTES = 1800
MAX_RESPONSE = 128 * 1024
SID = re.compile(r'NCP-CS-[0-9]{3}')
HEX = re.compile(r'[0-9a-f]{64}')
INPUTS = ('source_manifest.jsonl', 'selection_graph.json', 'collections.json',
          'review_findings.jsonl', 'extraction_queue.jsonl')
BOUNDARIES = {
    'authorizes_execution': False, 'operation_id': None, 'execution_id': None,
    'verification_id': None, 'scientific_adjudication': 'not_performed',
    'live_binding_checked': False, 'source_authenticity_checked': False,
    'data_role': 'untrusted_retained_source_or_curatorial_metadata',
    'snapshot_semantics': 'frozen_at_open_not_live_Drive',
}


def encode(value: Any) -> bytes:
    json_tree(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def parse(raw: bytes) -> Any:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FILE:
        raise ValueError('Document outside byte bounds')
    value = loads_json(raw.decode('utf-8'))
    json_tree(value)
    return value


def jsonl(raw: bytes) -> list[dict]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FILE:
        raise ValueError('JSONL outside byte bounds')
    lines = raw.splitlines()
    if not lines or len(lines) > MAX_SOURCES:
        raise ValueError('JSONL record count outside bounds')
    rows = [parse(line) for line in lines]
    if any(type(row) is not dict for row in rows):
        raise ValueError('JSONL requires objects')
    return rows


def relative_name(name: str) -> str:
    if (type(name) is not str or not name or len(name) > 512 or '\\' in name
            or ':' in name or '\x00' in name):
        raise ValueError('Invalid local member name')
    parts = name.split('/')
    if any(p in ('', '.', '..') for p in parts) or PurePosixPath(name).is_absolute():
        raise ValueError('Unsafe local member name')
    return name


def read_file(path: Path) -> bytes:
    """Reject ordinary symlink/special-file inputs; not a hostile-filesystem sandbox."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symbolic-link input refused')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE:
        raise ValueError('Require a bounded ordinary file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('Input identity changed before read')
        raw = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
    if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or len(raw) != after.st_size or not 0 < len(raw) <= MAX_FILE):
        raise ValueError('Input changed or exceeded bounds during read')
    return raw


def selection_id(value: str) -> str:
    if type(value) is not str or SID.fullmatch(value) is None:
        raise ValueError('Require a selection ID, never a path or URL')
    return value


def _object(value, label):
    if type(value) is not dict:
        raise ValueError(label + ' must be an object')
    return value


def _text(value, label):
    if type(value) is not str:
        raise ValueError(label + ' must be text')
    return value


def _array(value, label):
    if type(value) is not list:
        raise ValueError(label + ' must be an array')
    return value


def _text_array(value, label):
    for item in _array(value, label):
        _text(item, label + ' item')


def validate_sources(rows: list[dict]) -> dict[str, dict]:
    by_id, source_ids, copies = {}, set(), set()
    for r in _array(rows, 'Source selections'):
        _object(r, 'Source selection')
        for name in ('schema', 'selection_id', 'source_id', 'original', 'snapshot',
                     'staged_copy', 'curation', 'rights', 'pipeline', 'authorizes_execution'):
            if name not in r:
                raise ValueError('Missing selection field: ' + name)
        # Check containers before reading them; malformed raw sidecars must refuse
        # normally, not escape the CLI's refusal path with AttributeError.
        for name in ('original', 'snapshot', 'staged_copy', 'curation', 'rights', 'pipeline'):
            _object(r[name], 'Selection ' + name)
        sid = selection_id(r['selection_id'])
        if r['schema'] != 'notation.corpus-selection.source.v1' or r['authorizes_execution'] is not False:
            raise ValueError('Wrong selection schema or non-authorizing boundary')
        if any(r.get(k) is not None for k in ('operation_id', 'execution_id', 'verification_id')):
            raise ValueError('Selection records must not create operation/event identities')
        if type(r['source_id']) is not str or not r['source_id']:
            raise ValueError('Invalid source identity')
        s, c, a = r['snapshot'], r['staged_copy'], r['curation']
        for name in ('drive_file_id', 'url'):
            _text(c.get(name), 'Staged-copy ' + name)
        if r['original'].get('url') is not None:
            _text(r['original']['url'], 'Original URL')
        _text(r['rights'].get('status'), 'Rights status')
        for name in ('title', 'collection_code', 'priority'):
            _text(a.get(name), 'Curation ' + name)
        for name in ('source_reported_scope', 'proposed_ncp_application', 'source_role', 'reviewed_extent'):
            if name in a:
                _text(a[name], 'Curation ' + name)
        for name in ('proposed_extraction_targets', 'limitations_to_preserve'):
            if name in a:
                _text_array(a[name], 'Curation ' + name)
        if type(s.get('sha256')) is not str or HEX.fullmatch(s['sha256']) is None:
            raise ValueError('Invalid captured hash')
        if type(s.get('size_bytes')) is not int or s['size_bytes'] <= 0:
            raise ValueError('Invalid source byte count')
        if c.get('readback_sha256') != s['sha256'] or c.get('byte_match_verified') is not True:
            raise ValueError('Staged-copy declaration disagrees with captured source')
        relative_name(c['relative_path'])
        name = r['original']['filename']
        if relative_name(name) != PurePosixPath(name).name:
            raise ValueError('Original filename must be a basename')
        if PurePosixPath(c['relative_path']).name != name:
            raise ValueError('Staged basename differs from preserved original')
        if a.get('collection_code') not in {'NCP', 'MET', 'SIM', 'MAN', 'KGE', 'SPC'}:
            raise ValueError('Unknown collection')
        if a.get('priority') not in {'P0', 'P1', 'P2'}:
            raise ValueError('Unknown selection priority')
        if type(r['rights'].get('fulltext_ingestion_approved')) is not bool:
            raise ValueError('Rights flag must be a boolean')
        if sid in by_id or r['source_id'] in source_ids or c['drive_file_id'] in copies:
            raise ValueError('Duplicate selection, source, or copy identity')
        by_id[sid] = r
        source_ids.add(r['source_id']); copies.add(c['drive_file_id'])
    return by_id


def expected_graph(rows: list[dict], collections: dict) -> dict:
    """Reconstruct this v1 staging graph, not a scientific fact graph."""
    nodes, edges, seen_nodes = [], [], set()
    _object(collections, 'Collections')
    folders = _array(collections.get('folders'), 'Collection folders')
    for folder in folders:
        _object(folder, 'Collection folder')
        for name in ('code', 'label', 'id'):
            _text(folder.get(name), 'Collection ' + name)
    codes = [f['code'] for f in folders]
    if len(codes) != len(set(codes)):
        raise ValueError('Duplicate collection code')
    def add(node):
        if node['id'] in seen_nodes:
            raise ValueError('Duplicate staging graph node')
        seen_nodes.add(node['id']); nodes.append(node)
    for f in folders:
        add({'id': 'collection:' + f['code'], 'type': 'Collection', 'label': f['label'], 'folder_id': f['id']})
    for r in rows:
        c, s = r['curation'], r['snapshot']
        if c['collection_code'] not in codes:
            raise ValueError('Missing collection declaration')
        source = r['source_id']; snap = 'snapshot:' + s['sha256']; copy = 'gdrive-copy:' + r['staged_copy']['drive_file_id']
        add({'id': source, 'type': 'SourceRecord', 'selection_id': r['selection_id'],
             'title': c['title'], 'priority': c['priority'], 'annotation_role': 'curatorial_metadata'})
        # Existing v1 graph has one node per captured hash. Duplicate bytes may share it.
        sn = {'id': snap, 'type': 'SourceSnapshot', 'sha256': s['sha256'], 'size_bytes': s['size_bytes']}
        if snap not in seen_nodes:
            add(sn)
        elif next(n for n in nodes if n['id'] == snap) != sn:
            raise ValueError('Conflicting snapshot metadata')
        add({'id': copy, 'type': 'StagedCopy', 'url': r['staged_copy']['url'], 'sha256': s['sha256']})
        edges += [{'from': source, 'relation': 'HAS_CAPTURED_VERSION', 'to': snap, 'basis': 'materialized_source_bytes'},
                  {'from': copy, 'relation': 'BYTE_IDENTICAL_TO', 'to': snap, 'basis': 'sha256_readback_comparison'},
                  {'from': source, 'relation': 'SELECTED_FOR', 'to': 'collection:' + c['collection_code'], 'basis': 'curator_assessment'}]
    return {'schema': 'notation.corpus-selection.graph.v1', 'role': 'metadata_and_provenance_staging_graph',
            'not_a_scientific_fact_graph': True, 'authorizes_execution': False, 'nodes': nodes, 'edges': edges}


def graph_signature(graph: dict) -> str:
    _object(graph, 'Selection graph')
    for node in _array(graph.get('nodes'), 'Graph nodes'):
        _object(node, 'Graph node')
        _text(node.get('id'), 'Graph node ID')
    for edge in _array(graph.get('edges'), 'Graph edges'):
        _object(edge, 'Graph edge')
        for name in ('from', 'relation', 'to'):
            _text(edge.get(name), 'Graph edge ' + name)
    g = deepcopy(graph)
    g['nodes'] = sorted(g['nodes'], key=lambda n: n['id'])
    g['edges'] = sorted(g['edges'], key=lambda e: (e['from'], e['relation'], e['to']))
    return content_identity(g)


def validate_sidecars(raw: dict[str, bytes]):
    rows = jsonl(raw['source_manifest.jsonl']); by_id = validate_sources(rows)
    collections = parse(raw['collections.json']); graph = parse(raw['selection_graph.json'])
    if graph_signature(graph) != graph_signature(expected_graph(rows, collections)):
        raise ValueError('Graph does not exactly reconcile with retained manifest')
    findings = jsonl(raw['review_findings.jsonl']); tasks = jsonl(raw['extraction_queue.jsonl'])
    for finding in findings:
        for name in ('finding_id', 'kind', 'observation', 'next_action'):
            _text(finding.get(name), 'Review finding ' + name)
        _text_array(finding.get('selection_ids'), 'Review finding selection_ids')
    if len({f['finding_id'] for f in findings}) != len(findings):
        raise ValueError('Duplicate finding identity')
    for f in findings:
        if f.get('authorizes_execution') is not False or f.get('verification_id') is not None:
            raise ValueError('Review finding cannot confer authority')
        if any(s not in by_id and s != 'ALL' for s in f['selection_ids']):
            raise ValueError('Finding references missing selection')
    task_ids, tasked = set(), set()
    for t in tasks:
        sid = selection_id(t.get('selection_id'))
        _text(t.get('task_id'), 'Extraction task ID')
        if sid not in by_id or sid in tasked or t['task_id'] in task_ids:
            raise ValueError('Duplicate or unresolved extraction task')
        if (t['source_id'] != by_id[sid]['source_id'] or t['source_sha256'] != by_id[sid]['snapshot']['sha256']
                or t.get('authorizes_execution') is not False):
            raise ValueError('Extraction task disagrees with source identity')
        tasked.add(sid); task_ids.add(t['task_id'])
    if tasked != set(by_id):
        raise ValueError('Extraction queue must cover the selected corpus exactly')
    return rows, by_id, graph, findings, tasks


def compile_bundle(inputs: Path, snapshots: Path, output: Path, *, expose: tuple[str, ...]) -> str:
    """Create-only index inputs. Explicit host choice permits only retained internal text.

    External PDF full-text ingestion remains held; this does not adjudicate rights.
    """
    if type(expose) is not tuple or len(set(expose)) != len(expose):
        raise ValueError('Require distinct explicit text-source IDs')
    raw = {name: read_file(Path(inputs) / name) for name in INPUTS}
    _, by_id, _, _, _ = validate_sidecars(raw)
    exposure = {}
    for sid in expose:
        selection_id(sid)
        r = by_id[sid]; filename = r['original']['filename']
        if (r['curation']['collection_code'] != 'NCP' or r['rights']['status'] != 'Internal retained artifact'
                or Path(filename).suffix not in {'.md', '.json'}):
            raise ValueError('External full-text ingestion is not enabled by this profile')
        b = read_file(Path(snapshots) / filename)
        if sha256(b).hexdigest() != r['snapshot']['sha256'] or len(b) != r['snapshot']['size_bytes']:
            raise ValueError('Source snapshot byte mismatch')
        b.decode('utf-8')
        if filename.endswith('.json'):
            parse(b)
        member = 'sources/' + sid + Path(filename).suffix
        raw[member] = b; exposure[sid] = member
    if sum(map(len, raw.values())) > MAX_TOTAL:
        raise ValueError('Bundle exceeds aggregate byte budget')
    catalog = {'schema': 'notation.corpus-retrieval.bundle.v1',
               'authorizes_execution': False, 'files': {n: bytes_ref(b) for n, b in sorted(raw.items())},
               'exposed_internal_text': exposure,
               'basis': 'operator_selected_local_internal_text_only_external_fulltext_held'}
    root_bytes = encode(catalog); ref = bytes_ref(root_bytes)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('Output must be new and not traverse symbolic links')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the target; catalog last. Caller must retain the returned root pin independently.
    stage = Path(tempfile.mkdtemp(prefix='.corpus-build-', dir=output.parent))
    try:
        for name, b in raw.items():
            dest = stage / name; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b)
        (stage / 'catalog.json').write_bytes(root_bytes)
        Corpus.open(stage, expected_ref=ref)
        if output.exists():
            raise ValueError('Output appeared during compilation')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return ref


def spans(raw: bytes):
    """Contiguous UTF-8 byte windows, preferably at paragraph boundaries; no summaries."""
    raw.decode('utf-8'); start = 0
    while start < len(raw):
        end = min(start + MAX_SPAN_BYTES, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        if end < len(raw):
            boundary = raw.rfind(b'\n\n', start, end)
            if boundary >= start + 256:
                end = boundary + 2
        yield start, end, raw[start:end]
        start = end


def tokens(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text.casefold())


class Corpus:
    """Frozen local index. Open verifies source pins; responses are detached copies."""
    @classmethod
    def open(cls, root: Path, *, expected_ref: str) -> 'Corpus':
        content_ref(expected_ref)
        root = Path(root)
        catalog_raw = read_file(root / 'catalog.json')
        if bytes_ref(catalog_raw) != expected_ref:
            raise ValueError('Bundle root pin mismatch')
        cat = parse(catalog_raw)
        keys(cat, {'schema', 'authorizes_execution', 'files', 'exposed_internal_text', 'basis'})
        if cat['schema'] != 'notation.corpus-retrieval.bundle.v1' or cat['authorizes_execution'] is not False:
            raise ValueError('Wrong bundle schema or authority')
        if type(cat['files']) is not dict or len(cat['files']) > MAX_SOURCES + len(INPUTS):
            raise ValueError('Bundle member count exceeded')
        if type(cat['exposed_internal_text']) is not dict:
            raise ValueError('Exposure map required')
        expected_members = set(INPUTS) | set(cat['exposed_internal_text'].values())
        if set(cat['files']) != expected_members:
            raise ValueError('Unexpected or missing bundle members')
        raw, total = {}, 0
        for name, ref in cat['files'].items():
            relative_name(name); content_ref(ref)
            b = read_file(root / name); total += len(b)
            if total > MAX_TOTAL or bytes_ref(b) != ref:
                raise ValueError('Bundle member mismatch or aggregate budget exceeded')
            raw[name] = b
        rows, by_id, graph, findings, tasks = validate_sidecars(raw)
        self = cls(); self.ref = expected_ref
        self._records = deepcopy(by_id); self._graph = deepcopy(graph)
        self._findings = deepcopy(findings); self._tasks = deepcopy(tasks)
        self._raw, self._json, self._spans, self._docs = {}, {}, {}, []
        for sid, member in cat['exposed_internal_text'].items():
            selection_id(sid); r = by_id[sid]; b = raw[member]
            if (r['curation']['collection_code'] != 'NCP' or r['rights']['status'] != 'Internal retained artifact'
                    or Path(r['original']['filename']).suffix not in {'.md', '.json'}
                    or member != 'sources/' + sid + Path(r['original']['filename']).suffix):
                raise ValueError('Full-text source outside internal-only host profile')
            if sha256(b).hexdigest() != r['snapshot']['sha256'] or len(b) != r['snapshot']['size_bytes']:
                raise ValueError('Exposed source disagrees with original manifest')
            self._raw[sid] = b
            if member.endswith('.json'):
                self._json[sid] = parse(b)
            for start, end, span in spans(b):
                locator = {'byte_start': start, 'byte_end_exclusive': end,
                           'span_sha256': sha256(span).hexdigest(), 'source_sha256': r['snapshot']['sha256']}
                span_id = content_identity({'source_id': r['source_id'], 'locator': locator})
                entry = {'kind': 'retained_source_text', 'selection_id': sid,
                         'span_id': span_id, 'locator': locator, 'text': span.decode('utf-8')}
                self._spans[span_id] = entry
                self._docs.append(entry)
        for sid, r in sorted(by_id.items()):
            c = r['curation']
            text = '\n'.join([sid, c['title'], c.get('source_reported_scope', ''),
                             ' '.join(c.get('proposed_extraction_targets', [])),
                             c.get('proposed_ncp_application', ''),
                             ' '.join(c.get('limitations_to_preserve', []))])
            self._docs.append({'kind': 'curatorial_metadata', 'selection_id': sid, 'span_id': None,
                               'locator': {'manifest_byte_ref': bytes_ref(raw['source_manifest.jsonl']), 'selection_id': sid},
                               'text': text})
        if len(self._spans) > MAX_SPANS:
            raise ValueError('Too many retrieval spans')
        self._docs.sort(key=lambda d: (d['selection_id'], d['kind'], d['span_id'] or ''))
        self._terms = [Counter(tokens(d['text'])) for d in self._docs]
        self._postings = defaultdict(set)
        for index, counts in enumerate(self._terms):
            for term in counts:
                self._postings[term].add(index)
        self._lengths = [sum(c.values()) for c in self._terms]
        self._avg = sum(self._lengths) / max(1, len(self._lengths)) or 1
        return self

    def _response(self, payload: dict) -> dict:
        answer = {'status': 'ok', 'corpus_ref': self.ref, **deepcopy(BOUNDARIES), **deepcopy(payload)}
        answer['retrieval_id'] = content_identity(answer)
        if len(encode(answer)) > MAX_RESPONSE:
            raise ValueError('Response byte budget exceeded; narrow the request')
        return answer

    def _record(self, sid):
        selection_id(sid)
        if sid not in self._records:
            raise ValueError('Unknown selection ID')
        return self._records[sid]

    def _reviews(self, sid):
        return [f for f in self._findings if sid in f['selection_ids'] or 'ALL' in f['selection_ids']]

    def overview(self):
        return self._response({'sources': len(self._records), 'collections': len({r['curation']['collection_code'] for r in self._records.values()}),
                              'internal_text_sources': len(self._raw), 'external_fulltext_sources': 0,
                              'retained_text_spans': len(self._spans), 'search_documents': len(self._docs),
                              'graph_nodes': len(self._graph['nodes']), 'graph_edges': len(self._graph['edges']),
                              'review_findings': len(self._findings), 'search_method': 'lexical_BM25_not_embeddings',
                              'manifest_fulltext_flags_unchanged': True})

    def source(self, sid: str, offset: int = 0, limit: int = 20):
        r = self._record(sid)
        if type(offset) is not int or not 0 <= offset <= MAX_SPANS or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Invalid source-span pagination')
        selected = sorted((s for s in self._spans.values() if s['selection_id'] == sid),
                          key=lambda s: s['locator']['byte_start'])
        page = selected[offset:offset + limit]
        return self._response({'source': r, 'findings': self._reviews(sid),
                              'spans': [{'span_id': s['span_id'], 'locator': s['locator']} for s in page],
                              'total_spans': len(selected),
                              'next_offset': offset + len(page) if offset + len(page) < len(selected) else None,
                              'local_text_available': sid in self._raw,
                              'source_bytes_checked_at_open': sid in self._raw,
                              'document_type': 'retained_report_or_reference_not_live_attestation',
                              'historical_extraction_task': next(t for t in self._tasks if t['selection_id'] == sid)})

    def _search(self, query: str, limit: int, collection: str | None, *, indexed=True):
        if type(query) is not str or not 1 <= len(query) <= 512:
            raise ValueError('Require a query of 1..512 characters')
        terms = sorted(set(tokens(query)))
        if not 1 <= len(terms) <= 24 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Query term or result limit exceeded')
        if collection is not None and collection not in {'NCP', 'MET', 'SIM', 'MAN', 'KGE', 'SPC'}:
            raise ValueError('Unknown collection filter')
        candidate_ids = set().union(*(self._postings.get(t, set()) for t in terms)) if indexed else set(range(len(self._docs)))
        ranked = []
        n = len(self._docs)
        for i in candidate_ids:
            doc = self._docs[i]; r = self._records[doc['selection_id']]
            if collection is not None and r['curation']['collection_code'] != collection:
                continue
            score = 0.0
            for term in terms:
                tf = self._terms[i].get(term, 0)
                if tf:
                    df = len(self._postings[term]); idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * self._lengths[i] / self._avg))
            if score > 0:
                ranked.append((score, i))
        ranked.sort(key=lambda v: (-v[0], self._docs[v[1]]['selection_id'], self._docs[v[1]]['span_id'] or ''))
        hits = []
        for score, i in ranked[:limit]:
            doc = self._docs[i]; r = self._records[doc['selection_id']]
            hits.append({**deepcopy(doc), 'score': round(score, 8), 'title': r['curation']['title'],
                         'source_sha256': r['snapshot']['sha256'], 'source_url': r['staged_copy']['url'],
                         'limitations': deepcopy(r['curation'].get('limitations_to_preserve', [])),
                         'review_finding_ids': [f['finding_id'] for f in self._reviews(doc['selection_id'])]})
        return self._response({'query': query, 'collection': collection, 'limit': limit,
                              'search_method': 'lexical_BM25_not_embeddings', 'score_is_probability': False,
                              'total_matching_documents': len(ranked), 'truncated': len(ranked) > len(hits), 'hits': hits})

    def search(self, query: str, limit: int = 5, collection: str | None = None):
        return self._search(query, limit, collection)

    def span(self, span_id: str):
        content_ref(span_id)
        if span_id not in self._spans:
            raise ValueError('Unknown source span')
        item = self._spans[span_id]; r = self._records[item['selection_id']]
        return self._response({'span': item, 'findings': self._reviews(item['selection_id']),
                              'source_url': r['staged_copy']['url'], 'conditions_extracted': False,
                              'semantic_support_checked': False, 'truncation_note': 'Exact bounded window; not a complete semantic claim'})

    def reported_field(self, sid: str, pointer: str):
        self._record(sid)
        if sid not in self._json:
            raise ValueError('No exposed JSON source for this selection')
        if type(pointer) is not str or len(pointer) > 512 or (pointer and not pointer.startswith('/')):
            raise ValueError('Invalid JSON pointer')
        value = self._json[sid]
        for part in pointer[1:].split('/') if pointer else []:
            if re.search(r'~(?![01])', part):
                raise ValueError('Invalid JSON pointer escape')
            part = part.replace('~1', '/').replace('~0', '~')
            if type(value) is dict and part in value:
                value = value[part]
            elif type(value) is list and re.fullmatch(r'0|[1-9][0-9]*', part) and int(part) < len(value):
                value = value[int(part)]
            else:
                raise ValueError('JSON pointer does not resolve')
        return self._response({'selection_id': sid, 'json_pointer': pointer,
                              'source_sha256': self._records[sid]['snapshot']['sha256'],
                              'value': value, 'value_type': type(value).__name__,
                              'value_role': 'verbatim_parsed_retained_report_data_not_current_state'})

    def relations(self, sid: str):
        r = self._record(sid)
        ids = {r['source_id'], 'snapshot:' + r['snapshot']['sha256'],
               'gdrive-copy:' + r['staged_copy']['drive_file_id'], 'collection:' + r['curation']['collection_code']}
        edges = [e for e in self._graph['edges'] if e['from'] in ids and e['to'] in ids]
        return self._response({'selection_id': sid, 'nodes': [n for n in self._graph['nodes'] if n['id'] in ids],
                              'edges': edges, 'graph_role': 'selection_provenance_only_not_scientific_causality',
                              'findings': self._reviews(sid)})
