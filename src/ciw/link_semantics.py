"""Link's existing scalar chain bound into NET's mathematical substrate.

No new solver, proof engine, operation registry, ledger or device authority.
Scientific signatures and preservation composition are declarations. The separate
unit-path witness checks exact affine algebra for fixed, declared coefficients;
it neither converts the retained measurements nor changes their uncertainty.
"""
from __future__ import annotations
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from . import link_contract as c, link_study as study, link_study_io as io
from .operations.runner import seal

def _review_record(kind, **fields):
    return seal({"schema": "ciw." + kind + ".v1", **fields})
from .link_profiles import manifest_view
from .semantic_capabilities import SemanticRegistry, SemanticMorphism, EngineLowering
from .representation_morphisms import registry_from_specs
from .preservation_contracts import contract_from_spec, compose_contracts
from . import representation_lab as algebra

# Quantity is part of the key. A Celsius temperature and a temperature DIFFERENCE
# have different affine origins. Equal dimensions do not imply equal semantics.
_UNIT_MAPS = {
    ("length", "mm"): ("m", "1/1000", "0"), ("length", "m"): ("m", "1", "0"),
    ("temperature", "degC"): ("K", "1", "5463/20"), ("temperature", "K"): ("K", "1", "0"),
    ("temperature_difference", "degC"): ("K", "1", "0"),
    ("temperature_difference", "K"): ("K", "1", "0"),
    ("pressure", "bar"): ("Pa", "100000", "0"), ("pressure", "kPa"): ("Pa", "1000", "0"),
    ("pressure", "MPa"): ("Pa", "1000000", "0"), ("pressure", "Pa"): ("Pa", "1", "0"),
    ("force", "N"): ("N", "1", "0"), ("acceleration", "m/s^2"): ("m/s^2", "1", "0"),
    ("voltage", "V"): ("V", "1", "0"), ("current", "A"): ("A", "1", "0"),
    ("mass", "g"): ("kg", "1/1000", "0"), ("mass", "kg"): ("kg", "1", "0"),
}
_COMMON = ("source-context", "raw-recovery", "parent-occurrences", "clock-domain", "uncertainty-ancestry")
_STAGES = ("source", "indicated", "corrected", "candidate", "audit")
_KINDS = ("TRANSFORM", "TRANSFORM", "ESTIMATE", "VERIFY")
_NOTES = (
    "Finite scalar grammar; binary64 values are not exact decimal-source recovery. Raw bytes remain retained.",
    "Declared post-report affine correction. Conditional first-order full joint covariance; not authenticated calibration.",
    "Arithmetic mean of finite corrected readings. Per-reading variation is lost in the scalar summary, retained in parents.",
    "Independent arithmetic comparison. Audit completion differs from PASS; neither establishes physical validity.",
)


def unit_path_witness(quantity, unit, gain, offset, *, proposed_target_operator=None):
    """Check U A = (U A U^-1) U with existing bounded 2x2 rational algebra.

    Coefficients follow the existing scalar kernel's Decimal(str(number)) rule.
    This is exact equality of these affine matrices, not a floating execution
    equivalence theorem, uncertainty conversion, measurement or calibration.
    """
    c.text(quantity); c.text(unit); c.finite(gain); c.finite(offset)
    if (quantity, unit) not in _UNIT_MAPS:
        raise ValueError("No registered quantity/unit expression rule")
    target, scale, origin = _UNIT_MAPS[(quantity, unit)]
    a = algebra.matrix([[str(Fraction(str(gain))), str(Fraction(str(offset)))], ["0", "1"]])
    u = algebra.matrix([[scale, origin], ["0", "1"]])
    target_a = algebra.mul(algebra.mul(u, a), algebra.inv(u))
    tested = target_a if proposed_target_operator is None else algebra.matrix(proposed_target_operator)
    if tested[1] != [Fraction(0), Fraction(1)]:
        raise ValueError("Proposed target operator must be an affine scalar map")
    direct, via = algebra.mul(u, a), algebra.mul(tested, u)
    return _review_record("link-affine-expression-witness", quantity=quantity, source_unit=unit, target_unit=target,
        source_operator=algebra.encode(a), unit_map=algebra.encode(u), target_operator=algebra.encode(target_a), tested_target_operator=algebra.encode(tested),
        candidate_supplied=proposed_target_operator is not None, status="PASS" if direct == via else "FAIL",
        direct=algebra.encode(direct), via_reexpression=algebra.encode(via), commutes=direct == via,
        naive_unchanged_operator_commutes=direct == algebra.mul(a, u),
        affine_map_invertible=algebra.det(a) != 0,
        numeric_contract="exact_rational_decimal_roundtrip_coefficients",
        scope="fixed_affine_matrix_identity_only",
        claims={"measurements_converted": False, "uncertainty_converted": False,
                "physical_validation": False, "general_functor_law_proved": False,
                "sp1_proof": False, "execution_authority": False})


