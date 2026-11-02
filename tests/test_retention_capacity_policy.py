"""Private hosts refuse capacity exhaustion without evicting earlier evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import ciw
from ciw import coordinator, retention
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    return retention.Store("private-owner", evict=False)


def records(store):
    directory = retention._owner_dir(store.owner)
    return {p.name: p.read_bytes() for p in directory.glob("*.json")
            if p.name.startswith(("retained-", "failure-"))}


def test_count_and_failure_records_share_capacity_and_release_reopens_it(store):
    first = store.keep({"i": 0}, kind="fixture", catalog={})
    for i in range(1, retention._LIMIT):
        store.keep_failure({"i": i})
    before = records(store)
    with pytest.raises(ValueError, match="capacity exhausted"):
        store.keep({"extra": True}, kind="fixture", catalog={})
    assert records(store) == before
    assert store.native(first) == {"i": 0}
    store.release(first)
    new = store.keep({"after_release": True}, kind="fixture", catalog={})
    assert len(records(store)) == retention._LIMIT
    assert store.native(new) == {"after_release": True}


def test_serialized_unicode_bytes_are_bounded_across_record_kinds(store):
    value = {"value": "\u2603" * 340000}
    first = store.keep(value, kind="fixture", catalog={})
    before = records(store)
    assert 2_000_000 < sum(map(len, before.values())) < retention._BYTE_LIMIT
    with pytest.raises(ValueError, match="capacity exhausted"):
        store.keep_failure(value)
    assert records(store) == before
    assert store.native(first) == value


def test_claim_capacity_refusal_preserves_intent_and_retries_same_identity(store):
    sender = retention.Store("sender", evict=False)
    source = sender.keep({"claim": "fixture"}, kind="fixture", catalog={})
    grant = coordinator.handoff(sender, source, store.owner)["grant_id"]
    ids = [store.keep({"i": i}, kind="fixture", catalog={}) for i in range(retention._LIMIT)]
    before = records(store)
    with pytest.raises(ValueError, match="capacity exhausted"):
        coordinator.claim(store, grant)
    assert records(store) == before
    intent_path = retention._root() / "grants" / (grant + ".intent.json")
    intent = json.loads(intent_path.read_text())
    store.release(ids[0])
    result = coordinator.claim(store, grant)
    assert result["replayed"] is True
    assert store._read(result["artifact_id"])["sequence"] == intent["sequence"]
    assert store.native(result["artifact_id"]) == {"claim": "fixture"}
    assert coordinator.claim(store, grant)["artifact_id"] == result["artifact_id"]
    assert len(records(store)) == retention._LIMIT


def test_post_publication_error_does_not_erase_committed_artifact(store, monkeypatch):
    original = retention._publish
    failure = OSError("after durable publication")

    def publish(directory, name, raw):
        original(directory, name, raw)
        if name.startswith("retained-"):
            raise failure

    monkeypatch.setattr(retention, "_publish", publish)
    with pytest.raises(OSError) as caught:
        store.keep({"committed": True}, kind="fixture", catalog={})
    assert caught.value is failure
    existing = records(store)
    assert len(existing) == 1
    artifact = next(iter(existing)).removesuffix(".json")
    assert store.native(artifact) == {"committed": True}


def test_unsafe_existing_record_refuses_without_deleting_anything(store, tmp_path):
    original = store.keep({"preserved": True}, kind="fixture", catalog={})
    foreign = tmp_path / "foreign"
    foreign.write_text("external bytes")
    link = retention._owner_dir(store.owner) / ("failure-" + "a" * 32 + ".json")
    link.symlink_to(foreign)
    with pytest.raises(ValueError, match="safely"):
        store.keep({"new": True}, kind="fixture", catalog={})
    assert link.is_symlink() and foreign.read_text() == "external bytes"
    assert store.native(original) == {"preserved": True}


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
                    reason="Cross-process capacity requires qualified POSIX locking")
def test_two_processes_cannot_overfill_last_slot(store, tmp_path):
    for i in range(retention._LIMIT - 1):
        store.keep({"i": i}, kind="fixture", catalog={})
    child = """
import json, sys, time
from pathlib import Path
from ciw.retention import Store
until=time.monotonic()+10
while not Path(sys.argv[1]).exists():
    if time.monotonic()>until: raise TimeoutError('start barrier')
    time.sleep(.005)
try:
    value=Store('private-owner', evict=False).keep({'child':sys.argv[2]},kind='fixture',catalog={})
    print(json.dumps({'artifact':value}))
except ValueError as exc:
    assert 'capacity exhausted' in str(exc)
    print(json.dumps({'refused':True}))
"""
    start = tmp_path / "start"
    env = {**os.environ, "PYTHONPATH": str(Path(ciw.__file__).resolve().parents[1])}
    processes = [subprocess.Popen([sys.executable, "-c", child, str(start), str(i)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 cwd=tmp_path, env=env) for i in range(2)]
    try:
        start.write_text("go")
        outcomes = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            outcomes.append(json.loads(out))
        assert sum("artifact" in result for result in outcomes) == 1
        assert sum("refused" in result for result in outcomes) == 1
        assert len(records(store)) == retention._LIMIT
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_default_eviction_policy_is_preserved_and_policy_is_explicit(store):
    ordinary = retention.Store("ordinary")
    first = ordinary.keep({"i": 0}, kind="fixture", catalog={})
    for i in range(retention._LIMIT):
        ordinary.keep({"i": i + 1}, kind="fixture", catalog={})
    assert len(records(ordinary)) == retention._LIMIT
    with pytest.raises(ValueError, match="Unknown retained artifact"):
        ordinary.native(first)
    with pytest.raises(ValueError, match="boolean"):
        retention.Store("private-owner", evict="false")
