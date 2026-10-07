"""Trusted local task administration. These operations are not MCP tools.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .external_work import ExternalWorkStore, text
from .control_contracts import content_ref


def _database(path, *, create=False):
    path = Path(path).absolute()
    if any((p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())) for p in (path, *path.parents)):
        raise ValueError("Symlink database path refused")
    if create:
        # Exclusive reservation prevents accidentally initializing over another file.
        with path.open("xb"):
            pass
    elif not path.is_file():
        raise ValueError("Provision the local task database first")
    return ExternalWorkStore(path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, prog="ncp work")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "provision-client", "revoke-client", "revoke-context",
                 "authorize", "review"):
        a = sub.add_parser(name)
        a.add_argument("--database", type=Path, required=True)
        if name in ("provision-client", "revoke-client"):
            a.add_argument("--client-id", required=True)
        if name == "provision-client":
            a.add_argument("--role", required=True, choices=("requester", "worker"))
            a.add_argument("--parent-ref", required=True)
        if name == "revoke-context":
            a.add_argument("--context-id", required=True)
        if name in ("authorize", "review"):
            a.add_argument("--task-id", required=True)
        if name == "authorize":
            a.add_argument("--expected-hash", required=True)
            a.add_argument("--approval-ref", required=True,
                           help="Reference to a decision already verified by the trusted host")
            a.add_argument("--worker-id", required=True)
            a.add_argument("--expires-at", type=float, required=True, help="UTC Unix timestamp")
            a.add_argument("--lease-seconds", type=int, default=300)
            a.add_argument("--max-attempts", type=int, default=3)
            a.add_argument("--max-result-bytes", type=int, default=16384)
        if name == "review":
            a.add_argument("--result-id", required=True)
            a.add_argument("--decision", choices=("accepted", "rejected", "needs_review"), required=True)
            a.add_argument("--reviewer-ref", required=True)
            a.add_argument("--scope", required=True)
            a.add_argument("--findings", required=True)
    args = p.parse_args(argv)
    try:
        store = _database(args.database, create=args.command == "init")
        if args.command == "init":
            data = {"database_initialized": True, "clients": 0, "tasks": 0}
        elif args.command == "provision-client":
            store.provision_client(args.client_id, role=args.role, parent_ref=args.parent_ref)
            data = {"client_id": args.client_id, "role": args.role, "parent_ref": args.parent_ref,
                    "remote_identity_configured": False}
        elif args.command == "revoke-client":
            text(args.client_id, 128)
            with store._transaction() as db:
                if not db.execute("SELECT 1 FROM clients WHERE id=?", (args.client_id,)).fetchone():
                    raise ValueError("client_unavailable")
                db.execute("UPDATE clients SET enabled=0 WHERE id=?", (args.client_id,))
            data = {"client_id": args.client_id, "revocation_recorded": True}
        elif args.command == "revoke-context":
            content_ref(args.context_id)
            with store._transaction() as db:
                if not db.execute("SELECT 1 FROM contexts WHERE id=?", (args.context_id,)).fetchone():
                    raise ValueError("context_unavailable")
                db.execute("UPDATE contexts SET enabled=0 WHERE id=?", (args.context_id,))
            data = {"context_id": args.context_id, "revocation_recorded": True}
        elif args.command == "authorize":
            data = store.authorize(args.task_id, expected_hash=args.expected_hash,
                approval_ref=args.approval_ref, worker_id=args.worker_id,
                expires_at=args.expires_at, lease_seconds=args.lease_seconds,
                max_attempts=args.max_attempts, max_result_bytes=args.max_result_bytes)
        elif args.command == "review":
            data = store.record_review(args.task_id, result_id=args.result_id,
                decision=args.decision, reviewer_ref=args.reviewer_ref,
                scope=args.scope, findings=args.findings)
        else:
            with store._transaction() as db:
                counts = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                          for table in ("clients", "contexts", "tasks")}
                tasks = [store._load(db, r[0]) for r in db.execute("SELECT id FROM tasks")]
            from collections import Counter
            data = {**counts, "task_states": dict(Counter(t["execution_status"] for t in tasks)),
                    "review_states": dict(Counter(
                        t["review"]["decision"] if t["review"] else "not_reviewed" for t in tasks)),
                    "remote_identity_configured": "not_established_by_local_database"}
        print(json.dumps({"status": "ok", "data": data, "authority_boundary": "trusted_local_host",
                          "physical_qualification": "not_established"}, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)[:400]}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
