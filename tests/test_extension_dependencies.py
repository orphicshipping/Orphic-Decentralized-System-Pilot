"""Generic trusted extension seam; no private provider source or binary required."""
from copy import deepcopy
import uuid
import pytest
from ciw.operations import schemas
from ciw.operations.registry import Operation, default_registry
from ciw.operations.runner import seal
from ciw.polymer_workflow import make_source, _execute
from ciw.polymer_contract import example_request
from ciw.session import Session

@pytest.fixture
def extension(tmp_path):
    suffix=uuid.uuid4().hex
    parent=f"test.source{suffix}.v1";child=f"test.child{suffix}.v1"
    def validate(op,data,source,parameters,selection):
        assert data=={"declared":True}
    schemas.register_payload_validator(parent,validate)
    schemas.register_payload_validator(child,validate,role="verification")
    schemas.register_dependency_fields(child,("candidate",))
    calls=[];registry=default_registry()
    registry.register(Operation(parent,"backend",lambda *args:{"declared":True},lambda:{"provider":"test"}))
    def invoke(*args):calls.append(True);return {"declared":True}
    registry.register(Operation(child,"verification",invoke,lambda:{"provider":"test"}))
    session=Session(make_source(example_request()),tmp_path,operations=registry)
    return parent,child,session,calls


def test_live_exact_dependency_and_saved_reopen(extension,tmp_path):
    parent,child,session,calls=extension
    p=_execute(session,parent,{})["result"]
    assert _execute(session,child,{"candidate":p})["status"]=="completed"
    assert len(calls)==1
    session.save_workspace(tmp_path/"workspace.json")
    reloaded=Session.from_workspace(tmp_path/"workspace.json",tmp_path/"inspection")
    assert reloaded.results==session.results
    assert calls==[True]

@pytest.mark.parametrize("fault",["missing","null","wrong_type","unretained_id","changed_occurrence","changed_data"])
def test_invalid_dependencies_refuse_before_execution(extension,fault):
    parent,child,session,calls=extension
    p=_execute(session,parent,{})["result"];params={"candidate":deepcopy(p)}
    if fault=="missing":params={}
    elif fault=="null":params["candidate"]=None
    elif fault=="wrong_type":params["candidate"]=[]
    elif fault=="unretained_id":params["candidate"]["result_id"]="result-"+"0"*32
    elif fault=="changed_occurrence":params["candidate"]["execution_id"]="execution-"+"0"*32
    elif fault=="changed_data":params["candidate"]["data"]={"declared":False};seal(params["candidate"])
    assert _execute(session,child,params)["status"]=="refused"
    assert not calls

@pytest.mark.parametrize("fields",[(),("a","a"),("bad field",),["candidate"],("a",)*9,(1,)])
def test_invalid_registration(extension,fields):
    parent,_,_,_=extension
    with pytest.raises(ValueError):schemas.register_dependency_fields(parent,fields)


def test_no_builtin_override():
    with pytest.raises(ValueError):schemas.register_dependency_fields("statistics.v1",("candidate",))


def test_no_registration_from_unknown_schema():
    with pytest.raises(ValueError):schemas.register_dependency_fields("test.unbound.v1",("candidate",))


def test_no_duplicate_binding(extension):
    _,child,_,_=extension
    with pytest.raises(ValueError):schemas.register_dependency_fields(child,("candidate",))


def test_saved_dependency_missing_or_conflicting(extension):
    parent,child,s,_=extension
    p=_execute(s,parent,{})["result"];q=_execute(s,child,{"candidate":p})["result"]
    with pytest.raises(ValueError):schemas.validate_extension_result_dependencies({q["result_id"]:q})
    bad=deepcopy(p);bad["evidence_id"]="sha256:"+"0"*64
    with pytest.raises(ValueError):schemas.validate_extension_result_dependencies({p["result_id"]:bad,q["result_id"]:q})


def test_saved_cross_source_is_refused(extension):
    parent,child,s,_=extension
    p=_execute(s,parent,{})["result"];q=_execute(s,child,{"candidate":p})["result"]
    q["evidence_id"]="sha256:"+"0"*64
    with pytest.raises(ValueError,match="different source"):
        schemas.validate_extension_result_dependencies({p["result_id"]:p,q["result_id"]:q})


