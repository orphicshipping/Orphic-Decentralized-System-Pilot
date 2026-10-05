"""Offline public-recording ingress with no invented acquisition or metrology."""
import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ciw import temperature_recording as recording
from ciw.control_checks import inspect_record
from ciw.control_contracts import bytes_ref
from ciw.operations.runner import seal


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "temperature-recording"


@pytest.fixture
def public_source():
    return (EXAMPLE / "data.csv").read_bytes(), json.loads((EXAMPLE / "source.json").read_text(encoding="utf-8"))


def fixture_source(raw=b"time,T1,T2,Q1,Q2\n0,20,21,0,10\n2,20.5,,25,50\n"):
    manifest = json.loads((EXAMPLE / "source.json").read_text(encoding="utf-8"))
    manifest.update(origin="test_fixture", raw_sha256=bytes_ref(raw),
                    source_description="Software test fixture, not a physical recording.")
    manifest["columns"] = {name: name for name in ("time", "T1", "T2", "Q1", "Q2")}
    return raw, manifest


def test_public_recording_retains_exact_bytes_and_source_values(public_source, monkeypatch):
    import socket
    import urllib.request

    def forbid_network(*args, **kwargs):
        pytest.fail("Offline import or inspection attempted a network request")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)
    raw, manifest = public_source
    bundle = recording.import_recording(raw, manifest)
    assert base64.b64decode(bundle["raw_bytes_b64"], validate=True) == raw
    assert bundle["raw_sha256"] == "sha256:0be6231c36b8f5b1d05ee999ad7d5ec06cc66a39bc7392c7cdf85d57a5d034d7"
    assert bundle["manifest"]["columns"]["T1"] == " Temperature 1"
    assert len(bundle["rows"]) == 201
    assert bundle["rows"][0] == {"row_number": 2, "time_s": 0.0, "T1": 18.77, "T2": 16.61, "Q1": 0.0, "Q2": 0.0}
    assert bundle["rows"][-1] == {"row_number": 202, "time_s": 600.298707485199,
        "T1": 31.89, "T2": 32.37, "Q1": 78.57305615832082, "Q2": 30.801005344943754}
    assert recording.validate_recording(bundle) == bundle
    summary = recording.inspect_recording(bundle)
    assert summary["row_count"] == 201
    assert summary["sample_interval_s"]["minimum"] < summary["sample_interval_s"]["maximum"]
    assert summary["sample_interval_s"]["resampled"] is False
    assert summary["physical_qualification"]["status"] == "incomplete"
    assert summary["physical_qualification"]["measured_thermal_compatible"] is False
    assert summary["command_channels"]["Q1"]["unit"] == "%"
    assert summary["command_channels"]["Q1"]["measured_heat_power"] is False


def test_export_preserves_observed_values_but_no_device_identity_or_uncertainty(public_source):
    bundle = recording.import_recording(*public_source)
    context = bundle["recording_context"]
    for name in ("acquisition_execution_id", "instrument_serial", "installed_configuration_id",
                 "calibration_evidence", "measurement_covariance", "independent_reference"):
        assert context[name] is None
    assert context["clock"]["epoch_utc"] is None
    assert context["clock"]["uncertainty_s"] is None
    assert context["clock"]["acquisition_to_receipt_alignment"] == "unknown"
    assert "log_columns_only" in context["coordinate_scope"]
    for channel in ("T1", "T2"):
        stream = recording.observation_stream(bundle, channel)
        inspect_record(stream)
        assert len(stream["observations"]) == 201
        for source_row, observation in zip(bundle["rows"], stream["observations"]):
            assert observation["value"] == source_row[channel]
            assert observation["clock"] == {"id": context["clock"]["id"], "time_s": source_row["time_s"]}
            assert observation["frame"] == context["frame"]
            assert observation["unit"] == "degC"
            assert observation["quantity"] == "reported_temperature:" + channel
            assert observation["uncertainty"] is None
            assert observation["identity"]["execution_id"] is None
            assert observation["provenance"]["semantics"] == "observed"
            assert bundle["record_digest"] in observation["provenance"]["sources"]
            assert bundle["raw_sha256"] in observation["provenance"]["sources"]
    assert bundle["authority"]["new_physical_acquisition"] == "not_performed"
    assert bundle["authority"]["physical_validation"] == "not_established"


