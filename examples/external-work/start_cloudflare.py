"""Start the NCP origin and its prepared tunnel; prompt locally for the token.

This launcher grants no identities or task authorizations. Processes run until
stopped or the machine shuts down; this is not an installed Windows service.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from ciw.agent_api import parse
from ciw.external_work_http import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cloudflared", required=True, type=Path)
    parser.add_argument("--cloudflared-sha256", required=True)
    parser.add_argument("--log-dir", required=True, type=Path)
    args = parser.parse_args()
    config_path = args.config.resolve(strict=True)
    config = parse(config_path.read_bytes())
    config["database"] = str((config_path.parent / config["database"]).resolve(strict=True))
    create_app(config)  # Validate configuration and provisioned storage before prompting.
    if not all(endpoint["subjects"] for endpoint in config["endpoints"]):
        raise SystemExit("Bind the approved Access subjects in the configuration before starting.")
    binary = args.cloudflared.resolve(strict=True)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != args.cloudflared_sha256:
        raise SystemExit("cloudflared SHA256 does not match the operator-pinned release.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18766))  # Refuse an already occupied origin port.
    if not sys.stdin.isatty():
        raise SystemExit("Run this launcher in an interactive local terminal for hidden token input.")
    args.log_dir.mkdir(parents=True, exist_ok=False)
    token = getpass.getpass("Cloudflare tunnel token (hidden; never paste into chat): ").strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit("Invalid token input.")
    env = os.environ.copy()
    # The secret is passed only to the tunnel child, never in its command line.
    env["TUNNEL_TOKEN"] = token
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    origin = tunnel = None
    try:
        with (args.log_dir / "origin.log").open("ab") as origin_log, \
                (args.log_dir / "tunnel.log").open("ab") as tunnel_log:
            origin = subprocess.Popen([sys.executable, "-m", "ciw.external_work_http",
                "--config", str(config_path)], stdout=origin_log, stderr=origin_log,
                stdin=subprocess.DEVNULL, creationflags=flags)
            deadline = time.monotonic() + 10
            while True:
                if origin.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Origin failed to start; inspect origin.log.")
                try:
                    with socket.create_connection(("127.0.0.1", 18766), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            tunnel = subprocess.Popen([str(binary), "tunnel", "--no-autoupdate", "run"],
                env=env, stdout=tunnel_log, stderr=tunnel_log,
                stdin=subprocess.DEVNULL, creationflags=flags)
            time.sleep(2)
            if tunnel.poll() is not None:
                raise RuntimeError("Tunnel exited; inspect tunnel.log.")
        receipt = {"origin_pid": origin.pid, "tunnel_pid": tunnel.pid,
                   "config": str(config_path), "started_at": time.time(),
                   "status": "processes_started_remote_health_not_verified"}
        (args.log_dir / "processes.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt))
    except BaseException:
        for process in (tunnel, origin):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        raise
    finally:
        env.pop("TUNNEL_TOKEN", None)
        token = ""


if __name__ == "__main__":
    main()
