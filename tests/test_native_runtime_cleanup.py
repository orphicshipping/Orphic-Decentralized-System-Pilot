"""All three pinned profiles preserve an uncertain runtime hold across facades."""
from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest
from ciw.adapters import subprocess as runner
from ciw.principal_policy import PrincipalPolicy, PrincipalOutcomeUnknown
from ciw.services import invoke


@pytest.mark.parametrize("family,environment", [
    ("recompiler", "CIW_NSR_CHECKOUT"), ("schematic", "CIW_SRA_CHECKOUT"),
    ("clocksync", "CIW_CLOCKSYNC_CHECKOUT"),
])
def test_uncertain_binding_never_restarts_children_after_pipe_cleanup_failure(family, environment, monkeypatch):
    selected = os.environ.get(environment)
    if not selected:
        pytest.skip(environment + " must provide the approved pinned checkout")
    if family == "recompiler":
        from ciw import recompiler_profile as profile
        from test_recompiler_profile import INPUT
        host_type, name, error = profile.RecompilerHost, "ncp_recompiler_reconcile", profile.RecompilerRefusal
    elif family == "schematic":
        from ciw import schematic_profile as profile
        from test_schematic_profile import SOURCE as INPUT
        host_type, name, error = profile.SchematicHost, "ncp_schematic_assess", profile.SchematicRefusal
    else:
        from ciw import clocksync_profile as profile
        from test_clocksync_profile import INPUT
        host_type, name, error = profile.ClockSyncHost, "ncp_clocksync_reconcile", profile.ClockSyncRefusal
    interpreter = {"python" if family == "schematic" else "python_executable": sys.executable}
    host = host_type(checkout=Path(selected).absolute(), principal="fixture-owner", **interpreter)
    try:
        policy = {"schema": "ciw.principal-policy.v1", "revision": "uncertain-runtime-fixture",
            "principals": [{"principal": "fixture-owner", "enabled": True,
                "operations": [{"name": tool, "constraints": {}} for tool in profile.TOOLS],
                "scientific_operations": [profile.OPERATION], "resources": [],
                "reference_scopes": [], "handoff_recipients": []}]}
        arguments = {"request": deepcopy(INPUT)}
        facade = PrincipalPolicy(lambda: policy).facade("fixture-owner", **{family: host})
        uncertainty = runner.RuntimeCleanupUncertain()
        attempts = []
        def unresolved(*args, **kwargs):
            attempts.append(args)
            raise uncertainty
        with monkeypatch.context() as patch:
            patch.setattr(runner, "_bounded_process", unresolved)
            response = facade.call(name, arguments)
        assert response["status"] == "refused" and response["reason"] == "runtime_cleanup_uncertain"
        assert len(attempts) == 1
        with monkeypatch.context() as patch:
            patch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("held binding launched another child"))
            for _ in range(2):
                with pytest.raises(error, match="runtime_cleanup_uncertain"):
                    invoke(name, arguments, host=host)
                for active in (facade, PrincipalPolicy(lambda: policy).facade("fixture-owner", **{family: host})):
                    response = active.call(name, arguments)
                    assert response["status"] == "refused" and response["reason"] == "runtime_cleanup_uncertain"
                    with pytest.raises(PrincipalOutcomeUnknown, match="outcome_unknown"):
                        invoke(name, arguments, host=active)
                with pytest.raises(runner.RuntimeCleanupUncertain):
                    host._adapter.runtime_identity()
        assert len(attempts) == 1
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            close()