def test_fixture_missingness_is_retained_and_never_promoted_to_observed():
    raw, manifest = fixture_source()
    bundle = recording.import_recording(raw, manifest)
    stream = recording.observation_stream(bundle, "T2")
    inspect_record(stream)
    assert [row["value"] for row in stream["observations"]] == [21.0, None]
    assert all(row["provenance"]["semantics"] == "simulated" for row in stream["observations"])
    assert recording.inspect_recording(bundle)["temperature_channels"]["T2"]["missing_count"] == 1
    manifest["origin"] = "public_experimental_recording"
    with pytest.raises(ValueError, match="reviewed source manifest"):
        recording.import_recording(raw, manifest)
    bundle["manifest"]["origin"] = "public_experimental_recording"
    seal(bundle)
    with pytest.raises(ValueError, match="reviewed source manifest"):
        recording.validate_recording(bundle)


@pytest.mark.parametrize("channel", ["Q1", "Q2", "temperature", "core_reference", ""])
def test_command_histories_cannot_be_exported_as_temperature_observations(channel):
    with pytest.raises(ValueError, match="Only T1 and T2"):
        recording.observation_stream(recording.import_recording(*fixture_source()), channel)


@pytest.mark.parametrize("rows", [
    "", "\n", "0,20,21,0\n", "0,20,21,0,0,0\n",
    "0,20,21,0,0\n0,21,22,0,0\n", "2,20,21,0,0\n1,21,22,0,0\n",
    "-1,20,21,0,0\n", ",20,21,0,0\n", "0,20,21,,0\n", "0,20,21,0,\n",
    "0,20,21,-1,0\n", "0,20,21,100.1,0\n", "0,20,21,0,101\n",
    "0,NaN,21,0,0\n", "0,inf,21,0,0\n", "0,20,-inf,0,0\n",
    "0,1e999,21,0,0\n", "nan,20,21,0,0\n", "1e151,20,21,0,0\n",
    "0,True,21,0,0\n", "0,1_000,21,0,0\n", '0,"20,21,0,0\n',
])
def test_malformed_or_out_of_profile_rows_are_refused_without_dropping(rows):
    with pytest.raises(ValueError):
        recording.import_recording(*fixture_source(("time,T1,T2,Q1,Q2\n" + rows).encode()))


@pytest.mark.parametrize("header", ["time,T1,T1,Q1,Q2", "time,T1,T2,Q1", "time,T1,T2,Q1,Q2,extra", "time,T1,T2,Q1,Q3"])
def test_header_mapping_is_exact_and_unique(header):
    with pytest.raises(ValueError, match="header"):
        recording.import_recording(*fixture_source((header + "\n0,20,21,0,0\n").encode()))


def test_raw_digest_and_byte_row_budgets_are_enforced():
    raw, manifest = fixture_source()
    with pytest.raises(ValueError, match="exact raw digest"):
        recording.import_recording(raw + b"\n", manifest)
    with pytest.raises(ValueError, match="48 KiB"):
        recording.import_recording(b" " * (recording.MAX_BYTES + 1), manifest)
    with pytest.raises(ValueError):
        recording.import_recording(raw.decode(), manifest)
    too_many = b"time,T1,T2,Q1,Q2\n" + b"".join(f"{index},,,0,0\n".encode() for index in range(recording.MAX_ROWS + 1))
    assert len(too_many) <= recording.MAX_BYTES
    with pytest.raises(ValueError, match="row count"):
        recording.import_recording(*fixture_source(too_many))


@pytest.mark.parametrize("field,value", [("origin", {}), ("origin", []), ("temperature_unit", "K"),
    ("command_unit", "W"), ("time_unit", "ms"), ("revision", "main"), ("clock", {"time_kind": []}),
    ("clock", {"time_kind": "unspecified"}), ("producer", ""), ("source_description", [])])
def test_manifest_types_units_and_clock_are_explicit(field, value):
    raw, manifest = fixture_source()
    manifest[field] = value
    with pytest.raises(ValueError):
        recording.import_recording(raw, manifest)


def test_manifest_column_duplicates_and_mutable_urls_are_refused():
    raw, manifest = fixture_source()
    manifest["columns"]["T2"] = manifest["columns"]["T1"]
    with pytest.raises(ValueError, match="distinct"):
        recording.import_recording(raw, manifest)
    raw, manifest = fixture_source()
    manifest["source_url"] = manifest["source_url"].replace(manifest["revision"], "main")
    with pytest.raises(ValueError, match="exact retained revision"):
        recording.import_recording(raw, manifest)


