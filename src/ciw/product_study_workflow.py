"""Product study reviews consume existing Workbench data and Session occurrences."""
from copy import deepcopy

from .control_contracts import keys
from .core.identities import content_identity
from .data_sources import native_record, descriptor
from .operations.registry import Operation
from . import product_study

REVIEW = "data.review-product-study.v1"


from .data_runtime import product_study_identity as runtime_identity


def _record(source):
    if type(source) is not dict or source.get("kind") != "data-record":
        raise ValueError("Product review requires an exact retained Workbench data-record")
    record = product_study.validate(native_record(source))
    if descriptor(source)["dataset_id"] != record["identity"]["variant_id"]:
        raise ValueError("Each product variant requires its own matching dataset identity")
    return record


def _review(parameters):
    keys(parameters, {"source"})
    return {"source_id": parameters["source"]["source_id"], **product_study.review(_record(parameters["source"]))}


def operation():
    return Operation(REVIEW, "backend", lambda run, parameters: _review(parameters), runtime_identity)


def parameters(workbench, source_id):
    return {"source": deepcopy(workbench.get_source(source_id))}


def validate_payload(operation_id, data, run, parameters, selection):
    if operation_id != REVIEW or content_identity(data) != content_identity(_review(parameters)):
        raise ValueError("Product review differs from its exact retained study and residual holds")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id != REVIEW:
        return
    keys(parameters, {"source"})
    source = parameters["source"]
    if (type(source) is not dict or type(source.get("source_id")) is not str
            or source["source_id"] not in sources
            or content_identity(sources[source["source_id"]]) != content_identity(source)):
        raise ValueError("Product review requires the unchanged study source in this Workbench")
    _record(source)


def validate_saved_dependencies(results, sources):
    for result in results.values():
        if result["operation_id"] == REVIEW:
            validate_live_dependencies(REVIEW, result["parameters"], sources)
