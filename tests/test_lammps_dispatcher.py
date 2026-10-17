"""Required real-package seam test; not a native LAMMPS or admission test."""
from types import SimpleNamespace
import pytest
from execution.dispatcher import SpecificationDispatcher
from execution import lammps as lm


def test_lammps_result_is_consumed_by_the_existing_dispatcher():
    value=lm.example()
    spec=lm.specification(value,lm.VERSION_PREFIX+"EXPLICIT-TEST-DOUBLE","a"*64)
    seen=[]
    def synthetic_runner(s):
        seen.append(s)
        # Deliberate transport value; this is not a native engine result.
        return lm._result(s,"completed",0,b"synthetic scientific payload")
    dispatcher=SpecificationDispatcher(spec_for=lambda _:spec,
        interpret=lambda _,r:{"quantity":"test transport only","output":r.output.decode()},
        extracted_at="2026-10-05T00:00:00+00:00",runner=synthetic_runner)
    result=dispatcher.dispatch(SimpleNamespace(id="explicit-test-candidate"))
    assert seen==[spec]
    assert result.extraction_method.startswith("simulation:")
    assert result.record_locator.startswith("execution:")
    assert spec.identity() in result.record_raw_content
    assert result.content["output"]=="synthetic scientific payload"


def test_lammps_halt_cannot_become_a_dispatch_measurement():
    spec=lm.specification(lm.example(),lm.VERSION_PREFIX+"EXPLICIT-TEST-DOUBLE","a"*64)
    def interpret(*_): pytest.fail("halted execution must not be interpreted")
    dispatcher=SpecificationDispatcher(spec_for=lambda _:spec,interpret=interpret,
        extracted_at="2026-10-05T00:00:00+00:00",
        runner=lambda s:lm._result(s,"halted",10,detail="explicit test refusal"))
    with pytest.raises(RuntimeError,match="halted"):
        dispatcher.dispatch(SimpleNamespace(id="explicit-test-candidate"))
