"""Export the existing operator catalog as an offline, read-only tooling explorer."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
import tempfile

from . import operator_catalog


def render_html() -> bytes:
    """Embed catalog declarations unchanged, with inert data and hashed assets."""
    payload = {"schema": "ciw.tooling-explorer.v1", "catalog": operator_catalog.catalog()}
    assets = files("ciw").joinpath("web")
    template = assets.joinpath("tooling_explorer.html").read_text(encoding="utf-8")
    css = assets.joinpath("tooling_explorer.css").read_text(encoding="utf-8")
    js = assets.joinpath("tooling_explorer.js").read_text(encoding="utf-8")
    hashed = lambda value: base64.b64encode(sha256(value.encode("utf-8")).digest()).decode("ascii")
    csp = ("default-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'; "
           f"script-src 'sha256-{hashed(js)}'; style-src 'sha256-{hashed(css)}'; img-src data:")
    replacements = {"__CSP__": csp, "__STYLE__": css, "__SCRIPT__": js,
                    "__DATA__": base64.b64encode(json.dumps(payload, allow_nan=False).encode("utf-8")).decode("ascii")}
    if any(template.count(marker) != 1 for marker in replacements):
        raise ValueError("Packaged tooling explorer template is inconsistent")
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    raw = template.encode("utf-8")
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Tooling explorer HTML exceeds byte budget")
    return raw


def write_html(path: Path) -> None:
    """Publish a complete snapshot atomically without replacing an existing file."""
    raw = render_html()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-tooling-", dir=path.parent) as tmp:
        staged = Path(tmp) / "tooling.html"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="net visualize", description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New standalone HTML file; existing paths are never replaced")
    args = parser.parse_args(argv)
    try:
        write_html(args.output)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "created", "output": str(args.output), "read_only": True,
                      "provider_execution": False, "qualification": "not_performed_by_catalog"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
