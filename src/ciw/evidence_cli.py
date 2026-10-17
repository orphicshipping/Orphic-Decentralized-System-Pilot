"""Trusted local ingestion and binding of selected source text.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .evidence_corpus import EvidenceCorpus, compile_evidence
from .external_work import ExternalWorkStore
from .knowledge_cli import _path
from .proto_corpus import ProtoCorpus


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, prog="ncp evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "status", "search", "representations", "source", "span", "bind-context"):
        sub = commands.add_parser(name)
        sub.add_argument("--catalog", type=Path, required=True)
        sub.add_argument("--catalog-ref", required=True)
        if name == "build":
            sub.add_argument("--selection", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
        else:
            sub.add_argument("--bundle", type=Path, required=True)
            sub.add_argument("--expected-ref", required=True)
        if name in ("search", "representations", "source"):
            sub.add_argument("--limit", type=int, default=10)
            sub.add_argument("--offset", type=int, default=0)
        if name == "search":
            sub.add_argument("--query", required=True)
        if name in ("search", "representations"):
            sub.add_argument("--record-id")
        if name == "source":
            sub.add_argument("--representation-id", required=True)
        if name in ("span", "bind-context"):
            sub.add_argument("--span-id", required=True)
        if name == "bind-context":
            sub.add_argument("--database", type=Path, required=True)
            sub.add_argument("--parent-ref", required=True)
            sub.add_argument("--disclosure-ref", required=True,
                             help="Reference to the host's verified task-context disclosure decision")
    args = parser.parse_args(argv)
    try:
        catalog = ProtoCorpus.open(args.catalog, expected_ref=args.catalog_ref)
        if args.command == "build":
            ref = compile_evidence(catalog=catalog, selection=args.selection, output=args.output)
            data = {"status": "compiled", "bundle_ref": ref, "catalog_ref": catalog.ref,
                    "bundle": str(args.output.absolute()), "authorizes_execution": False}
        else:
            corpus = EvidenceCorpus.open(args.bundle, expected_ref=args.expected_ref, catalog=catalog)
            if args.command == "search":
                data = corpus.search(args.query, limit=args.limit, offset=args.offset,
                                     record_id=args.record_id)
            elif args.command == "representations":
                data = corpus.representations(record_id=args.record_id, limit=args.limit, offset=args.offset)
            elif args.command == "source":
                data = corpus.source(args.representation_id, limit=args.limit, offset=args.offset)
            elif args.command == "span":
                data = corpus.span(args.span_id)
            elif args.command == "bind-context":
                store = ExternalWorkStore(_path(args.database, existing=True))
                data = corpus.bind_context(store, span_id=args.span_id, parent_ref=args.parent_ref,
                                           disclosure_ref=args.disclosure_ref)
            else:
                data = corpus.overview()
        print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)[:400],
                          "authorizes_execution": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
