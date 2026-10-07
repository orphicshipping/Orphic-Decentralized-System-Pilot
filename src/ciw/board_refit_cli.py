"""Host-selected local Board MCP profiles; no grants are created by MCP tools.

SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import MAX_BYTES, bytes_ref, keys, load, save_new, text
from .operations.runner import check_seal, seal
from .core.identities import content_identity

PROFILE_SCHEMA = "ciw.board-refit-profile.v1"


def safe_path(path, *, existing=False):
    value = Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
           for p in (value, *value.parents)):
        raise ValueError("Linked profile paths are not supported")
    if existing and not value.is_file():
        raise ValueError("Expected an existing local profile input")
    return value


def _binding(path):
    path = safe_path(path, existing=True)
    # load applies the existing control-document size and JSON limits.
    value = load(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Profile input exceeds byte budget")
    if value != load(path, expected_sha256=bytes_ref(raw)):
        raise ValueError("Profile input changed during binding")
    return {"sha256": bytes_ref(raw)}, value


def read_profile(path):
    path = safe_path(path, existing=True)
    profile = load(path)
    keys(profile, {"schema", "record_digest", "investigation_ref", "board", "source", "grant_path", "grant_ref"})
    check_seal(profile)
    if profile["schema"] != PROFILE_SCHEMA:
        raise ValueError("Unsupported Board refit profile")
    text(profile["investigation_ref"])
    for label in ("board", "source"):
        keys(profile[label], {"sha256"})
    board = load(safe_path(path.parent / "board.json", existing=True), expected_sha256=profile["board"]["sha256"])
    source = load(safe_path(path.parent / "source.json", existing=True), expected_sha256=profile["source"]["sha256"])
    grant_path = safe_path(profile["grant_path"], existing=True)
    if not Path(profile["grant_path"]).is_absolute():
        raise ValueError("Grant path must be absolute")
    grant = load(grant_path)
    if content_identity(grant) != profile["grant_ref"]:
        raise ValueError("Selected grant has changed or been revoked")
    return path, profile, board, source, grant


def _client_config(profile_path):
    return {"mcpServers": {"ncp-board-refit": {
        "command": sys.executable,
        "args": ["-m", "ciw.board_refit_cli", "serve", "--profile", str(profile_path)],
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    }}}


def open_host(profile_path):
    from .board_refit import BoardRefitHost
    path, profile, board, source, grant = read_profile(profile_path)
    grant_path = Path(profile["grant_path"])

    def authority_check():
        # The trusted local host may revoke by removing/changing this grant.
        try:
            current = load(safe_path(grant_path, existing=True))
            if content_identity(current) != profile["grant_ref"]:
                raise ValueError("Grant changed")
        except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError):
            raise PermissionError("Selected grant is unavailable or revoked") from None

    runtime = safe_path(path.parent / "runtime")
    return BoardRefitHost(board, runtime, source=source, grant=grant,
                         investigation_ref=profile["investigation_ref"],
                         authority_check=authority_check, resume=runtime.exists())


def configure(board_path, source_path, grant_path, output_dir, investigation_ref):
    board = load(safe_path(board_path, existing=True))
    source = load(safe_path(source_path, existing=True))
    grant_path = safe_path(grant_path, existing=True)
    grant = load(grant_path)
    output = safe_path(output_dir)
    text(investigation_ref)
    output.mkdir(parents=True, exist_ok=False)
    return _configure_values(board, source, grant_path, grant, output, investigation_ref)


def _configure_values(board, source, grant_path, grant, output, investigation_ref):
    save_new(output / "board.json", board)
    save_new(output / "source.json", source)
    board_binding, _ = _binding(output / "board.json")
    source_binding, _ = _binding(output / "source.json")
    profile = seal({"schema": PROFILE_SCHEMA, "investigation_ref": investigation_ref,
                    "board": board_binding, "source": source_binding,
                    "grant_path": str(grant_path), "grant_ref": content_identity(grant)})
    path = output / "profile.json"
    save_new(path, profile)
    host = open_host(path)  # validate all bindings without running a provider
    try:
        host.descriptions()
    finally:
        if hasattr(host, "close"):
            host.close()
    save_new(output / "mcp-config.json", _client_config(path))
    return {"status": "configured", "profile": str(path),
            "mcp_config": str(output / "mcp-config.json"), "transport": "stdio",
            "activated": False, "providers_executed": False,
            "investigation_ref": investigation_ref}


def demo(output_dir):
    from .instruments import make_demo_run
    from .visual_board import demo_board
    board, source = demo_board(), make_demo_run()
    output = safe_path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    grant = {"schema": "ciw.board-refit-grant.v1", "grant_id": "synthetic-oscillator-refit-demo",
             "board_ref": board["record_digest"], "source_evidence_id": source["evidence_id"],
             "source_ref": content_identity(source), "allow_baseline": True,
             "targets": [{"node_id": "statistics", "parameter": "channel"}],
             "max_candidate_runs": 4, "max_previews": 16}
    grant_path = output / "grant.json"
    save_new(grant_path, grant)
    result = _configure_values(board, source, grant_path, grant, output, "demo:oscillator-board-refit")
    result.update({"synthetic": True, "physical_qualification": "not_established"})
    save_new(output / "example-preview.json", {"base_board_ref": board["record_digest"],
             "node_id": "statistics", "parameter": "channel", "replacement": "v"})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ncp refit", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure", help="Select existing host inputs and grant; runs no providers")
    setup.add_argument("--board", type=Path, required=True)
    setup.add_argument("--source", type=Path, required=True)
    setup.add_argument("--grant", type=Path, required=True)
    setup.add_argument("--output-dir", type=Path, required=True)
    setup.add_argument("--investigation-ref", required=True)
    demonstration = commands.add_parser("demo", help="Create a synthetic local demonstration profile and bounded operator grant")
    demonstration.add_argument("--output-dir", type=Path, required=True)
    serve = commands.add_parser("serve", help="Serve or resume the selected local profile over MCP stdio")
    serve.add_argument("--profile", type=Path, required=True)
    inspect = commands.add_parser("status", help="Inspect profile and retained state without running providers")
    inspect.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            value = configure(args.board, args.source, args.grant, args.output_dir, args.investigation_ref)
        elif args.command == "demo":
            value = demo(args.output_dir)
        else:
            host = open_host(args.profile)
            try:
                if args.command == "serve":
                    import anyio
                    from .board_refit_mcp import serve_stdio
                    anyio.run(serve_stdio, host)
                    return 0
                value = host.call("ncp_board_status", {})
            finally:
                if hasattr(host, "close"):
                    host.close()
        print(json.dumps(value, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)[:300]}), file=sys.stderr)
        return 2
    except ImportError:
        print(json.dumps({"status": "refused", "reason": "Install the board-mcp optional dependency to serve MCP"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
