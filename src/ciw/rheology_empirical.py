"""One source-pinned secondary empirical rheology investigation.

Historical NIST SRM2491 Table5c summaries, not native acquisition or current
calibration. Reuses materials_bench's JSON, digest and publication boundaries.
Unknown joint covariance remains unknown; no independence is manufactured.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import math
from pathlib import Path
import platform
import re
import uuid

from . import materials_bench as mb
from . import thermochemical_benchmark as tc

Refusal = tc.Refusal
REQUEST_SCHEMA = "ciw.rheology-empirical-request.v1"
RESULT_SCHEMA = "ciw.rheology-empirical-result.v1"
INVESTIGATION = "nist2491-table5c-newtonian-storage.v1"
OPERATION = "polymer.rheology.empirical-screen.v1"
CSV_SHA256 = "98909434425e89d838bd6688f54c6ba6ad81a05a34b4cb13de2d91becbfb86b2"
_HEADER = ["temperature_c", "omega_rad_s", "storage_pa", "expanded_storage_pa",
           "loss_pa", "expanded_loss_pa"]
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SCALES = {"Pa": 1.0, "kPa": 0.001}
AUTHORITY = {
    "source_kind": "published-experimental-summary",
    "native_measurement_ingress": False,
    "measurement_by_NET": False,
    "current_instrument_calibration": False,
    "parameter_fit": False,
    "independent_material_validation": False,
    "measurement_uncertainty_coverage_validated_by_NET": False,
    "joint_statistical_test": False,
    "state_admission": False,
    "hardware_actuation": False,
    "part_release": False,
}
# This reviewed source profile cannot be relabeled as raw or current evidence.
EXPECTED_SOURCE = {'publisher': 'National Institute of Standards and Technology',
 'document': 'SRM 2491 Certificate of Analysis',
 'url': 'https://tsapps.nist.gov/srmext/certificates/archives/2491.pdf',
 'issue_date': '2002-12-31',
 'certification_expiration': '2012-12-31',
 'table': '5c',
 'page_1_indexed': 10,
 'uncertainty_statement_page': 1,
 'method_page': 2,
 'material': 'NIST SRM 2491 polydimethylsiloxane',
 'data_kind': 'published-certified-summary-not-raw-acquisition',
 'source_measurement_time': None,
 'bottle_id': None,
 'rheometer_serial': None,
 'instrument_as_reported': 'Rheometric Scientific ARES controlled-strain rheometer',
 'geometry_as_reported': '50 mm parallel plates; nominal 1 mm gap',
 'strain_amplitude_as_reported': 0.02,
 'calibration_provenance': 'certificate-described; original calibration records not retained',
 'coverage_factor': 2.0,
 'publisher_stated_coverage': 0.95,
 'uncertainty_kind': 'expanded-combined',
 'joint_covariance': 'not-reported',
 'original_pdf_sha256': None,
 'original_pdf_retained': False,
 'capture_kind': 'assistant-transcription-checked-against-publisher-page-image',
 'capture_review_authenticated': False,
 'current_calibration_use_authorized': False}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise Refusal("Invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise Refusal("Timestamp must be timezone-aware UTC") from exc
    return parsed


def _uuid(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
        if type(value) is not str or str(parsed) != value or parsed.version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise Refusal("Invalid occurrence identity") from exc


def _hash(value: str) -> None:
    if type(value) is not str or not _HEX.fullmatch(value):
        raise Refusal("Invalid SHA256 identity")


def _exact(a, b, name: str) -> None:
    # Protect booleans, nulls, string labels and field sets as well as numbers.
    mb._equal(a, b, name, rtol=0.0, atol=0.0)


def validate_request(request: dict) -> dict:
    mb._guard_tree(request)
    tc._keys(request, {"schema", "investigation", "purpose", "selection",
                      "primary_omega_rad_s", "report_unit", "capture"}, "request")
    p = deepcopy(request)
    _exact(REQUEST_SCHEMA, p["schema"], "schema")
    _exact(INVESTIGATION, p["investigation"], "investigation")
    _exact("historical-secondary-model-screen", p["purpose"], "purpose")
    _exact("exploratory-not-preregistered", p["selection"], "selection")
    tc._number(p["primary_omega_rad_s"], 10.0, 10.0, "primary frequency")
    if type(p["report_unit"]) is not str or p["report_unit"] not in _SCALES:
        raise Refusal("Explicit Pa or kPa output unit required")
    cap = p["capture"]
    tc._keys(cap, {"source", "created_at", "csv_utf8", "csv_sha256"}, "capture")
    _exact(EXPECTED_SOURCE, cap["source"], "reviewed source profile")
    created = _timestamp(cap["created_at"])
    if created < datetime(2002, 12, 31, tzinfo=timezone.utc):
        raise Refusal("Capture precedes the declared certificate issue date")
    raw = cap["csv_utf8"]
    if type(raw) is not str or len(raw) > 4096:
        raise Refusal("Capture requires a bounded UTF-8 table transcription")
    _hash(cap["csv_sha256"])
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != cap["csv_sha256"]:
        raise Refusal("Captured table byte identity differs")
    if cap["csv_sha256"] != CSV_SHA256:
        raise Refusal("Table is not the reviewed source snapshot; new data needs review")
    # Normalize the permitted numeric input so its content identity is stable.
    p["primary_omega_rad_s"] = 10.0
    _read_rows(p)
    return p


def _read_rows(p: dict) -> list[list[str]]:
    try:
        data = list(csv.reader(io.StringIO(p["capture"]["csv_utf8"], newline=""), strict=True))
    except csv.Error as exc:
        raise Refusal("Invalid CSV capture") from exc
    if not data or data[0] != _HEADER or len(data) != 17:
        raise Refusal("Table header or complete row count differs")
    previous = -1.0
    for row in data[1:]:
        if len(row) != 6 or any(not _DECIMAL.fullmatch(value) for value in row):
            raise Refusal("Table contains non-decimal, missing or additional values")
        values = [float(value) for value in row]
        if (not all(math.isfinite(x) and x > 0 for x in values)
                or values[0] != 20 or values[1] <= previous):
            raise Refusal("Invalid source temperature, clock or positive measurement")
        previous = values[1]
    return data[1:]


def _condition(omega: float) -> dict:
    return {
        "temperature_k": 293.15,
        "omega_rad_s": omega,
        "strain_amplitude": 0.02,
        "geometry": "parallel-plate-50mm-nominal-gap-1mm",
        "frame": "simple-oscillatory-shear",
        "condition_basis": "certificate-reported-settings-not-native-achieved-trace",
        "temperature_standard_uncertainty_k": None,
        "omega_standard_uncertainty_rad_s": None,
    }


def _evidence(p: dict) -> dict:
    scientific_source = {"source": p["capture"]["source"], "csv_sha256": CSV_SHA256}
    source_id = mb.digest(scientific_source)
    records = []
    scale = _SCALES[p["report_unit"]]
    for row_number, row in enumerate(_read_rows(p), start=1):
        for quantity, y_index, u_index in (("shear-storage-modulus", 2, 3),
                                           ("shear-loss-modulus", 4, 5)):
            locator = {"page_1_indexed": 10, "table": "5c", "data_row_1_indexed": row_number,
                       "quantity": quantity}
            observation_id = mb.digest({"source_evidence_id": source_id, "locator": locator})
            value, expanded = float(row[y_index])*scale, float(row[u_index])*scale
            standard = expanded/2.0
            representation = {
                "observation_id": observation_id, "source_evidence_id": source_id,
                "subject": EXPECTED_SOURCE["material"], "quantity": quantity,
                "source_locator": locator, "source_value_pa": float(row[y_index]),
                "source_expanded_uncertainty_pa": float(row[u_index]),
                "unit": p["report_unit"], "value": value,
                "conditions": _condition(float(row[1])),
                "uncertainty": {
                    "kind": "published-combined-marginal",
                    "unit": p["report_unit"], "variance_unit": p["report_unit"]+"^2",
                    "expanded": expanded, "coverage_factor": 2.0,
                    "standard": standard, "variance": standard*standard,
                    "publisher_stated_coverage": 0.95,
                    "conversion": "u=U/k; variance=(U/k)^2; linear unit scaling",
                    "joint_covariance": "not-reported",
                    "additional_components": "not-invented-or-double-counted",
                },
            }
            records.append({**representation, "representation_id": mb.digest(representation)})
    return {
        "evidence_id": source_id,
        "source_snapshot_kind": "reviewed-table-transcription",
        "capture_created_at": p["capture"]["created_at"],
        "publisher_document_bytes_retained": False,
        "publisher_document_sha256": None,
        "measurement_time": None,
        "records": records,
    }


def _claim(record: dict) -> dict:
    # sigma=eta*gamma_dot implies G*=i*omega*eta, hence G'=0 for any eta.
    # No shear<->tensile transform and no fitting on the reference observations.
    return {
        "model": "purely-viscous-newtonian-zero-storage.v1",
        "subject": EXPECTED_SOURCE["material"],
        "quantity": "shear-storage-modulus", "conditions": deepcopy(record["conditions"]),
        "unit": record["unit"], "value": 0.0,
        "prediction_basis": "exact-constitutive-null-not-an-uncertain-fitted-estimate",
        "fitted_parameter_evidence": None,
    }


def compare_observation(request: dict, record: dict, claim: dict) -> dict:
    """One marginal comparison to an exact constitutive zero, not a joint test."""
    # Direct callers must cross the same evidence boundary as the workflow.
    # A self-resealed detached measurement is not an admissible replacement.
    p = validate_request(request)
    mb._guard_tree(record)
    mb._guard_tree(claim)
    if type(record) is not dict or type(record.get("observation_id")) is not str:
        raise Refusal("Comparison requires a source-bound observation")
    known = {r["observation_id"]: r for r in _evidence(p)["records"]}
    trusted = known.get(record["observation_id"])
    if trusted is None:
        raise Refusal("Observation does not belong to this investigation")
    _exact(trusted, record, "source-bound comparison observation")
    _exact(_claim(record), claim, "fixed Newtonian claim")
    if record["quantity"] != claim["quantity"]:
        raise Refusal("Shear storage, loss and tensile observables are not interchangeable")
    for field in ("subject", "unit", "conditions"):
        _exact(record[field], claim[field], "comparison "+field)
    u = record["uncertainty"]
    residual = record["value"] - claim["value"]
    lower, upper = residual-u["expanded"], residual+u["expanded"]
    return {
        "observation_id": record["observation_id"],
        "representation_id": record["representation_id"],
        "omega_rad_s": record["conditions"]["omega_rad_s"],
        "unit": record["unit"], "claim": claim, "residual": residual,
        "residual_standard_uncertainty": u["standard"],
        "residual_expanded_interval": [lower, upper],
        "outcome": "WITHIN_PUBLISHED_INTERVAL" if lower <= 0.0 <= upper
                   else "INCOMPATIBLE_WITH_PUBLISHED_INTERVAL",
        "scope": "one-marginal-interval-screen-not-material-or-model-certification",
        "p_value": None, "joint_confidence": None,
    }


def _comparisons(p: dict, evidence: dict) -> list[dict]:
    return [compare_observation(p, r, _claim(r)) for r in evidence["records"]
            if r["quantity"] == "shear-storage-modulus"]


def _holds() -> dict:
    return {
        "complex_viscosity_uncertainty": {
            "status": "HELD", "reason": "joint-Gprime-Gdoubleprime-and-frequency-uncertainty-not-reported",
            "value": None, "standard_uncertainty": None,
        },
        "joint_frequency_goodness_of_fit": {
            "status": "HELD", "reason": "between-frequency-covariance-not-reported",
            "statistic": None, "p_value": None,
        },
        "current_instrument_calibration": {
            "status": "HELD", "reason": "historical-certificate-expired-2012-12-31-and-no-native-acquisition",
        },
    }


def _runtime() -> dict:
    return {
        "python": platform.python_version(), "platform": platform.system(),
        "source_sha256": {
            "rheology_empirical.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "materials_bench.py": mb._source(mb),
            "thermochemical_benchmark.py": mb._source(tc),
        },
        "authenticated": False,
    }


def _seal_record(record: dict) -> dict:
    record["record_sha256"] = mb.digest({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def run(request: dict) -> dict:
    p = validate_request(request)
    started = _now()
    if _timestamp(p["capture"]["created_at"]) > _timestamp(started):
        raise Refusal("Cannot use a capture from the future")
    evidence = _evidence(p)
    comparisons = _comparisons(p, evidence)
    primary = next(c for c in comparisons if c["omega_rad_s"] == p["primary_omega_rad_s"])
    body = {
        "schema": RESULT_SCHEMA, "operation_id": OPERATION,
        "request": p, "request_sha256": mb.digest(p), "evidence": evidence,
        "comparisons": comparisons, "primary_observation_id": primary["observation_id"],
        "primary_outcome": primary["outcome"],
        "holds": _holds(), "authority": deepcopy(AUTHORITY), "runtime": _runtime(),
    }
    return _seal_record({
        **body, "content_sha256": mb.digest(body), "execution_id": str(uuid.uuid4()),
        "result_id": str(uuid.uuid4()), "executed_at": started,
    })


def inspect_result(report: dict) -> dict:
    """Retained integrity/source/normalization checks, not fresh claim comparison."""
    mb._guard_tree(report)
    tc._keys(report, {"schema", "operation_id", "request", "request_sha256", "evidence",
                      "comparisons", "primary_observation_id", "primary_outcome", "holds",
                      "authority", "runtime", "content_sha256", "execution_id", "result_id",
                      "executed_at", "record_sha256"}, "empirical result")
    _exact(RESULT_SCHEMA, report["schema"], "result schema")
    _exact(OPERATION, report["operation_id"], "operation identity")
    for key in ("execution_id", "result_id"):
        _uuid(report[key])
    if report["execution_id"] == report["result_id"]:
        raise Refusal("Execution and result identities must be distinct")
    for key in ("record_sha256", "content_sha256", "request_sha256", "primary_observation_id"):
        _hash(report[key])
    if mb.digest({k:v for k,v in report.items() if k != "record_sha256"}) != report["record_sha256"]:
        raise Refusal("Retained record integrity differs")
    excluded = {"record_sha256", "content_sha256", "execution_id", "result_id", "executed_at"}
    if mb.digest({k:v for k,v in report.items() if k not in excluded}) != report["content_sha256"]:
        raise Refusal("Scientific content identity differs")
    p = validate_request(report["request"])
    if mb.digest(p) != report["request_sha256"]:
        raise Refusal("Request binding differs")
    if _timestamp(report["executed_at"]) < _timestamp(p["capture"]["created_at"]):
        raise Refusal("Execution precedes the recorded capture")
    _exact(_evidence(p), report["evidence"], "evidence identity and normalization")
    _exact(AUTHORITY, report["authority"], "authority")
    _exact(_holds(), report["holds"], "held decisions")
    runtime = report["runtime"]
    tc._keys(runtime, {"python", "platform", "source_sha256", "authenticated"}, "runtime")
    _exact(False, runtime["authenticated"], "runtime authentication")
    for key in ("python", "platform"):
        if type(runtime[key]) is not str or not 1 <= len(runtime[key]) <= 128:
            raise Refusal("Invalid observed runtime label")
    tc._keys(runtime["source_sha256"], set(_runtime()["source_sha256"]), "source identity")
    for value in runtime["source_sha256"].values(): _hash(value)
    comparisons = report["comparisons"]
    if type(comparisons) is not list or len(comparisons) != 16:
        raise Refusal("Incomplete empirical comparison rows")
    records = [r for r in report["evidence"]["records"] if r["quantity"] == "shear-storage-modulus"]
    for record, c in zip(records, comparisons):
        tc._keys(c, {"observation_id", "representation_id", "omega_rad_s", "unit", "claim",
                    "residual", "residual_standard_uncertainty", "residual_expanded_interval",
                    "outcome", "scope", "p_value", "joint_confidence"}, "comparison")
        for key in ("observation_id", "representation_id", "unit"):
            _exact(record[key], c[key], "comparison binding "+key)
        _exact(record["conditions"]["omega_rad_s"], c["omega_rad_s"], "comparison frequency")
        _exact(_claim(record), c["claim"], "retained claim")
        tc._number(c["residual"], -1e6, 1e6, "residual")
        tc._number(c["residual_standard_uncertainty"], 0, 1e6, "residual uncertainty")
        if type(c["residual_expanded_interval"]) is not list or len(c["residual_expanded_interval"]) != 2:
            raise Refusal("Invalid residual interval")
        for value in c["residual_expanded_interval"]: tc._number(value, -1e6, 1e6, "interval")
        if c["residual_expanded_interval"][0] > c["residual_expanded_interval"][1]:
            raise Refusal("Reversed residual interval")
        if c["outcome"] not in ("WITHIN_PUBLISHED_INTERVAL", "INCOMPATIBLE_WITH_PUBLISHED_INTERVAL"):
            raise Refusal("Unrecognized comparison outcome")
        _exact("one-marginal-interval-screen-not-material-or-model-certification", c["scope"], "scope")
        _exact(None, c["p_value"], "unestablished p value")
        _exact(None, c["joint_confidence"], "unestablished joint confidence")
    primary = next(c for c in comparisons if c["omega_rad_s"] == 10.0)
    _exact(primary["observation_id"], report["primary_observation_id"], "primary binding")
    _exact(primary["outcome"], report["primary_outcome"], "primary outcome binding")
    return {
        "schema": "ciw.rheology-empirical-inspection.v1", "inspection_id": str(uuid.uuid4()),
        "result_id": report["result_id"], "record_sha256": report["record_sha256"],
        "evidence_id": report["evidence"]["evidence_id"],
        "status": "PASS", "claim_comparison_recomputed": False,
        "scope": "retained-integrity-source-pin-and-normalization-only",
        "source_matches_current": runtime["source_sha256"] == _runtime()["source_sha256"],
        "publisher_pdf_bytes_verified": False, "source_review_authenticated": False,
        "authority": deepcopy(AUTHORITY),
    }


def _oracle_close(expected: Decimal, actual, name: str) -> None:
    tc._number(actual, -1e12, 1e12, name)
    target = float(expected)
    if target == 0.0:
        if actual != 0.0: raise Refusal(name+": exact zero differs")
    elif abs(actual-target) > 8*max(math.ulp(target), math.ulp(float(actual))):
        raise Refusal(name+": independent Decimal result differs")


def verify_result(report: dict) -> dict:
    """Fresh independent Decimal arithmetic audit; no physical-coverage claim."""
    inspection = inspect_result(report)
    # Parse the retained decimal literals afresh. Do not call _evidence,
    # compare_observation or _comparisons for the oracle calculation.
    rows = list(csv.DictReader(io.StringIO(report["request"]["capture"]["csv_utf8"])))
    scale = Decimal(1) if report["request"]["report_unit"] == "Pa" else Decimal("0.001")
    with localcontext() as context:
        context.prec = 50
        for i, row in enumerate(rows):
            for j, (value_key, expanded_key) in enumerate((("storage_pa", "expanded_storage_pa"),
                                                          ("loss_pa", "expanded_loss_pa"))):
                record = report["evidence"]["records"][2*i+j]
                value = Decimal(row[value_key])*scale
                expanded = Decimal(row[expanded_key])*scale
                standard = expanded/Decimal(2)
                _oracle_close(value, record["value"], "value")
                for key, expected in (("expanded", expanded), ("standard", standard),
                                      ("variance", standard**2)):
                    _oracle_close(expected, record["uncertainty"][key], "uncertainty "+key)
                if j == 0:
                    comparison = report["comparisons"][i]
                    _oracle_close(value, comparison["residual"], "residual")
                    _oracle_close(standard, comparison["residual_standard_uncertainty"], "residual uncertainty")
                    lower, upper = value-expanded, value+expanded
                    _oracle_close(lower, comparison["residual_expanded_interval"][0], "lower interval")
                    _oracle_close(upper, comparison["residual_expanded_interval"][1], "upper interval")
                    outcome = ("WITHIN_PUBLISHED_INTERVAL" if lower <= 0 <= upper
                               else "INCOMPATIBLE_WITH_PUBLISHED_INTERVAL")
                    _exact(outcome, comparison["outcome"], "independent interval decision")
    return _seal_record({
        "schema": "ciw.rheology-empirical-verification.v1",
        "operation_id": "polymer.rheology.empirical-audit.v1",
        "execution_id": str(uuid.uuid4()), "verification_id": str(uuid.uuid4()),
        "verified_at": _now(), "result_id": report["result_id"],
        "result_record_sha256": report["record_sha256"],
        "evidence_id": inspection["evidence_id"], "status": "PASS",
        "method": "independent-50-digit-decimal-normalization-and-zero-storage-intervals",
        "observations_checked": 32, "marginal_comparisons_checked": 16,
        "joint_statistic": None, "publisher_pdf_bytes_verified": False,
        "source_review_authenticated": False, "runtime": _runtime(),
        "authority": deepcopy(AUTHORITY),
    })
