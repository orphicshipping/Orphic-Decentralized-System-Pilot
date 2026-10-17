"""Fixed-grid identifiability, held-out prediction, covariance and retained audits."""
import copy
import json
import math
import subprocess
import sys

import numpy as np
import pytest
# Generic polymer CI need not install the optional numerical engine. The
# dedicated scientific matrix installs numerical-checks and must run all cases.
pytest.importorskip("scipy", reason="fixed-grid identification requires numerical-checks")
from scipy.optimize import nnls

from ciw import polymer_rheology_identification as r
from ciw import polymer_rheology as forward
from ciw.core.covariance import covariance_identity
from ciw.core.identities import content_identity
from ciw.operations.runner import seal, check_seal


def bind(p):
    """Update declared evidence after an intentional test-data transformation."""
    p["source_ref"] = content_identity({"samples": p["samples"], "synthetic": True})
    cov = p["covariance"]
    cov["quantity_ids"] = [x["sample_id"] for x in p["samples"]]
    cov["units"] = ["Pa"] * len(p["samples"])
    cov["reference_values"] = [x["value_pa"] for x in p["samples"]]
    cov["basis"]["id"] = p["source_ref"]
    cov["provenance"]["source_evidence_ids"] = [p["source_ref"]]
    cov["covariance_id"] = covariance_identity(cov)
    return p


def independent_design(p):
    # Complex harmonic response, unlike the production reciprocal branches.
    basis = []
    for row in p["samples"]:
        q, x = row["quantity"], row["coordinate"]
        result = [0.0 if q == "loss_modulus_pa" else 1.0]
        for tau in p["relaxation_times_s"]:
            if q == "relaxation_modulus_pa":
                result.append(math.exp(-x / tau))
            else:
                v = (1j * x * tau) / (1 + 1j * x * tau)
                result.append(v.real if q == "storage_modulus_pa" else v.imag)
        basis.append(result)
    return np.array(basis)


@pytest.fixture
def request_data():
    return r.example_request()


@pytest.fixture(scope="module")
def baseline():
    return r.identify(r.example_request())


def test_recovers_known_spectrum_and_reuses_existing_forward_tool(request_data):
    untouched = copy.deepcopy(request_data)
    result = r.identify(request_data)
    assert request_data == untouched
    assert result["coefficients_pa"] == pytest.approx([1e6, 2e6, 3e6], rel=2e-13)
    assert result["holdout_status"] == "WITHIN_DECLARED_LIMIT"
    assert result["candidate_export_refusal"] is None
    assert r.verify(result)["status"] == "PASS"
    response = forward.analyze(result["candidate_response_request"])
    assert forward.verify(response)["status"] == "PASS"
    assert response["authority"]["physical_validation"] is False
    assert response["request"]["material_id"] == request_data["material_id"]


@pytest.mark.parametrize("seed", range(16))
def test_correlated_noisy_fits_match_independent_nnls(seed, request_data):
    rng = np.random.default_rng(seed)
    p = request_data
    truth = rng.uniform(1e4, 9e7, 3)
    a = independent_design(p)
    n = len(a)
    covariance = 1e4 * np.fromfunction(lambda i, j: .65 ** np.abs(i-j), (n, n))
    y = a @ truth + np.linalg.cholesky(covariance) @ rng.normal(size=n)
    for row, value in zip(p["samples"], y):
        row["value_pa"] = float(value)
    p["covariance"]["matrix"] = covariance.tolist()
    bind(p)
    result = r.identify(p)
    train = [i for i, row in enumerate(p["samples"]) if row["partition"] == "train"]
    lower = np.linalg.cholesky(covariance[np.ix_(train, train)])
    reference, _ = nnls(np.linalg.solve(lower, a[train]), np.linalg.solve(lower, y[train]))
    assert result["coefficients_pa"] == pytest.approx(reference, rel=1e-9)
    assert r.verify(result)["status"] == "PASS"
    assert result["request"]["covariance"]["matrix"] == covariance.tolist()


