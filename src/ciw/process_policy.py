"""Host-created Linux process limits and optional Bubblewrap isolation.

Limits mode is not a network/filesystem sandbox. Bubblewrap mode requires an
actual successful namespace probe before launching the requested executable.
Neither mode attests dependencies or makes caller-supplied executable paths safe.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from types import MappingProxyType

from .adapters.protocol import AdapterRefusal

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
_BROAD_ROOTS = {"/", "/home", "/workspace", "/tmp", "/usr", "/etc", "/var", "/proc", "/dev", "/sys", "/run"}


class ProcessPolicyHeld(AdapterRefusal):
    def __init__(self, reason="sandbox_backend_unavailable"):
        self.reason = reason
        super().__init__("PROCESS_POLICY_HELD", "Selected process policy is unavailable: " + reason)


def executable_sha256(path: str | Path) -> str:
    """Local host helper; never derives authority from a tool-call argument."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("process policy executable must be an absolute regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111 or info.st_mode & 0o6000:
            raise ValueError("unsafe process policy executable")
        digest = sha256()
        while block := os.read(fd, 65536):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ProcessPolicy:
    mode: str = "limits"
    environment: Mapping[str, str] = field(default_factory=lambda: dict(_DEFAULT_ENV))
    wall_seconds: float = 30.0
    max_input_bytes: int = 262144
    max_output_bytes: int = 262144
    cpu_seconds: int = 30
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    file_bytes: int = 16 * 1024 * 1024
    max_open_files: int = 128
    max_processes: int = 512
    read_only_roots: tuple[str, ...] = ()
    prlimit_executable: str = "/usr/bin/prlimit"
    expected_prlimit_sha256: str | None = None
    bwrap_executable: str = "/usr/bin/bwrap"
    expected_bwrap_sha256: str | None = None

    def __post_init__(self):
        if self.mode not in {"limits", "bubblewrap"}:
            raise ValueError("unknown process policy mode")
        if type(self.wall_seconds) not in (int, float) or not math.isfinite(self.wall_seconds) or not 0 < self.wall_seconds <= 300:
            raise ValueError("invalid process wall limit")
        for name, minimum, maximum in (("max_input_bytes", 1, 32*1024*1024),
                ("max_output_bytes", 1, 32*1024*1024), ("cpu_seconds", 1, 300),
                ("memory_bytes", 32*1024*1024, 64*1024*1024*1024),
                ("file_bytes", 0, 1024*1024*1024), ("max_open_files", 16, 4096),
                ("max_processes", 1, 4096)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("invalid process resource limit: " + name)
        if not isinstance(self.environment, Mapping) or len(self.environment) > 64:
            raise ValueError("invalid explicit process environment")
        env = dict(self.environment)
        for key, value in env.items():
            if (type(key) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key)
                    or type(value) is not str or len(value.encode("utf-8")) > 4096 or "\0" in value):
                raise ValueError("invalid explicit process environment")
        if sum(len(k.encode())+len(v.encode()) for k,v in env.items()) > 32768:
            raise ValueError("process environment exceeds budget")
        if any(k in env for k in ("LD_PRELOAD", "LD_AUDIT", "PYTHONHOME", "PYTHONPATH", "NODE_OPTIONS", "NODE_PATH")):
            raise ValueError("process environment contains an injection variable")
        path = env.get("PATH", "")
        if not path or any(not part.startswith("/") or "/../" in part or part.endswith("/..") for part in path.split(":")):
            raise ValueError("process PATH must contain only absolute host directories")
        object.__setattr__(self, "environment", MappingProxyType(env))
        roots = []
        for item in self.read_only_roots:
            p = Path(item)
            if (not p.is_absolute() or ".." in p.parts or str(p) in {"/", "/home", "/workspace", "/tmp", "/proc", "/dev", "/sys", "/run"}
                    or any(q.is_symlink() for q in (p, *p.parents)) or not p.is_dir()):
                raise ValueError("read-only roots must be explicit real host directories")
            roots.append(str(p))
        if len(set(roots)) != len(roots) or len(roots) > 32:
            raise ValueError("invalid read-only root list")
        object.__setattr__(self, "read_only_roots", tuple(sorted(roots)))
        if self.expected_prlimit_sha256 is None or not _HEX.fullmatch(self.expected_prlimit_sha256):
            raise ValueError("an explicit prlimit executable digest is required")
        if self.mode == "bubblewrap" and (not self.read_only_roots or self.expected_bwrap_sha256 is None
                or not _HEX.fullmatch(self.expected_bwrap_sha256)):
            raise ValueError("Bubblewrap requires explicit roots and executable digest")

    def _check_binaries(self):
        if sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW"):
            raise ProcessPolicyHeld("linux_process_policy_required")
        launchers = [(self.prlimit_executable, self.expected_prlimit_sha256)]
        if self.mode == "bubblewrap":
            launchers.append((self.bwrap_executable, self.expected_bwrap_sha256))
        for path, expected in launchers:
            try:
                actual = executable_sha256(path)
            except (OSError, ValueError) as exc:
                raise ProcessPolicyHeld("pinned_launcher_unavailable") from exc
            if actual != expected:
                raise ProcessPolicyHeld("pinned_launcher_changed")

    def identity(self):
        config = {name: getattr(self, name) for name in self.__dataclass_fields__}
        config["environment"] = dict(self.environment)
        encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        return {"schema":"ciw.process-policy.v1", "mode":self.mode,
                "policy_sha256":"sha256:"+sha256(encoded).hexdigest(),
                "environment_keys":sorted(self.environment),
                "network_isolation_required":self.mode == "bubblewrap",
                "filesystem_isolation_required":self.mode == "bubblewrap",
                "process_limit_scope":"Linux RLIMIT_NPROC per real UID; not a per-job cgroup",
                "memory_limit_scope":"address space per process; not aggregate descendants",
                "descendant_cleanup":"owned process group; escaped sessions are not contained in limits mode",
                "wall_seconds":self.wall_seconds,"max_input_bytes":self.max_input_bytes,
                "max_output_bytes":self.max_output_bytes,"cpu_seconds":self.cpu_seconds,
                "memory_bytes":self.memory_bytes,"file_bytes":self.file_bytes,
                "max_open_files":self.max_open_files,"max_processes":self.max_processes}

    def _limits(self):
        return [self.prlimit_executable, f"--cpu={self.cpu_seconds}:{self.cpu_seconds}",
                f"--as={self.memory_bytes}:{self.memory_bytes}", f"--fsize={self.file_bytes}:{self.file_bytes}",
                f"--nofile={self.max_open_files}:{self.max_open_files}",
                f"--nproc={self.max_processes}:{self.max_processes}", "--core=0:0", "--"]

    def _sandbox(self, command, cwd):
        # Nothing from the host root is visible except these explicit mounts.
        args = [self.bwrap_executable, "--unshare-all", "--die-with-parent", "--new-session",
                "--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        args += ["--bind", str(cwd), str(cwd)]
        # Read-only roots take precedence if the working directory overlaps a
        # selected source/runtime mount. A writable cwd must not mask that pin.
        for root in self.read_only_roots:
            args += ["--ro-bind", root, root]
        # Merged-/usr distributions need these standard runtime aliases.
        for alias in ("/bin", "/sbin", "/lib", "/lib64"):
            if Path(alias).is_symlink():
                target = str(Path(alias).resolve())
                if any(Path(target).is_relative_to(root) for root in self.read_only_roots):
                    args += ["--symlink", target, alias]
        args += ["--chdir", str(cwd)]
        for key, value in sorted(self.environment.items()):
            args += ["--setenv", key, value]
        return args + ["--", *command]

    def qualify(self, cwd: Path):
        cwd = self._working_directory(cwd)
        self._check_binaries()
        if self.mode == "limits":
            return {"status":"available", "mode":"limits", "network_isolated":False,
                    "filesystem_isolated":False}
        from .adapters.subprocess import _bounded_process
        true = shutil.which("true", path=self.environment["PATH"])
        if true is None:
            raise ProcessPolicyHeld("sandbox_probe_executable_missing")
        command = self._limits() + self._sandbox([true], cwd)
        code, _ = _bounded_process(command, cwd=cwd, timeout=min(self.wall_seconds, 3.0),
                                   limit=4096, environment=self.environment)
        if code:
            raise ProcessPolicyHeld("namespace_probe_refused")
        return {"status":"available", "mode":"bubblewrap", "network_isolated":True,
                "filesystem_isolated":True, "scope":"explicit roots; read-only roots take precedence over work directory"}

    @staticmethod
    def _working_directory(cwd):
        cwd = Path(cwd)
        if (not cwd.is_absolute() or ".." in cwd.parts or str(cwd) in _BROAD_ROOTS or not cwd.is_dir()
                or any(p.is_symlink() for p in (cwd, *cwd.parents))):
            raise ValueError("unsafe process working directory")
        return cwd

    def prepare(self, command, cwd, stdin):
        if (type(command) is not list or not command or any(type(a) is not str or "\0" in a for a in command)
                or sum(len(a.encode("utf-8")) for a in command) > 65536):
            raise ValueError("invalid bounded process command")
        if type(stdin) is not bytes or len(stdin) > self.max_input_bytes:
            raise AdapterRefusal("INPUT_LIMIT", "Process input exceeds its host policy")
        cwd = self._working_directory(cwd)
        self.qualify(cwd)
        executable = command[0]
        if not Path(executable).is_absolute():
            executable = shutil.which(executable, path=self.environment["PATH"])
            if executable is None:
                raise ProcessPolicyHeld("target_executable_unavailable")
        selected = [executable, *command[1:]]
        if self.mode == "bubblewrap":
            selected = self._sandbox(selected, cwd)
        return self._limits() + selected, dict(self.environment)
