"""Opt-in real pinned PyArrow round-trip, not a substitute serializer."""
import os
import pytest
from ciw import link_measurements as m, link_measurement_io as io, link_study_io as prior
from ciw.link_study_cli import example

pytestmark=pytest.mark.skipif(os.environ.get('NET_MEASUREMENT_PARQUET')!='1',reason='Explicit real PyArrow gate not selected')


def test_real_parquet_exact_records_and_correlated_companion(tmp_path):
    prior.run(example(),tmp_path/'study');b=m.build(tmp_path/'study')
    a=io.export_objects(b,tmp_path/'objects',parquet=True)
    assert a['parquet']=='EXPORTED'
    restored=io.review_objects(tmp_path/'objects')
    assert restored==b
    assert restored['native']['covariance']['matrix'][0][1]!=0
