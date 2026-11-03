"""Private local-only qualification processes; not a deployment launcher.

SPDX-License-Identifier: AGPL-3.0-or-later
"""
import json
import getpass
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalRuntime:
    def __init__(self, root, pg_bin, nats_server):
        self.root, self.pg_bin, self.nats_server = Path(root), Path(pg_bin), Path(nats_server)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.socket = self.root / "socket"
        self.socket.mkdir(mode=0o700)
        self.data = self.root / "postgres-data"
        self.pg_port, self.nats_port = _port(), _port()
        self.pg = self.nats = None
        self.logs, self.commands = [], []
        self.token = secrets.token_urlsafe(24)
        self.dsn = f"host={self.socket} port={self.pg_port} dbname=postgres user={getpass.getuser()}"
        self.direct_dsn = self.dsn
        self.pooler = None
        self.nats_url = f"nats://127.0.0.1:{self.nats_port}"

    def _record(self, action, exit_code=None):
        self.commands.append({"action": action, "exit_code": exit_code})
        (self.root / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n")

    def start(self):
        self.dsn = f"host={self.socket} port={self.pg_port} dbname=postgres user={getpass.getuser()}"
        share = self.pg_bin.parent.parent.parent.parent / "share/postgresql" / self.pg_bin.parent.name
        # Debian separates bin/ and share/. Explicit -L makes extraction relocatable.
        command = [str(self.pg_bin / "initdb"), "-D", str(self.data), "-L", str(share),
                   "--auth-local=trust", "--auth-host=reject", "--no-locale", "--encoding=UTF8"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        (self.root / "initdb.log").write_bytes(result.stdout)
        self._record("initdb private Unix-socket database", result.returncode)
        if result.returncode:
            raise RuntimeError("initdb failed; see " + str(self.root / "initdb.log"))
        self.start_postgres()
        config = self.root / "nats.conf"
        config.write_text(f"host: 127.0.0.1\nport: {self.nats_port}\nmax_connections: 32\nmax_payload: 4096\n"
                          f"authorization {{ token: {json.dumps(self.token)} }}\n"
                          f"jetstream {{ store_dir: {json.dumps(str(self.root / 'jetstream'))}, "
                          "max_memory_store: 16MB, max_file_store: 64MB }\n")
        config.chmod(0o600)
        self.start_nats()

    def start_postgres(self):
        import psycopg
        log = (self.root / "postgres.log").open("ab"); self.logs.append(log)
        self.pg = subprocess.Popen([str(self.pg_bin / "postgres"), "-D", str(self.data),
                                    "-k", str(self.socket), "-p", str(self.pg_port),
                                    "-c", "listen_addresses=", "-c", "unix_socket_permissions=0700",
                                    "-c", "max_connections=40", "-c", "shared_buffers=16MB",
                                    "-c", "fsync=on", "-c", "full_page_writes=on"],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.pg.poll() is None:
            try:
                with psycopg.connect(self.direct_dsn, connect_timeout=1):
                    self._record("start PostgreSQL, TCP disabled")
                    if self.pooler is not None:
                        self.pooler.wait_ready()
                    return
            except psycopg.OperationalError:
                time.sleep(0.05)
        raise RuntimeError("PostgreSQL did not start; see " + str(self.root / "postgres.log"))

    def start_nats(self):
        log = (self.root / "nats.log").open("ab"); self.logs.append(log)
        self.nats = subprocess.Popen([str(self.nats_server), "-c", str(self.root / "nats.conf")],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.nats.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", self.nats_port), timeout=0.2) as sock:
                    if sock.recv(1024).startswith(b"INFO "):
                        self._record("start authenticated loopback-only NATS JetStream")
                        return
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError("NATS did not start; see " + str(self.root / "nats.log"))

    def stop_process(self, name, *, crash=False):
        process = getattr(self, name)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL if crash else signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            self._record(("crash " if crash else "stop ") + name, process.returncode)
        setattr(self, name, None)

    def close(self):
        if self.pooler is not None:
            self.pooler.close()
        self.stop_process("nats")
        self.stop_process("pg")
        for log in self.logs:
            log.close()
