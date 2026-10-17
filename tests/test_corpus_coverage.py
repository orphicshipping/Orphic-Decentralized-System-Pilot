"""Synthetic, independent corpus coverage expectations."""
from hashlib import sha256
import json
from pathlib import Path
import pytest
from ciw.corpus_coverage import build_coverage, Coverage


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf8')


def request(tmp_path, count=3):
    records, checks = [], []
    for i in range(count):
        file_id = 'Source' + str(i)
        digest = sha256(str(0 if i == 1 else i).encode()).hexdigest()
        row = dict(source_id='gdrive:'+file_id, drive_file_id=file_id, intake_id='intake-'+file_id,
                   sha256=digest, size_bytes=100, original_filename=file_id+'.pdf',
                   bibliographic_title='Independent thermal reference '+str(i),
                   title_basis='first-page transcription', category='03_Sensors_MEMS_and_Metrology',
                   source_url='https://drive.google.com/file/d/'+file_id+'/view',
                   original_path='Queue/'+file_id+'.pdf', destination_path='Knowledge/'+file_id+'.pdf',
                   destination_parent_id='Folder', classification_basis='retained title only',
                   authorizes_execution=False, rights_review='unresolved', related_source_ids=[],
                   semantic_adjudication='not_performed')
        if i == 1:
            row['duplicate_of'] = 'gdrive:Source0'
        if i == 2:
            row['related_source_ids'] = ['gdrive:Source0']
        records.append(row)
        checks.append(dict(drive_file_id=file_id, sha256=digest, destination_parent_id='Folder',
                           bytes_match=True, metadata_match=True, parents_match=True, permissions_match=True))
    write(tmp_path/'intake.json', dict(schema='notation.knowledge-kernel.intake.v1', batch_id='fixture',
          records=records, source_count=count, unique_sha256_count=len({x['sha256'] for x in records})))
    write(tmp_path/'verification.json', dict(batch_id='fixture', all_sources_removed_from_queue=True, checks=checks))
    config=dict(schema='ciw.corpus-coverage-selection.v1', authorizes_execution=False,
                batches=[dict(intake_manifest=str(tmp_path/'intake.json'), verification=str(tmp_path/'verification.json'))])
    write(tmp_path/'selection.json', config)
    return tmp_path/'selection.json'


def test_metadata_coverage_retains_each_id_duplicates_versions_and_rights(tmp_path):
    selection=request(tmp_path)
    receipt=build_coverage(selection=selection, output=tmp_path/'out')
    coverage=Coverage.open(tmp_path/'out', receipt['coverage_ref'])
    assert receipt['source_count'] == 3 and receipt['distinct_hashes'] == 2
    assert receipt['catalogued_count'] == 3 and receipt['held_count'] == 0
    assert coverage.search('Independent thermal reference 2')['total'] == 1
    assert coverage.search('absentquasar')['total'] == 0
    first=coverage.resolve('gdrive:Source0')
    assert first['exact_duplicate_source_ids'] == ['gdrive:Source1']
    assert coverage.resolve('gdrive:Source2')['related_source_ids'] == ['gdrive:Source0']
    assert first['rights_review'] == 'unresolved'
    assert first['evidence_ref'] is None and first['fulltext_admission'] == 'held'
    assert first['authorizes_execution'] is False
    assert coverage.resolve('gdrive:Source2')['sha256'] != first['sha256']
    assert build_coverage(selection=selection, output=tmp_path/'again') == receipt
    with pytest.raises(ValueError): build_coverage(selection=selection, output=tmp_path/'out')


def test_partition_preserves_limits_and_duplicates(tmp_path):
    result=build_coverage(selection=request(tmp_path, 83), output=tmp_path/'out')
    assert result['source_count']==83 and result['distinct_hashes']==82
    assert [x['source_count'] for x in result['partitions']] == [64,19]
    assert all(x['source_bytes'] <= 256*1024*1024 for x in result['partitions'])


def test_failed_verification_becomes_explicit_hold_without_disappearing(tmp_path):
    path=request(tmp_path)
    verification=json.loads((tmp_path/'verification.json').read_text())
    verification['checks'][2]['bytes_match']=False
    write(tmp_path/'verification.json', verification)
    receipt=build_coverage(selection=path, output=tmp_path/'out')
    row=Coverage.open(tmp_path/'out',receipt['coverage_ref']).resolve('gdrive:Source2')
    assert receipt['held_count']==1 and row['catalog_ref'] is None
    assert 'verification' in row['hold_reason']