@pytest.mark.parametrize("mutate", [
    lambda b: b["rows"][0].update(T1=100.0),
    lambda b: b["rows"][0].update(time_s=1.0),
    lambda b: b["rows"].pop(),
    lambda b: b["authority"].update(physical_validation="established"),
    lambda b: b["authority"].update(new_physical_acquisition="performed"),
    lambda b: b["recording_context"]["clock"].update(uncertainty_s=0.0),
    lambda b: b["recording_context"]["clock"].update(epoch_utc="2026-10-04T00:00:00Z"),
    lambda b: b["recording_context"].update(instrument_serial="fabricated"),
    lambda b: b["recording_context"].update(independent_reference="T2"),
    lambda b: b.update(raw_sha256="sha256:" + "0" * 64),
])
def test_resealing_derived_metadata_cannot_change_retained_source_meaning(mutate):
    bundle = recording.import_recording(*fixture_source())
    mutate(bundle)
    seal(bundle)
    with pytest.raises(ValueError, match="contradict"):
        recording.validate_recording(bundle)


def test_public_source_cannot_be_relabelled_by_a_resealed_manifest(public_source):
    raw, manifest = public_source
    manifest["producer"] = "invented instrument serial"
    with pytest.raises(ValueError, match="reviewed source manifest"):
        recording.import_recording(raw, manifest)


def test_validate_returns_detached_data_and_preserves_original_csv_encoding():
    raw = b"\xef\xbb\xbftime,T1,T2,Q1,Q2\r\n0,,20,0,100\r\n2,22,,100,0\r\n"
    raw, manifest = fixture_source(raw)
    bundle = recording.import_recording(raw, manifest)
    original = deepcopy(bundle)
    manifest["producer"] = "changed after import"
    restored = recording.validate_recording(bundle)
    restored["rows"][0]["T1"] = 1.0
    restored["manifest"]["producer"] = "changed after validation"
    assert bundle == original
    assert base64.b64decode(bundle["raw_bytes_b64"]) == raw
    assert bundle["rows"][0]["T1"] is None
    assert bundle["rows"][1]["T2"] is None


def test_noncanonical_and_invalid_base64_are_refused():
    for encoded in ("not base64!", "", " " * (4 * ((recording.MAX_BYTES + 2) // 3) + 1)):
        bundle = recording.import_recording(*fixture_source())
        bundle["raw_bytes_b64"] = encoded
        seal(bundle)
        with pytest.raises(ValueError, match="base64"):
            recording.validate_recording(bundle)


@pytest.mark.parametrize("column,index", [("time", 0), ("T1", 1), ("T2", 2), ("Q1", 3), ("Q2", 4)])
def test_nonzero_decimal_underflow_is_refused_instead_of_becoming_zero(column, index):
    cells = ["0", "20", "21", "0", "10"]
    cells[index] = "1e-999"
    raw = ("time,T1,T2,Q1,Q2\n" + ",".join(cells) + "\n").encode()
    with pytest.raises(ValueError, match=f"CSV record 2, column {column}:.*silent conversion to zero"):
        recording.import_recording(*fixture_source(raw))


def test_signed_and_exponential_zero_remain_valid_and_missing_temperature_stays_null():
    raw = b"time,T1,T2,Q1,Q2\n0,-0e-999,,0e999999999999999999999999,-0\n"
    bundle = recording.import_recording(*fixture_source(raw))
    assert bundle["rows"][0]["T1"] == 0.0
    assert bundle["rows"][0]["T2"] is None
    assert bundle["rows"][0]["Q1"] == bundle["rows"][0]["Q2"] == 0.0
    assert base64.b64decode(bundle["raw_bytes_b64"]) == raw


@pytest.mark.parametrize("row,column", [
    ("2,NaN,21,0,0", "T1"),
    ("2,20,21,0,101", "Q2"),
    ("0,20,21,0,0", "time"),
])
def test_invalid_later_cell_identifies_its_csv_record_and_quantity(row, column):
    raw = ("time,T1,T2,Q1,Q2\n0,20,21,0,0\n" + row + "\n").encode()
    with pytest.raises(ValueError, match=f"CSV record 3, column {column}:"):
        recording.import_recording(*fixture_source(raw))