@pytest.mark.parametrize("factor", [1e-12, 1e-6, 1, 1e3, 1e6])
def test_common_covariance_scaling_cannot_change_coefficients(factor, request_data, baseline):
    request_data["covariance"]["matrix"] = (np.array(request_data["covariance"]["matrix"]) * factor).tolist()
    bind(request_data)
    result = r.identify(request_data)
    assert result["coefficients_pa"] == pytest.approx(baseline["coefficients_pa"], rel=1e-12)


@pytest.mark.parametrize("factor", [1e-9, 1e-3, 1e3, 1e5])
def test_modulus_and_uncertainty_unit_scaling_is_consistent(factor, request_data):
    for row in request_data["samples"]:
        row["value_pa"] *= factor
    request_data["covariance"]["matrix"] = (np.array(request_data["covariance"]["matrix"]) * factor**2).tolist()
    bind(request_data)
    result = r.identify(request_data)
    assert result["coefficients_pa"] == pytest.approx(np.array([1e6, 2e6, 3e6])*factor, rel=1e-12)


def test_holdout_changed_without_refit(request_data, baseline):
    for row in request_data["samples"]:
        if row["partition"] == "holdout":
            row["value_pa"] += 500000
    bind(request_data)
    result = r.identify(request_data)
    assert result["coefficients_pa"] == baseline["coefficients_pa"]
    assert result["holdout_status"] == "OUTSIDE_DECLARED_LIMIT"
    assert result["metrics"]["holdout"]["max_abs_error_pa"] == pytest.approx(500000)
    assert r.verify(result)["status"] == "PASS"  # Truthful failure remains valid evidence.


def test_holdout_covariance_does_not_enter_fit(request_data, baseline):
    n = len(request_data["samples"])
    is_holdout = np.array([row["partition"] == "holdout" for row in request_data["samples"]])
    # Nonzero training/holdout covariance, without changing the training marginal.
    c = np.eye(n)*10000
    for i in range(n):
        for j in range(n):
            if is_holdout[i] != is_holdout[j]:
                c[i,j] = 50
    request_data["covariance"]["matrix"] = c.tolist()
    bind(request_data)
    assert r.identify(request_data)["coefficients_pa"] == baseline["coefficients_pa"]


def test_correlations_are_not_silently_discarded(request_data):
    p = request_data
    for i, row in enumerate(p["samples"]):
        row["value_pa"] += 80000 * math.sin(i)
    bind(p)
    diagonal = r.identify(p)
    n = len(p["samples"])
    p["covariance"]["matrix"] = (1e4*np.fromfunction(lambda i,j: .85 ** np.abs(i-j), (n,n))).tolist()
    bind(p)
    correlated = r.identify(p)
    assert not np.allclose(diagonal["coefficients_pa"], correlated["coefficients_pa"], rtol=1e-4)


@pytest.mark.parametrize("which", [0, 1, 2])
def test_negative_unconstrained_modulus_is_not_silently_kept(which, request_data):
    truth = np.array([1e6, 2e6, 3e6]); truth[which] *= -1
    for row, y in zip(request_data["samples"], independent_design(request_data) @ truth):
        row["value_pa"] = float(y)
    bind(request_data)
    result = r.identify(request_data)
    assert result["coefficients_pa"][which] == 0
    assert min(result["coefficients_pa"]) >= 0
    assert r.verify(result)["status"] == "PASS"


def test_zero_signal_retained_without_fabricated_stiffness(request_data):
    for row in request_data["samples"]:
        row["value_pa"] = 0.0
    bind(request_data)
    result = r.identify(request_data)
    assert result["coefficients_pa"] == [0,0,0]
    assert result["candidate_response_request"] is None
    assert result["candidate_export_refusal"] == "no-positive-stiffness"


def test_small_positive_mode_cannot_be_dropped_during_export(request_data):
    candidate, reason = r._candidate(request_data, [1.0, 1e-8, 0.0])
    assert candidate is None and reason == "positive-mode-below-existing-forward-envelope"


