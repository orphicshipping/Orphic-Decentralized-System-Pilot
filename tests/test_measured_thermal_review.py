"""Independent regressions for measured thermal evidence and portable inspection."""
import base64
from copy import deepcopy
import json

import pytest

from ciw import measured_thermal as measured
from ciw import thermal_contract as thermal
from ciw.control_checks import inspect_record
from ciw.control_contracts import bytes_ref
from ciw.core.covariance import covariance_identity
from ciw.telemetry import canonical
from test_measured_thermal import _source, _rewrite_stream


def _held_out_ref(source, kind):
    return (source["series"]["core_reference"] if kind == "stream"
            else source["channels"]["core_reference"][kind])


def _covariance_edit(row, *, evidence=None, ancestors=None):
    covariance = row["uncertainty"]
    if evidence is not None:
        covariance["provenance"]["source_evidence_ids"].append(evidence)
    if ancestors is not None:
        covariance["provenance"]["source_covariance_ids"] = ancestors
    covariance["covariance_id"] = covariance_identity(covariance)


def _retain(source, value):
    # Deliberately different from the content-identity encoder: lineage must use
    # covariance_id, while the transport still commits to these exact bytes.
    raw = json.dumps(value, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    ref = bytes_ref(raw)
    source["artifacts"].append({"sha256": ref, "bytes_b64": base64.b64encode(raw).decode("ascii")})
    return ref


def _ancestor(source, *, evidence=None, parents=(), label="ancestor"):
    covariance = deepcopy(measured.streams(source)["shell"]["observations"][0]["uncertainty"])
    covariance["basis"]["id"] = label
    covariance["provenance"]["source_covariance_ids"] = list(parents)
    if evidence is not None:
        covariance["provenance"]["source_evidence_ids"].append(evidence)
    covariance["covariance_id"] = covariance_identity(covariance)
    byte_ref = _retain(source, covariance)
    assert byte_ref != covariance["covariance_id"]
    return covariance


@pytest.mark.parametrize("channel", ["shell", "heat_power", "ambient"])
def test_model_evidence_overlap_deduplicates_exported_provenance(channel):
    source = _source()
    source["model_evidence_refs"].append(source["series"][channel])
    assert measured.validate_source(canonical(source)) == source
    data = measured.calculate(source)
    measured.validate_data(source, data)
    exported = measured.observation_stream(source, data, stage="posterior", execution_id="execution:review")
    inspect_record(exported)
    expected = [source["series"][name] for name in ("shell", "heat_power", "ambient")]
    expected += source["model_evidence_refs"][:1]
    for row in exported["observations"]:
        assert row["provenance"]["sources"] == expected
        assert row["uncertainty"]["provenance"]["source_evidence_ids"] == expected


@pytest.mark.parametrize("kind", ["stream", "raw_ref", "application_ref"])
def test_held_out_data_cannot_justify_model(kind):
    source = _source()
    source["model_evidence_refs"].append(_held_out_ref(source, kind))
    with pytest.raises(ValueError, match="cannot justify fitted"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("channel", ["shell", "heat_power", "ambient"])
@pytest.mark.parametrize("kind", ["stream", "raw_ref", "application_ref"])
def test_held_out_data_cannot_enter_inference_observation_lineage(channel, kind):
    source = _source()
    ref = _held_out_ref(source, kind)
    _rewrite_stream(source, channel, lambda rows: rows[0]["provenance"]["sources"].append(ref))
    with pytest.raises(ValueError, match="Inference recording references held-out data"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("kind", ["stream", "raw_ref", "application_ref"])
@pytest.mark.parametrize("ancestral", [False, True])
def test_held_out_data_cannot_enter_direct_or_ancestral_covariance(kind, ancestral):
    source = _source()
    ref = _held_out_ref(source, kind)
    if ancestral:
        ancestor = _ancestor(source, evidence=ref)
        mutate = lambda rows: _covariance_edit(rows[0], ancestors=[ancestor["covariance_id"]])
    else:
        mutate = lambda rows: _covariance_edit(rows[0], evidence=ref)
    _rewrite_stream(source, "shell", mutate)
    with pytest.raises(ValueError, match="Inference uncertainty references held-out data"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("channel", ["shell", "core_reference", "heat_power", "ambient"])
@pytest.mark.parametrize("kind", ["evidence", "covariance"])
def test_dangling_uncertainty_lineage_is_refused(channel, kind):
    source = _source()
    missing = bytes_ref(b"not retained in the recording")
    kwargs = {"evidence": missing} if kind == "evidence" else {"ancestors": [missing]}
    _rewrite_stream(source, channel, lambda rows: _covariance_edit(rows[0], **kwargs))
    with pytest.raises(ValueError, match="dangling evidence|Unresolved source covariance"):
        measured.validate_source(canonical(source))


def test_ancestral_covariance_resolves_by_content_identity_and_closes():
    source = _source()
    grandparent = _ancestor(source, label="grandparent")
    parent = _ancestor(source, parents=[grandparent["covariance_id"]], label="parent")
    _rewrite_stream(source, "shell", lambda rows: _covariance_edit(
        rows[0], ancestors=[parent["covariance_id"]]))
    assert measured.validate_source(canonical(source)) == source
    data = measured.calculate(source)
    measured.validate_data(source, data)
    assert data["recording"]["origin"] == "test_fixture"
    assert data["authority"]["physical_validation"] == "not_established"


def test_dangling_ancestor_evidence_is_refused():
    source = _source()
    ancestor = _ancestor(source, evidence=bytes_ref(b"unretained ancestral evidence"))
    _rewrite_stream(source, "shell", lambda rows: _covariance_edit(
        rows[0], ancestors=[ancestor["covariance_id"]]))
    with pytest.raises(ValueError, match="dangling evidence"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("within_tolerance", [True, False])
def test_inspection_allows_portable_roundoff_but_rejects_changed_numerics(monkeypatch, within_tolerance):
    source = _source()
    data = measured.calculate(source)
    before = canonical(data)
    original = measured.thermal_reference.reference

    def changed_host(request):
        result = original(request)
        initial = result["observer"]["trace"][0]["posterior_mean"][0]
        tolerance = max(thermal.TOLERANCES["absolute"], abs(initial) * thermal.TOLERANCES["relative"])
        result["observer"]["trace"][0]["posterior_mean"][0] += tolerance * (0.25 if within_tolerance else 100)
        return result

    monkeypatch.setattr(measured.thermal_reference, "reference", changed_host)
    if within_tolerance:
        measured.validate_data(source, data)
    else:
        with pytest.raises(ValueError, match="differs from the independent analytical reference"):
            measured.validate_data(source, data)
    assert canonical(data) == before


def test_portable_check_still_refuses_derived_metric_tampering():
    source = _source()
    data = measured.calculate(source)
    data["evaluation"]["rmse_k"] += 0.01
    with pytest.raises(ValueError, match="differs from retained inputs"):
        measured.validate_data(source, data)


def test_prediction_only_claim_does_not_assert_shell_conditioning():
    source = _source(shell_values=(None, None, None))
    data = measured.calculate(source)
    measured.validate_data(source, data)
    assert data["claim_scope"] == "linear_thermal_prediction_and_held_out_core_comparison"
    assert data["evaluation"]["estimation_mode"] == "prediction_only"
    assert all(row[0] is None and row[1] is None for row in data["request"]["observations"])
    assert data["authority"]["physical_validation"] == "not_established"
