"""Installed-only public-client fixtures for check_private_host_qualification.

Imports of application code occur only in the selected isolated wheel runtime.
Fixture preparation uses the repository's existing public demo preparation; no
provider methods, execution results, transport responses, or stores are mocked.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


ACTORS = ("fixture-alpha", "fixture-beta", "fixture-gamma")
OPERATIONS = (
    "ncp_capabilities_list", "ncp_index_list", "ncp_plan_template", "ncp_plan_create",
    "ncp_plan_get", "ncp_plan_admit", "ncp_run_submit", "ncp_run_get", "ncp_run_cancel",
    "ncp_artifact_read", "ncp_domain_run", "ncp_retained_read", "ncp_retained_release", "ncp_coordinate",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Transcript(list):
    """Keep completed observations even when a later mandatory assertion fails."""
    def __init__(self, path):
        super().__init__()
        self.path = path

    def append(self, value):
        super().append(value)
        save(self.path, list(self))


def load_module(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def finish(args, checks, details):
    evidence_path = args.output_dir / "observations.json"
    save(evidence_path, details)
    evidence = {"path": evidence_path.name, "sha256": sha(evidence_path)}
    receipt = {"schema": "odsp.private-fixture-phase.v1", "phase": args.phase, "status": "PASS",
               "skipped": 0, "checks": [{"id": check, "status": "PASS", "evidence": [evidence]} for check in checks]}
    save(args.output_dir / "result.json", receipt)


def runtime(args):
    import ciw
    from ciw.private_release import verify_release
    binding = load_module("private_qualification_existing_binding", args.candidate_root / "scripts/check_domain_examples.py")
    installed = binding.installed_binding(args.wheel)
    require(not Path(ciw.__file__).resolve().is_relative_to(args.candidate_root.resolve()), "Source import refused")
    verified = verify_release(args.manifest)
    commit = subprocess.check_output(["git", "-C", str(args.candidate_root), "rev-parse", "HEAD"], text=True, timeout=10).strip()
    tree = subprocess.check_output(["git", "-C", str(args.candidate_root), "rev-parse", "HEAD^{tree}"], text=True, timeout=10).strip()
    require(verified["source_commit"] == commit and verified["source_tree"] == tree,
            "Installed release identity differs from the exact candidate source commit/tree")
    versions = {name: importlib.metadata.version(name) for name in
                ("mcp", "numpy", "websockets", "starlette", "uvicorn", "jsonschema", "httpx")}
    require(versions["mcp"] == "2.3.0", "Official client must use the existing pinned MCP SDK")
    finish(args, ["installed_wheel_bytes", "release_service_bytes", "source_wheel_identity", "isolated_outside_checkout", "required_dependencies"],
           {"installed_binding": installed, "release": verified, "dependencies": versions,
            "python": sys.version, "isolated": sys.flags.isolated, "cwd": str(Path.cwd())})


def fixture_policy():
    rules = [{"name": name, "constraints": {}} for name in OPERATIONS]
    for rule in rules:
        if rule["name"] == "ncp_domain_run":
            rule["constraints"] = {"domain": ["sequence"]}
        if rule["name"] == "ncp_coordinate":
            rule["constraints"] = {"action": ["submit", "recall", "handoff", "claim", "recover"]}
    return {"schema": "ciw.principal-policy.v1", "revision": "fixture-1", "principals": [
        {"principal": actor, "enabled": True, "operations": deepcopy(rules),
         "scientific_operations": ["statistics.v1", "sequence.markov.v1"],
         "resources": ["ncp://retained/", "ncp://runs/", "ncp://plans/", "ncp://artifacts/"],
         "reference_scopes": [], "handoff_recipients": [other for other in ACTORS if other != actor]}
        for actor in ACTORS]}


def publish_policy(path, policy):
    pending = path.with_suffix(".pending")
    save(pending, policy)
    pending.replace(path)


def prepare_clients(args):
    from ciw.agent_mcp import demo_config
    from ciw.principal_policy import PrincipalPolicy
    root = args.output_dir / "fixture"
    root.mkdir(parents=True, exist_ok=False)
    os.environ["NCP_RETAIN_DIR"] = str(root / "retained")
    profiles = {actor: demo_config(root / actor) for actor in ACTORS}
    policy_path = root / "policy.json"
    policy = fixture_policy()
    publish_policy(policy_path, policy)
    PrincipalPolicy.from_file(policy_path).snapshot()  # Validate trusted fixture configuration before startup.
    return profiles, policy_path, policy


@asynccontextmanager
async def session(profile, policy_path, actor, destination, *, resume=False, catalog=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    arguments = ["-I", "-B", "-m", "ciw.principal_mcp", "--profile", str(profile),
                 "--policy", str(policy_path), "--principal", actor]
    if resume:
        arguments.append("--resume")
    if catalog is not None:
        arguments.extend(["--catalog", str(catalog[0]), "--catalog-ref", catalog[1]])
    parameters = StdioServerParameters(command=sys.executable, args=arguments,
        env=dict(os.environ), cwd=str(destination))
    with (destination / (actor + "-stderr.log")).open("a", encoding="utf-8") as errors:
        async with stdio_client(parameters, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=10) as client:
                await client.discover()
                yield client


async def call(client, name, arguments=None, *, refused=False, transcript=None):
    start = time.monotonic()
    response = await asyncio.wait_for(client.call_tool(name, arguments or {}), timeout=10)
    value = response.structured_content
    if transcript is not None:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        transcript.append({"tool": name, "status": value.get("status") if type(value) is dict else None,
                           "duration_seconds": time.monotonic() - start,
                           "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw)})
    return control_response(value, response.is_error, refused=refused)


def control_response(value, is_error, *, refused=False):
    require(type(value) is dict and value.get("status") in {"ok", "refused"}, "Unknown or missing MCP result")
    require((value.get("error") or {}).get("code") != "outcome_unknown",
            "Unknown outcome cannot satisfy an expected safe refusal")
    require(is_error is refused and (value["status"] == "refused") is refused,
            "MCP allowed/refused outcome differs from fixture expectation")
    return value.get("data")


def sdk_seed(profile, policy_path):
    from ciw.agent_mcp import from_profile
    from ciw.control_gateway import ControlHost
    from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal, owner_for
    from ciw.services import invoke
    from ciw.sequence_kinetics import example_request
    with from_profile(profile) as owner, ControlHost(owner, retained_owner=owner_for(ACTORS[0])) as host:
        facade = PrincipalPolicy.from_file(policy_path).facade(ACTORS[0], control=host)
        capabilities = invoke("ncp_capabilities_list", {}, host=facade)
        template = invoke("ncp_plan_template", {"baseline": "baseline"}, host=facade)
        plan = invoke("ncp_plan_create", {"source": "source", "baseline": "baseline", "graph": template["graph"]}, host=facade)
        digest = {"plan_id": plan["plan_id"], "expected_digest": plan["digest"]}
        try:
            invoke("ncp_domain_run", {"domain": "impact", "example": True}, host=facade)
        except PrincipalRefusal:
            pass
        else:
            raise ValueError("SDK executed outside its current domain grant")
        invoke("ncp_plan_admit", digest, host=facade)
        run = invoke("ncp_run_submit", {**digest, "attempt": "native-fixture"}, host=facade)
        deadline = time.monotonic() + 10
        while run["state"] in {"queued", "running"}:
            require(time.monotonic() < deadline, "SDK fixture execution exceeded deadline")
            time.sleep(0.01)
            run = invoke("ncp_run_get", {"run_id": run["run_id"]}, host=facade)
        require(run["state"] == "completed" and len(run["result"]["execution_ids"]) == 1,
                "SDK fixture lacks exact completed statistics execution")
        domain = invoke("ncp_domain_run", {"domain": "sequence", "request": example_request()}, host=facade)
        retained = invoke("ncp_retained_read", {"artifact_id": domain["artifact_id"]}, host=facade)
    return {"capabilities": capabilities, "run": run, "retained": retained, "digest": digest}


async def native_clients(args):
    from ciw.sequence_kinetics import example_request
    profiles, policy_path, policy = prepare_clients(args)
    seeded = sdk_seed(profiles[ACTORS[0]], policy_path)
    transcript = Transcript(args.output_dir / "calls.json")
    async with session(profiles[ACTORS[0]], policy_path, ACTORS[0], args.output_dir, resume=True) as alpha, \
            session(profiles[ACTORS[1]], policy_path, ACTORS[1], args.output_dir) as beta, \
            session(profiles[ACTORS[2]], policy_path, ACTORS[2], args.output_dir) as gamma:
        for client in (alpha, beta, gamma):
            listed = await client.list_tools()
            require("ncp_capabilities_list" in {tool.name for tool in listed.tools}, "Principal discovery missing allowed tool")
        observed = await call(alpha, "ncp_capabilities_list", transcript=transcript)
        require(observed == seeded["capabilities"], "Python SDK and official MCP discovery disagree")
        require(await call(alpha, "ncp_run_get", {"run_id": seeded["run"]["run_id"]}, transcript=transcript) == seeded["run"],
                "MCP process restart changed SDK retained run")
        artifact = seeded["retained"]["artifact_id"]
        read = await call(alpha, "ncp_retained_read", {"artifact_id": artifact}, transcript=transcript)
        require(read == seeded["retained"], "Retained owner changed across SDK/MCP process handoff")
        await call(beta, "ncp_retained_read", {"artifact_id": artifact}, refused=True, transcript=transcript)
        await call(alpha, "ncp_domain_run", {"domain": "impact", "example": True}, refused=True, transcript=transcript)
        chunks, offset, page_count = [], 0, 0
        while True:
            page = await call(alpha, "ncp_retained_read", {"artifact_id": artifact, "offset": offset, "limit": 257}, transcript=transcript)
            require(page["offset"] == offset and page["digest"] == read["digest"], "Paging identity changed")
            chunks.append(base64.b64decode(page["chunk_b64"], validate=True))
            page_count += 1
            require(page_count <= 1024, "Paging did not terminate within fixture bound")
            if page["next_offset"] is None:
                break
            require(page["next_offset"] > offset, "Paging failed to advance")
            offset = page["next_offset"]
        raw = b"".join(chunks)
        require("sha256:" + hashlib.sha256(raw).hexdigest() == read["digest"], "Paged bytes digest mismatch")
        require(page_count > 1 and json.loads(raw) == read["bundle"], "Paged bytes differ from bounded full fixture")
        grant = await call(alpha, "ncp_coordinate", {"action": "handoff", "artifact_id": artifact, "recipient": ACTORS[1]}, transcript=transcript)
        await call(gamma, "ncp_coordinate", {"action": "claim", "grant_id": grant["grant_id"]}, refused=True, transcript=transcript)
        copied = await call(beta, "ncp_coordinate", {"action": "claim", "grant_id": grant["grant_id"]}, transcript=transcript)
        received = await call(beta, "ncp_retained_read", {"artifact_id": copied["artifact_id"]}, transcript=transcript)
        require(received["bundle"] == read["bundle"], "Handoff changed retained scientific payload")
        again = await call(beta, "ncp_coordinate", {"action": "claim", "grant_id": grant["grant_id"]}, transcript=transcript)
        require(again["replayed"] and again["artifact_id"] == copied["artifact_id"], "Handoff duplicate created a second copy")
        started = time.monotonic()
        concurrency = await asyncio.gather(*(call(client, "ncp_capabilities_list", transcript=transcript)
            for client in (alpha, beta, gamma) for _ in range(8)))
        require(len(concurrency) == 24 and all(v["authorizes_execution"] is False for v in concurrency), "Concurrent discovery failed")
        concurrency_seconds = time.monotonic() - started
        policy["revision"] = "fixture-revoked"
        policy["principals"][1]["enabled"] = False
        publish_policy(policy_path, policy)
        await call(beta, "ncp_retained_read", {"artifact_id": copied["artifact_id"]}, refused=True, transcript=transcript)
        await call(beta, "ncp_coordinate", {"action": "claim", "grant_id": grant["grant_id"]}, refused=True, transcript=transcript)
        policy["revision"] = "fixture-reduced"
        policy["principals"][0]["scientific_operations"] = []
        policy["principals"][0]["operations"] = [rule for rule in policy["principals"][0]["operations"] if rule["name"] != "ncp_domain_run"]
        publish_policy(policy_path, policy)
        await call(alpha, "ncp_domain_run", {"domain": "sequence", "request": example_request()}, refused=True, transcript=transcript)
        await call(alpha, "ncp_run_submit", {**seeded["digest"], "attempt": "reduced-policy"}, refused=True, transcript=transcript)
        require(await call(alpha, "ncp_retained_read", {"artifact_id": artifact}, transcript=transcript) == read,
                "Reduced execution grants changed separately allowed retained reads")
    finish(args, ["python_official_mcp_parity", "execution_refusal", "restart_owner_continuity", "paging", "handoff",
                  "revocation", "reduced_grants", "concurrent_discovery_read"],
           {"transport": "native_stdio_mcp", "client": "official mcp Python SDK", "seed_client": "ciw.services.invoke bound facade",
            "transcript": transcript, "clients": 3, "concurrent_calls": 24, "concurrency_duration_seconds": concurrency_seconds,
            "page_count": page_count, "paged_bytes": len(raw), "retained_digest": read["digest"],
            "run_id": seeded["run"]["run_id"], "execution_ids": seeded["run"]["result"]["execution_ids"]})


async def soak(args):
    profiles, policy_path, policy = prepare_clients(args)
    durations, completed = [], [0, 0, 0]
    async with session(profiles[ACTORS[0]], policy_path, ACTORS[0], args.output_dir) as alpha, \
            session(profiles[ACTORS[1]], policy_path, ACTORS[1], args.output_dir) as beta, \
            session(profiles[ACTORS[2]], policy_path, ACTORS[2], args.output_dir) as gamma:
        clients = (alpha, beta, gamma)
        started = time.monotonic()
        while time.monotonic() - started < 60:
            before = time.monotonic()
            results = await asyncio.gather(*(call(client, "ncp_capabilities_list") for client in clients))
            require(all(result["authorizes_execution"] is False for result in results), "Soak response changed authority")
            durations.append(time.monotonic() - before)
            completed = [count + 1 for count in completed]
            if completed[0] % 10 == 0:
                save(args.output_dir / "soak-progress.json", {"elapsed_seconds": time.monotonic() - started,
                     "completed_per_client": dict(zip(ACTORS, completed)), "failed_calls": 0, "retries": 0})
            await asyncio.sleep(0.1)
        elapsed = time.monotonic() - started
    require(elapsed >= 60 and elapsed < 75, "Measured soak duration outside declared interval")
    require(sum(completed) >= 100 and min(completed) > 0, "Soak completed fewer than declared minimum calls")
    ordered = sorted(durations)
    finish(args, ["three_client_soak", "minimum_completed_calls", "bounded_duration"],
           {"transport": "native_stdio_mcp", "duration_seconds": elapsed, "completed_calls": sum(completed),
            "completed_per_client": dict(zip(ACTORS, completed)), "failed_calls": 0, "retries": 0,
            "round_latency_seconds": {"minimum": min(durations), "maximum": max(durations),
                "p50": ordered[len(ordered) // 2], "p95": ordered[int((len(ordered) - 1) * 0.95)]}})


def prepare_reference(args, root, policy):
    fixtures = load_module("private_qualification_reference_fixture", args.candidate_root / "tests/reference_fixture_helpers.py")
    corpus, record_id, source_path = fixtures.make_catalog(root / "mounted", b"Synthetic calibration reference; no engineering qualification.\n")
    catalog_path = root / "mounted/catalog-inputs/bundle"
    prefix = "ncp://proto-corpus/" + corpus.ref[7:] + "/"
    for actor in policy["principals"][:2]:
        actor["operations"].extend({"name": name, "constraints": {}} for name in
            ("ncp_knowledge_overview", "ncp_knowledge_search", "ncp_knowledge_source"))
        actor["reference_scopes"] = [corpus.ref]
        actor["resources"].append(prefix)
    return catalog_path, corpus.ref, record_id, prefix + "source/" + record_id[7:]


async def reference_clients(args):
    from ciw.principal_policy import PrincipalPolicy
    from ciw.services import invoke, open_reference
    profiles, policy_path, policy = prepare_clients(args)
    catalog_path, catalog_ref, record_id, uri = prepare_reference(args, args.output_dir / "fixture", policy)
    publish_policy(policy_path, policy)
    catalog_hashes = {path.relative_to(catalog_path).as_posix(): sha(path) for path in catalog_path.rglob("*") if path.is_file()}
    authority = PrincipalPolicy.from_file(policy_path)
    facade = authority.facade(ACTORS[0], reference=open_reference(catalog_path=catalog_path, catalog_ref=catalog_ref))
    calls = [("ncp_knowledge_overview", {}), ("ncp_knowledge_search", {"query": "calibration", "limit": 2}),
             ("ncp_knowledge_source", {"record_id": record_id})]
    expected = [invoke(name, arguments, host=facade) for name, arguments in calls]
    expected_resource = facade.read_resource(uri)
    transcript = Transcript(args.output_dir / "calls.json")
    async with session(profiles[ACTORS[0]], policy_path, ACTORS[0], args.output_dir,
                       catalog=(catalog_path, catalog_ref)) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        require(all(name in names for name, _ in calls), "Mounted reference tools were absent from actual principal MCP")
        for (name, arguments), value in zip(calls, expected):
            require(await call(client, name, arguments, transcript=transcript) == value,
                    "Mounted reference SDK and official MCP result differ")
        resource = await client.read_resource(uri)
        require(len(resource.contents) == 1 and json.loads(resource.contents[0].text) == expected_resource,
                "Mounted reference resource changed across clients")
        policy["revision"] = "reference-scope-reduced"
        policy["principals"][0]["reference_scopes"] = []
        publish_policy(policy_path, policy)
        await call(client, "ncp_knowledge_source", {"record_id": record_id}, refused=True, transcript=transcript)
        try:
            await client.read_resource(uri)
        except Exception as exc:
            from mcp.shared.exceptions import MCPError
            require(isinstance(exc, MCPError), "Reference resource failed for an unrelated transport reason")
        else:
            raise ValueError("Removed reference scope still disclosed an MCP resource")
    require(catalog_hashes == {path.relative_to(catalog_path).as_posix(): sha(path) for path in catalog_path.rglob("*") if path.is_file()},
            "Read-only reference journey mutated the mounted catalog")
    finish(args, ["mounted_reference_sdk_mcp_parity", "reference_resource_read", "reference_scope_reduction", "immutable_catalog"],
           {"transport": "native_stdio_MCP_and_bound_Python_SDK", "catalog_ref": catalog_ref, "record_id": record_id,
            "catalog_file_sha256": catalog_hashes, "source_urls": "inert_synthetic_metadata_never_fetched", "transcript": transcript})


async def adapter_process(args):
    import httpx
    from ciw.numerical_contracts import OPERATIONS as SCIENTIFIC_OPERATIONS
    from ciw.principal_policy import PrincipalPolicy
    from ciw.sequence_kinetics import example_request
    context = args.manifest.parent.parent
    adapter = context / "tools/private-mcp-host/adapter.py"
    require(adapter.is_file(), "Release context lacks the actual candidate adapter")
    root = args.output_dir / "fixture"
    root.mkdir(exist_ok=False)
    policy_path = root / "policy.json"
    policy = fixture_policy()
    for actor in policy["principals"]:
        actor["scientific_operations"] = [*SCIENTIFIC_OPERATIONS, "sequence.markov.v1"]
    catalog_path, catalog_ref, record_id, reference_uri = prepare_reference(args, root, policy)
    publish_policy(policy_path, policy)
    authority = PrincipalPolicy.from_file(policy_path)
    release = json.loads(args.manifest.read_text())["identity"]
    state, base_commit = None, None
    records, stopped_children = Transcript(args.output_dir / "transactions.json"), set()
    sampler = load_module("private_qualification_sampler", args.candidate_root / "scripts/check_private_host_qualification.py")
    process, output, url = None, None, None
    env = dict(os.environ, NCP_TRANSACTION_STATE=str(root / "state"), ODSP_OWNER_ID="fixture-deployment",
               ODSP_CHECKPOINT_GENERATION="fixture-generation-1", ODSP_PRINCIPAL_POLICY=str(policy_path),
               ODSP_RELEASE_MANIFEST=str(args.manifest), ODRP_CATALOG=str(catalog_path), ODRP_CATALOG_REF=catalog_ref)

    async def start(*, expected_fenced=False):
        nonlocal process, output, url
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = "http://127.0.0.1:" + str(listener.getsockname()[1])
        output = (args.output_dir / "adapter-stderr.log").open("ab")
        process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "uvicorn", "adapter:app",
            "--app-dir", str(adapter.parent), "--fd", str(listener.fileno()), "--no-access-log", "--log-level", "warning"],
            cwd=args.output_dir, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            pass_fds=(listener.fileno(),))
        listener.close()
        deadline = time.monotonic() + 10
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            while True:
                require(process.poll() is None, "Actual adapter exited during startup; inspect adapter-stderr.log")
                try:
                    response = await client.get(url + "/health")
                    health = response.json()
                    if health.get("release") == release and health.get("fenced") is expected_fenced:
                        require(health["ready"] is not expected_fenced, "Readiness/fence response is inconsistent")
                        records.append({"event": "process_start", "pid": process.pid, "health": health})
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                require(time.monotonic() < deadline, "Actual adapter never reached declared readiness")
                await asyncio.sleep(0.05)

    async def stop():
        nonlocal process, output
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), 7)
            except TimeoutError:
                process.kill()
                await asyncio.to_thread(process.wait)
        if output is not None:
            output.close()
            output = None

    def envelope(actor, action, payload):
        return {"action": action, "payload": payload, "state": state,
                "identity": authority.context(actor), "transaction": {"id": str(uuid.uuid4()),
                    "owner_id": "fixture-deployment", "generation": "fixture-generation-1", "epoch": 1,
                    "base_commit": base_commit, "release": release}}

    async def send(body, *, expected_status=200):
        nonlocal state, base_commit
        started = time.monotonic()
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            response = await client.post(url + "/transaction", content=raw, headers={"content-type": "application/json"})
        records.append({"event": "transaction", "action": body["action"], "tool": body["payload"].get("name"),
                        "principal": body["identity"]["principal"], "transaction_id": body["transaction"]["id"],
                        "http_status": response.status_code, "request_bytes": len(raw),
                        "response_bytes": len(response.content), "duration_seconds": time.monotonic() - started,
                        "response_sha256": hashlib.sha256(response.content).hexdigest()})
        require(response.status_code == expected_status, "Actual adapter HTTP outcome differs from expectation: " + str(response.status_code))
        require(len(response.content) <= 32 * 1024 * 1024, "Full serialized response exceeded declared adapter ceiling")
        value = response.json()
        if expected_status != 200:
            require("state" not in value and "retention" not in value, "Refusal unexpectedly acknowledged a checkpoint")
            return value
        require(value.get("status") == 200 and value.get("transaction") == body["transaction"] and value.get("identity") == body["identity"],
                "Adapter response lost transaction/identity binding")
        require(type(value.get("state")) is str and value.get("checkpoint_sha256") == hashlib.sha256(value["state"].encode("ascii")).hexdigest(),
                "Adapter checkpoint bytes lack matching receipt")
        state, base_commit = value["state"], body["transaction"]["id"]
        require(value["result"]["data"] == json.loads(value["result"]["raw_json"]), "Exact result JSON disagrees with navigation projection")
        return value["result"]["data"]

    async def tool(actor, name, arguments=None, *, refused=False):
        value = await send(envelope(actor, "call", {"name": name,
            "arguments_json": json.dumps(arguments or {}, ensure_ascii=False), "confirmed": True}))
        structured = value.get("structuredContent")
        return control_response(structured, value.get("isError", False), refused=refused)

    try:
        await start()
        require(not (root / "state").exists(), "Readiness mutated transaction state")
        listed = await send(envelope(ACTORS[0], "tools", {}))
        require("ncp_plan_create" in {item["name"] for item in listed}, "Actual installed MCP discovery missing")
        require("ncp_knowledge_source" in {item["name"] for item in listed}, "Mounted reference wrapper discovery missing")
        from ciw.services import invoke, open_reference
        reference_facade = authority.facade(ACTORS[0], reference=open_reference(catalog_path=catalog_path, catalog_ref=catalog_ref))
        reference = await tool(ACTORS[0], "ncp_knowledge_source", {"record_id": record_id})
        require(reference == invoke("ncp_knowledge_source", {"record_id": record_id}, host=reference_facade),
                "Mounted HTTP reference wrapper differs from bound SDK")
        template = await tool(ACTORS[0], "ncp_plan_template", {"baseline": "baseline"})
        plan = await tool(ACTORS[0], "ncp_plan_create", {"source": "source", "baseline": "baseline", "graph": template["graph"]})
        digest = {"plan_id": plan["plan_id"], "expected_digest": plan["digest"]}
        denied = await send(envelope(ACTORS[0], "call", {"name": "ncp_domain_run", "confirmed": True,
            "arguments_json": json.dumps({"domain": "impact", "example": True})}), expected_status=403)
        require(denied == {"error": {"code": "principal_policy_refused"}}, "Predispatch scope denial was not explicit")
        await tool(ACTORS[0], "ncp_plan_admit", digest)
        run = await tool(ACTORS[0], "ncp_run_submit", {**digest, "attempt": "private-local-qualification"})
        require(run["state"] == "completed" and len(run["result"]["execution_ids"]) == 7,
                "Private adapter did not complete all seven declared synthetic operations")
        domain = await tool(ACTORS[0], "ncp_domain_run", {"domain": "sequence", "request": example_request()})
        artifact = domain["artifact_id"]
        original = await tool(ACTORS[0], "ncp_retained_read", {"artifact_id": artifact})
        await tool(ACTORS[1], "ncp_retained_read", {"artifact_id": artifact}, refused=True)
        grant = await tool(ACTORS[0], "ncp_coordinate", {"action": "handoff", "artifact_id": artifact, "recipient": ACTORS[1]})
        await stop()
        await start()
        require(await tool(ACTORS[0], "ncp_run_get", {"run_id": run["run_id"]}) == run,
                "Actual process restart changed the acknowledged retained run")
        copied = await tool(ACTORS[1], "ncp_coordinate", {"action": "claim", "grant_id": grant["grant_id"]})
        received = await tool(ACTORS[1], "ncp_retained_read", {"artifact_id": copied["artifact_id"]})
        require(received["bundle"] == original["bundle"], "Global checkpoint restart lost owner-scoped handoff bytes")
        # A multibyte payload that fits in character count exceeds the encoded
        # JSON payload limit. It must refuse before allocating a workspace/child.
        unicode_payload = {"name": "ncp_plan_template", "arguments_json": json.dumps({"baseline": "é" * 140000}, ensure_ascii=False)}
        unicode_json = json.dumps(unicode_payload, ensure_ascii=False, separators=(",", ":"))
        require(len(unicode_json) < 262144 < len(unicode_json.encode()), "Fixture does not cross the UTF-8 byte boundary")
        oversize = await send(envelope(ACTORS[0], "call", unicode_payload), expected_status=413)
        require(oversize["error"]["code"] == "payload_budget_exceeded", "Unicode envelope limit was not enforced at payload boundary")
        records.append({"event": "unicode_payload_boundary", "characters": len(unicode_json), "utf8_bytes": len(unicode_json.encode()),
                        "declared_maximum_bytes": 262144})
        # Hold an actual child at startup, saturate the actual bounded adapter
        # queue, then disconnect its caller. No provider or bridge is replaced.
        pending_body = envelope(ACTORS[0], "call", {"name": "ncp_domain_run", "confirmed": True,
            "arguments_json": json.dumps({"domain": "sequence", "request": example_request()})})
        client = httpx.AsyncClient(trust_env=False, timeout=20)
        pending = asyncio.create_task(client.post(url + "/transaction", json=pending_body))
        deadline, child_pid = time.monotonic() + 10, None
        while child_pid is None:
            require(not pending.done(), "Fixture failed to observe an actual MCP child before completion")
            for pid in sampler.process_tree(process.pid):
                try:
                    command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                except OSError:
                    continue
                if b"ciw.principal_mcp" in command:
                    child_pid = pid
                    os.kill(pid, signal.SIGSTOP)
                    stopped_children.add(pid)
                    break
            require(time.monotonic() < deadline, "No actual installed MCP child observed for bounded fault fixture")
            await asyncio.sleep(0.005)
        async def overflow():
            before = time.monotonic()
            response = await client.post(url + "/transaction", json=envelope(ACTORS[0], "tools", {}))
            require(response.status_code == 429 and response.json() == {"error": {"code": "host_busy"}},
                    "Above-capacity request was not explicitly refused")
            return {"http_status": response.status_code, "duration_seconds": time.monotonic() - before, "response_bytes": len(response.content)}
        overflow_results = await asyncio.wait_for(asyncio.gather(*(overflow() for _ in range(9))), timeout=5)
        os.kill(child_pid, 0)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        await client.aclose()
        await asyncio.sleep(0.05)  # Let the actual socket disconnect reach ASGI before resuming the child.
        os.kill(child_pid, signal.SIGCONT)
        stopped_children.discard(child_pid)
        # Allow the real child to close after the disconnect; its transaction
        # must remain fenced and cannot return a checkpoint or trigger a rerun.
        deadline = time.monotonic() + 10
        while child_pid in sampler.process_tree(process.pid):
            require(time.monotonic() < deadline, "Cancelled caller left an unbounded actual MCP child")
            await asyncio.sleep(0.05)
        refused = await send(envelope(ACTORS[0], "tools", {}), expected_status=409)
        require(refused == {"error": {"code": "outcome_unknown"}}, "Disconnected transaction lost the unknown-outcome fence")
        await stop()
        await start(expected_fenced=True)
        refused = await send(envelope(ACTORS[0], "tools", {}), expected_status=409)
        require(refused == {"error": {"code": "outcome_unknown"}}, "Process restart cleared an uncertain fence")
        records.append({"event": "actual_child_disconnect_fault", "child_pid": child_pid, "queue_limit": 8,
                        "requested_tool": "ncp_domain_run", "effect_outcome": "unknown_not_retried",
                        "above_capacity_requests": 9, "overflow": overflow_results,
                        "child_alive_during_overflow": True, "caller_disconnected": True, "fence_survived_restart": True})
    finally:
        for pid in stopped_children:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        await stop()
    finish(args, ["real_loopback_process", "installed_stdio_execution", "checkpoint_restart", "owner_isolation", "mounted_reference_wrapper",
                  "remote_handoff_restart", "unicode_envelope_bound", "queue_overflow", "unknown_outcome_fence"],
           {"transport": "loopback_HTTP_operator_wrapper_to_actual_stdio_MCP", "native_http_mcp": False,
            "durable_storage_scope": "checkpoint_carried_by_local_fixture_client_not_Cloudflare_DO_R2",
            "records": records, "run_id": run["run_id"], "execution_ids": run["result"]["execution_ids"],
            "last_acknowledged_transaction": base_commit, "last_acknowledged_checkpoint_sha256": hashlib.sha256(state.encode()).hexdigest()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("runtime", "native_clients", "reference_clients", "soak", "adapter_process"))
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    require(sys.flags.isolated == 1 and not Path.cwd().resolve().is_relative_to(args.candidate_root.resolve()),
            "Journey must execute under python -I outside candidate checkout")
    try:
        if args.phase == "runtime":
            runtime(args)
        else:
            asyncio.run(globals()[args.phase](args))
    except Exception as exc:
        save(args.output_dir / "result.json", {"schema": "odsp.private-fixture-phase.v1", "phase": args.phase,
             "status": "FAIL", "skipped": 0, "error_type": type(exc).__name__, "error": str(exc)[:300], "checks": []})
        raise


if __name__ == "__main__":
    main()