@pytest.mark.parametrize("scenario", ["same-times", "nearly-identical-taus", "loss-only"])
def test_unidentifiable_design_refused_before_optimizer(scenario, request_data, monkeypatch):
    if scenario == "nearly-identical-taus":
        request_data["relaxation_times_s"] = [1.0, math.nextafter(1.0, math.inf)]
    else:
        for row in request_data["samples"]:
            row.update(quantity="relaxation_modulus_pa", coordinate=0.0, coordinate_unit="s")
            if scenario == "loss-only":
                row.update(quantity="loss_modulus_pa", coordinate_unit="rad/s")
    def forbidden(*a, **kw):
        raise AssertionError("invalid design reached optimization")
    monkeypatch.setattr("scipy.optimize.lsq_linear", forbidden)
    with pytest.raises(ValueError, match="identify|rank-deficient|ill-conditioned"):
        r.identify(request_data)


@pytest.mark.parametrize("mutation", ["zero", "negative", "singular", "nonsymmetric", "wrong-frame", "wrong-order",
    "wrong-units", "wrong-source", "wrong-reference", "unknown", "bool", "underflow"])
def test_covariance_contract_refusals(mutation, request_data):
    p = request_data; cov = p["covariance"]
    if mutation == "zero": cov["matrix"][0][0] = 0
    elif mutation == "negative": cov["matrix"][0][0] = -1e-30
    elif mutation == "singular": cov["matrix"] = [[10000.0]*len(p["samples"]) for _ in p["samples"]]
    elif mutation == "nonsymmetric": cov["matrix"][0][1] = 1e-12
    elif mutation == "wrong-frame": cov["frame"] = "other"
    elif mutation == "wrong-order": cov["quantity_ids"].reverse()
    elif mutation == "wrong-units": cov["units"][0] = "MPa"
    elif mutation == "wrong-source": cov["basis"]["id"] = "other"
    elif mutation == "wrong-reference": cov["reference_values"][0] += 1
    elif mutation == "unknown": p["covariance"] = None
    elif mutation == "bool": cov["matrix"][0][0] = True
    elif mutation == "underflow": cov["matrix"][0][0] = 1e-30
    if p["covariance"] is not None:
        cov["covariance_id"] = covariance_identity(cov)
    with pytest.raises(ValueError): r.identify(p)


@pytest.mark.parametrize("mutation", ["extra", "schema", "model", "physical", "source-ref", "empty-grid", "grid-order",
    "duplicate-grid", "too-many-modes", "grid-bool", "duplicate-row", "unknown-partition", "no-holdout", "no-train",
    "quantity", "hz", "negative-coordinate", "tiny-coordinate", "nonfinite", "row-bool", "temperature", "limit-bool"])
def test_request_refusals(mutation, request_data):
    p = request_data
    if mutation == "extra": p["execute"] = True
    elif mutation == "schema": p["schema"] = "unversioned"
    elif mutation == "model": p["model"] = "shear-Maxwell"
    elif mutation == "physical": p["source_kind"] = "validated"
    elif mutation == "source-ref": p["source_ref"] = "not-a-hash"
    elif mutation == "empty-grid": p["relaxation_times_s"] = []
    elif mutation == "grid-order": p["relaxation_times_s"].reverse()
    elif mutation == "duplicate-grid": p["relaxation_times_s"] = [1,1]
    elif mutation == "too-many-modes": p["relaxation_times_s"] = list(range(1,10))
    elif mutation == "grid-bool": p["relaxation_times_s"][0] = True
    elif mutation == "duplicate-row": p["samples"][1]["sample_id"] = p["samples"][0]["sample_id"]
    elif mutation == "unknown-partition": p["samples"][0]["partition"] = "validate"
    elif mutation == "no-holdout":
        for row in p["samples"]: row["partition"] = "train"
    elif mutation == "no-train":
        for row in p["samples"]: row["partition"] = "holdout"
    elif mutation == "quantity": p["samples"][0]["quantity"] = "shear_modulus"
    elif mutation == "hz": p["samples"][-1]["coordinate_unit"] = "Hz"
    elif mutation == "negative-coordinate": p["samples"][0]["coordinate"] = -1
    elif mutation == "tiny-coordinate": p["samples"][0]["coordinate"] = 1e-30
    elif mutation == "nonfinite": p["samples"][0]["value_pa"] = float("inf")
    elif mutation == "row-bool": p["samples"][0]["value_pa"] = True
    elif mutation == "temperature": p["temperature_k"] = 0
    elif mutation == "limit-bool": p["holdout_max_abs_error_pa"] = False
    with pytest.raises(ValueError): r.identify(p)


