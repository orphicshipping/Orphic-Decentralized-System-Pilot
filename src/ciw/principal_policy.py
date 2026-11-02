"""Current principal grants for authenticated hosts and trusted bound SDK/MCP.

This facade is an authority boundary for requests, not a sandbox against a local
administrator. Launchers own policy files, profile bindings and the deployment's
retention root. Raw in-process hosts intentionally remain trusted authority.
Policy is never restored from a scientific checkpoint. Revocation prevents new
admissions; it cannot undo an operation that has already begun.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock

from .agent_api import encode

MAX_POLICY = 262144
MAX_REQUEST = 65536
MAX_ENVELOPE = 262144
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FORBIDDEN = frozenset({"principal", "owner", "agent_id", "grants", "policy", "configuration",
    "checkout", "python", "python_executable", "provider_revision", "pin", "repository",
    "endpoint", "token", "policy_revision", "policy_sha256"})


class PrincipalRefusal(ValueError):
    def __init__(self):
        super().__init__("principal_policy_refused")


class PrincipalOutcomeUnknown(ValueError):
    def __init__(self):
        super().__init__("outcome_unknown")


def _require(value):
    if not value:
        raise PrincipalRefusal()


def _keys(value, expected):
    _require(type(value) is dict and set(value) == set(expected))


def owner_for(principal):
    _require(type(principal) is str and _ID.fullmatch(principal))
    # Deployment separation is the trusted retention root, not a tool argument.
    return "principal:" + sha256(principal.encode("ascii")).hexdigest()


def _strings(value, maximum=256):
    _require(type(value) is list and len(value) <= maximum and
             all(type(v) is str and 0 < len(v) <= 512 for v in value) and len(set(value)) == len(value))


def _specs():
    from .control_gateway import TOOLS
    from .knowledge_mcp import TOOLS as REFERENCE, EVIDENCE_TOOLS
    from .state_read_profile import _tool_descriptions
    from .logistics_profile import TOOLS as LOGISTICS
    from .recompiler_profile import TOOLS as RECOMPILER
    from .schematic_profile import TOOLS as SCHEMATIC
    return {**TOOLS, **REFERENCE, **EVIDENCE_TOOLS, **LOGISTICS, **RECOMPILER, **SCHEMATIC,
            **{v["name"]: v for v in _tool_descriptions()}}


def validate_policy(value):
    _require(len(encode(value)) <= MAX_POLICY)
    _keys(value, {"schema", "revision", "principals"})
    _require(value["schema"] == "ciw.principal-policy.v1" and
             type(value["revision"]) is str and _ID.fullmatch(value["revision"]))
    _require(type(value["principals"]) is list and 1 <= len(value["principals"]) <= 64)
    principals = set()
    specs = _specs()
    for grant in value["principals"]:
        _keys(grant, {"principal", "enabled", "operations", "scientific_operations", "resources",
                      "reference_scopes", "handoff_recipients"})
        owner_for(grant["principal"])
        _require(grant["principal"] not in principals and type(grant["enabled"]) is bool)
        principals.add(grant["principal"])
        _require(type(grant["operations"]) is list and len(grant["operations"]) <= 256)
        names = set()
        for operation in grant["operations"]:
            _keys(operation, {"name", "constraints"})
            name = operation["name"]
            _require(type(name) is str and name in specs and name not in names)
            names.add(name)
            _require(type(operation["constraints"]) is dict and len(operation["constraints"]) <= 32)
            for path, allowed in operation["constraints"].items():
                _require(type(path) is str and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*", path)
                         and type(allowed) is list and 1 <= len(allowed) <= 64)
        for key in ("scientific_operations", "resources", "reference_scopes", "handoff_recipients"):
            _strings(grant[key])
        for scope in grant["reference_scopes"]:
            _require(_REF.fullmatch(scope))
        for recipient in grant["handoff_recipients"]:
            owner_for(recipient)
        for prefix in grant["resources"]:
            _require(prefix.startswith("ncp://") and not any(c in prefix for c in ("%", "\\", "?", "#"))
                     and not any(ord(c) <= 32 for c in prefix) and ".." not in prefix)
    _require(all(set(g["handoff_recipients"]) <= principals for g in value["principals"]))
    return deepcopy(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _read_policy(path):
    _require(path.is_absolute() and hasattr(os, "O_NOFOLLOW"))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size <= MAX_POLICY)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_POLICY + 1)
        _require(len(raw) <= MAX_POLICY)
        return json.loads(raw, object_pairs_hook=_unique)
    finally:
        os.close(fd)


def _schematic_content_key(path, key, value, arguments):
    """Recognize fixed scientific content, never caller-selected host policy.

    SRA certificates/decisions use the word owner for a scientific module, and
    its receipt carries the frozen assessment configuration. Only these exact
    paths/values are inert. The profile still validates the complete contract
    and reproduces receipts before returning verified results.
    """
    from .declared_workload import ASSESSMENT_POLICY
    steps = (("receipt", "bundle", "steps", 0),
             ("receipt", "bundle", "verification", "reproduction"))
    if key == "configuration":
        locations = {("receipt", "bundle"), *(step + ("request",) for step in steps)}
        return path in locations and encode(value) == encode(ASSESSMENT_POLICY)
    if key != "owner" or type(value) is not str or value not in {"jspt", "plsr", "rci", "cse"}:
        return False
    graphs = [("schematic",), ("request", "schematic")]
    for step in steps:
        graphs.extend((step + ("request", "schematic"),
                       step + ("result", "data", "schematic"),
                       step + ("numerical_result", "data", "schematic")))
        for result in ("result", "numerical_result"):
            decisions = step + (result, "data", "decisions")
            if len(path) == len(decisions) + 1 and path[:-1] == decisions and type(path[-1]) is int:
                return True
    for graph in graphs:
        if (len(path) == len(graph) + 3 and path[:len(graph)] == graph
                and path[-3] == "nodes" and type(path[-2]) is int and path[-1] == "attrs"):
            node = arguments
            for part in path[:-1]:
                node = node[part]
            return type(node) is dict and node.get("kind") == "certificate"
    return False


def _request(name, arguments):
    _require(type(name) is str and type(arguments) is dict and
             len(encode({"name": name, "arguments": arguments})) <= MAX_REQUEST)
    def check(value, path=()):
        if type(value) is dict:
            # compare's existing selector names a frozen host comparison
            # policy; it cannot supply policy contents or alter grants.
            forbidden = _FORBIDDEN - {"policy"} if name == "ncp_compare" and value is arguments else _FORBIDDEN
            for key, child in value.items():
                if key in forbidden:
                    _require(name.startswith("ncp_schematic_") and
                             _schematic_content_key(path, key, child, arguments))
                check(child, path + (key,))
        elif type(value) is list:
            for index, child in enumerate(value):
                check(child, path + (index,))
    check(arguments)
    from .agent_tools import _shape
    specs = _specs()
    _require(name in specs)
    _shape(arguments, specs[name]["inputSchema"])


def _rule(grant, name, arguments):
    rule = next((r for r in grant["operations"] if r["name"] == name), None)
    _require(rule is not None)
    for path, allowed in rule["constraints"].items():
        value = arguments
        for key in path.split("."):
            _require(type(value) is dict and key in value)
            value = value[key]
        _require(any(encode(value) == encode(option) for option in allowed))


def _direct_science(name, arguments):
    """Execution identities behind direct tools, shared with nested admission.

    Use the same catalogs as dispatch. An example or retained input never
    substitutes for an execution grant. Non-registered computation wrappers
    retain their existing catalog/ledger capability identity rather than
    inventing a registered operation.
    """
    if name == "ncp_domain_run":
        from .domain_surface import OPERATIONS
        matches = [row[0] for row in OPERATIONS
                   if row[1] == arguments["domain"] and row[2] == arguments.get("name")]
        _require(len(matches) == 1)
        return set(matches)
    if name == "ncp_workbench_run":
        from .workbench import OPERATIONS
        return {OPERATIONS[arguments["workflow"]]}
    if name == "ncp_fluid_field":
        from .fluid_contract import ALL_OPERATIONS
        return set(ALL_OPERATIONS[arguments["solver"]])
    if name == "ncp_lesson" and arguments.get("mode", "learn") == "work":
        return {"statistics.v1"}
    if name in {"ncp_recompiler_reconcile", "ncp_recompiler_verify_receipt"}:
        from .recompiler_profile import OPERATION
        return {OPERATION}
    if name in {"ncp_schematic_validate", "ncp_schematic_retrieve", "ncp_schematic_assess", "ncp_schematic_verify_receipt"}:
        from .schematic_profile import OPERATION
        return {OPERATION}
    capability = {"ncp_demo": "synthetic-oscillator-demo", "ncp_linear_preview": "linear-response.library",
                  "ncp_polymer_cycle": "data.assess-polymer-cycle.v1",
                  "ncp_toolpoint": "data.review-toolpoint-containment.v1",
                  "ncp_hypergraph": "hypergraph_rewriting"}.get(name)
    return {capability} if capability else set()


def _resource(uri):
    _require(type(uri) is str and len(uri) <= 1024)
    for family, name, key in (("plans", "ncp_plan_get", "plan_id"), ("runs", "ncp_run_get", "run_id"),
            ("artifacts", "ncp_artifact_read", "artifact"), ("retained", "ncp_retained_read", "artifact_id")):
        match = re.fullmatch(r"ncp://" + family + r"/([A-Za-z0-9_-]+)", uri)
        if match:
            return "control", name, {key: match[1]}, None
    match = re.fullmatch(r"ncp://(proto-corpus|evidence)/([0-9a-f]{64})/(overview|(?:source|span)/[0-9a-f]{64})", uri)
    _require(match is not None)
    family, digest, route = match.groups()
    if family == "proto-corpus":
        _require(route == "overview" or route.startswith("source/"))
        name, key = "ncp_knowledge_", "record_id"
    else:
        _require(route == "overview" or route.startswith("span/"))
        name, key = "ncp_evidence_", "span_id"
    name += route.split("/")[0]
    return "reference", name, {} if route == "overview" else {key: "sha256:" + route.split("/")[1]}, "sha256:" + digest


class PrincipalPolicy:
    def __init__(self, read_policy):
        _require(callable(read_policy))
        self._reader = read_policy
        self.lock = RLock()

    @classmethod
    def from_file(cls, path):
        path = Path(path)
        _require(path.is_absolute())
        return cls(lambda: _read_policy(path))

    def snapshot(self):
        try:
            return validate_policy(self._reader())
        except Exception:
            raise PrincipalRefusal() from None

    @staticmethod
    def _grant(snapshot, principal):
        owner_for(principal)
        grant = next((g for g in snapshot["principals"] if g["principal"] == principal), None)
        _require(grant is not None and grant["enabled"])
        return grant

    def check_principal(self, principal):
        return self._grant(self.snapshot(), principal)

    def context(self, principal):
        snapshot = self.snapshot()
        self._grant(snapshot, principal)
        return {"schema": "ciw.principal-context.v1", "principal": principal, "owner": owner_for(principal),
                "policy_revision": snapshot["revision"], "policy_sha256": "sha256:" + sha256(encode(snapshot)).hexdigest()}

    def validate_context(self, identity):
        _keys(identity, {"schema", "principal", "owner", "policy_revision", "policy_sha256"})
        _require(identity == self.context(identity["principal"]))
        return identity["principal"]

    def authorize(self, principal, name, arguments):
        try:
            _request(name, arguments)
            grant = self.check_principal(principal)
            _rule(grant, name, arguments)
            _require(_direct_science(name, arguments) <= set(grant["scientific_operations"]))
            if name == "ncp_coordinate" and arguments["action"] == "submit":
                inner, selected = arguments.get("operation"), arguments.get("arguments", {})
                _require(inner == "ncp_domain_run")
                _request(inner, selected)
                _rule(grant, inner, selected)
                _require(_direct_science(inner, selected) <= set(grant["scientific_operations"]))
            return grant
        except Exception:
            raise PrincipalRefusal() from None

    def authorize_resource(self, principal, uri):
        _, name, arguments, scope = _resource(uri)
        grant = self.authorize(principal, name, arguments)
        _require(any(uri == p or (p.endswith("/") and uri.startswith(p)) for p in grant["resources"]))
        _require(scope is None or scope in grant["reference_scopes"])
        return grant

    def facade(self, principal, *, control=None, reference=None, state=None, logistics=None, recompiler=None, schematic=None):
        self.check_principal(principal)
        return PrincipalFacade(self, principal, control=control, reference=reference, state=state,
                               logistics=logistics, recompiler=recompiler, schematic=schematic)


class PrincipalFacade:
    instructions = ("Principal-bound ODSP services. Each operation and resource uses current host policy. "
                    "Identity and grants cannot be selected by request arguments. No physical qualification.")

    def __init__(self, policy, principal, **hosts):
        self.policy, self.principal, self.hosts = policy, principal, hosts
        self.retained = None
        for family in ("state", "logistics", "recompiler", "schematic"):
            if hosts.get(family) is not None:
                _require(getattr(hosts[family], "_bound_principal", None) == principal)
        if hosts.get("logistics") is not None:
            from .retention import Store
            # Same owner and durable records; principal-bound publication never
            # evicts earlier evidence. Raw trusted hosts keep their own default.
            store = hosts["logistics"].store
            _require(type(store) is Store)
            hosts["logistics"].store = Store(store.owner, evict=False)
        control = hosts.get("control")
        if control is not None:
            _require(control.retained.owner == owner_for(principal))
            self.retained = control.retained
            _require(getattr(control, "_execution_authorizer", None) is None)
            control._execution_authorizer = self._authorize_execution

    @staticmethod
    def refusal():
        return {"schema": "ncp.control-response.v1", "status": "refused", "data": None,
                "error": {"code": "principal_policy_refused"}}

    def _host(self, name):
        family = ("schematic" if name.startswith("ncp_schematic_") else
                  "recompiler" if name.startswith("ncp_recompiler_") else
                  "state" if name.startswith("ncp_state_") else "logistics" if name.startswith("ncp_logistics_")
                  else "reference" if name == "ncp_system_status" or name.startswith(("ncp_knowledge_", "ncp_evidence_"))
                  else "control")
        host = self.hosts.get(family)
        _require(host is not None)
        return family, host

    def _science(self, grant, value):
        operations = set()
        def visit(item):
            if type(item) is dict:
                if "operation_id" in item:
                    _require(type(item["operation_id"]) is str)
                    operations.add(item["operation_id"])
                for child in item.values():
                    visit(child)
            elif type(item) is list:
                for child in item:
                    visit(child)
        visit(value)
        _require(operations <= set(grant["scientific_operations"]))

    def _authorize_execution(self, plan, job):
        with self.policy.lock:
            arguments = {"plan_id": plan["plan_id"], "expected_digest": plan["digest"], "attempt": job["attempt"]}
            grant = self.policy.authorize(self.principal, "ncp_run_submit", arguments)
            self._scope(grant, "control", self.hosts["control"], "ncp_run_submit", arguments)
            self._science(grant, plan["compilation"]["experiment"])

    def _scope(self, grant, family, host, name, args):
        if family == "reference":
            _require(host.corpus_ref in grant["reference_scopes"])
            if name.startswith("ncp_evidence_"):
                _require(host.evidence_ref in grant["reference_scopes"])
        if family == "logistics" and name == "ncp_logistics_retained_read":
            source = host.store.native(args["artifact_id"])["source"]
            candidates = [("ncp_logistics_inspect", {"source": source})]
            if source == {"kind": "catalog"}:
                candidates.append(("ncp_logistics_catalog", {}))
            elif source.get("kind") == "compute":
                candidates.append(("ncp_logistics_job", {"job_id": source.get("jobId"), "include": source.get("include")}))
            permitted = False
            for operation, selectors in candidates:
                try:
                    self.policy.authorize(self.principal, operation, selectors)
                    permitted = True
                except PrincipalRefusal:
                    pass
            _require(permitted)
        if family != "control":
            return
        for tool, family_name, field in (("ncp_plan_get", "plans", "plan_id"),
                ("ncp_plan_admit", "plans", "plan_id"), ("ncp_run_submit", "plans", "plan_id"),
                ("ncp_run_get", "runs", "run_id"), ("ncp_run_cancel", "runs", "run_id"),
                ("ncp_retained_read", "retained", "artifact_id"), ("ncp_retained_release", "retained", "artifact_id")):
            if name == tool:
                uri = "ncp://" + family_name + "/" + args[field]
                _require(any(uri == p or p.endswith("/") and uri.startswith(p) for p in grant["resources"]))
        fields = {"ncp_plan_template": ("baseline",), "ncp_plan_create": ("source", "baseline"),
                  "ncp_artifact_read": ("artifact",), "ncp_observe": ("artifact",),
                  "ncp_compare": ("left", "right")}.get(name, ())
        artifacts = [args[field] for field in fields]
        if name == "ncp_qualify":
            artifacts.extend(args["inputs"].values())
        for artifact in artifacts:
            _require(type(artifact) is str)
            uri = "ncp://artifacts/" + artifact
            _require(any(uri == p or p.endswith("/") and uri.startswith(p) for p in grant["resources"]))
            self._science(grant, host.owner._get(artifact))
        if name == "ncp_workbench_run":
            for artifact in ([args["upstream_artifact"]] if "upstream_artifact" in args else args.get("upstream_artifacts", [])):
                uri = "ncp://retained/" + artifact
                _require(any(uri == p or p.endswith("/") and uri.startswith(p) for p in grant["resources"]))
        if name == "ncp_plan_template":
            self._science(grant, host.owner._get(args["baseline"]))
        elif name == "ncp_plan_create":
            reverse = {v: k for k, v in host._names.items()}
            for node in args["graph"]["nodes"]:
                _require(reverse.get(node["capability"]) in grant["scientific_operations"])
        elif name in {"ncp_plan_get", "ncp_plan_admit", "ncp_run_submit"}:
            self._science(grant, host.get_plan(args["plan_id"]))
        elif name in {"ncp_run_get", "ncp_run_cancel"}:
            self._science(grant, host.get_plan(host.get_run(args["run_id"])["plan_id"]))
        elif name == "ncp_artifact_read":
            self._science(grant, host.owner._get(args["artifact"]))

    def _handoff(self, grant, args):
        from .coordinator import _claim_record, _grant_identity, _GRANT_BYTE_LIMIT
        from .retention import _root
        action = args["action"]
        if action == "submit":
            _require(args["operation"] != "ncp_coordinate")
            self.policy.authorize(self.principal, args["operation"], args.get("arguments", {}))
        elif action == "handoff":
            recipient = args["recipient"]
            _require(recipient in grant["handoff_recipients"])
            self.policy.check_principal(recipient)
            self.policy.authorize_resource(self.principal, "ncp://retained/" + args["artifact_id"])
            grant_id = _grant_identity(owner_for(self.principal), owner_for(recipient), args["artifact_id"])
            self.policy.authorize(recipient, "ncp_coordinate", {"action": "claim", "grant_id": grant_id})
            self.policy.authorize_resource(recipient, "ncp://retained/" + args["artifact_id"])
            args["recipient"] = owner_for(recipient)
        elif action == "claim":
            grant_id = args["grant_id"]
            _require(re.fullmatch(r"grant-[0-9a-f]{32}", grant_id))
            root = _root() / "grants"
            record = _claim_record(root / (grant_id + ".claimed.json"))
            if record is None:
                record = _claim_record(root / (grant_id + ".json"), byte_limit=_GRANT_BYTE_LIMIT)
            _require(record is not None and record.get("recipient") == owner_for(self.principal))
            source_owner = record.get("source_owner", record.get("owner"))
            sender = next((g for g in self.policy.snapshot()["principals"] if owner_for(g["principal"]) == source_owner), None)
            _require(sender is not None and sender["enabled"] and self.principal in sender["handoff_recipients"])
            self.policy.authorize(sender["principal"], "ncp_coordinate", {"action": "handoff",
                "artifact_id": record["source_artifact_id"], "recipient": self.principal})
            self.policy.authorize_resource(sender["principal"], "ncp://retained/" + record["source_artifact_id"])
            self.policy.authorize_resource(self.principal, "ncp://retained/" + record["source_artifact_id"])

    def call(self, name, arguments):
        dispatched = False
        read_only = False
        try:
            with self.policy.lock:
                arguments = deepcopy(arguments)
                grant = self.policy.authorize(self.principal, name, arguments)
                family, host = self._host(name)
                self._scope(grant, family, host, name, arguments)
                if name == "ncp_coordinate":
                    self._handoff(grant, arguments)
                read_only = _specs()[name].get("annotations", {}).get("readOnlyHint") is True
                dispatched = True
                value = host.call(name, arguments)
                _require(type(value) is dict)
                if not read_only and value.get("status") != "ok":
                    # Older host envelopes cannot distinguish pre-effect
                    # refusal from cleanup failure after publication/unlink.
                    # Conservatively retain uncertainty and any failure locator.
                    value = deepcopy(value)
                    value["error"] = {**(value.get("error") or {}), "code": "outcome_unknown"}
                if value.get("status") == "ok" and name in {"ncp_capabilities_list", "ncp_capability_describe"}:
                    value = deepcopy(value)
                    rows = value["data"].get("items", []) if name == "ncp_capabilities_list" else [value["data"]]
                    for row in rows:
                        if "authorized" in row:
                            row["authorized"] = bool(row["authorized"] and row.get("operation_id") in grant["scientific_operations"])
                # Check the complete duplicated MCP envelope, including escaped
                # Unicode text. A refused over-budget response discloses no data.
                self.mcp_result(name, value)
                return value
        except Exception:
            if dispatched and not read_only:
                return {"schema": "ncp.control-response.v1", "status": "refused", "data": None,
                        "error": {"code": "outcome_unknown"}}
            return self.refusal()

    def invoke(self, name, arguments=None):
        value = self.call(name, {} if arguments is None else arguments)
        if value.get("status") != "ok":
            error = value.get("error") or {}
            artifact = error.get("artifact_id")
            if type(artifact) is str and re.fullmatch(r"(?:retained|failure)-[0-9a-f]{32}", artifact):
                from .retention import RetainedFailure
                raise RetainedFailure("outcome_unknown" if error.get("code") == "outcome_unknown" else
                                      "request_refused", artifact)
            if error.get("code") == "outcome_unknown":
                raise PrincipalOutcomeUnknown()
            raise PrincipalRefusal()
        return value["data"]

    @staticmethod
    def mcp_result(name, value):
        result = {"content": [{"type": "text", "text": encode(value).decode()}],
                  "structuredContent": value, "isError": value.get("status") != "ok"}
        _require(len(encode(result)) <= MAX_ENVELOPE)
        return result

    def descriptions(self):
        with self.policy.lock:
            grant = self.policy.check_principal(self.principal)
            allowed = {r["name"] for r in grant["operations"]}
            result = []
            for family, host in self.hosts.items():
                if host is None:
                    continue
                for row in host.descriptions():
                    if row["name"] not in allowed:
                        continue
                    if family == "reference" and (host.corpus_ref not in grant["reference_scopes"] or
                            row["name"].startswith("ncp_evidence_") and host.evidence_ref not in grant["reference_scopes"]):
                        continue
                    result.append(deepcopy(row))
            _require(len(encode(result)) <= MAX_ENVELOPE)
            return result

    def read_resource(self, uri):
        with self.policy.lock:
            grant = self.policy.authorize_resource(self.principal, uri)
            family, name, args, _ = _resource(uri)
            _, host = self._host(name)
            self._scope(grant, family, host, name, args)
            if family == "control" and uri.startswith("ncp://retained/"):
                value = self.invoke(name, args)
            else:
                value = host.read_resource(uri)
                if type(value) is str:
                    value = json.loads(value)
            envelope = {"contents": [{"uri": uri, "mimeType": "application/json", "text": encode(value).decode()}],
                        "ttlMs": 0, "cacheScope": "private"}
            _require(len(encode(envelope)) <= MAX_ENVELOPE)
            return value

    def list_resources(self):
        with self.policy.lock:
            self.policy.check_principal(self.principal)
            result = []
            for host in self.hosts.values():
                if host is None:
                    continue
                getter = getattr(host, "list_resources", getattr(host, "resource_descriptions", lambda: []))
                for row in getter():
                    try:
                        self.policy.authorize_resource(self.principal, row["uri"])
                    except PrincipalRefusal:
                        continue
                    result.append(deepcopy(row))
            return result

    @property
    def resource_templates(self):
        # Resource read authorization is exact and live; discovery grants none.
        self.policy.check_principal(self.principal)
        return []
