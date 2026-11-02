"""Every caller passes the one request policy in services.admit.

The coordinator used to check only the schema of its inner ncp_domain_run call.
An oversized ``request`` was admitted (job record and lease written, idempotency
key consumed, domain_run entered) and refused only deep inside domain_surface.
The ControlHost carried its own copy of the budget and schema checks without
the pin refusal. These tests hold
the MCP host, the SDK and the coordinator to the same refusals.
"""
import pytest

from ciw import coordinator
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.retention import Store
from ciw.services import admit, invoke

OVERSIZED = {"domain": "sequence", "request": {"blob": "x" * 70000}}


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("arguments, message", [
    (OVERSIZED, "argument budget"),
    ({"domain": "sequence", "checkout": "/tmp/other"}, "checkout or a pin"),
    ({"domain": "sequence", "owner": "someone-else"}, "impersonation"),
    ({"domain": "not-a-domain"}, "outside the declared set"),
])
def test_coordinator_refuses_what_the_host_refuses(tmp_path, monkeypatch, arguments, message):
    retain = tmp_path / "retain"
    monkeypatch.setenv("NCP_RETAIN_DIR", str(retain))
    with pytest.raises(ValueError, match=message):
        admit("ncp_domain_run", arguments)
    with pytest.raises(ValueError, match=message):
        invoke("ncp_domain_run", arguments)
    store = Store("policy-owner")
    before = coordinator._EXECUTIONS
    with pytest.raises(ValueError, match=message):
        coordinator.submit(store, "ncp_domain_run", arguments, "policy-key")
    assert coordinator._EXECUTIONS == before
    # Refused before admission: no job record, lease or failure artifact exists.
    assert [f for f in _files(retain) if not f.endswith(".lock")] == []


def test_host_refuses_the_oversized_coordinated_request(tmp_path):
    owner = from_profile(demo_config(tmp_path / "demo"))
    host = ControlHost(owner)
    try:
        direct = host.call("ncp_domain_run", OVERSIZED)
        nested = host.call("ncp_coordinate", {"action": "submit", "idempotency_key": "k-1",
                                               "operation": "ncp_domain_run", "arguments": OVERSIZED})
        assert direct["status"] == "refused"
        assert nested["status"] == "refused"
    finally:
        owner.close()


def test_admit_accepts_a_valid_request():
    admit("ncp_domain_run", {"domain": "sequence", "example": True})
