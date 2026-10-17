"""Freeze partitioned intake metadata with per-source coverage, never PDF text.

Uses the native ProtoCorpus compiler. Historical receipts are assertions, not a
fresh cloud read or byte recheck. No grant, execution, or admission is created.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import defaultdict
import json
from pathlib import Path
import re
import shutil
import tempfile
from .proto_corpus import (ProtoCorpus, ProtoCorpusError, _encode, _parse, _read,
                           _ref, _id, _no_links, _publish_directory, compile_bundle)
from .corpus_refresh import MAX_SOURCES, MAX_SOURCE_TOTAL

MAX_INTAKE = 1024

def require(condition, message):
    if not condition:
        raise ProtoCorpusError(message)


def dump(path, value):
    path.write_bytes(_encode(value))


def is_hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def topic(category):
    require(type(category) is str and 0 < len(category) <= 256, 'invalid category')
    # Only reconcile explicit root aliases; never infer a new scientific topic.
    for root in ('Knowledge Kernel/', 'Notations Kernel/'):
        if category.startswith(root):
            category = category[len(root):]
    if category.startswith('02_Source_Library/'):
        category = category[len('02_Source_Library/'):]
    return category


def verified(record, check, manifest, receipt):
    if not isinstance(check, dict):
        return False
    common = (check.get('sha256') == record.get('sha256') and
              check.get('destination_parent_id') == record.get('destination_parent_id'))
    if 'batch_id' in receipt:
        return (common and receipt['batch_id'] == manifest.get('batch_id') and
                receipt.get('all_sources_removed_from_queue') is True and
                all(check.get(k) is True for k in ('bytes_match','metadata_match','parents_match','permissions_match')))
    return (common and receipt.get('status') == 'PASS' and receipt.get('queue_count_after') == 0 and
            check.get('destination_path') == record.get('destination_path') and
            check.get('size_bytes') == record.get('size_bytes') and
            all(check.get(k) is True for k in ('bytes_unchanged','name_unchanged','file_id_unchanged',
                                               'modified_time_unchanged','permissions_unchanged')))


def load_rows(config, root):
    batches = config.get('batches')
    require(type(batches) is list and 1 <= len(batches) <= 16, 'invalid batch count')
    rows, originals, seen = [], {}, set()
    for batch in batches:
        docs, refs = [], {}
        for kind in ('intake_manifest','verification'):
            raw = _read(Path(batch[kind]))
            ref = _ref(raw)
            (root/'provenance'/ (ref[7:]+'.json')).write_bytes(raw)
            docs.append(_parse(raw)); refs[kind] = ref
        manifest, receipt = docs
        require(manifest.get('schema') == 'notation.knowledge-kernel.intake.v1', 'unsupported intake')
        records = manifest.get('records')
        require(type(records) is list and len(rows)+len(records) <= MAX_INTAKE, 'intake count outside bounds')
        checks = defaultdict(list)
        for check in receipt.get('checks', []):
            checks[check.get('source_id') or 'gdrive:'+str(check.get('drive_file_id'))].append(check)
        for original in records:
            record = deepcopy(original)
            sid = 'gdrive:'+_id(record.get('drive_file_id'))
            require(record.get('source_id') == sid and sid not in seen, 'duplicate or conflicting source identity')
            require(record.get('authorizes_execution') is False, 'intake cannot authorize execution')
            seen.add(sid); originals[sid] = record
            digest = record.get('sha256')
            hash_ok = is_hash(digest)
            size = record.get('size_bytes')
            hold = None
            if not hash_ok: hold = 'original hash missing or invalid'
            elif type(size) is not int or not 0 <= size <= MAX_SOURCE_TOTAL: hold = 'source size outside refresh bounds'
            elif not str(record.get('original_filename','')).lower().endswith('.pdf'): hold = 'this coverage profile accepts PDF metadata only'
            elif len(checks[sid]) != 1 or not verified(record,checks[sid][0],manifest,receipt): hold = 'retained intake verification unavailable or failed'
            rows.append(dict(source_id=sid, sha256=digest, size_bytes=size,
                destination=record.get('destination_path'), original_category=record.get('category'),
                topic=topic(record.get('category')), title=record.get('bibliographic_title'),
                bibliographic_identity=dict(title_basis=record.get('title_basis','unknown'),
                    doi_candidates_unverified=record.get('doi_candidates_unverified',[]),
                    note=record.get('bibliographic_note'), external_identity_verified=False),
                review_extent=record.get('title_basis','retained intake metadata only'),
                source_role=record.get('source_role','external_reference'),
                scientific_review=record.get('semantic_adjudication','not_performed'),
                rights_review=record.get('rights_review','unresolved'),
                related_source_ids=record.get('related_source_ids',[]),
                related_publication_doi=record.get('related_publication_doi'),
                declared_duplicate_of=record.get('duplicate_of'), exact_duplicate_source_ids=[],
                catalog_ref=None, record_id=None, evidence_ref=None, hold_reason=hold,
                fulltext_admission='held', fulltext_hold_reason='PDF extraction and disclosure review not admitted',
                authorizes_execution=False, provenance=refs))
    require(rows, 'empty intake')
    return sorted(rows,key=lambda x:x['source_id']), originals

def partitions(rows):
    by_hash = defaultdict(list)
    for row in rows:
        if is_hash(row['sha256']):
            by_hash[row['sha256']].append(row)
    by_id = {r['source_id']:r for r in rows}
    for row in rows:
        declared = row['declared_duplicate_of']
        if declared:
            require(declared in by_id, 'duplicate target unavailable')
            if is_hash(row['sha256']) and is_hash(by_id[declared]['sha256']):
                require(by_id[declared]['sha256'] == row['sha256'], 'duplicate hash conflict')
        related = row['related_source_ids']
        require(type(related) is list and len(related) <= 128 and
                all(type(x) is str and 0<len(x)<=512 for x in related), 'invalid related identities')
        row['exact_duplicate_source_ids'] = (sorted(x['source_id'] for x in by_hash.get(row['sha256'],[]) if x is not row)
                                               if is_hash(row['sha256']) else [])
    groups = defaultdict(list)
    for group in by_hash.values():
        admitted = [r for r in group if not r['hold_reason']]
        if not admitted: continue
        primary = next((r for r in admitted if not r['declared_duplicate_of']),admitted[0])
        if len(admitted)>MAX_SOURCES or sum(r['size_bytes'] for r in admitted)>MAX_SOURCE_TOTAL:
            for r in admitted: r['hold_reason']='duplicate group exceeds partition bounds'
        else: groups[primary['topic']].append(admitted)
    output = []
    for name, duplicate_groups in sorted(groups.items()):
        current, size = [], 0
        for group in duplicate_groups:
            amount = sum(r['size_bytes'] for r in group)
            if len(current)+len(group)>MAX_SOURCES or size+amount>MAX_SOURCE_TOTAL:
                output.append((name,current)); current,size=[],0
            current.extend(group); size+=amount
        if current: output.append((name,current))
    return output


def compile_topic(root, index, name, rows, originals):
    directory=root/('topic-%03d'%index)
    directory.mkdir(); registry=directory/'registry'; registry.mkdir()
    intake=directory/'intake'; intake.mkdir()
    (registry/'source_membership.jsonl').write_bytes(b'\n')
    dump(registry/'lot_registry.json',dict(schema_version='ncp-lot-registry.v1',source_count=0,
         packaged_source_count=0,oversize_source_count=0,lot_count=0,lots=[]))
    dump(registry/'oversize_register.json',dict(records=[]))
    batch='coverage-topic-%03d'%index
    records,receipts=[],[]
    ids={r['source_id'] for r in rows}
    for row in rows:
        record=deepcopy(originals[row['source_id']])
        record['category']=row['topic']
        if record.get('duplicate_of') not in ids:
            record['duplicate_of']=None
        records.append(record)
        receipts.append(dict(file_id=record['drive_file_id'],actual_parent_ids=[record['destination_parent_id']],
            parent_ok=True,name_ok=True,size_ok=True,modified_time_unchanged=True,move_success=True,
            destination_path=record['destination_path'],expected_sha256=record['sha256'],
            observed_sha256=record['sha256'],observed_size_bytes=record['size_bytes'],hash_status='matched',
            assertion_scope='historical_intake_receipt_only_no_fresh_byte_or_cloud_check',
            original_receipt=row['provenance']['verification']))
    for filename,schema,values in [('intake_manifest.json','notation.knowledge-kernel.intake.v1',records),
          ('repository_manifest.json','notation.knowledge-kernel.repository-intake.v1',[])]:
        dump(intake/filename,dict(schema=schema,batch_id=batch,source_count=len(values),
            unique_sha256_count=len({r['sha256'] for r in values}),records=values,published_lots_modified=False))
    dump(intake/'verification.json',dict(schema='notation.knowledge-kernel.intake-verification.v1',batch_id=batch,
        source_files=len(rows),pdf_files=len(rows),repository_archives=0,hashes_matched=len(rows),
        all_checks_passed=True,published_lots_modified=False,records=receipts))
    pin=compile_bundle(registry=registry,intake=intake,selected=None,output=directory/'catalog')
    catalog=ProtoCorpus.open(directory/'catalog',pin)
    for row in rows:
        row['catalog_ref']=pin
        row['record_id']=catalog.resolve(row['source_id'])['record']['record_id']
    return dict(path=directory.name+'/catalog',topic=name,catalog_ref=pin,source_count=len(rows),
                source_bytes=sum(r['size_bytes'] for r in rows))


def build_coverage(*, selection:Path, output:Path):
    raw=_read(selection); config=_parse(raw)
    require(config.get('schema')=='ciw.corpus-coverage-selection.v1' and
            config.get('authorizes_execution') is False,'invalid coverage selection or authority')
    output=_no_links(output)
    require(not output.exists() and output.parent.is_dir(),'output must be new with existing parent')
    temporary=Path(tempfile.mkdtemp(prefix='.corpus-coverage-',dir=output.parent))
    try:
        (temporary/'provenance').mkdir()
        (temporary/'provenance'/'selection.json').write_bytes(raw)
        rows,originals=load_rows(config,temporary)
        groups=partitions(rows)
        catalogs=[compile_topic(temporary,i,name,group,originals) for i,(name,group) in enumerate(groups)]
        content=dict(schema='ciw.corpus-coverage.v1',selection_ref=_ref(raw),sources=rows,partitions=catalogs,
            source_count=len(rows),distinct_hashes=len({r['sha256'] for r in rows if is_hash(r['sha256'])}),
            catalogued_count=sum(r['catalog_ref'] is not None for r in rows),
            held_count=sum(r['catalog_ref'] is None for r in rows),metadata_only=True,
            authorizes_execution=False,authorizes_acceptance=False,activated=False,raw_sources_rehashed=False,
            fulltext_admitted_count=0)
        encoded=_encode(content); (temporary/'coverage.json').write_bytes(encoded)
        receipt={k:v for k,v in content.items() if k!='sources'} | dict(coverage_ref=_ref(encoded))
        dump(temporary/'receipt.json',receipt)
        _publish_directory(temporary,output)
        return receipt
    finally:
        if temporary.exists(): shutil.rmtree(temporary)

class Coverage:
    """Read only pinned catalogs; source text is never loaded or interpreted."""
    @classmethod
    def open(cls, directory:Path, expected_ref:str):
        directory=_no_links(directory)
        raw=_read(directory/'coverage.json')
        require(_ref(raw)==expected_ref,'coverage pin mismatch')
        data=_parse(raw)
        require(data.get('schema')=='ciw.corpus-coverage.v1','unsupported coverage')
        require(_ref(_read(directory/'provenance'/'selection.json'))==data['selection_ref'],
                'selection provenance mismatch')
        provenance={ref for row in data['sources'] for ref in row['provenance'].values()}
        for ref in provenance:
            require(type(ref) is str and re.fullmatch('sha256:[0-9a-f]{64}',ref), 'invalid provenance pin')
            require(_ref(_read(directory/'provenance'/(ref[7:]+'.json')))==ref,'intake provenance mismatch')
        self=cls(); self.ref=expected_ref; self.rows={r['source_id']:r for r in data['sources']}
        self.catalogs=[]
        for index,part in enumerate(data['partitions']):
            require(part['path']=='topic-%03d/catalog'%index,'invalid catalog path')
            self.catalogs.append(ProtoCorpus.open(directory/part['path'],part['catalog_ref']))
        for row in self.rows.values():
            if row['catalog_ref']:
                catalog=next(c for c in self.catalogs if c.ref==row['catalog_ref'])
                require(catalog.resolve(row['source_id'])['record']['record_id']==row['record_id'],'coverage record mismatch')
        return self

    def resolve(self, source_id):
        require(type(source_id) is str and source_id in self.rows,'source identity unavailable')
        return deepcopy(self.rows[source_id])

    def search(self, query, limit=20):
        require(type(limit) is int and 1<=limit<=100,'invalid search limit')
        items=[]
        for catalog in self.catalogs:
            result=catalog.search(query,limit=64)
            for item in result['items']:
                items.append(item | dict(catalog_ref=catalog.ref,rights_review=self.rows[item['source_id']]['rights_review'],
                                        fulltext_admission='held'))
        items.sort(key=lambda x:(-x['score'],x['source_id']))
        return dict(coverage_ref=self.ref,total=len(items),items=items[:limit],
                    search_basis='native_catalog_metadata_tokens',passage_relevance_evaluated=False,
                    authorizes_execution=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(build_coverage(selection=args.selection,output=args.output),indent=2))
        return 0
    except (ValueError,TypeError,KeyError,OSError) as exc:
        print(json.dumps(dict(status='refused',reason=str(exc)[:400],authorizes_execution=False)))
        return 2

if __name__=='__main__':
    raise SystemExit(main())
