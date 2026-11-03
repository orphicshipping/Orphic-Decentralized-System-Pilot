"""Test-only installed-runtime preparation and actual adapter process launcher.

No credentials are written. Policy and scientific inputs are synthetic. The
adapter and MCP service come from the explicitly verified installed release.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys

ACTORS = ("panel-alpha", "panel-beta")
OWNER = "panel-local-fixture"
GENERATION = "panel-local-generation-1"


def write_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def verify(manifest):
    import ciw
    from ciw.private_release import verify_release
    module = Path(ciw.__file__).resolve()
    if not module.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError("fixture_requires_installed_package")
    return verify_release(manifest)


def prepare(args):
    from ciw.numerical_contracts import OPERATIONS
    from ciw.principal_policy import PrincipalPolicy
    release = verify(args.manifest)
    args.root.mkdir(parents=True, exist_ok=True)
    path = args.root / "policy.json"
    names = ("ncp_capabilities_list", "ncp_capability_describe", "ncp_integrations_list",
             "ncp_plan_template", "ncp_plan_create", "ncp_plan_get", "ncp_plan_admit",
             "ncp_run_submit", "ncp_run_get", "ncp_artifact_read", "ncp_observe")
    if args.mode == "initial":
        if path.exists():
            raise ValueError("fixture_policy_already_exists")
        policy = {"schema": "ciw.principal-policy.v1", "revision": "panel-fixture-1", "principals": [
            {"principal": actor, "enabled": True,
             "operations": [{"name": name, "constraints": {}} for name in names],
             "scientific_operations": list(OPERATIONS),
             "resources": ["ncp://plans/", "ncp://runs/", "ncp://artifacts/"],
             "reference_scopes": [], "handoff_recipients": []}
            for actor in ACTORS]}
    else:
        policy = json.loads(path.read_text())
        if args.mode != "inspect":
            generation = int(policy["revision"].rsplit("-", 1)[1]) + 1
            policy["revision"] = "panel-fixture-" + str(generation)
            policy["principals"][0]["enabled"] = args.mode == "enable-alpha"
    if args.mode != "inspect":
        write_json(path, policy)
    authority = PrincipalPolicy.from_file(path)
    context = authority.context(ACTORS[1])
    print(json.dumps({"release": release, "policy_path": str(path), "owner_id": OWNER,
                      "generation": GENERATION, "policy_revision": context["policy_revision"],
                      "policy_sha256": context["policy_sha256"],
                      "principals": [{"principal": row["principal"], "enabled": row["enabled"]}
                                     for row in policy["principals"]]}), flush=True)


def serve(args):
    verify(args.manifest)
    adapter_root = args.manifest.parent.parent / "tools/private-mcp-host"
    if not (adapter_root / "adapter.py").is_file():
        raise ValueError("verified_release_lacks_adapter")
    os.environ.update(NCP_TRANSACTION_STATE=str(args.root / "state"), ODSP_OWNER_ID=OWNER,
                      ODSP_CHECKPOINT_GENERATION=GENERATION,
                      ODSP_PRINCIPAL_POLICY=str(args.root / "policy.json"),
                      ODSP_RELEASE_MANIFEST=str(args.manifest))
    # No optional native provider or reference snapshot is selected by the
    # fixture browser or by inherited launch configuration.
    for name in tuple(os.environ):
        if name.startswith("ODRP_") or name in {"NCP_HOST_MANIFEST", "NCP_RETAIN_DIR", "NCP_RETAIN_OWNER"}:
            os.environ.pop(name, None)
    sys.path.insert(0, str(adapter_root))
    import uvicorn
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    print(json.dumps({"url": "http://127.0.0.1:" + str(listener.getsockname()[1]), "pid": os.getpid()}), flush=True)
    config = uvicorn.Config("adapter:app", access_log=False, log_level="warning", lifespan="on")
    uvicorn.Server(config).run(sockets=[listener])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mode", choices=("initial", "inspect", "disable-alpha", "enable-alpha"), default="initial")
    args = parser.parse_args()
    args.manifest, args.root = args.manifest.resolve(), args.root.resolve()
    if args.action == "prepare":
        prepare(args)
    else:
        serve(args)


if __name__ == "__main__":
    main()
