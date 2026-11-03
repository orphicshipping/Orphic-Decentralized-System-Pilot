"""Private Unix-socket PgBouncer fixture; never a hosted deployment launcher.

SPDX-License-Identifier: AGPL-3.0-or-later
The private test directory gates socket access. Trust authentication is confined
to that directory and does not describe a supported hosted authentication setup.
"""
import getpass
import json
import os
from pathlib import Path
import signal
import subprocess
import time


class LocalPooler:
    def __init__(self, root, binary, postgres_socket, postgres_port):
        self.root, self.binary = Path(root), Path(binary)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.socket = self.root / "socket"
        self.socket.mkdir(mode=0o700)
        self.port = postgres_port
        self.dsn = f"host={self.socket} port={self.port} dbname=postgres user={getpass.getuser()}"
        self.process = self.log = None
        self.users = {getpass.getuser()}
        self.commands = []
        self._users()
        self.config = self.root / "pgbouncer.ini"
        # No startup parameter ignores, no session reset crutch and no prepared
        # statement tracking: clients must really be transaction-pool portable.
        self.config.write_text(
            "[databases]\n"
            f"postgres = host={postgres_socket} port={postgres_port} dbname=postgres\n"
            "[pgbouncer]\n"
            "listen_addr =\n"
            f"listen_port = {self.port}\n"
            f"unix_socket_dir = {self.socket}\n"
            "unix_socket_mode = 0700\n"
            "auth_type = trust\n"
            f"auth_file = {self.root / 'users.txt'}\n"
            "pool_mode = transaction\n"
            "default_pool_size = 4\n"
            "max_client_conn = 40\n"
            "max_prepared_statements = 0\n"
            "server_reset_query_always = 0\n"
            "server_idle_timeout = 30\n"
            "query_wait_timeout = 5\n"
            "server_connect_timeout = 3\n"
            "log_connections = 0\n"
            "log_disconnections = 0\n"
        )
        self.config.chmod(0o600)

    def _record(self, action, exit_code=None):
        self.commands.append({"action": action, "exit_code": exit_code})
        (self.root / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n")

    def _users(self):
        path = self.root / "users.txt"
        path.write_text("".join(f'"{name}" ""\n' for name in sorted(self.users)))
        path.chmod(0o600)

    def register_user(self, name):
        # Test-only locally created SQL role; never an arbitrary configuration line.
        if not name.isascii() or not name.replace("_", "").isalnum():
            raise ValueError("invalid local test role")
        self.users.add(name)
        self._users()
        if self.process is not None:
            os.kill(self.process.pid, signal.SIGHUP)

    def start(self):
        self.log = (self.root / "pgbouncer.log").open("ab")
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        libraries = self.binary.parent.parent / "lib/x86_64-linux-gnu"
        if libraries.is_dir():
            env["LD_LIBRARY_PATH"] = str(libraries)
        self.process = subprocess.Popen([str(self.binary), str(self.config)], env=env,
                                        stdin=subprocess.DEVNULL, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        self.wait_ready()
        self._record("start private transaction pooler, TCP disabled")

    def wait_ready(self):
        import psycopg
        # A restarted backend may be ready before PgBouncer's documented login
        # backoff expires. Probe SELECT 1 only; never retry a queue transition.
        deadline, attempts = time.monotonic() + 25, 0
        while time.monotonic() < deadline and self.process.poll() is None:
            attempts += 1
            try:
                with psycopg.connect(self.dsn, connect_timeout=1, prepare_threshold=None) as db:
                    if db.execute("SELECT 1").fetchone() != (1,):
                        raise RuntimeError("unexpected pooler readiness result")
                    self._record("read-only pooler readiness after " + str(attempts) + " probes")
                    return
            except psycopg.Error:
                time.sleep(0.05)
        raise RuntimeError("PgBouncer did not start; see " + str(self.root / "pgbouncer.log"))

    def close(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
            self._record("stop private pooler", self.process.returncode)
        if self.log is not None:
            self.log.close()