def _property(name):
    return "link.property." + name + ".v1"


def _representation(name, channel, refs):
    return {"representation_id": "link.representation." + name + ".v1", "role": "OTHER" if name == "audit" else "OBSERVATION",
        "source_state_type": "retained_scalar_investigation", "schema_id": study.SCHEMA if name == "source" else "ciw.operation-result.v1",
        "quantity_semantics": channel["quantity"] if name != "audit" else "arithmetic_check_not_physical_quantity",
        "unit_semantics": channel["unit"] if name != "audit" else "not_applicable.audit_status",
        "frame_semantics": channel["frame"], "time_semantics": "original_receipt_and_declared_acquisition_domains_remain_separate",
        "scale": {"length_m": None, "time_s": None, "energy_j": None, "resolution": None, "label": "retained_declared_scalar"},
        "uncertainty_semantics": "full_joint_ancestry_or_explicit_unknown; no_new_budget",
        "equivalence_contract": "TASK_SPECIFIC", "preserved_queries": ["source_lineage"],
        "supported_interventions": [], "recovery_route": "original_link_study_request_and_parent_occurrences",
        "provenance_refs": refs, "notes": "Typed retained representation, not a canonical physical state."}


def _contracts(channel, refs):
    concrete = study.registry(bind=False)
    semantic = SemanticRegistry(concrete)
    for operation in study.OPS:
        contract = concrete.contract(operation)
        semantic.declare(SemanticMorphism(operation, "compute", contract["inputs"],
            {k: v["type"] for k, v in contract["outputs"].items()}, description="Existing Link scalar operation; no new implementation."))
        semantic.implement(EngineLowering(operation, operation, "ciw.link_study", "python", "scientific", "headless"))
    reps = [_representation(name, channel, refs) for name in _STAGES]
    specs = []
    for i, operation in enumerate(study.OPS):
        specs.append({"morphism_id": "link.morphism." + _STAGES[i+1] + ".v1", "kind": _KINDS[i],
            "domain_representation_id": reps[i]["representation_id"], "codomain_representation_id": reps[i+1]["representation_id"],
            "semantic_capability": operation, "parameter_names": list(concrete.contract(operation)["inputs"]),
            "preconditions": ["exact_source_and_parent_occurrence_bindings", "existing_operation_validator_passes"],
            "validity": {"assumptions": ["declared_single_channel_scalar_profile"],
                "operating_regime": ["finite_retained_recording"], "failure_conditions": ["source_flags_or_failed_prerequisite"]},
            "preservation": {"queries": ["source_lineage"], "interventions": [],
                "invariants": list(_COMMON), "approximation_tolerance": None},
            "loss": {"class": "TASK_SPECIFIC", "description": _NOTES[i], "metrics": {}},
            "uncertainty": {"behavior": "PROPAGATE" if i in (1, 2) else "UNKNOWN",
                            "method": "existing_full_joint_covariance" if i in (1, 2) else None},
            "reversibility": "UNKNOWN" if i == 1 else "NONE", "authority_requirements": ["separate_host_execution_permission"],
            "verification_requirements": ["existing_independent_arithmetic_audit", "separate_physical_qualification"],
            "provenance_refs": refs, "notes": _NOTES[i]})
    registry = registry_from_specs(reps, specs, semantic)
    contracts = []
    for i, spec in enumerate(specs):
        common = [{"property_id": _property(p), "effect": "PRESERVE", "output_property_id": _property(p),
                   "transform_id": None, "bound": None, "notes": "Preservation obligation through retained parent references, not duplicated values."}
                  for p in _COMMON]
        contracts.append(contract_from_spec(registry, semantic, {
            "contract_id": "link.preservation." + _STAGES[i+1] + ".v1", "morphism_id": spec["morphism_id"],
            "requires": [_property(p) for p in _COMMON] + [_property(_STAGES[i])],
            "effects": common + [{"property_id": _property(_STAGES[i]), "effect": "TRANSFORM",
                "output_property_id": _property(_STAGES[i+1]), "transform_id": spec["morphism_id"],
                "bound": None, "notes": _NOTES[i]}],
            "notes": "Contract obligations are not evidence that execution or physical qualification occurred."}))
    compositions = [compose_contracts(registry, semantic, contracts[i], contracts[i+1]) for i in range(3)]
    return semantic, registry, contracts, compositions


