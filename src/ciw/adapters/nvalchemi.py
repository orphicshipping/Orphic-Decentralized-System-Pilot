"""Operator-bound, source-pinned ALCHEMI CPU qualification adapter.

Uses NET's existing bounded subprocess transport. This binding is local code,
not loaded from retained JSON, and is not in the default operation registry.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from .nvalchemi_contract import OPERATION, checked_input, validate_output
from .protocol import AdapterRefusal
from .subprocess import PinnedSubprocessAdapter, _json

_PROBE = """import importlib.metadata as m, json, platform
packages = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in m.distributions()}
print(json.dumps({'python_version': platform.python_version(), 'dependencies': packages,
                  'platform': platform.system(), 'machine': platform.machine()}))
"""
_FILES = """import importlib.metadata as m, hashlib, json, pathlib
hashes = {}
for name in ('nvalchemi-toolkit', 'nvalchemi-toolkit-ops', 'torch', 'warp-lang', 'numpy'):
    dist = m.distribution(name)
    entries = {}
    for f in dist.files or ():
        p = pathlib.PurePosixPath(str(f))
        if p.is_absolute() or '..' in p.parts or '__pycache__' in p.parts or p.suffix == '.pyc':
            continue
        if any(x.endswith(('.dist-info', '.egg-info')) for x in p.parts):
            continue
        path = pathlib.Path(dist.locate_file(f))
        if path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for b in iter(lambda: stream.read(1024*1024), b''): h.update(b)
            entries[str(p)] = h.hexdigest()
    if not entries: raise RuntimeError('Missing installed provider file inventory: ' + name)
    hashes[name] = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
print(json.dumps(hashes))
"""


class NvalchemiLJBinding(PinnedSubprocessAdapter):
    def __init__(self, repository_root, revision, *, python_executable, timeout_seconds=90.0):
        root = Path(repository_root).resolve()
        lock_path = root / "runtimes/nvalchemi-lj/environment.json"
        lock = json.loads(lock_path.read_text())
        if lock.get("schema") != "ciw.nvalchemi-cpu-environment.v1":
            raise ValueError("Unsupported ALCHEMI environment lock")
        self._expected_host = (lock["platform"], lock["machine"])
        super().__init__(root, revision, "ciw.adapters.nvalchemi_worker", source_root="src",
                         python_executable=python_executable, timeout_seconds=timeout_seconds,
                         max_output_bytes=128 * 1024, expected_python_version=lock["python_version"],
                         expected_dependencies=lock["packages"])
        self._lock_sha256 = sha256(lock_path.read_bytes()).hexdigest()
        self._provider_files = self._installed_files()

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
        code, payload = self._run(_FILES, [])
        if code:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "Cannot fingerprint installed ALCHEMI runtime files")
        return _json(payload)

    def runtime_identity(self):
        identity = super().runtime_identity()
        if self._installed_files() != self._provider_files:
            raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "Installed ALCHEMI runtime file bytes changed")
        return {**identity, "environment_lock_sha256": self._lock_sha256,
                "provider_files_sha256": dict(self._provider_files),
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
