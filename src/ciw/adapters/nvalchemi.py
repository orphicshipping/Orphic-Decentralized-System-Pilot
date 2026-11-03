"""Operator-bound, source-pinned ALCHEMI CPU qualification adapter.

Uses NET's existing bounded subprocess transport. This binding is local code,
not loaded from retained JSON, and is not in the default operation registry.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile

from .nvalchemi_contract import OPERATION, checked_input, validate_output
from .protocol import AdapterRefusal
from .subprocess import PinnedSubprocessAdapter, _bounded_process, _json

_PROBE = """import importlib.metadata as m, json, platform
packages = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in m.distributions()}
print(json.dumps({'python_version': platform.python_version(), 'dependencies': packages,
                  'platform': platform.system(), 'machine': platform.machine()}))
"""
_ISOLATED = """import sys
# -S prevents .pth and sitecustomize execution. Add only the explicitly bound
# wheel installation, after standard-library paths, without site.addsitedir.
sys.path.append(sys.argv.pop(1))
source = sys.argv.pop(1)
exec(compile(source, '<ciw-nvalchemi-worker>', 'exec'), {'__name__': '__main__'})
"""


class NvalchemiLJBinding(PinnedSubprocessAdapter):
    def __init__(self, repository_root, revision, *, python_executable, timeout_seconds=90.0,
                 process_policy=None):
        from ..process_policy import require_companion_policy
        process_policy = require_companion_policy(process_policy)
        root = Path(repository_root).resolve()
        lock_path = root / "runtimes/nvalchemi-lj/environment.json"
        lock = json.loads(lock_path.read_text())
        if lock.get("schema") != "ciw.nvalchemi-cpu-environment.v1":
            raise ValueError("Unsupported ALCHEMI environment lock")
        self._expected_host = (lock["platform"], lock["machine"])
        # The qualified Linux profile uses a conventional isolated venv. Keep
        # its interpreter symlink path so the venv, not the base installation,
        # selects the one allowed third-party import root.
        executable = Path(python_executable).expanduser().absolute()
        major, minor, _patch = lock["python_version"].split(".")
        if not major.isdigit() or not minor.isdigit():
            raise ValueError("Invalid locked ALCHEMI Python version")
        self._site_packages = executable.parent.parent / "lib" / f"python{major}.{minor}" / "site-packages"
        # Snapshot in the host before starting any provider interpreter. RECORD
        # inventories alone miss injected files; -B alone still reads bytecode.
        self._provider_files = self._installed_files()
        super().__init__(root, revision, "ciw.adapters.nvalchemi_worker", source_root="src",
                         python_executable=python_executable, timeout_seconds=timeout_seconds,
                         max_output_bytes=128 * 1024, expected_python_version=lock["python_version"],
                         expected_dependencies=lock["packages"], process_policy=process_policy)
        self._lock_sha256 = sha256(lock_path.read_bytes()).hexdigest()

    def _run(self, code, arguments, stdin=b""):
        from ..process_policy import require_companion_policy
        selected = require_companion_policy(self.process_policy)
        # Check before even the metadata probe, so a changed .pth, extension,
        # source module or sourceless .pyc never runs before drift refusal.
        if self._installed_files() != self._provider_files:
            raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "Installed ALCHEMI environment file bytes changed")
        with tempfile.TemporaryDirectory(prefix="ciw-nvalchemi-process-") as directory:
            return _bounded_process(
                [str(self.python_executable), "-I", "-S", "-B", "-X",
                 f"pycache_prefix={directory}/bytecode", "-c", _ISOLATED,
                 str(self._site_packages), code, *arguments],
                cwd=Path(directory), timeout=self.timeout_seconds,
                limit=self.max_output_bytes, stdin=stdin, process_policy=selected,
            )

    def _probe(self):
        code, payload = self._run(_PROBE, [])
        if code:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "Cannot inspect the isolated ALCHEMI environment")
        result = _json(payload)
        if not isinstance(result, dict) or set(result) != {"python_version", "dependencies", "platform", "machine"}:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "ALCHEMI environment probe failed")
        if (result.pop("platform"), result.pop("machine")) != self._expected_host:
            raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "ALCHEMI host differs from the qualified platform")
        return result

    def _installed_files(self):
        """Bind every installed byte, including unlisted and cached bytecode.

        Source-associated caches are additionally bypassed with a fresh prefix.
        Legacy/sourceless bytecode and native extensions remain executable only
        when their bytes match this operator-trusted initial snapshot. This is
        drift detection, not authentication or an OS sandbox.
        """
        root = self._site_packages
        if not root.is_dir() or root.is_symlink():
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "A conventional local ALCHEMI venv is required")
        entries = {}
        try:
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "ALCHEMI dependency symlinks are not bound")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "ALCHEMI dependency changed file type")
                h = sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                entries[path.relative_to(root).as_posix()] = h.hexdigest()
        except OSError as exc:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "Cannot fingerprint the ALCHEMI environment") from exc
        if not entries:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "ALCHEMI environment is empty")
        return {"site-packages": sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()}

    def runtime_identity(self):
        identity = super().runtime_identity()
        return {**identity, "environment_lock_sha256": self._lock_sha256,
                "provider_files_sha256": dict(self._provider_files),
                "dependency_import_policy": "bound-site-packages-no-site-startup-fresh-bytecode-prefix-v1",
                "profile": {"device": "cpu", "precision": "float64", "seed": 0,
                            "torch_threads": 1, "checkpoint": None}}

    def evaluate(self, request):
        request = checked_input(request)
        result = self.invoke(OPERATION, request)
        try:
            validate_output(result, request)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise AdapterRefusal("MALFORMED_RESPONSE", str(exc)) from exc
        return result