@pytest.mark.parametrize("mutation", ["seal", "authority", "evidence", "identity", "coefficient-shape", "negative-coefficient",
    "prediction-shape", "prediction-bool", "counts", "rmse", "max-error", "holdout-status", "export", "solver", "runtime"])
def test_retained_shape_and_binding_refusals(mutation, baseline):
    value = copy.deepcopy(baseline)
    if mutation == "seal": value["coefficients_pa"][0] += 1
    elif mutation == "authority": value["authority"]["physical_validation"] = True
    elif mutation == "evidence": value["evidence_id"] = content_identity({})
    elif mutation == "identity": value["execution_id"] = value["result_id"]
    elif mutation == "coefficient-shape": value["coefficients_pa"].pop()
    elif mutation == "negative-coefficient": value["coefficients_pa"][0] = -1e-30
    elif mutation == "prediction-shape": value["predicted_modulus_pa"].pop()
    elif mutation == "prediction-bool": value["predicted_modulus_pa"][0] = True
    elif mutation == "counts": value["metrics"]["train"]["count"] = True
    elif mutation == "rmse": value["metrics"]["holdout"]["rmse_pa"] *= 2
    elif mutation == "max-error": value["metrics"]["train"]["max_abs_error_pa"] = 0
    elif mutation == "holdout-status": value["holdout_status"] = "RELEASE"
    elif mutation == "export": value["candidate_response_request"]["equilibrium_modulus_pa"] += 1
    elif mutation == "solver": value["solver"]["max_iter"] += 1
    elif mutation == "runtime": value["runtime"]["source_refs"].pop("ciw.core.covariance")
    if mutation != "seal": seal(value)
    with pytest.raises(ValueError): r.validate_result(value)


def test_coherently_forged_fit_cannot_pass_independent_stationarity(baseline):
    result = copy.deepcopy(baseline); p = result["request"]
    result["coefficients_pa"] = [x*1.1 for x in result["coefficients_pa"]]
    result["predicted_modulus_pa"] = r._predict(p,result["coefficients_pa"])
    result["metrics"] = r._metrics(p,result["predicted_modulus_pa"])
    result["holdout_status"] = "OUTSIDE_DECLARED_LIMIT"
    result["candidate_response_request"],result["candidate_export_refusal"] = r._candidate(p,result["coefficients_pa"])
    seal(result)
    assert r.validate_result(result) is result
    assert "training-kkt" in r.verify(result)["failures"]


def test_independent_audit_cannot_call_production_or_refit(baseline, monkeypatch):
    def forbidden(*a, **kw): raise AssertionError("production path called")
    for name in ("_basis", "_system", "_predict", "identify"):
        monkeypatch.setattr(r,name,forbidden)
    monkeypatch.setattr("scipy.optimize.lsq_linear",forbidden)
    monkeypatch.setattr(forward,"_compute",forbidden)
    assert r.verify(baseline)["status"] == "PASS"


def test_inspection_never_fits_or_predicts(baseline, monkeypatch):
    def forbidden(*a, **kw): raise AssertionError("numerical execution during inspection")
    for name in ("_basis", "_system", "_predict", "identify", "verify"):
        monkeypatch.setattr(r,name,forbidden)
    monkeypatch.setattr("ciw.polymer_rheology_identification_reference.audit_solution",forbidden)
    assert r.validate_result(baseline) is baseline


