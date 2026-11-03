"""PostgreSQL custody through terminal execution of the seven native kernels.

SPDX-License-Identifier: AGPL-3.0-or-later
Host-only composition. No tool, grant, provider, connection or credential is
created. Pure computation may finish after custody loss; private occurrence
files are evidence, and only the current PostgreSQL result is publishable.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from .agent_api import AgentHost, encode
from .audited_host import AuditedPrincipalHost
from .campaign_contracts import canonical
from .control_gateway import ControlHost, TOOLS
from .distributed_queue import PostgresQueue, QueueLimits, QueueRefusal
from .distributed_services import DistributedServices, _refuse
from .numerical_contracts import OPERATIONS
from .principal_policy import PrincipalFacade, PrincipalOutcomeUnknown, _specs, owner_for
from .rust_numerical import RustNumericalBackend

_SUBMIT = "ncp_run_submit"
_READS = frozenset({_SUBMIT, "ncp_run_get", "ncp_artifact_read"})


class _NumericalQueue(PostgresQueue):
    """Additional canonical output grants inside settlement and every read."""
    def __init__(self, *args, adapter, **kwargs):
        self._adapter = adapter
        super().__init__(*args, **kwargs)

    def _authorize_completion(self, row, result):
        super()._authorize_completion(row, result)
        self._adapter._authorize_result(result)

    def _public(self, row, *, replayed=False):
        if row.get("result") is not None:
            self._adapter._authorize_result(json.loads(bytes(row["result"])))
        return super()._public(row, replayed=replayed)


class DistributedNumerical(DistributedServices):
    """Dedicate one unused native source host to one durable PG budget.

    Its canonical submit path is disabled permanently for this source output
    directory. Every queued attempt counts against the same max_executions,
    across workers/restarts. Only an already admitted pure numerical plan is
    accepted; queued arguments cannot select a provider, path or replacement
    host. Existing controls and AgentHost execution remain authoritative inside
    an isolated occurrence, under their unchanged scientific contracts.
    """
    def __init__(self, dsn, *, host, backend, generation, limits=None, lease_seconds=15):
        _refuse(type(host) is AuditedPrincipalHost and type(backend) is RustNumericalBackend)
        facade = host._check_identity()
        _refuse(type(facade) is PrincipalFacade)
        family, control = facade._host(_SUBMIT)
        _refuse(family == "control" and type(control) is ControlHost and type(control.owner) is AgentHost)
        owner = control.owner
        _refuse(not control._jobs and not owner._attempts and owner._allow
                and owner._allow <= set(OPERATIONS) and owner._max_nodes <= 8)
        _refuse(_READS <= host._audit.chain.allowed)
        self.host = self._host = host
        self._facade, self._policy, self._principal = facade, facade.policy, facade.principal
        self._audit, self._chain = host._audit, host._audit.chain
        self._hosts = dict(facade.hosts)
        self._scope = self._host_scope()
        self._operations = frozenset({_SUBMIT})
        self._specification = {name: canonical(_specs()[name]) for name in _READS}
        self._identity_refused = False
        self._authorizer = self._authorize
        self._control, self._owner, self._backend = control, owner, backend
        self._execution_authorizer = control._execution_authorizer
        _refuse(getattr(self._execution_authorizer, "__self__", None) is facade)
        self._runtime = canonical(backend.runtime_identity())
        # Rebuild the fixed native bindings; matching metadata on an arbitrary
        # supplied operation callback is never sufficient.
        from .control_plane import builtin_registry
        _refuse(encode(builtin_registry(bind=True, numerical_backend=backend).catalog()) == owner._catalog)
        self._source = self._source_identity()
        self._root = owner._check_output()
        limits = QueueLimits(max_jobs=owner._maximum, max_pending=owner._maximum, max_running=1) if limits is None else limits
        _refuse(type(limits) is QueueLimits and limits.max_jobs <= owner._maximum)
        # A worker's filesystem path is not a budget identity. Identical frozen
        # inputs/policy/principal in different worker directories share one PG
        # generation and total attempt limit.
        namespace = "native-" + sha256(canonical({"owner": owner_for(self._principal),
            "source_sha256": sha256(self._source).hexdigest()})).hexdigest()
        self.queue = _NumericalQueue(dsn, adapter=self, owner=namespace,
            principal=self._principal, generation=generation, operations=self._operations,
            authorize=self._authorizer, limits=limits, lease_seconds=lease_seconds)
        self._queue = self.queue
        self._queue_identity = self.queue.identity
        binding = sha256(canonical({"queue": self.queue.identity, "limits": limits.persisted(),
            "source_sha256": sha256(self._source).hexdigest(), "runtime_sha256": sha256(self._runtime).hexdigest()})).hexdigest()
        control.seal_distributed_execution(binding)
        self._guard, self._binding = owner._execution_guard, binding
        self._custody = self._root / "distributed-native"
        if not os.path.lexists(self._custody):
            self._custody.mkdir(mode=0o700)
        _refuse(self._custody.is_dir() and not self._custody.is_symlink()
                and self._custody.stat().st_mode & 0o077 == 0)
        self._custody_identity = (self._custody.stat().st_dev, self._custody.stat().st_ino)
        self._authorize_identity()

    def _source_identity(self):
        return canonical({"policy": self._owner._policy_bytes.decode("utf-8"),
            "inputs": {alias: {"id": key, "sha256": sha256(self._owner._artifacts[key]).hexdigest()}
                       for alias, key in self._owner._aliases.items()},
            "catalog": self._owner._catalog.decode("utf-8"), "allowed": sorted(self._owner._allow),
            "domains": self._owner._domains, "comparisons": self._owner._policies,
            "suites": self._owner._suites, "maximum": self._owner._maximum,
            "max_nodes": self._owner._max_nodes})

    def _authorize_identity(self):
        super()._authorize_identity()
        try:
            _refuse(self._control.owner is self._owner and self._source_identity() == self._source
                    and self._control._execution_authorizer is self._execution_authorizer
                    and canonical(self._backend.runtime_identity()) == self._runtime
                    and self._owner._check_output() == self._root)
            if hasattr(self, "_guard"):
                _refuse(self._owner._execution_guard is self._guard
                        and self._owner._execution_guard_binding == self._binding)
                from .retention import _read_regular
                from .agent_api import parse
                _refuse(parse(_read_regular(self._root / "execution-guard.json", byte_limit=1024)) == {
                    "schema": "ciw.execution-guard.v1", "binding": self._binding})
            if hasattr(self, "_custody_identity"):
                stat = self._custody.stat()
                _refuse(not self._custody.is_symlink() and (stat.st_dev, stat.st_ino) == self._custody_identity)
        except Exception:
            self._identity_refused = True
            raise
        return True

    def _authorize(self, principal, operation, arguments):
        from .agent_tools import _shape
        self._authorize_identity()
        _refuse(principal == self._principal and operation == _SUBMIT)
        _shape(arguments, TOOLS[_SUBMIT]["inputSchema"])
        with self._policy.lock:
            self._authorize_identity()
            _refuse(all(canonical(_specs()[name]) == spec for name, spec in self._specification.items()))
            plan = self._control._checked_plan(arguments["plan_id"], arguments["expected_digest"])
            _refuse(plan["admitted"] and all(node["operation_id"] in OPERATIONS
                for node in plan["compilation"]["experiment"]["nodes"]))
            self._execution_authorizer(plan, {"attempt": arguments["attempt"]})
        return True

    def enqueue(self, plan_id, expected_digest, *, attempt):
        self._authorize_identity()
        return self.queue.enqueue(_SUBMIT, {"plan_id": plan_id, "expected_digest": expected_digest,
                                           "attempt": attempt}, attempt)

    def _authorize_result(self, value):
        self._authorize_identity()
        _refuse(type(value) is dict and value.get("schema") == "ciw.distributed-numerical-result.v1"
                and value.get("runtime_sha256") == sha256(self._runtime).hexdigest())
        run = value["run"]
        self._authorize(self._principal, _SUBMIT, {"plan_id": run["plan_id"],
            "expected_digest": run["plan_digest"], "attempt": run["attempt"]})
        with self._policy.lock:
            self._policy.authorize_resource(self._principal, run["uri"])
            _refuse(run["state"] == "completed")
            for name, args in value["read_requests"]:
                _refuse(name == "ncp_artifact_read")
                grant = self._policy.authorize(self._principal, name, args)
                uri = "ncp://artifacts/" + args["artifact"]
                _refuse(any(uri == scope or scope.endswith("/") and uri.startswith(scope)
                            for scope in grant["resources"]))
                self._facade._science(grant, value["results"])
        return True

    def _dispatch(self, operation, arguments, claim):
        from .services import invoke
        self._scope_guard(operation, arguments, claim)
        _refuse(arguments["attempt"] == claim._row["attempt"])
        occurrence = self._custody / claim.fence.job_id
        # Never reopen or choose another directory following an uncertain start.
        # PostgreSQL has already consumed the permanent claim and dispatch intent.
        occurrence.mkdir(mode=0o700, exist_ok=False)
        guard = lambda: self._scope_guard(operation, arguments, claim)
        binding = sha256(canonical({**vars(claim.fence), "parent": self._binding})).hexdigest()
        with ExitStack() as stack:
            owner, control = self._control.native_occurrence(arguments["plan_id"], arguments["expected_digest"],
                backend=self._backend, output_dir=occurrence / "owner", execution_guard=guard, binding=binding)
            stack.enter_context(owner)
            stack.enter_context(control)  # Close/wait before releasing the private writer.
            facade = self._policy.facade(self._principal, control=control)
            audited = AuditedPrincipalHost(facade, chain=self._chain, read_anchor=self._audit.read_anchor,
                                          compare_and_set_anchor=self._audit.compare_and_set_anchor)
            # Share the original mutex and sticky audit hold as well as its
            # durable chain. A new occurrence must not reset an uncertain sink.
            audited._audit = self._audit
            with audited.scope_guard(guard):
                job = invoke(_SUBMIT, arguments, host=audited)
            deadline = time.monotonic() + min(120, 15 + self._owner._max_nodes * self._backend.process_policy.wall_seconds)
            while True:
                guard()
                job = control.get_run(job["run_id"])  # Private poll; never disclosed.
                if job["state"] not in {"queued", "running"}:
                    break
                if time.monotonic() >= deadline:
                    raise PrincipalOutcomeUnknown()
                time.sleep(0.02)
            if job["state"] != "completed":
                raise PrincipalOutcomeUnknown()
            with audited.scope_guard(guard):
                job = invoke("ncp_run_get", {"run_id": job["run_id"]}, host=audited)
                artifact = job["result"]["artifacts"]["workspace"]["artifact_id"]
                request = {"artifact": artifact, "selector": ["results"], "limit": 128}
                values = invoke("ncp_artifact_read", request, host=audited)
            # Existing workspace inspection returns a dictionary keyed by the
            # validated result identity, unlike the saved workspace array.
            rows = values["data"]
            result_ids = job["result"]["result_ids"]
            _refuse(values["next_offset"] is None and type(rows) is dict
                    and set(rows) == set(result_ids) and len(result_ids) <= self._owner._max_nodes
                    and all(rows[key]["result_id"] == key for key in result_ids))
            result = {"schema": "ciw.distributed-numerical-result.v1", "run": job,
                "results": [rows[key] for key in result_ids], "workspace": values["binding"],
                "read_requests": [["ncp_artifact_read", request]],
                "runtime_sha256": sha256(self._runtime).hexdigest(),
                "fence": vars(claim.fence), "authorizes_execution": False,
                "physical_validation": "not_established", "proof_established": False}
            self._authorize_result(result)
            guard()
            return result