@pytest.mark.parametrize('change',['authority','duplicate_id','hash_conflict'])
def test_refuses_contradictory_identity_or_authority(tmp_path,change):
    path=request(tmp_path)
    data=json.loads((tmp_path/'intake.json').read_text())
    if change=='authority': data['records'][0]['authorizes_execution']=True
    if change=='duplicate_id': data['records'][2]['source_id']=data['records'][0]['source_id']
    if change=='hash_conflict': data['records'][1]['sha256']='f'*64
    write(tmp_path/'intake.json',data)
    with pytest.raises(ValueError): build_coverage(selection=path,output=tmp_path/'out')
    assert not (tmp_path/'out').exists()

def test_reopen_checks_retained_intake_provenance_bytes(tmp_path):
    receipt=build_coverage(selection=request(tmp_path),output=tmp_path/'out')
    coverage=Coverage.open(tmp_path/'out',receipt['coverage_ref'])
    digest=coverage.resolve('gdrive:Source0')['provenance']['intake_manifest'][7:]
    (tmp_path/'out/provenance'/(digest+'.json')).write_text('{}')
    with pytest.raises(ValueError,match='provenance'):
        Coverage.open(tmp_path/'out',receipt['coverage_ref'])


def test_missing_hash_and_oversize_sources_are_held_and_aliases_preserved(tmp_path):
    path=request(tmp_path)
    data=json.loads((tmp_path/'intake.json').read_text())
    data['records'][1]['sha256']=None
    data['records'][1]['duplicate_of']=None
    data['records'][2]['size_bytes']=257*1024*1024
    data['records'][0]['category']='Knowledge Kernel/02_Source_Library/03_Sensors_MEMS_and_Metrology'
    write(tmp_path/'intake.json',data)
    receipt=build_coverage(selection=path,output=tmp_path/'out')
    coverage=Coverage.open(tmp_path/'out',receipt['coverage_ref'])
    assert receipt['held_count']==2
    assert coverage.resolve('gdrive:Source0')['topic']=='03_Sensors_MEMS_and_Metrology'
    assert coverage.resolve('gdrive:Source0')['original_category'].startswith('Knowledge Kernel/')
    assert coverage.resolve('gdrive:Source1')['hold_reason']=='original hash missing or invalid'
    assert coverage.resolve('gdrive:Source2')['hold_reason']=='source size outside refresh bounds'

def test_source_role_and_review_limits_do_not_promote_financial_references(tmp_path):
    path=request(tmp_path)
    data=json.loads((tmp_path/'intake.json').read_text())
    data['records'][0]['source_role']='external_research_reference'
    data['records'][0]['category']='02_Source_Library/06_AI_Computation_and_Control/Financial_Computation_and_Algorithmic_Trading'
    write(tmp_path/'intake.json',data)
    result=build_coverage(selection=path,output=tmp_path/'out')
    row=Coverage.open(tmp_path/'out',result['coverage_ref']).resolve('gdrive:Source0')
    assert row['source_role']=='external_research_reference'
    assert row['scientific_review']=='not_performed'
    assert row['fulltext_admission']=='held' and row['authorizes_execution'] is False

def test_invalid_hashes_are_not_duplicate_or_distinct_content_claims(tmp_path):
    path=request(tmp_path)
    data=json.loads((tmp_path/'intake.json').read_text())
    for row in data['records'][:2]:
        row['sha256']='UNKNOWN'
        row['duplicate_of']=None
    write(tmp_path/'intake.json',data)
    result=build_coverage(selection=path,output=tmp_path/'out')
    coverage=Coverage.open(tmp_path/'out',result['coverage_ref'])
    assert result['source_count']==3 and result['held_count']==2
    assert result['distinct_hashes']==1
    for sid in ('gdrive:Source0','gdrive:Source1'):
        row=coverage.resolve(sid)
        assert row['sha256']=='UNKNOWN' and row['exact_duplicate_source_ids']==[]
