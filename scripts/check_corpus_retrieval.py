#!/usr/bin/env python3
"""Non-authorizing real-bundle checks; stdout is a validation report, not a certificate."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import statistics
import time
from ciw.corpus_retrieval import Corpus, encode


def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True,type=Path);p.add_argument('--expected-ref',required=True)
    a=p.parse_args();c=Corpus.open(a.bundle,expected_ref=a.expected_ref)
    queries=['provider runtime identity','unknown units','calibration uncertainty','PolyJarvis',
             'ToPolyAgent','injection molding','identifiability','Agentic ZTA','diagnostic debugging',
             'NCP-CS-001','verification occurrence','physical qualification','not implemented']
    checks=[]
    for q in queries:
        actual=c.search(q,20);reference=c._search(q,20,None,indexed=False)
        assert actual==reference
        checks.append({'query':q,'index_matches_reference_scan':True,'returned':len(actual['hits']),
                       'top_selection':actual['hits'][0]['selection_id'] if actual['hits'] else None})
    recon=[]
    for sid in sorted(c._raw):
        start=0;parts=[]
        while True:
            page=c.source(sid,start,1)
            for row in page['spans']:
                span=c.span(row['span_id'])['span'];parts.append(span['text'].encode('utf-8'))
                assert sha256(parts[-1]).hexdigest()==span['locator']['span_sha256']
            if page['next_offset'] is None:break
            start=page['next_offset']
        joined=b''.join(parts)
        assert joined==c._raw[sid]
        recon.append({'selection_id':sid,'bytes':len(joined),'windows':len(parts),'reconstructed_sha256':sha256(joined).hexdigest(),'exact_match':True})
    for sid in c._records:
        g=c.relations(sid)
        assert len(g['edges'])==3 and len(g['nodes'])==4
    timing={}
    for indexed in [True,False]:
        samples=[]
        for _ in range(5):
            t=time.perf_counter()
            for q in queries:
                c._search(q,5,None,indexed=indexed)
            samples.append((time.perf_counter()-t)*1000/len(queries))
        timing['indexed' if indexed else 'reference_scan']={'median_ms_per_query':statistics.median(samples),
             'samples_ms_per_query':samples,'scope':'this small frozen bundle; not a scalability benchmark'}
    print(json.dumps({'status':'PASS','scope':'local deterministic retrieval, pointers, byte reconstruction, graph relationships',
        'overview':c.overview(),'retrieval_cases':checks,'byte_reconstruction':recon,'timing':timing,
        'scientific_validation':False,'authorization_granted':False,'external_fulltext_ingested':False},indent=2))

if __name__=='__main__':main()
