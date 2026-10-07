"""Real corpus reader and existing MCP transport; all fixtures here are synthetic."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest


def test_feature_exists():
    from ciw.corpus_retrieval import Corpus
    assert callable(Corpus.open)

from ciw.corpus_retrieval import (Corpus, compile_bundle, encode, bytes_ref, expected_graph,
    spans, read_file, relative_name, parse, validate_sources, content_identity)
from ciw.corpus_agent import CorpusHost, descriptions
from ciw.agent_mcp import Server, serve
import io
import os
import subprocess
import sys


def write_jsonl(path, rows):
    path.write_bytes(b'\n'.join(encode(r) for r in rows) + b'\n')


def source_row(index, filename, raw, internal=True):
    sid = f'NCP-CS-{index:03d}'; h = sha256(raw).hexdigest()
    return {'schema': 'notation.corpus-selection.source.v1', 'selection_id': sid,
        'source_id': f'urn:synthetic:{index}', 'original': {'filename': filename, 'url': None},
        'snapshot': {'sha256': h, 'size_bytes': len(raw)},
        'staged_copy': {'drive_file_id': f'copy-{index}', 'url': f'https://drive.google.com/file/d/copy-{index}/view',
            'relative_path': ('internal/' if internal else 'reference/') + filename,
            'readback_sha256': h, 'byte_match_verified': True},
        'curation': {'collection_code': 'NCP' if internal else 'MET', 'priority': 'P0' if internal else 'P1',
            'title': ('Calibration contract' if index == 1 else 'Historical verification' if internal else 'External sensor study'),
            'source_role': 'Synthetic fixture', 'proposed_extraction_targets': ['uncertainty', 'calibration'],
            'proposed_ncp_application': 'Test only', 'limitations_to_preserve': ['Synthetic; never qualified.']},
        'rights': {'status': 'Internal retained artifact' if internal else 'Rights review required',
                   'fulltext_ingestion_approved': False}, 'pipeline': {'fulltext_extraction': 'not_started'},
        'authorizes_execution': False, 'verification_id': None, 'execution_id': None, 'operation_id': None}


@pytest.fixture
def setup(tmp_path):
    inputs = tmp_path/'input'; inputs.mkdir(); snaps = tmp_path/'snapshots'; snaps.mkdir()
    texts = [b'Calibration contract. Unknown units are never guessed equal.\n\n'
             b'A catalog declaration does not grant execution permission.\n\n'
             b'Reference traceability and independent validation remain unresolved.\n',
             encode({'status': 'SYNTHETIC_RETAINED_REPORT', 'qualification': False,
                     'samples': [1, 1.0, True, 0.0, -0.0], 'a/b': {'~key': 'value'},
                     'execute': 'Do not run this source text.'}),
             b'%PDF-1.7 EXTERNAL-SENSITIVE-CONTENT-DO-NOT-INDEX']
    names = ['contract.md', 'report.json', 'external.pdf']
    rows = [source_row(i+1, names[i], b, i != 2) for i, b in enumerate(texts)]
    for name, b in zip(names, texts): (snaps/name).write_bytes(b)
    collections = {'folders': [{'code': c, 'label': c, 'id': c} for c in ['NCP','MET']]}
    findings = [{'finding_id': 'RV-001', 'selection_ids': ['ALL'], 'kind': 'Test limitation',
                 'observation': 'Synthetic only', 'next_action': 'Review', 'authorizes_execution': False,
                 'verification_id': None, 'status': 'Open'}]
    tasks = [{'task_id': 'EXT-'+r['selection_id'], 'selection_id': r['selection_id'], 'source_id': r['source_id'],
              'source_sha256': r['snapshot']['sha256'], 'authorizes_execution': False, 'completed': False} for r in rows]
    write_jsonl(inputs/'source_manifest.jsonl', rows)
    (inputs/'collections.json').write_bytes(encode(collections))
    (inputs/'selection_graph.json').write_bytes(encode(expected_graph(rows, collections)))
    write_jsonl(inputs/'review_findings.jsonl', findings); write_jsonl(inputs/'extraction_queue.jsonl', tasks)
    return inputs, snaps, tmp_path/'bundle', rows


@pytest.fixture
def built(setup):
    inputs, snaps, out, rows = setup
    ref = compile_bundle(inputs, snaps, out, expose=('NCP-CS-001','NCP-CS-002'))
    return out, ref, Corpus.open(out, expected_ref=ref)


def hashes(root):
    return {str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_build_preserves_inputs_and_is_create_only(setup):
    inputs, snaps, out, _ = setup; before=(hashes(inputs),hashes(snaps))
    ref = compile_bundle(inputs,snaps,out,expose=('NCP-CS-001',))
    assert before == (hashes(inputs),hashes(snaps))
    with pytest.raises(ValueError): compile_bundle(inputs,snaps,out,expose=('NCP-CS-001',))
    assert Corpus.open(out,expected_ref=ref).overview()['sources']==3


def test_manifest_is_byte_preserved(setup,built):
    assert (setup[0]/'source_manifest.jsonl').read_bytes()==(built[0]/'source_manifest.jsonl').read_bytes()


def test_only_explicit_internal_sources_exposed(built):
    out, ref, c=built
    assert c.overview()['internal_text_sources']==2
    assert c.overview()['external_fulltext_sources']==0
    assert c.source('NCP-CS-003')['local_text_available'] is False
    assert c.search('SENSITIVE')['hits']==[]
    assert not any(p.suffix=='.pdf' for p in out.rglob('*'))


def test_external_exposure_is_refused(setup):
    inputs,snaps,out,_=setup
    with pytest.raises(ValueError,match='External full-text'): compile_bundle(inputs,snaps,out,expose=('NCP-CS-003',))
    assert not out.exists()


def test_zero_exposure_remains_searchable(setup):
    inputs,snaps,out,_=setup
    ref=compile_bundle(inputs,snaps,out,expose=())
    c=Corpus.open(out,expected_ref=ref)
    assert c.overview()['internal_text_sources']==0
    assert c.search('calibration')['hits']


@pytest.mark.parametrize('bad',[False,True,0,None,'not-a-pin','sha256:'+'0'*63])
def test_invalid_pin_rejected(built,bad):
    with pytest.raises((ValueError,TypeError)): Corpus.open(built[0],expected_ref=bad)


def test_wrong_well_formed_pin_rejected(built):
    with pytest.raises(ValueError,match='root pin'): Corpus.open(built[0],expected_ref='sha256:'+'0'*64)


@pytest.mark.parametrize('member',['source_manifest.jsonl','review_findings.jsonl','selection_graph.json','sources/NCP-CS-001.md'])
def test_file_tampering_rejected(built,member):
    out,ref,_=built; p=out/member;p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='member mismatch'): Corpus.open(out,expected_ref=ref)


def test_source_tamper_even_if_catalog_resealed(built):
    out,_,_=built; member='sources/NCP-CS-001.md'
    (out/member).write_bytes(b'Forged source')
    cat=parse((out/'catalog.json').read_bytes());cat['files'][member]=bytes_ref((out/member).read_bytes())
    b=encode(cat);(out/'catalog.json').write_bytes(b)
    with pytest.raises(ValueError,match='original manifest'): Corpus.open(out,expected_ref=bytes_ref(b))


@pytest.mark.parametrize('mutation',['edge','label','duplicate','authority','snapshot'])
def test_graph_tamper_even_if_catalog_resealed(built,mutation):
    out,_,_=built;g=parse((out/'selection_graph.json').read_bytes())
    if mutation=='edge':g['edges'][0]['relation']='APPROVES_EXECUTION'
    if mutation=='label':g['nodes'][0]['label']='Forged label'
    if mutation=='duplicate':g['edges'].append(deepcopy(g['edges'][0]))
    if mutation=='authority':g['authorizes_execution']=True
    if mutation=='snapshot':g['nodes'][3]['size_bytes']=True
    b=encode(g);(out/'selection_graph.json').write_bytes(b)
    cat=parse((out/'catalog.json').read_bytes());cat['files']['selection_graph.json']=bytes_ref(b)
    b=encode(cat);(out/'catalog.json').write_bytes(b)
    with pytest.raises(ValueError,match='reconcile'):Corpus.open(out,expected_ref=bytes_ref(b))


def test_graph_order_irrelevant(setup):
    inputs,snaps,out,_=setup;g=parse((inputs/'selection_graph.json').read_bytes())
    g['nodes'].reverse();g['edges'].reverse();(inputs/'selection_graph.json').write_bytes(encode(g))
    ref=compile_bundle(inputs,snaps,out,expose=())
    assert Corpus.open(out,expected_ref=ref).relations('NCP-CS-001')['edges']


@pytest.mark.parametrize('v',[True,0,1,None,'false'])
def test_source_authority_requires_exact_false(setup,v):
    inputs,snaps,out,rows=setup;rows[0]['authorizes_execution']=v;write_jsonl(inputs/'source_manifest.jsonl',rows)
    with pytest.raises(ValueError):compile_bundle(inputs,snaps,out,expose=())


@pytest.mark.parametrize('v',[True,False,0,-1,1.0,'10'])
def test_byte_count_not_coerced(setup,v):
    _,_,_,rows=setup;rows[0]['snapshot']['size_bytes']=v
    with pytest.raises(ValueError):validate_sources(rows)


def test_duplicate_source_rejected(setup):
    _,_,_,rows=setup
    with pytest.raises(ValueError,match='Duplicate'):validate_sources(rows+[rows[0]])


@pytest.mark.parametrize('name',['../secret','a/../b','/root','C:/secret','a\\b','a//b','./a','a\x00b'])
def test_member_paths_refused(name):
    with pytest.raises(ValueError):relative_name(name)


def test_symlink_refused(tmp_path):
    target=tmp_path/'real';target.write_text('safe');link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(ValueError,match='link'):read_file(link)


def test_symlink_parent_refused(tmp_path):
    d=tmp_path/'real';d.mkdir();(d/'x').write_text('safe');s=tmp_path/'alias';s.symlink_to(d,target_is_directory=True)
    with pytest.raises(ValueError,match='link'):read_file(s/'x')


def test_special_file_refused(tmp_path):
    if hasattr(os, 'mkfifo'):
        p=tmp_path/'pipe';os.mkfifo(p)
    else:
        # Windows' NUL is an actual character device, not a mocked FIFO.
        p=Path(os.devnull)
    with pytest.raises(ValueError,match='ordinary'):read_file(p)


@pytest.mark.parametrize('bad',[b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'{"a":1e309}',b''])
def test_invalid_json_refused(bad):
    with pytest.raises(ValueError):parse(bad)


def test_utf8_spans_are_lossless_and_bounded():
    raw=('Î±Î²Î³ðŸ™‚\n\n' * 2000).encode()
    parts=list(spans(raw))
    assert b''.join(p[2] for p in parts)==raw
    assert all(0<len(p[2])<=1800 and p[2].decode('utf-8') for p in parts)
    assert all(parts[i][1]==parts[i+1][0] for i in range(len(parts)-1))


def test_span_locator_reconstructs_source(built,setup):
    c=built[2];sid='NCP-CS-001';raw=(setup[1]/'contract.md').read_bytes()
    result=c.search('unknown units',20)
    hit=next(h for h in result['hits'] if h['span_id'])
    s=c.span(hit['span_id'])['span'];l=s['locator']
    b=raw[l['byte_start']:l['byte_end_exclusive']]
    assert s['text'].encode()==b and sha256(b).hexdigest()==l['span_sha256']
    assert c.span(hit['span_id'])['semantic_support_checked'] is False


@pytest.mark.parametrize('query',['calibration','unknown units','verification','traceability','NCP-CS-003','does not grant','absent-quasar-zyz'])
def test_index_matches_reference_scan(built,query):
    c=built[2]
    assert c.search(query,20)==c._search(query,20,None,indexed=False)


def test_search_and_results_are_deterministic_and_detached(built):
    c=built[2];before=c.search('calibration');another=c.search('calibration')
    assert before==another
    before['hits'][0]['text']='corrupted';assert c.search('calibration')==another
    s=c.source('NCP-CS-001');s['source']['rights']['status']='changed'
    assert c.source('NCP-CS-001')['source']['rights']['status']=='Internal retained artifact'


def test_metadata_and_text_are_not_conflated(built):
    c=built[2];hits=c.search('calibration',20)['hits']
    assert {h['kind'] for h in hits}=={'curatorial_metadata','retained_source_text'}
    assert all(h['locator'] and h['review_finding_ids'] for h in hits)


def test_all_sources_have_provenance_and_findings(built):
    c=built[2]
    for i in range(1,4):
        result=c.relations(f'NCP-CS-{i:03d}')
        assert len(result['edges'])==3 and len(result['nodes'])==4
        assert result['findings'] and not result['authorizes_execution']


@pytest.mark.parametrize('limit',[True,False,0,21,-1,1.0,'5'])
def test_search_bounds(built,limit):
    with pytest.raises(ValueError):built[2].search('calibration',limit)


@pytest.mark.parametrize('query',['',' ' * 10,'?' * 3,'x'*513,None,False,' '.join(str(x) for x in range(25))])
def test_query_bounds(built,query):
    with pytest.raises(ValueError):built[2].search(query)


def test_filter_and_missing_results(built):
    c=built[2]
    assert all(h['selection_id']=='NCP-CS-003' for h in c.search('sensor',20,'MET')['hits'])
    assert c.search('NOTFOUNDZZ')['hits']==[]
    with pytest.raises(ValueError):c.search('x',collection='Not a collection')
    with pytest.raises(ValueError):c.source('../../etc/passwd')
    with pytest.raises(ValueError):c.source('NCP-CS-999')


@pytest.mark.parametrize('index,expected_type',[(0,'int'),(1,'float'),(2,'bool'),(3,'float'),(4,'float')])
def test_json_scalar_types_retained(built,index,expected_type):
    r=built[2].reported_field('NCP-CS-002',f'/samples/{index}')
    assert r['value_type']==expected_type and r['authorizes_execution'] is False


def test_json_pointer_escapes(built):
    assert built[2].reported_field('NCP-CS-002','/a~1b/~0key')['value']=='value'


@pytest.mark.parametrize('pointer',['bad','/~2','/samples/-1','/samples/01','/samples/999','/unknown'])
def test_invalid_json_pointers(built,pointer):
    with pytest.raises(ValueError):built[2].reported_field('NCP-CS-002',pointer)


def test_retained_success_is_not_current_qualification(built):
    r=built[2].reported_field('NCP-CS-002','/status')
    assert r['value']=='SYNTHETIC_RETAINED_REPORT'
    assert r['verification_id'] is None and r['execution_id'] is None and r['live_binding_checked'] is False
    with pytest.raises(ValueError):built[2].reported_field('NCP-CS-003','')


def test_frozen_open_does_not_silently_follow_mutations(built):
    out,ref,c=built;before=c.search('calibration');p=out/'sources/NCP-CS-001.md'
    p.write_text('Mutated later')
    assert c.search('calibration')==before
    with pytest.raises(ValueError):Corpus.open(out,expected_ref=ref)


def rpc(server,index,method,params=None):
    v={'jsonrpc':'2.0','id':index,'method':method}
    if params is not None:v['params']=params
    return server.handle(encode(v))


def initialized(c):
    s=Server(CorpusHost(c))
    assert 'result' in rpc(s,1,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
    assert s.handle(encode({'jsonrpc':'2.0','method':'notifications/initialized'})) is None
    return s


def test_existing_mcp_transport_lists_six_readonly_tools(built):
    s=initialized(built[2]);tools=rpc(s,2,'tools/list')['result']['tools']
    assert len(tools)==6
    assert all(t['annotations']['readOnlyHint'] and t['inputSchema']['additionalProperties'] is False for t in tools)


@pytest.mark.parametrize('tool,args',[
    ('ncp_corpus_overview',{}),('ncp_corpus_search',{'query':'calibration'}),
    ('ncp_corpus_source',{'selection_id':'NCP-CS-001'}),
    ('ncp_corpus_reported_field',{'selection_id':'NCP-CS-002','pointer':'/qualification'}),
    ('ncp_corpus_relations',{'selection_id':'NCP-CS-001'})])
def test_actual_mcp_tool_calls(built,tool,args):
    s=initialized(built[2]);r=rpc(s,2,'tools/call',{'name':tool,'arguments':args})['result']
    assert not r['isError'];v=r['structuredContent']
    assert v['authorizes_execution'] is False and v['verification_id'] is None


@pytest.mark.parametrize('tool,args',[
    ('execute',{}),('net_execute',{}),('ncp_corpus_source',{'selection_id':'NCP-CS-001','path':'/etc/passwd'}),
    ('ncp_corpus_search',{'query':'x','allow_execute':True}),('ncp_corpus_overview',{'foo':1}),
    ('ncp_corpus_search',{'query':'x','limit':True}),('ncp_corpus_search',{'query':'x','collection':None})])
def test_mcp_refuses_authority_expansion_and_unknown_fields(built,tool,args):
    s=initialized(built[2]);r=rpc(s,2,'tools/call',{'name':tool,'arguments':args})['result']
    assert r['isError'] and r['structuredContent']['status']=='refused'


def test_notifications_never_invoke_tool(built,monkeypatch):
    host=CorpusHost(built[2]);s=Server(host)
    monkeypatch.setattr(host,'call',lambda *a:pytest.fail('A notification invoked a tool'))
    assert s.handle(encode({'jsonrpc':'2.0','method':'tools/call','params':{'name':'ncp_corpus_overview'}})) is None


def test_query_calls_no_network_subprocess_or_scientific_provider(built,monkeypatch):
    import socket,ciw.control_plane
    fail=lambda *a,**k:pytest.fail('Forbidden side effect')
    monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(subprocess,'Popen',fail)
    monkeypatch.setattr(ciw.control_plane,'builtin_registry',fail)
    out,_,c=built;before=hashes(out)
    h=CorpusHost(c);h.call('ncp_corpus_overview',{});h.call('ncp_corpus_search',{'query':'execution permission'})
    assert hashes(out)==before


def test_stdio_real_process(built):
    out,ref,_=built
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
              {'jsonrpc':'2.0','method':'notifications/initialized'},
              {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'ncp_corpus_search','arguments':{'query':'unknown units'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.corpus_agent','serve','--bundle',str(out),'--expected-ref',ref],
                        input=b'\n'.join(encode(m) for m in messages)+b'\n',capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    responses=[json.loads(x) for x in proc.stdout.splitlines()]
    assert len(responses)==2 and responses[1]['result']['structuredContent']['hits']


def test_cli_bad_pin_no_output_files(built):
    out,_,_=built;before=hashes(out)
    p=subprocess.run([sys.executable,'-m','ciw.corpus_agent','inspect','--bundle',str(out),'--expected-ref','sha256:'+'0'*64],capture_output=True,timeout=30)
    assert p.returncode==2 and not p.stdout and hashes(out)==before


def test_html_text_escaped_and_no_scripts(built):
    from ciw.corpus_view import render_html
    c=built[2];c._records['NCP-CS-001']['curation']['title']='</script><script>alert(1)</script>'
    c._records['NCP-CS-001']['staged_copy']['url']='javascript:alert(1)'
    html=render_html(c)
    assert '<script' not in html and 'javascript:' not in html
    assert '&lt;script&gt;' in html and 'Content-Security-Policy' in html
    assert 'lang="en"' in html and '<summary>' in html and 'Skip to sources' in html


def test_every_exposed_source_byte_recoverable_via_spans(built,setup):
    c=built[2]
    for sid, name in [('NCP-CS-001','contract.md'),('NCP-CS-002','report.json')]:
        offset=0; parts=[]
        while True:
            page=c.source(sid,offset,1)
            parts.extend(c.span(row['span_id'])['span']['text'].encode() for row in page['spans'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        assert b''.join(parts)==(setup[1]/name).read_bytes()


@pytest.mark.parametrize('offset,limit',[(-1,1),(True,1),(0,False),(0,51),(10001,1)])
def test_invalid_span_pagination(built,offset,limit):
    with pytest.raises(ValueError):built[2].source('NCP-CS-001',offset,limit)


@pytest.mark.parametrize('field', ['original', 'snapshot', 'staged_copy', 'curation', 'rights', 'pipeline'])
@pytest.mark.parametrize('value', [None, [], 'malformed', 1])
def test_malformed_source_objects_refuse_before_output(setup, field, value):
    inputs, snapshots, out, rows = setup
    rows[0][field] = value
    write_jsonl(inputs/'source_manifest.jsonl', rows)
    with pytest.raises(ValueError, match='object'):
        compile_bundle(inputs, snapshots, out, expose=())
    assert not out.exists()


@pytest.mark.parametrize('member,path,value', [
    ('source_manifest.jsonl', [0, 'curation', 'title'], []),
    ('source_manifest.jsonl', [0, 'curation', 'limitations_to_preserve'], 'not a list'),
    ('source_manifest.jsonl', [0, 'curation', 'proposed_extraction_targets'], [None]),
    ('source_manifest.jsonl', [0, 'staged_copy', 'drive_file_id'], []),
    ('collections.json', ['folders'], {}),
    ('collections.json', ['folders', 0], None),
    ('collections.json', ['folders', 0, 'code'], []),
    ('selection_graph.json', ['nodes'], {}),
    ('selection_graph.json', ['nodes', 0], None),
    ('selection_graph.json', ['edges', 0, 'from'], []),
    ('review_findings.jsonl', [0, 'selection_ids'], {}),
    ('review_findings.jsonl', [0, 'selection_ids'], [['NCP-CS-001']]),
    ('review_findings.jsonl', [0, 'finding_id'], []),
    ('extraction_queue.jsonl', [0, 'selection_id'], []),
    ('extraction_queue.jsonl', [0, 'task_id'], []),
])
def test_malformed_sidecar_shapes_refuse_before_output(setup, member, path, value):
    inputs, snapshots, out, _ = setup
    location = inputs/member
    data = ([parse(line) for line in location.read_bytes().splitlines()]
            if member.endswith('.jsonl') else parse(location.read_bytes()))
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    if member.endswith('.jsonl'):
        write_jsonl(location, data)
    else:
        location.write_bytes(encode(data))
    with pytest.raises(ValueError):
        compile_bundle(inputs, snapshots, out, expose=())
    assert not out.exists()


def test_malformed_sidecar_cli_returns_structured_refusal(setup, capfd):
    from ciw.corpus_agent import main
    inputs, snapshots, out, rows = setup
    rows[0]['snapshot'] = []
    write_jsonl(inputs/'source_manifest.jsonl', rows)
    before = hashes(inputs), hashes(snapshots)
    assert main(['build', '--inputs', str(inputs), '--snapshots', str(snapshots),
                 '--output', str(out)]) == 2
    captured = capfd.readouterr()
    assert captured.out == ''
    refusal = json.loads(captured.err)
    assert refusal['status'] == 'refused'
    assert refusal['authorizes_execution'] is False
    assert not out.exists()
    assert (hashes(inputs), hashes(snapshots)) == before