def review(directory, *, expected_request_ref):
    """Describe the actual retained chain, including failures, with no new run.

    Uses the same reader/Session validators as inspection. Existing bytes and
    scientific occurrences are never edited; a review is not an execution grant.
    """
    c.content_ref(expected_request_ref)
    request, raw, session, graph = io.read(directory, expected_request_ref=expected_request_ref)
    device = request["capture"]["manifest"]
    channel = c.channel_for(device, request["channel_id"])
    refs = [c.bytes_ref(raw), c.content_identity(device)]
    semantic, registry, contracts, compositions = _contracts(channel, refs)
    results = {value["operation_id"]: value for value in session.results.values()}
    executions = {value["operation_id"]: value for value in session.executions.values()}
    stages = []
    for i, operation in enumerate(study.OPS):
        execution, result = executions.get(operation), results.get(operation)
        stages.append({"operation_id": operation, "morphism_ref": registry["morphisms"]["link.morphism."+_STAGES[i+1]+".v1"]["record_digest"],
            "execution_id": None if execution is None else execution["execution_id"],
            "result_id": None if result is None else result["result_id"],
            "status": "NOT_EXECUTED" if execution is None else execution["status"],
            "runtime": None if execution is None else execution["runtime"]})
    audit = results.get(study.OPS[-1], {}).get("data", {}).get("status", "NOT_RUN")
    try:
        witness = {"status": "EVALUATED", "record": unit_path_witness(channel["quantity"], channel["unit"],
            request["calibration"]["gain"], request["calibration"]["offset"])}
    except ValueError as exc:
        witness = {"status": "UNRESOLVED", "reason": str(exc), "record": None}
    # Manifest is already canonical data inside the exact retained request. Do not
    # pretend these bytes are the originally supplied standalone manifest file.
    encoded_manifest = c.bounded(device, 65536)
    scope = {"hardware_discovery": False, "new_scientific_execution": False,
             "state_admission": False, "physical_qualification": "HELD",
             "machine_control": False, "sp1_proof": False, "general_functor_law_proved": False}
    from . import link_profiles, semantic_capabilities, representation_morphisms, preservation_contracts, control_contracts
    from .operations import runner
    from .core import identities
    modules = (c, study, io, algebra, link_profiles, semantic_capabilities,
               representation_morphisms, preservation_contracts, control_contracts, runner, identities)
    code_ref = "sha256:" + sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n") +
        b"".join(Path(m.__file__).read_bytes().replace(b"\r\n", b"\n") for m in modules)).hexdigest()
    return _review_record("link-semantic-review", application="Link", request_ref=refs[0], evidence_id=session.run["evidence_id"],
        graph_ref=graph["record_digest"], reviewer_source_ref=code_ref,
        manifest_view=manifest_view(encoded_manifest, expected_ref=c.bytes_ref(encoded_manifest)),
        manifest_bytes_scope="canonical_embedded_manifest_not_original_standalone_bytes",
        semantic_catalog=semantic.catalog(), morphism_registry=registry, preservation_contracts=contracts,
        compositions=compositions, original_stages=stages,
        arithmetic_audit=audit, unit_expression_witness=witness,
        scope=scope)


def verify_review(value, directory, *, expected_request_ref):
    """Reconstruct from retained source, not trusted PASS strings in a report."""
    c.bounded(value)
    expected = review(directory, expected_request_ref=expected_request_ref)
    if c.canonical_json(value) != c.canonical_json(expected):
        raise ValueError("Link semantic review differs from retained evidence and current contracts")
    return expected
