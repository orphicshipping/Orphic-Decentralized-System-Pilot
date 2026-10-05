"""Empirical-source, uncertainty, identity and negative-comparison regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from ciw import materials_bench as mb
from ciw import rheology_empirical as er

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/polymer/nist2491-investigation.json'


@pytest.fixture
def request_data():
    return json.loads(EXAMPLE.read_text(encoding='utf-8'))


def reseal(report):
    excluded = {'record_sha256', 'content_sha256', 'execution_id', 'result_id', 'executed_at'}
    report['content_sha256'] = mb.digest({k:v for k,v in report.items() if k not in excluded})
    er._seal_record(report)


def test_actual_table_primary_measurement_and_no_synthetic_promotion(request_data):
    r = er.run(request_data)
    c = r['comparisons'][10]
    assert c['omega_rad_s'] == 10
    assert c['residual'] == 2636
    assert c['residual_standard_uncertainty'] == 42
    assert c['residual_expanded_interval'] == [2552, 2720]
    assert c['outcome'] == r['primary_outcome'] == 'INCOMPATIBLE_WITH_PUBLISHED_INTERVAL'
    record = r['evidence']['records'][20]
    assert record['uncertainty']['expanded'] == 84
    assert record['uncertainty']['standard'] == 42
    assert record['uncertainty']['variance'] == 1764
    assert record['source_locator'] == {'page_1_indexed':10,'table':'5c',
        'data_row_1_indexed':11,'quantity':'shear-storage-modulus'}
    assert r['evidence']['measurement_time'] is None
    assert not r['evidence']['publisher_document_bytes_retained']
    assert r['evidence']['publisher_document_sha256'] is None
    assert not r['authority']['measurement_by_NET']
    assert not r['authority']['current_instrument_calibration']


def test_complete_snapshot_crosschecks_companion_csv(request_data):
    raw = request_data['capture']['csv_utf8'].encode('utf-8')
    # Companion CSV may be checked out with CRLF; embedded snapshot is canonical LF.
    companion = (ROOT/'examples/polymer/nist2491-table5c.csv').read_text(encoding='utf-8')
    assert companion == raw.decode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == er.CSV_SHA256
    r = er.run(request_data)
    assert len(r['evidence']['records']) == 32
    assert len({x['observation_id'] for x in r['evidence']['records']}) == 32
    assert len(r['comparisons']) == 16
    assert all(c['outcome']=='INCOMPATIBLE_WITH_PUBLISHED_INTERVAL' for c in r['comparisons'])
    # Measurements at differing frequencies are not independent replicates.
    assert r['holds']['joint_frequency_goodness_of_fit']['statistic'] is None


def test_content_occurrences_and_verification_identities(request_data):
    original_request = deepcopy(request_data)
    a, b = er.run(request_data), er.run(request_data)
    assert request_data == original_request
    assert a['evidence'] == b['evidence']
    assert a['content_sha256'] == b['content_sha256']
    assert len({a['execution_id'],a['result_id'],b['execution_id'],b['result_id']}) == 4
    assert a['record_sha256'] != b['record_sha256']
    before = deepcopy(a)
    i = er.inspect_result(a)
    v = er.verify_result(a)
    assert a == before
    assert len({a['execution_id'],a['result_id'],i['inspection_id'],v['execution_id'],v['verification_id']}) == 5
    assert v['result_record_sha256'] == a['record_sha256']
    assert v['evidence_id'] == a['evidence']['evidence_id']
    assert v['observations_checked'] == 32 and v['marginal_comparisons_checked'] == 16
    assert v['status'] == 'PASS'
    assert er.verify_result(a)['verification_id'] != v['verification_id']


def test_pa_kpa_commuting_comparison_and_preserved_observation_identity(request_data):
    a = er.run(request_data)
    request_data['report_unit'] = 'kPa'
    b = er.run(request_data)
    assert a['evidence']['evidence_id'] == b['evidence']['evidence_id']
    for ra, rb in zip(a['evidence']['records'], b['evidence']['records']):
        assert ra['observation_id'] == rb['observation_id']
        assert ra['representation_id'] != rb['representation_id']
        assert rb['value'] == pytest.approx(ra['value']/1000, rel=1e-15)
        assert rb['uncertainty']['standard'] == pytest.approx(ra['uncertainty']['standard']/1000, rel=1e-15)
        assert rb['uncertainty']['variance'] == pytest.approx(ra['uncertainty']['variance']/1e6, rel=1e-15)
    for ca, cb in zip(a['comparisons'], b['comparisons']):
        assert ca['observation_id'] == cb['observation_id']
        assert ca['outcome'] == cb['outcome']
    assert b['comparisons'][10]['residual'] == pytest.approx(2.636)
    assert b['comparisons'][10]['residual_standard_uncertainty'] == pytest.approx(.042)
    assert er.verify_result(b)['status'] == 'PASS'


@pytest.mark.parametrize('unit', ['Pa', 'kPa'])
def test_independent_decimal_checks_every_normalized_cell(request_data, unit):
    request_data['report_unit'] = unit
    r = er.run(request_data)
    import csv, io
    factor = Decimal('1') if unit == 'Pa' else Decimal('0.001')
    for row, a, b in zip(csv.DictReader(io.StringIO(request_data['capture']['csv_utf8'])),
                          r['evidence']['records'][::2], r['evidence']['records'][1::2]):
        for record, value_key, u_key in ((a,'storage_pa','expanded_storage_pa'),(b,'loss_pa','expanded_loss_pa')):
            expected = Decimal(row[value_key])*factor
            u = Decimal(row[u_key])*factor/2
            assert record['value'] == pytest.approx(float(expected), rel=1e-15)
            assert record['uncertainty']['standard'] == pytest.approx(float(u), rel=1e-15)
            assert record['uncertainty']['variance'] == pytest.approx(float(u*u), rel=1e-15)


@pytest.mark.parametrize('source_key,value', [
    ('coverage_factor',1), ('coverage_factor',True), ('uncertainty_kind','standard'),
    ('joint_covariance','independent'), ('data_kind','native-acquisition'),
    ('source_measurement_time','2026-10-05T12:00:00+00:00'), ('bottle_id','invented'),
    ('rheometer_serial','invented'), ('calibration_provenance','operator-calibrated'),
    ('certification_expiration','2030-12-31'), ('issue_date','2026-10-05'),
    ('current_calibration_use_authorized',True), ('current_calibration_use_authorized',0),
    ('original_pdf_sha256','0'*64), ('original_pdf_retained',True),
    ('capture_review_authenticated',True), ('page_1_indexed',9), ('table','4b'),
    ('strain_amplitude_as_reported',.2), ('material','some other PDMS'),
    ('url','https://example.invalid/data'),
])
def test_source_identity_and_uncertainty_cannot_be_relabelled(request_data, source_key, value):
    request_data['capture']['source'][source_key] = value
    with pytest.raises(ValueError): er.run(request_data)


@pytest.mark.parametrize('mutation', ['value', 'uncertainty', 'frequency', 'temperature', 'row_order', 'missing_row', 'duplicate_row'])
@pytest.mark.parametrize('rehash', [False, True])
def test_corrupt_or_replaced_snapshot_refused_even_if_rehashed(request_data, mutation, rehash):
    raw = request_data['capture']['csv_utf8']
    if mutation == 'value': raw = raw.replace('2636,84','2637,84')
    elif mutation == 'uncertainty': raw = raw.replace('2636,84','2636,42')
    elif mutation == 'frequency': raw = raw.replace('20.0,10.00','20.0,62.83')
    elif mutation == 'temperature': raw = raw.replace('20.0,','25.0,')
    else:
        lines = raw.splitlines(True)
        if mutation == 'row_order': lines[1],lines[2] = lines[2],lines[1]
        elif mutation == 'missing_row': lines.pop(3)
        else: lines.insert(3,lines[3])
        raw = ''.join(lines)
    request_data['capture']['csv_utf8'] = raw
    if rehash: request_data['capture']['csv_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(ValueError): er.run(request_data)


@pytest.mark.parametrize('key', ['schema','investigation','purpose','selection','primary_omega_rad_s','report_unit','capture'])
def test_missing_request_field(request_data,key):
    del request_data[key]
    with pytest.raises(ValueError): er.run(request_data)


@pytest.mark.parametrize('key,value', [
    ('primary_omega_rad_s',True),('primary_omega_rad_s',float('nan')),
    ('primary_omega_rad_s',float('inf')),('primary_omega_rad_s',10**1000),
    ('primary_omega_rad_s',100.0),('report_unit','MPa'),('report_unit',{}),
    ('purpose','current-instrument-calibration'),('selection','preregistered'),
    ('schema','ciw.polymer-viscoelastic-request.v1'),
])
def test_unsupported_input(request_data,key,value):
    request_data[key]=value
    with pytest.raises(ValueError): er.run(request_data)


@pytest.mark.parametrize('timestamp',['not-a-date','2000-01-01T00:00:00+00:00','2026-10-05T00:00:00','2026-10-05T12:00:00-04:00',True])
def test_bad_capture_time(request_data,timestamp):
    request_data['capture']['created_at']=timestamp
    with pytest.raises(ValueError): er.run(request_data)


def test_no_future_capture_or_backdated_execution(request_data):
    p = deepcopy(request_data)
    p['capture']['created_at'] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match='future'): er.run(p)
    r = er.run(request_data)
    r['executed_at'] = '2003-01-01T00:00:00+00:00'
    reseal(r)
    with pytest.raises(ValueError, match='precedes'): er.inspect_result(r)


@pytest.mark.parametrize('field,value',[
    ('subject','another polymer'),('quantity','tensile-storage-modulus'),
    ('quantity','shear-loss-modulus'),('unit','kPa'),('value',1),
    ('prediction_basis','fitted-independent-prediction'),('fitted_parameter_evidence','invented'),
])
def test_incompatible_prediction_cannot_be_compared(request_data,field,value):
    r = er.run(request_data)['evidence']['records'][20]
    claim = er._claim(r); claim[field]=value
    with pytest.raises(ValueError): er.compare_observation(request_data,r,claim)


@pytest.mark.parametrize('field,value', [('omega_rad_s',1.591549),('temperature_k',298.15),
    ('frame','uniaxial-tension'),('strain_amplitude',.002)])
def test_condition_mismatch_refused(request_data,field,value):
    r=er.run(request_data)['evidence']['records'][20]
    claim=er._claim(r);claim['conditions'][field]=value
    with pytest.raises(ValueError): er.compare_observation(request_data,r,claim)


def test_loss_record_not_accepted_as_storage(request_data):
    r=er.run(request_data)['evidence']['records'][21]
    with pytest.raises(ValueError): er.compare_observation(request_data,r,er._claim(r))


def test_missing_covariance_never_becomes_zero(request_data):
    r=er.run(request_data)
    assert all(x['uncertainty']['joint_covariance']=='not-reported' for x in r['evidence']['records'])
    assert r['holds']['complex_viscosity_uncertainty']['standard_uncertainty'] is None
    assert r['holds']['joint_frequency_goodness_of_fit']['p_value'] is None
    assert all(c['p_value'] is None and c['joint_confidence'] is None for c in r['comparisons'])
    bad=deepcopy(r)
    bad['holds']['complex_viscosity_uncertainty']['standard_uncertainty']=0.0
    reseal(bad)
    with pytest.raises(ValueError): er.inspect_result(bad)


@pytest.mark.parametrize('mutation', ['measurement','standard','expanded','variance','observation_id',
    'representation_id','evidence_id','primary_id','duplicate_observation','missing_observation',
    'authority','authority_alias','false_covariance','forged_pdf','field','occurrence_alias'])
def test_resealed_retained_record_semantics_refused(request_data,mutation):
    r=er.run(request_data)
    obs=r['evidence']['records'][20]
    if mutation=='measurement': obs['value']+=1
    elif mutation in ('standard','expanded','variance'): obs['uncertainty'][mutation]*=2
    elif mutation=='observation_id':obs['observation_id']='0'*64
    elif mutation=='representation_id':obs['representation_id']='0'*64
    elif mutation=='evidence_id':r['evidence']['evidence_id']='0'*64
    elif mutation=='primary_id':r['primary_observation_id']='0'*64
    elif mutation=='duplicate_observation':r['evidence']['records'][1]=deepcopy(r['evidence']['records'][0])
    elif mutation=='missing_observation':r['evidence']['records'].pop()
    elif mutation=='authority':r['authority']['part_release']=True
    elif mutation=='authority_alias':r['authority']['part_release']=0
    elif mutation=='false_covariance':obs['uncertainty']['joint_covariance']='independent'
    elif mutation=='forged_pdf':r['evidence']['publisher_document_sha256']='0'*64
    elif mutation=='field':r['undocumented_field']='anything'
    else:r['result_id']=r['execution_id']
    reseal(r)
    with pytest.raises(ValueError): er.inspect_result(r)


@pytest.mark.parametrize('key',['record_sha256','content_sha256','request_sha256'])
def test_unsealed_tampering_refused(request_data,key):
    r=er.run(request_data);r[key]='0'*64
    with pytest.raises(ValueError): er.inspect_result(r)


@pytest.mark.parametrize('mutation',['residual','uncertainty','interval','decision'])
def test_integrity_inspection_is_not_an_arithmetic_audit(request_data,mutation):
    r=er.run(request_data)
    c=r['comparisons'][10]
    if mutation=='residual':c['residual']+=1
    elif mutation=='uncertainty':c['residual_standard_uncertainty']*=2
    elif mutation=='interval':c['residual_expanded_interval'][0]+=1
    else:
        c['outcome']='WITHIN_PUBLISHED_INTERVAL'
        r['primary_outcome']=c['outcome']
    reseal(r)
    # Rehashing is not authorship or scientific truth; inspection says precisely what it checked.
    assert er.inspect_result(r)['claim_comparison_recomputed'] is False
    with pytest.raises(ValueError): er.verify_result(r)


def test_inspection_and_independent_oracle_do_not_reuse_comparison(request_data,monkeypatch):
    r=er.run(request_data)
    def forbidden(*args): raise AssertionError('production comparison called')
    monkeypatch.setattr(er,'compare_observation',forbidden)
    monkeypatch.setattr(er,'_comparisons',forbidden)
    assert er.inspect_result(r)['status']=='PASS'
    assert er.verify_result(r)['status']=='PASS'


def test_source_drift_is_not_silent_attestation(request_data):
    r=er.run(request_data)
    r['runtime']['source_sha256']['rheology_empirical.py']='0'*64
    reseal(r)
    i=er.inspect_result(r)
    assert i['source_matches_current'] is False
    assert not i['publisher_pdf_bytes_verified']
    assert not i['source_review_authenticated']


@pytest.mark.parametrize('site_disabled',[False,True])
def test_cli_end_to_end_and_create_only(request_data,tmp_path,site_disabled):
    p=tmp_path/'request.json';p.write_text(json.dumps(request_data),encoding='utf-8')
    prefix=[sys.executable]+(['-S'] if site_disabled else [])+['-m','ciw.materials_bench']
    result_path=tmp_path/'result.json'
    for action in ('empirical-run','empirical-inspect','empirical-verify'):
        source=p if action=='empirical-run' else result_path
        target=result_path if action=='empirical-run' else tmp_path/f'{action}.json'
        cmd=prefix+[action,str(source),'--output',str(target)]
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        assert run.returncode==0,run.stderr
        raw=target.read_bytes()
        again=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        assert again.returncode==2 and target.read_bytes()==raw
    verification=json.loads((tmp_path/'empirical-verify.json').read_text())
    assert verification['status']=='PASS'
    assert not verification['authority']['current_instrument_calibration']


def test_cli_bad_data_publishes_nothing(tmp_path):
    p=tmp_path/'bad.json';p.write_text('{"schema":1,"schema":2}')
    dest=tmp_path/'result.json'
    r=subprocess.run([sys.executable,'-m','ciw.materials_bench','empirical-run',str(p),'--output',str(dest)],
                     capture_output=True,text=True,timeout=20)
    assert r.returncode==2
    assert not dest.exists()


@pytest.mark.parametrize('mutation', ['value','uncertainty','conditions','source_id','unit','quantity'])
def test_direct_comparison_rebinds_even_resealed_detached_observations(request_data,mutation):
    record=er.run(request_data)['evidence']['records'][20]
    if mutation=='value': record['value']*=2
    elif mutation=='uncertainty': record['uncertainty']['standard']*=2
    elif mutation=='conditions': record['conditions']['temperature_k']=298.15
    elif mutation=='source_id': record['source_evidence_id']='0'*64
    elif mutation=='unit': record['unit']='kPa'
    else: record['quantity']='tensile-storage-modulus'
    record['representation_id']=mb.digest({k:v for k,v in record.items() if k!='representation_id'})
    with pytest.raises(ValueError):
        er.compare_observation(request_data,record,er._claim(record))


@pytest.mark.parametrize('value',[None,[],{},True,{'observation_id':[]}])
def test_direct_comparison_malformed_observation_refuses(request_data,value):
    with pytest.raises(ValueError): er.compare_observation(request_data,value,{})
