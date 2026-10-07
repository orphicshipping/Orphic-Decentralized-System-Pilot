"""Operator knowledge commands; model clients receive only the MCP data plane.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .control_contracts import bytes_ref
from .external_work import ExternalWorkStore, text
from .proto_corpus import ProtoCorpus, compile_bundle


def _write(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2))


def _path(path, *, existing=False):
    value = Path(path).absolute()
    if any((p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())) for p in (value, *value.parents)):
        raise ValueError("Symlink path refused")
    if existing and not value.is_file():
        raise ValueError("Existing local file required")
    return value


def bind_metadata(store, corpus, *, record_id, parent_ref, disclosure_ref):
    """Bind a metadata record, never claim it is PDF/ZIP text or a source license."""
    text(parent_ref)
    text(disclosure_ref)
    record = corpus.source(record_id)["record"]
    raw = corpus.record_bytes(record_id)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 8192:
        raise ValueError("Catalog record exceeds context budget")
    digest = bytes_ref(raw)
    context_id = store.retain_context(
        parent_ref=parent_ref, snapshot_ref=corpus.ref,
        source_id="urn:notation:proto-corpus:metadata:" + record_id.removeprefix("sha256:"),
        version_ref=digest, source_bytes=raw, expected_source_hash=digest,
        start=0, end=len(raw),
        provenance={
            "representation": "catalog_metadata",
            "metadata_only": True,
            "record_id": record_id,
            "original_source_id": record["source_id"],
            "original_source_sha256": record["source_sha256"],
            "original_representation_sha256": record.get("representation_sha256"),
            "original_sha256_scope": record.get("sha256_scope"),
            "original_representation": record["representation"],
            "source_url": record["source_url"],
            "disclosure_ref": disclosure_ref,
            "limitations": [
                "The context bytes are catalog metadata, not original source content.",
                "Source hashes, metadata hashes and extraction hashes are distinct.",
                "No scientific adjudication, execution permission or source license is conferred.",
            ],
            "semantic_support_checked": False,
            "scientific_adjudication": "not_performed",
        })
    return {"context_id": context_id, "record_id": record_id, "metadata_hash": digest,
            "source_sha256": record["source_sha256"], "metadata_only": True,
            "authorizes_execution": False}


def _host(corpus, args):
    from .knowledge_mcp import KnowledgeHost
    values = (args.database, args.principal, args.parent_ref)
    if any(v is not None for v in values) and not all(v is not None for v in values):
        raise ValueError("Database, principal and parent-ref must be supplied together")
    evidence = None
    evidence_values = (args.evidence_bundle, args.evidence_ref)
    if any(v is not None for v in evidence_values):
        if not all(v is not None for v in evidence_values):
            raise ValueError("Evidence bundle and evidence-ref must be supplied together")
        from .evidence_corpus import EvidenceCorpus
        evidence = EvidenceCorpus.open(args.evidence_bundle, expected_ref=args.evidence_ref, catalog=corpus)
    if args.database is None:
        return KnowledgeHost(corpus, evidence=evidence)
    from .external_work_mcp import ExternalWorkHost
    store = ExternalWorkStore(_path(args.database, existing=True))
    return KnowledgeHost(corpus, work_host=ExternalWorkHost(store, principal=args.principal),
                         work_parent_ref=args.parent_ref, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, prog="ncp knowledge")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Compile exact metadata registries into a new pinned bundle")
    build.add_argument("--registry", type=Path, required=True)
    build.add_argument("--intake", type=Path, required=True)
    build.add_argument("--selected", type=Path)
    build.add_argument("--output", type=Path, required=True)
    for command in ("status", "search", "source", "resolve", "relations", "review-queue",
                    "bind-context", "serve", "config"):
        sub = commands.add_parser(command)
        sub.add_argument("--bundle", type=Path, required=True)
        sub.add_argument("--expected-ref", required=True, help="Trusted pin from the build receipt")
        if command == "resolve":
            sub.add_argument("--source-id", required=True)
        if command in ("source", "relations", "bind-context"):
            sub.add_argument("--record-id", required=True)
        if command in ("search", "relations", "review-queue"):
            sub.add_argument("--limit", type=int, default=10)
            sub.add_argument("--offset", type=int, default=0)
        if command in ("search", "review-queue"):
            sub.add_argument("--kind")
        if command == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--category")
        if command == "bind-context":
            sub.add_argument("--database", type=Path, required=True)
            sub.add_argument("--parent-ref", required=True)
            sub.add_argument("--disclosure-ref", required=True,
                             help="Reference to the host's verified metadata disclosure decision")
        if command in ("status", "serve", "config"):
            sub.add_argument("--database", type=Path)
            sub.add_argument("--principal")
            sub.add_argument("--evidence-bundle", type=Path)
            sub.add_argument("--evidence-ref")
            sub.add_argument("--parent-ref")
        if command == "config":
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            ref = compile_bundle(registry=args.registry, intake=args.intake,
                                 selected=args.selected, output=args.output)
            value = {"status": "compiled", "bundle_ref": ref,
                     "bundle": str(args.output.absolute()), "authorizes_execution": False}
        else:
            corpus = ProtoCorpus.open(args.bundle, expected_ref=args.expected_ref)
            if args.command == "search":
                value = corpus.search(args.query, limit=args.limit, category=args.category,
                                      kind=args.kind, offset=args.offset)
            elif args.command == "resolve":
                value = corpus.resolve(args.source_id)
            elif args.command == "source":
                value = corpus.source(args.record_id)
            elif args.command == "relations":
                value = corpus.relations(args.record_id, limit=args.limit, offset=args.offset)
            elif args.command == "review-queue":
                value = corpus.review_queue(limit=args.limit, offset=args.offset, kind=args.kind)
            elif args.command == "bind-context":
                store = ExternalWorkStore(_path(args.database, existing=True))
                value = bind_metadata(store, corpus, record_id=args.record_id,
                                      parent_ref=args.parent_ref, disclosure_ref=args.disclosure_ref)
            else:
                host = _host(corpus, args)
                host.descriptions()  # check the host-bound principal before protocol/config creation
                if args.command == "serve":
                    import anyio
                    from .knowledge_mcp import serve_stdio
                    anyio.run(serve_stdio, host)
                    return 0
                if args.command == "config":
                    arguments = ["-m", "ciw.knowledge_cli", "serve", "--bundle",
                                 str(args.bundle.absolute()), "--expected-ref", corpus.ref]
                    if args.evidence_bundle is not None:
                        arguments += ["--evidence-bundle", str(args.evidence_bundle.absolute()),
                                      "--evidence-ref", args.evidence_ref]
                    if args.database is not None:
                        arguments += ["--database", str(args.database.absolute()),
                                      "--principal", args.principal, "--parent-ref", args.parent_ref]
                    value = {"mcpServers": {"ncp-knowledge": {
                        "command": sys.executable, "args": arguments,
                        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}}}}
                    output = _path(args.output)
                    with output.open("x", encoding="utf-8") as stream:
                        json.dump(value, stream, indent=2, ensure_ascii=True)
                    value = {"status": "config_created", "path": str(output),
                             "bundle_ref": corpus.ref, "transport": "stdio",
                             "activated": False, "authorizes_execution": False}
                else:
                    value = host.call("ncp_system_status", {})
        _write(value)
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)[:400],
                          "authorizes_execution": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
