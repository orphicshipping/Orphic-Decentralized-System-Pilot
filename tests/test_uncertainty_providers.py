"""Bounded provider conformance; every measurement and timing declaration is synthetic."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from ciw import link_contract as c, uncertainty_providers as p
from ciw.core.covariance import create_covariance_artifact, covariance_identity, validate_covariance_artifact
from ciw.link_study_cli import example


def fir(*, mode="valid", scale=1., exact=False, uncertain_coefficients=False):
    request = {"schema": p.FIR_SCHEMA,
        "input": {"values": [1., 2., 3.], "quantity_ids": ["sample.0", "sample.1", "sample.2"],
            "unit": "N", "frame": "instrument", "source_ref": c.bytes_ref(b"synthetic samples")},
        "coefficients": {"values": [.5, .5], "quantity_ids": ["coefficient.0", "coefficient.1"],
            "unit": "1", "provenance_ref": c.bytes_ref(b"synthetic moving average")},
        "sampling": {"kind": "uniform_acquisition", "timestamp_event": "sensor_acquisition", "clock_id": "synthetic.clock",
            "boot_id": "synthetic.boot", "ticks": [100, 200, 300], "tick_hz": 1000,
            "uncertainty_state": "declared_exact", "evidence_ref": c.bytes_ref(b"synthetic exact sampling")},
        "filter": {"mode": mode, "initial_conditions": "zero"}, "constraints": deepcopy(p.CONSTRAINTS),
        "uncertainty": {"state": "declared_exact" if exact else "covariance", "joint_covariance": None,
            "declaration_ref": c.bytes_ref(b"synthetic uncertainty declaration")}}
    if not exact:
        matrix = [[v*scale for v in row] for row in [[.04,.01,0,0,0], [.01,.04,.01,0,0], [0,.01,.04,0,0],
            [0,0,0,.001 if uncertain_coefficients else 0,0], [0,0,0,0,.001 if uncertain_coefficients else 0]]]
        request["uncertainty"]["joint_covariance"] = create_covariance_artifact(matrix=matrix,
            quantity_ids=request["input"]["quantity_ids"]+request["coefficients"]["quantity_ids"], units=["N"]*3+["1"]*2,
            frame="instrument", reference_values=[1.,2.,3.,.5,.5], method="synthetic_declared_covariance",
            basis={"kind":"parameter", "id":p.fir_basis(request)}, provenance={"provider":"synthetic.test",
                "source_evidence_ids":[request["input"]["source_ref"], request["coefficients"]["provenance_ref"], request["sampling"]["evidence_ref"]],
                "source_covariance_ids":[]}, assumptions=["synthetic", "input_and_coefficients_independent"])
    return request


def comparison():
    return {"schema": p.COMPARISON_SCHEMA, "study": example(), "uncertainty_state": "covariance", "constraints": deepcopy(p.CONSTRAINTS)}


def reseal(request):
    cov = request["uncertainty"]["joint_covariance"]
    cov["basis"]["id"] = p.fir_basis(request)
    cov["covariance_id"] = covariance_identity(cov)


@pytest.fixture
def providers():
    # A default NCP install need not contain optional numerical providers. The
    # qualification script explicitly requires both and rejects test skips.
    pytest.importorskip("PyDynamic")
    pytest.importorskip("metrolopy")


@pytest.mark.parametrize("path,value", [
    (("uncertainty","state"), "unknown"), (("uncertainty","state"), None),
    (("sampling","timestamp_event"), "instrument_report_receipt"),
    (("sampling","uncertainty_state"), "unknown"), (("sampling","ticks"), [100,201,300]),
    (("sampling","ticks"), [100,100,100]), (("sampling","tick_hz"), True),
    (("sampling","evidence_ref"), "unidentified"), (("coefficients","unit"), "N"),
    (("filter","initial_conditions"), "constant"), (("filter","mode"), "same"),
    (("constraints","kind"), "sum_to_one"), (("constraints","coordinate_basis"), "implicit"),
    (("input","values"), [True,2.,3.]), (("input","quantity_ids"), ["x","x","x"]),
])
def test_invalid_declarations_refuse_before_provider(path, value, monkeypatch):
    request = fir(); request[path[0]][path[1]] = value
    monkeypatch.setattr(p, "_identity", lambda *a: pytest.fail("provider accessed before validation"))
    with pytest.raises(ValueError):
        p.pydynamic_fir(request)


@pytest.mark.parametrize("kind", ["cross", "unit", "reference", "source", "independence", "asymmetry"])
def test_covariance_binding_and_cross_correlation_refuse(kind, monkeypatch):
    request = fir(uncertain_coefficients=True); cov = request["uncertainty"]["joint_covariance"]
    if kind == "cross": cov["matrix"][0][3] = cov["matrix"][3][0] = .0001
    elif kind == "unit": cov["units"][0] = "m"
    elif kind == "reference": cov["reference_values"][0] = 9.
    elif kind == "source": cov["provenance"]["source_evidence_ids"] = []
    elif kind == "independence": cov["assumptions"] = ["synthetic"]
    else: cov["matrix"][0][1] += .001
    cov["covariance_id"] = covariance_identity(cov)
    monkeypatch.setattr(p, "_identity", lambda *a: pytest.fail("provider accessed before validation"))
    with pytest.raises(ValueError): p.pydynamic_fir(request)


def test_missing_is_not_declared_exact():
    request = fir(); request["uncertainty"]["joint_covariance"] = None
    with pytest.raises(ValueError): p.validate_fir(request)
    request = fir(); request["uncertainty"]["state"] = "declared_exact"
    with pytest.raises(ValueError): p.validate_fir(request)


@pytest.mark.parametrize("mode", ["valid", "full"])
@pytest.mark.parametrize("uncertain_coefficients", [False, True])
def test_fir_known_analytic_reference(providers, mode, uncertain_coefficients):
    request = fir(mode=mode, uncertain_coefficients=uncertain_coefficients)
    original = deepcopy(request); result = p.pydynamic_fir(request)
    assert request == original
    assert result["values"] == ([1.5,2.5] if mode == "valid" else [.5,1.5,2.5,1.5])
    assert result["covariance"]["matrix"][0][1] > 0
    validate_covariance_artifact(result["covariance"])
    assert all(check["passed"] for check in result["checks"])
    assert result["limits"]["physical_qualification"] == "HELD"
    assert result["covariance"]["provenance"]["source_covariance_ids"] == [request["uncertainty"]["joint_covariance"]["covariance_id"]]


def test_exact_declaration_is_visible(providers):
    result = p.pydynamic_fir(fir(exact=True))
    assert result["uncertainty_state"] == "declared_exact"
    assert result["covariance"]["matrix"] == [[0.,0.],[0.,0.]]


def test_fir_tiny_covariance_is_not_erased(providers):
    result = p.pydynamic_fir(fir(scale=1e-280))
    assert 0 < result["covariance"]["matrix"][0][0] < 1e-280
    assert 0 < result["covariance"]["matrix"][0][1] < 1e-280


def test_provider_corrupt_small_covariance_is_refused(providers, monkeypatch):
    import PyDynamic.uncertainty.propagate_convolution as module
    original = module.convolve_unc
    def wrong(*a, **kw):
        value, covariance = original(*a, **kw)
        covariance[:] = 0
        return value, covariance
    monkeypatch.setattr(module, "convolve_unc", wrong)
    with pytest.raises(p.ProviderRefusal, match="Independent reference mismatch"):
        p.pydynamic_fir(fir(scale=1e-280))


def test_shared_error_and_mean_full_covariance(providers):
    result = p.compare_link_affine(comparison())
    assert result["mean"] == pytest.approx(2.03)
    assert result["mean_covariance"]["matrix"][0][0] == pytest.approx(.00190808)
    assert result["mean_covariance"]["matrix"][0][0] > sum(result["covariance"]["matrix"][i][i] for i in range(3))/9
    assert result["applicability"] == ["ACQUISITION_TIME_UNKNOWN"]*3
    assert result["limits"]["physical_qualification"] == "HELD"
    validate_covariance_artifact(result["covariance"])


def test_metrolopy_tiny_full_covariance(providers):
    request = comparison(); cov = request["study"]["calibration"]["joint_covariance"]
    cov["matrix"] = [[v*1e-280 for v in row] for row in cov["matrix"]]
    cov["covariance_id"] = covariance_identity(cov)
    result = p.compare_link_affine(request)
    assert 0 < result["mean_covariance"]["matrix"][0][0] < 1e-280


def test_metrolopy_extreme_mixed_scale_cancellation_refuses_lost_cross_covariance(providers):
    from ciw import link_study as study
    request = comparison(); req = request["study"]; d = study.decode(req)
    req["calibration"].update(gain=1.,offset=0.)
    # The constant input component and gain are perfectly anticorrelated;
    # retain a tiny independent offset after cancellation for the first row.
    matrix = [[0.]*5 for _ in range(5)]
    matrix[0][0] = matrix[3][3] = 1.; matrix[0][3] = matrix[3][0] = -1.
    matrix[4][4] = 1e-280
    req["calibration"]["joint_covariance"] = create_covariance_artifact(matrix=matrix,
        quantity_ids=[r["id"]+":input" for r in d["rows"]]+["gain","offset"], units=["N"]*3+["1","N"],
        frame=d["channel"]["frame"], reference_values=[1.,2.,3.,1.,0.], method="synthetic_cancellation",
        basis={"kind":"parameter", "id":study.calibration_basis(req)}, provenance={"provider":"synthetic.test",
        "source_evidence_ids":[d["capture_ref"]], "source_covariance_ids":[]}, assumptions=["synthetic"])
    # Upstream retains the first marginal but loses its tiny cross-covariance
    # with much larger marginals. Never repair or publish that truncated result.
    with pytest.raises(p.ProviderRefusal, match="Independent reference mismatch: covariance.0.1"):
        p.compare_link_affine(request)


def test_metrolopy_degenerate_shared_cancellation_is_refused(providers):
    from ciw import link_study as study
    request = comparison(); req = request["study"]; d = study.decode(req)
    req["calibration"].update(gain=1.,offset=0.)
    vector = [1.,2.,3.,-1.,0.]
    matrix = [[a*b for b in vector] for a in vector]
    matrix[4][4] = 1e-280
    req["calibration"]["joint_covariance"] = create_covariance_artifact(matrix=matrix,
        quantity_ids=[r["id"]+":input" for r in d["rows"]]+["gain","offset"], units=["N"]*3+["1","N"],
        frame=d["channel"]["frame"], reference_values=[1.,2.,3.,1.,0.], method="synthetic_shared_cancellation",
        basis={"kind":"parameter", "id":study.calibration_basis(req)}, provenance={"provider":"synthetic.test",
        "source_evidence_ids":[d["capture_ref"]], "source_covariance_ids":[]}, assumptions=["synthetic"])
    with pytest.raises(p.ProviderRefusal, match="MetroloPy failed|Independent reference mismatch"):
        p.compare_link_affine(request)


def test_metrolopy_single_row_cancellation_preserves_small_offset(providers, tmp_path):
    from ciw import link_study as study
    from ciw.link_gateway import capture
    from ciw.link_runtime import DeviceRuntime
    from ciw.link_store import Journal
    request = comparison(); req = request["study"]; device = req["capture"]["manifest"]
    with Journal(tmp_path/"source.sqlite", create=True) as journal:
        rt = DeviceRuntime(device, journal, origin="synthetic", boot_id="synthetic.cancellation.boot")
        rt.emit("CH0",b"1.000 N\r\n",tick=1000)
        req["capture"] = capture(journal,expected_manifest=device,boot_id=rt.boot_id)
    req["calibration"].update(gain=1.,offset=0.,declaration=None,joint_covariance=None)
    d = study.decode(req)
    req["calibration"]["joint_covariance"] = create_covariance_artifact(
        matrix=[[1.,-1.,0.],[-1.,1.,0.],[0.,0.,1e-280]],
        quantity_ids=[d["rows"][0]["id"]+":input","gain","offset"], units=["N","1","N"],
        frame=d["channel"]["frame"], reference_values=[1.,1.,0.], method="synthetic_single_cancellation",
        basis={"kind":"parameter", "id":study.calibration_basis(req)}, provenance={"provider":"synthetic.test",
        "source_evidence_ids":[d["capture_ref"]], "source_covariance_ids":[]}, assumptions=["synthetic"])
    result = p.compare_link_affine(request)
    assert result["covariance"]["matrix"][0][0] == pytest.approx(1e-280, rel=1e-12, abs=0)
    assert result["mean_covariance"]["matrix"][0][0] == pytest.approx(1e-280, rel=1e-12, abs=0)


@pytest.mark.parametrize("kind", ["unknown", "missing", "constraint", "unit"])
def test_metrolopy_invalid_inputs(kind, monkeypatch):
    request = comparison()
    if kind == "unknown": request["uncertainty_state"] = "unknown"
    elif kind == "missing": request["study"]["calibration"]["joint_covariance"] = None
    elif kind == "constraint": request["constraints"]["kind"] = "sum_to_one"
    else:
        cov = request["study"]["calibration"]["joint_covariance"]; cov["units"][-1] = "m"
        cov["covariance_id"] = covariance_identity(cov)
    monkeypatch.setattr(p, "_identity", lambda *a: pytest.fail("provider accessed before validation"))
    with pytest.raises(ValueError): p.compare_link_affine(request)


def test_version_drift_refuses(monkeypatch):
    class Wrong:
        version = "999.0"
    monkeypatch.setattr(p.metadata, "distribution", lambda n: Wrong())
    with pytest.raises(p.ProviderRefusal, match="qualified pin"): p._identity("PyDynamic")


@pytest.mark.parametrize("operation,maker", [("pydynamic-fir", fir), ("metrolopy-link", comparison)])
def test_cli_retains_input_bytes_ref_and_will_not_overwrite(providers, tmp_path, operation, maker):
    source, output = tmp_path/"request.json", tmp_path/"result.json"
    raw = (json.dumps(maker(), indent=4)+"\n").encode(); source.write_bytes(raw)
    command = [sys.executable,"-m","ciw.uncertainty_providers",operation,str(source),"--output",str(output)]
    first = subprocess.run(command, capture_output=True, timeout=45)
    assert first.returncode == 0, first.stderr.decode()
    retained = output.read_bytes(); result = json.loads(retained)
    assert result["input_bytes_ref"] == c.bytes_ref(raw)
    assert source.read_bytes() == raw
    second = subprocess.run(command, capture_output=True, timeout=45)
    assert second.returncode != 0
    assert output.read_bytes() == retained


@pytest.mark.parametrize("mode", ["valid", "full"])
def test_fir_correlated_coefficients_and_longer_signal(providers, mode):
    request = fir(mode=mode)
    request["input"]["values"] = [1., -2., 3., .25, 2., -.5, 1.]
    request["input"]["quantity_ids"] = [f"sample.{i}" for i in range(7)]
    request["coefficients"]["values"] = [.25, .5, .25]
    request["coefficients"]["quantity_ids"] = [f"coefficient.{i}" for i in range(3)]
    request["sampling"]["ticks"] = list(range(100, 800, 100))
    xfactor = [[((i+2*j)%5-2)/64 for j in range(7)] for i in range(7)]
    hfactor = [[((i+j)%3-1)/128 for j in range(3)] for i in range(3)]
    matrix = [[0.]*10 for _ in range(10)]
    for offset, factor in [(0,xfactor),(7,hfactor)]:
        for i, row in enumerate(factor):
            for j, col in enumerate(factor):
                matrix[offset+i][offset+j] = sum(a*b for a,b in zip(row,col))
    request["uncertainty"]["joint_covariance"] = create_covariance_artifact(matrix=matrix,
        quantity_ids=request["input"]["quantity_ids"]+request["coefficients"]["quantity_ids"],units=["N"]*7+["1"]*3,
        frame="instrument",reference_values=request["input"]["values"]+request["coefficients"]["values"],method="synthetic_two_Gram_blocks",
        basis={"kind":"parameter","id":p.fir_basis(request)},provenance={"provider":"synthetic.test",
            "source_evidence_ids":[request["input"]["source_ref"],request["coefficients"]["provenance_ref"],request["sampling"]["evidence_ref"]],
            "source_covariance_ids":[]},assumptions=["synthetic","input_and_coefficients_independent"])
    result = p.pydynamic_fir(request)
    assert len(result["values"]) == (5 if mode=="valid" else 9)
    assert all(item["passed"] for item in result["checks"])


def test_no_ulp_tolerance_can_erase_tiny_positive_covariance():
    from fractions import Fraction
    with pytest.raises(p.ProviderRefusal): p._check(0., Fraction(1e-280), "tiny")
    with pytest.raises(p.ProviderRefusal): p._check(1e-280, Fraction(0), "zero")
    with pytest.raises(p.ProviderRefusal): p._number(2**53+1)