def test_evidence_execution_result_and_verification_are_distinct(request_data, baseline):
    result = r.identify(request_data)
    assert result["evidence_id"] == baseline["evidence_id"]
    assert result["execution_id"] != baseline["execution_id"] and result["result_id"] != baseline["result_id"]
    original = copy.deepcopy(result)
    one,two=r.verify(result),r.verify(result)
    assert one["verification_id"] != two["verification_id"]
    assert one["result_record_digest"] == result["record_digest"]
    assert result == original
    check_seal(result);check_seal(one)


def test_cli_end_to_end_and_create_only(tmp_path,capfd):
    from ciw.polymer_cli import main
    files = [tmp_path / (name+".json") for name in ("request","result","verification","forward","response","audit")]
    request,result,audit,export,response,forward_audit=files
    prefix=["rheology","identify"]
    assert main(prefix+["example","--output",str(request)]) == 0
    assert main(prefix+["run",str(request),"--output",str(result)]) == 0
    original=result.read_bytes()
    assert main(prefix+["inspect",str(result)]) == 0
    assert main(prefix+["verify",str(result),"--output",str(audit)]) == 0
    assert main(prefix+["export",str(result),"--output",str(export)]) == 0
    assert main(["rheology","analyze",str(export),"--output",str(response)]) == 0
    assert main(["rheology","verify",str(response),"--output",str(forward_audit)]) == 0
    assert main(prefix+["run",str(request),"--output",str(result)]) == 1
    assert result.read_bytes() == original
    assert json.loads(audit.read_text())["status"] == "PASS"


def test_cli_keeps_truthful_heldout_failure(tmp_path,capfd,request_data):
    from ciw.polymer_cli import main
    for row in request_data["samples"]:
        if row["partition"] == "holdout": row["value_pa"] += 1e6
    bind(request_data)
    source=tmp_path/"request.json"; output=tmp_path/"failure.json"; audit=tmp_path/"audit.json"
    source.write_text(json.dumps(request_data))
    assert main(["rheology","identify","run",str(source),"--output",str(output)]) == 2
    assert output.is_file()
    assert main(["rheology","identify","verify",str(output),"--output",str(audit)]) == 0
    assert json.loads(audit.read_text())["status"] == "PASS"


@pytest.mark.parametrize("raw", ['{"schema":1,"schema":2}', '[]', '{"x":NaN}', '['*100+']'*100],
                         ids=["duplicate-key","wrong-root","nan","nested"])
def test_cli_bad_input_refused(raw,tmp_path,capfd):
    from ciw.polymer_cli import main
    source=tmp_path/"bad.json";output=tmp_path/"output.json";source.write_text(raw)
    assert main(["rheology","identify","run",str(source),"--output",str(output)]) == 1
    assert not output.exists()


def test_optional_scipy_absence_is_explicit(request_data,monkeypatch):
    monkeypatch.setitem(sys.modules,"scipy",None)
    with pytest.raises(ValueError,match="numerical-checks"):
        r.identify(request_data)