def test_builtin_dependencies_unchanged():
    p={"candidate":{"result_id":"r"},"assessment":{"result_id":"a"},"processing":{"result_id":"p"}}
    assert schemas.dependency_result_ids("system.verify.v1",p)==["r"]
    assert schemas.dependency_result_ids("polymer.verify-cycle.v1",p)==["a"]
    assert schemas.dependency_result_ids("metrology.temperature.verify.v1",p)==["p"]
    assert schemas.dependency_result_ids("statistics.v1",p)==[]


def test_optional_domain_help_without_provider(monkeypatch):
    import builtins
    from ciw.domain_cli import main
    original=builtins.__import__
    def deny(name,*args,**kwargs):
        if name.startswith("notation_domains"):
            raise ModuleNotFoundError("not installed",name="notation_domains")
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",deny)
    assert main(["--help"])==0
    assert main(["demo"])==2


def test_live_cross_source_refuses_before_runtime_lookup(extension, tmp_path):
    from ciw.operations.runner import execute
    parent, child, original, calls = extension
    prerequisite = _execute(original, parent, {})["result"]
    other_request = example_request()
    other_request["identity"]["cycle_id"] = "synthetic.other-source"
    other = Session(make_source(other_request), tmp_path / "other")
    runtime_calls = []
    registry = default_registry()
    registry.register(Operation(child, "verification", lambda *args: calls.append(True) or {"declared": True},
                                lambda: runtime_calls.append(True) or {"provider": "test"}))
    assert prerequisite["evidence_id"] != other.run["evidence_id"]
    attempt, result = execute(registry, other.run, other.selection, "source.json", child,
                              {"candidate": prerequisite},
                              retained_results={prerequisite["result_id"]: prerequisite})
    assert attempt["status"] == "refused"
    assert "different source" in attempt["refusal"]["message"]
    assert result is None and not calls and not runtime_calls
    assert attempt["runtime"] is None


@pytest.mark.parametrize("fault", ["boolean_revision", "integer_for_boolean"])
def test_python_equal_is_not_exact_retained_json(extension, fault):
    parent, child, session, calls = extension
    prerequisite = _execute(session, parent, {})["result"]
    altered = deepcopy(prerequisite)
    if fault == "boolean_revision":
        assert altered["selection_revision"] == 0
        altered["selection_revision"] = False
    else:
        altered["data"]["declared"] = 1
    assert altered == prerequisite  # Python equality collapses distinct JSON types.
    reply = _execute(session, child, {"candidate": altered})
    assert reply["status"] == "refused"
    assert not calls


@pytest.mark.parametrize("fault", ["boolean_revision", "integer_for_boolean"])
def test_saved_python_equal_alias_is_refused(extension, fault):
    parent, child, session, _ = extension
    prerequisite = _execute(session, parent, {})["result"]
    dependent = _execute(session, child, {"candidate": prerequisite})["result"]
    altered = deepcopy(dependent)
    candidate = altered["parameters"]["candidate"]
    if fault == "boolean_revision":
        candidate["selection_revision"] = False
    else:
        candidate["data"]["declared"] = 1
    seal(altered)
    with pytest.raises(ValueError, match="exact retained"):
        schemas.validate_extension_result_dependencies({prerequisite["result_id"]: prerequisite,
                                                         altered["result_id"]: altered})


def test_extension_live_check_requires_source_context(extension):
    parent, child, session, _ = extension
    prerequisite = _execute(session, parent, {})["result"]
    with pytest.raises(ValueError, match="source evidence context"):
        schemas.validate_request_dependencies(child, {"candidate": prerequisite}, session.results)


def test_builtin_dependency_call_remains_backward_compatible():
    schemas.validate_request_dependencies("statistics.v1", {}, {})


@pytest.mark.parametrize("fault", ["boolean_revision", "integer_for_boolean"])
def test_saved_execution_copy_cannot_alias_the_result(extension, tmp_path, fault):
    parent, child, session, calls = extension
    prerequisite = _execute(session, parent, {})["result"]
    dependent = _execute(session, child, {"candidate": prerequisite})["result"]
    path = session.save_workspace(tmp_path / "workspace.json")
    import json
    saved = json.loads(path.read_text())
    execution = next(x for x in saved["executions"] if x["execution_id"] == dependent["execution_id"])
    if fault == "boolean_revision":
        execution["parameters"]["candidate"]["selection_revision"] = False
    else:
        execution["parameters"]["candidate"]["data"]["declared"] = 1
    assert execution["parameters"] == dependent["parameters"]
    seal(execution)
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="Execution/result parameters binding mismatch"):
        Session.from_workspace(path, tmp_path / "reopened")
    assert calls == [True]
