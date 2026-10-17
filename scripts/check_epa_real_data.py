"""Run retained NET comparisons on the EPA CASTNET calibration-check archive.

No fitting or physical qualification. The zero-tolerance comparison is a
predeclared diagnostic equality check, not an EPA acceptance rule.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import math
import sys
from ciw import control_checks, control_contracts
from pathlib import Path
import zipfile

from ciw.control_contracts import observation, save_new, load
from ciw.control_checks import compare, validate_comparison

SOURCE_ID = 'epa-castnet-gas-calibration-2025'
DATA_MEMBER = 'gas_calibration_web_2025.csv'
FIELDS = ['SITE_ID','DATE_TIME','END_TIME','PARAMETER','UNITS','EXPECTED_VALUE',
          'CALIBRATION_VALUE','CALIBRATION_TYPE','QA_CODE','UPDATE_DATE']


def read_archive(corpus):
    catalog = json.loads((corpus / 'catalog.json').read_text(encoding='utf-8'))
    records = catalog['records'] if 'records' in catalog else catalog['sources']
    record = next(r for r in records if r['id'] == SOURCE_ID)
    item = next(f for f in record['files'] if f['local_path'] and f['local_path'].endswith('.zip'))
    path = (corpus / item['local_path']).resolve()
    if not path.is_relative_to(corpus.resolve()):
        raise ValueError('Corpus asset escapes root')
    raw = path.read_bytes()
    if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise ValueError('EPA archive does not match retained corpus manifest')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist()) > 20_000_000:
            raise ValueError('Archive expansion exceeds bounded acquisition')
        data = z.read(DATA_MEMBER)
        columns = z.read('gas_calibration_web_2025_columninfo.csv')
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    if reader.fieldnames != FIELDS:
        raise ValueError('Unexpected EPA data columns')
    rows = list(reader)
    return rows, item, record, hashlib.sha256(data).hexdigest(), columns


def series(rows, field, archive_hash, member_hash):
    result = []
    for row in rows:
        dt = datetime.strptime(row['DATE_TIME'], '%Y-%m-%d %H:%M:%S')
        # Deliberately local standard time, with no invented UTC conversion.
        stamp = (dt - datetime(2025, 1, 1)).total_seconds()
        raw = row[field].strip()
        value = float(Decimal(raw)) if raw else None
        if value is not None and not math.isfinite(value):
            raise ValueError('Nonfinite published value')
        result.append(observation(identity={'model_id': 'epa-castnet-calibration-check.v1',
            'entity_id': row['SITE_ID'] + ':' + row['PARAMETER'], 'execution_id': None},
            clock={'id': row['SITE_ID'] + ':local-standard-time:2025-01-01', 'time_s': stamp},
            frame='site-sensor', quantity=row['PARAMETER'], unit=row['UNITS'], value=value,
            uncertainty=None, provenance={'provider': 'US EPA CASTNET/' + field,
            'sources': ['sha256:' + archive_hash, 'sha256:' + member_hash],
            'semantics': 'observed' if field == 'CALIBRATION_VALUE' else 'reference'}))
    return result


def verify_metrics(rows, outcome):
    with localcontext() as ctx:
        ctx.prec = 50
        errors = [abs(Decimal(r['CALIBRATION_VALUE']) - Decimal(r['EXPECTED_VALUE'])) for r in rows]
        rmse = (sum(e * e for e in errors) / Decimal(len(errors))).sqrt()
        expected = {'rmse': float(rmse), 'max_abs_error': float(max(errors)),
                    'max_tolerance_excess': float(max(errors))}
    for key, value in expected.items():
        if not math.isclose(outcome['metrics'][key], value, rel_tol=1e-12, abs_tol=1e-10):
            raise AssertionError(f'NET {key} mismatch: {outcome["metrics"][key]} != {value}')
    if outcome['status'] != ('PASS' if max(errors) == 0 else 'FAIL'):
        raise AssertionError('Incorrect diagnostic comparison outcome')
    return expected


def campaign(corpus, output):
    output.mkdir(parents=True, exist_ok=False)
    rows, item, source, member_hash, columns = read_archive(corpus)
    groups = defaultdict(list)
    for number, row in enumerate(rows, 1):
        groups[(row['SITE_ID'], row['PARAMETER'], row['CALIBRATION_TYPE'], row['UNITS'], row['QA_CODE'])].append((number, row))
    reports = []
    for group_index, (key, indexed) in enumerate(sorted(groups.items())):
        indexed.sort(key=lambda pair: pair[1]['DATE_TIME'])
        selected = [r for _, r in indexed]
        if len({r['DATE_TIME'] for r in selected}) != len(selected):
            raise ValueError(f'Duplicate source times require explicit resolution: {key}')
        left = series(selected, 'CALIBRATION_VALUE', item['sha256'], member_hash)
        right = series(selected, 'EXPECTED_VALUE', item['sha256'], member_hash)
        comparison = compare(left, right, atol=0.0, rtol=0.0)
        missing = [i for i, r in enumerate(selected) if not r['EXPECTED_VALUE'].strip() or not r['CALIBRATION_VALUE'].strip()]
        if missing:
            if comparison['outcome']['status'] != 'INDETERMINATE' or comparison['outcome']['reason'] != 'missing_sample':
                raise AssertionError('Missing source data silently accepted')
        else:
            verify_metrics(selected, comparison['outcome'])
        path = output / f'group-{group_index:03d}-all.json'
        save_new(path, comparison)
        validate_comparison(load(path))
        complete = [r for r in selected if r['EXPECTED_VALUE'].strip() and r['CALIBRATION_VALUE'].strip()]
        complete_report = None
        if complete:
            compact = compare(series(complete, 'CALIBRATION_VALUE', item['sha256'], member_hash),
                              series(complete, 'EXPECTED_VALUE', item['sha256'], member_hash), atol=0.0, rtol=0.0)
            independent = verify_metrics(complete, compact['outcome'])
            cpath = output / f'group-{group_index:03d}-complete-pairs.json'
            save_new(cpath, compact)
            validate_comparison(load(cpath))
            complete_report = {'rows': len(complete), 'outcome': compact['outcome'], 'independent_decimal_metrics': independent, 'retained': cpath.name}
        reports.append({'group': dict(zip(['site','parameter','check_type','unit','qa_code'], key)),
                        'rows':len(selected), 'missing_pairs':len(missing),
                        'source_record_numbers_1based':[n for n, _ in indexed],
                        'all_rows_outcome':comparison['outcome'], 'retained':path.name,
                        'complete_pairs_only':complete_report})
    # Preserve every source field, including QA, end/update times and blanks.
    (output / 'source-rows.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    (output / 'column-metadata.csv').write_bytes(columns)
    report = {'schema':'net.institutional-epa-campaign.v1','executed_at_utc':datetime.now(timezone.utc).isoformat(),
      'software_test_status':'PASS','runtime':{'python':sys.version,'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'modules':{m.__name__:{'path':str(Path(m.__file__).resolve()),'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} for m in (control_checks,control_contracts)}},'source':{'id':SOURCE_ID,'url':item['url'],'landing_url':source['landing_url'],
      'archive_sha256':item['sha256'],'member_sha256':member_hash,'retrieved_at_utc':item['retrieved_at_utc'],
      'attribution':'U.S. Environmental Protection Agency Clean Air Markets Division, Clean Air Status and Trends Network (CASTNET), GAS_CALIBRATION_WEB 2025'},
      'counts':{'rows':len(rows),'groups':len(groups),'sites':len({r['SITE_ID'] for r in rows}),
      'complete_pairs':sum(bool(r['EXPECTED_VALUE'].strip() and r['CALIBRATION_VALUE'].strip()) for r in rows),
      'missing_pairs':sum(not (r['EXPECTED_VALUE'].strip() and r['CALIBRATION_VALUE'].strip()) for r in rows)},
      'diagnostic_policy':{'atol':0.0,'rtol':0.0,'meaning':'Exact equality diagnostic, not EPA or physical acceptance'},
      'arithmetic_gate':{'reference':'50-digit Decimal residual RMSE/max','rtol':1e-12,'atol':1e-10},
      'workflow':'hash validation -> original fields -> NET observed/reference records -> NET compare -> retained readback validation',
      'limits':['No calibration fitting or traceable gas certificates','No cross-site UTC synchronization; site local-standard clock preserved',
        'QA codes retained without reinterpretation','Complete-pair subset is explicitly counted; all-row missing evidence remains indeterminate',
        'No physical NET qualification; diagnostic FAIL does not mean software failure'],
      'groups':reports}
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    report=campaign(a.corpus,a.output_dir)
    print(json.dumps({'software_test_status':report['software_test_status'],'counts':report['counts']}))

if __name__ == '__main__':
    main()