def test_solver_failure_not_promoted(request_data,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("scipy.optimize.lsq_linear",lambda *a,**kw: SimpleNamespace(success=False))
    with pytest.raises(ValueError,match="solver"):
        r.identify(request_data)


def test_actual_module_cli_in_subprocess(tmp_path):
    path=tmp_path/"example.json"
    outcome=subprocess.run([sys.executable,"-m","ciw.polymer_rheology_identification_cli","example","--output",str(path)],
                           capture_output=True,text=True,check=False)
    assert outcome.returncode==0,outcome.stderr
    assert path.exists()


def test_maximum_size_and_eight_modes(request_data):
    p=request_data
    p["relaxation_times_s"]=np.geomspace(.01,100,8).tolist()
    rows=[]
    for i,x in enumerate(np.geomspace(.001,1000,128)):
        q=("relaxation_modulus_pa","storage_modulus_pa","loss_modulus_pa")[i%3]
        rows.append({"sample_id":f"bounded-{i}","quantity":q,"coordinate":float(x),
                     "coordinate_unit":r.QUANTITIES[q],"value_pa":0.,"partition":"holdout" if i%8==0 else "train"})
    p["samples"]=rows
    truth=np.array([1e5]+[1e6*(i+1) for i in range(8)])
    for row,y in zip(rows,independent_design(p)@truth):row["value_pa"]=float(y)
    p["covariance"]["matrix"]=(np.eye(128)*1e4).tolist()
    bind(p)
    result=r.identify(p)
    assert result["coefficients_pa"]==pytest.approx(truth,rel=1e-10)
    assert r.verify(result)["status"]=="PASS"


def test_sample_permutation_moves_covariance_axes_consistently(request_data,baseline):
    order=list(reversed(range(len(request_data["samples"]))))
    request_data["samples"]=[request_data["samples"][i] for i in order]
    request_data["covariance"]["matrix"]=np.array(request_data["covariance"]["matrix"])[np.ix_(order,order)].tolist()
    bind(request_data)
    assert r.identify(request_data)["coefficients_pa"]==pytest.approx(baseline["coefficients_pa"],rel=1e-12)


def test_rmse_roundoff_tolerance_never_changes_hard_holdout_limit(baseline):
    value=copy.deepcopy(baseline)
    for item in value["metrics"].values():item["rmse_pa"]=math.nextafter(item["rmse_pa"],math.inf)
    seal(value)
    assert r.validate_result(value) is value
    # Set the declared hard limit one float below the retained maximum error.
    value["request"]["holdout_max_abs_error_pa"]=math.nextafter(value["metrics"]["holdout"]["max_abs_error_pa"],0)
    value["evidence_id"]=content_identity(r.validate_request(value["request"]))
    value["candidate_response_request"],value["candidate_export_refusal"]=r._candidate(value["request"],value["coefficients_pa"])
    seal(value)
    with pytest.raises(ValueError,match="Holdout decision"):
        r.validate_result(value)


def test_resealed_prediction_forgery_fails_independent_forward_audit(baseline):
    value=copy.deepcopy(baseline)
    value["predicted_modulus_pa"][0]+=1000
    value["metrics"]=r._metrics(value["request"],value["predicted_modulus_pa"])
    seal(value)
    assert "prediction" in r.verify(value)["failures"]


def test_declared_condition_number_is_independently_checked(baseline):
    value=copy.deepcopy(baseline);value["solver"]["scaled_design_condition"]*=2;seal(value)
    assert "design-condition" in r.verify(value)["failures"]


def test_tiny_indefinite_covariance_not_repaired(request_data):
    n=len(request_data["samples"]); c=np.eye(n)*1e-20
    c[0,1]=c[1,0]=1e-20*(1+1e-12)
    request_data["covariance"]["matrix"]=c.tolist();bind(request_data)
    with pytest.raises(ValueError,match="positive-definite"):
        r.identify(request_data)


@pytest.mark.parametrize("failure",["system","optimizer","reference"])
def test_numerical_backend_failure_becomes_explicit_refusal(failure,request_data,monkeypatch):
    def broken(*a,**kw):raise np.linalg.LinAlgError("injected backend failure")
    if failure=="system":monkeypatch.setattr(r,"_system",broken)
    elif failure=="optimizer":monkeypatch.setattr("scipy.optimize.lsq_linear",broken)
    else:monkeypatch.setattr("ciw.polymer_rheology_identification_reference.audit_solution",broken)
    with pytest.raises(ValueError,match="linear algebra|numerically unsupported"):
        r.identify(request_data)


def test_atomic_output_failure_never_publishes_success(tmp_path,capfd,monkeypatch):
    import os
    from ciw.polymer_cli import main
    target=tmp_path/"not-published.json"
    def broken(*a,**kw):raise OSError("injected filesystem failure")
    monkeypatch.setattr(os,"link",broken)
    assert main(["rheology","identify","example","--output",str(target)])==1
    assert not target.exists()


def test_historical_source_hashes_remain_provenance_not_reexecution_trigger(baseline):
    value=copy.deepcopy(baseline)
    value["runtime"]["source_refs"]={k:"sha256:"+"0"*64 for k in value["runtime"]["source_refs"]}
    seal(value)
    assert r.validate_result(value) is value
    assert r.verify(value)["status"]=="PASS"
