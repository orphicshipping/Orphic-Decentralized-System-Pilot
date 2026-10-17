"""Durable capture regression, fault injection, process races and lossless codecs."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from ciw.core.identities import canonical_json, content_identity as identity
from ciw.manufacturing_contracts import load, save_new
from ciw.toolpoint_stream import CaptureJournal, make_spec, verify
from ciw.toolpoint_stream_example import CASES, demo, fixture, prefix
from ciw.toolpoint_stream_store import StreamStore, initialize
from ciw import toolpoint_stream_store as storage
from ciw import toolpoint_stream_codec as codec
from ciw.toolpoint_store_cli import main


@pytest.fixture(params=["native", "legacy"])
def profile(request):
    if request.param == "legacy":
        pytest.importorskip("ciw.toolpoint_quality")
    return request.param


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "quality.sqlite3"
    initialize(p)
    return p


def populated(path, profile="native", case="late-unknown", *, stop=None):
    report = demo(profile=profile, case=case)
    store = StreamStore(path)
    store.register("cycle0", report["spec"])
    acks = [store.append("cycle0", u, expected_spec_ref=report["spec_ref"]) for u in report["updates"][:stop]]
    return store, report, acks


def sql(path, statement, params=()):
    with sqlite3.connect(path) as db:
        return db.execute(statement, params).fetchall()


def mutate_sql(path, table, statement, params=()):
    """Simulate an external writer bypassing the ordinary API, then restore guards."""
    with sqlite3.connect(path) as db:
        for verb in ("update", "delete"):
            db.execute(f"DROP TRIGGER immutable_{table}_{verb}")
        db.execute(statement, params)
        for verb in ("update", "delete"):
            db.execute(storage._TRIGGERS[f"immutable_{table}_{verb}"])


@pytest.mark.parametrize("case", CASES)
def test_all_profiles_cases_reopen_identical(path, profile, case):
    store, report, acks = populated(path, profile, case)
    reopened = StreamStore(path)
    exported = reopened.export("cycle0", expected_head_ref=acks[-1]["receipt"]["receipt_ref"],
        expected_spec_ref=report["spec_ref"], expected_commit_ref=acks[-1]["commit_ref"])
    assert exported["updates"] == report["updates"]
    assert exported["receipts"] == report["receipts"]
    assert exported["result"] == report["result"]
    assert exported["execution_id"] != report["execution_id"]
    assert verify(exported)["status"] == "PASS"
    checked = reopened.verify("cycle0", expected_spec_ref=report["spec_ref"],
        expected_head_ref=acks[-1]["receipt"]["receipt_ref"], expected_commit_ref=acks[-1]["commit_ref"])
    assert checked["head_anchor_checked"] and checked["commit_anchor_checked"]
    assert checked["quality_status"] == report["result"]["status"]
    assert checked["authority"]["action_authorized"] is False
    assert reopened.audit()["status"] == "PASS"
    # 5 samples on 3 sensors, each stored once, regardless of packet boundaries.
    final = report["updates"][-1]["observation"]
    field = "signals" if profile == "native" else "streams"
    assert reopened.head("cycle0")["storage_statistics"]["stored_sample_count"] == sum(len(c["samples"]) for c in final[field])


def test_registered_empty_is_pending_not_a_clean_export(path):
    report = demo()
    store = StreamStore(path)
    assert store.register("empty", report["spec"])["quality_status"] == "AWAITING_EVIDENCE"
    with pytest.raises(ValueError, match="without an accepted"):
        store.export("empty")
    assert store.audit()["streams"][0]["accepted_updates"] == 0


@pytest.mark.parametrize("index", [0, 1, 2])
def test_ack_lost_retry_after_reopen_identical_no_new_occurrence(path, index):
    store, report, acks = populated(path)
    assert StreamStore(path).append("cycle0", report["updates"][index], expected_spec_ref=report["spec_ref"]) == acks[index]
    assert len(sql(path, "SELECT * FROM entries")) == 3
    u = report["updates"][index]
    assert store.append_capture("cycle0", u["observation"], received_at_s=u["received_at_s"], final=u["final"],
        expected_head_ref=u["previous_receipt_ref"], expected_spec_ref=report["spec_ref"]) == acks[index]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_conflicting_retry_cannot_replace_history(path, index):
    store, report, _ = populated(path)
    before = store.export("cycle0")
    update = deepcopy(report["updates"][index]); update["received_at_s"] += .01
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        store.append("cycle0", update, expected_spec_ref=report["spec_ref"])
    assert store.export("cycle0")["result_ref"] == before["result_ref"]
    assert len(sql(path, "SELECT * FROM entries")) == 3


@pytest.mark.parametrize("bad", ["spec", "head", "commit"])
def test_reject_wrong_external_anchors(path, bad):
    store, report, acks = populated(path)
    kw = {"expected_spec_ref": report["spec_ref"], "expected_head_ref": acks[-1]["receipt"]["receipt_ref"],
          "expected_commit_ref": acks[-1]["commit_ref"]}
    kw["expected_" + bad + "_ref"] = identity({"wrong": True})
    with pytest.raises(ValueError):
        store.export("cycle0", **kw)


def test_rollback_database_copy_detected_with_external_anchor(path, tmp_path):
    store, report, acks = populated(path, stop=1)
    backup = tmp_path / "backup.sqlite3"; shutil.copy2(path, backup)
    last = store.append("cycle0", report["updates"][1], expected_spec_ref=report["spec_ref"])
    historical = StreamStore(backup)
    assert historical.verify("cycle0")["status"] == "PASS"  # Valid historical prefix, not evidence of freshness.
    with pytest.raises(ValueError, match="head anchor"):
        historical.verify("cycle0", expected_head_ref=last["receipt"]["receipt_ref"])
    with pytest.raises(ValueError, match="commit anchor"):
        historical.verify("cycle0", expected_commit_ref=last["commit_ref"])


def test_register_idempotent_but_cannot_fork_cycle_or_plan(path):
    store, report, _ = populated(path)
    assert store.register("cycle0", report["spec"])["accepted_updates"] == 3
    changed = deepcopy(report["spec"]); changed["plan"]["revision"] = "B"
    with pytest.raises(ValueError): store.register("cycle0", changed)
    with pytest.raises(ValueError, match="fork"):
        store.register("alias", changed)
    assert len(sql(path, "SELECT * FROM streams")) == 1


@pytest.mark.parametrize("table", ["metadata", "streams", "entries"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_sql_immutability_guards(path, table, verb):
    populated(path)
    cols = {"metadata": "format_version", "streams": "stream_id", "entries": "sequence"}
    statement = f"DELETE FROM {table}" if verb == "DELETE" else f"UPDATE {table} SET {cols[table]}={cols[table]}"
    with pytest.raises(sqlite3.IntegrityError, match="Immutable"):
        sql(path, statement)


@pytest.mark.parametrize("mutation", ["schema", "version", "app", "trigger", "foreign", "extra", "missing"])
def test_corrupt_database_metadata_and_schema_fail_closed(path, mutation):
    populated(path)
    if mutation == "schema": sql(path, "ALTER TABLE streams ADD COLUMN unsafe TEXT")
    elif mutation == "version": sql(path, "PRAGMA user_version=2")
    elif mutation == "app": sql(path, "PRAGMA application_id=0")
    elif mutation == "trigger": sql(path, "DROP TRIGGER immutable_entries_delete")
    elif mutation == "foreign":
        sql(path, "INSERT INTO entries VALUES('foreign',0,'{}','foreign','{}','foreign')")
    elif mutation == "extra": sql(path, "CREATE TABLE alien(x TEXT)")
    else: mutate_sql(path, "entries", "DELETE FROM entries WHERE sequence=1")
    with pytest.raises((ValueError, sqlite3.Error)):
        StreamStore(path).audit()


@pytest.mark.parametrize("mutation", ["delta", "ack", "receipt", "commit", "spec", "offset", "update_hash", "nan", "duplicates", "overflow"])
def test_data_corruption_detected_by_reconstruction_and_replay(path, mutation):
    store, report, _ = populated(path)
    row = sql(path, "SELECT delta_json, acknowledgement_json FROM entries WHERE sequence=1")[0]
    delta, ack = json.loads(row[0]), json.loads(row[1])
    if mutation == "delta":
        delta["channels"][0]["samples"][0]["value"] += 1
    elif mutation == "offset": delta["channels"][0]["prior_count"] += 1
    elif mutation == "update_hash": delta["update_ref"] = identity({"fake": True})
    elif mutation == "ack": ack["quality_status"] = "NO_PROCESS_HOLD_IDENTIFIED"
    elif mutation == "spec":
        altered = deepcopy(report["spec"]); altered["plan"]["revision"] = "C"
        mutate_sql(path, "streams", "UPDATE streams SET spec_json=?", (codec.dumps(altered),))
    elif mutation in {"receipt", "commit"}:
        mutate_sql(path, "entries", f"UPDATE entries SET {mutation}_ref=? WHERE sequence=1", (identity({"fake": True}),))
    raw = codec.dumps(delta)
    if mutation == "nan": raw = raw.replace('"final":false', '"final":NaN')
    if mutation == "duplicates": raw = raw.replace('"sequence":1', '"sequence":1,"sequence":1')
    if mutation == "overflow": raw = raw.replace('"sequence":1', '"sequence":1e999')
    if mutation not in {"receipt", "commit", "spec"}:
        mutate_sql(path, "entries", "UPDATE entries SET delta_json=?, acknowledgement_json=? WHERE sequence=1",
                   (raw, codec.dumps(ack)))
    with pytest.raises((ValueError, TypeError, KeyError)):
        store.export("cycle0")


def test_hash_only_forged_hold_clear_is_refused(path):
    store, report, _ = populated(path)
    raw = sql(path, "SELECT acknowledgement_json FROM entries WHERE sequence=1")[0][0]
    ack = json.loads(raw)
    ack["quality_status"] = "NO_PROCESS_HOLD_IDENTIFIED"
    ack["receipt"]["status"] = "NO_PROCESS_HOLD_IDENTIFIED"
    body = {k:v for k,v in ack["receipt"].items() if k!="receipt_ref"}
    ack["receipt"]["receipt_ref"] = identity(body)
    ack["commit_ref"] = identity({k:v for k,v in ack.items() if k!="commit_ref"})
    mutate_sql(path,"entries", "UPDATE entries SET acknowledgement_json=?, receipt_ref=?, commit_ref=? WHERE sequence=1",
               (codec.dumps(ack),ack["receipt"]["receipt_ref"],ack["commit_ref"]))
    with pytest.raises(ValueError, match="complete QC replay"):
        store.export("cycle0")


def test_commit_failure_rolls_back_no_success_return(path, monkeypatch):
    store, report, _ = populated(path, stop=1)
    before = store.head("cycle0")
    original = storage._commit
    def fail(db): raise OSError("simulated fsync/commit failure")
    monkeypatch.setattr(storage, "_commit", fail)
    with pytest.raises(OSError):
        store.append("cycle0", report["updates"][1], expected_spec_ref=report["spec_ref"])
    monkeypatch.setattr(storage, "_commit", original)
    assert store.head("cycle0") == before
    assert store.audit()["status"] == "PASS"


@pytest.mark.parametrize("when", ["before_commit", "after_commit"])
def test_process_killed_at_commit_boundary(path, tmp_path, when):
    _, report, acks = populated(path, stop=1)
    source = tmp_path / "input.json"; save_new(source, report)
    program = textwrap.dedent('''
        import json, os, signal, sys
        from ciw import toolpoint_stream_store as s
        from ciw.manufacturing_contracts import load
        from pathlib import Path
        original_connect=s._connect
        def small_cache(path, timeout):
            db=original_connect(path,timeout)
            db.execute("PRAGMA cache_size=1")
            db.execute("PRAGMA cache_spill=ON")
            return db
        s._connect=small_cache
        store=s.StreamStore(sys.argv[1])
        report=load(Path(sys.argv[2]))
        original=s._commit
        def kill(db):
            if sys.argv[3]=="after_commit": original(db)
            side=Path(sys.argv[1]+"-journal")
            Path(sys.argv[1]+".crash-marker").write_text(json.dumps({
                "journal_exists":side.exists(),"journal_bytes":side.stat().st_size if side.exists() else 0}))
            os.kill(os.getpid(), signal.SIGKILL)
        s._commit=kill
        store.append("cycle0",report["updates"][1],expected_spec_ref=report["spec_ref"])
    ''')
    proc = subprocess.run([sys.executable,"-c",program,str(path),str(source),when], capture_output=True,timeout=10)
    assert proc.returncode < 0
    marker=json.loads(Path(str(path)+".crash-marker").read_text())
    if when=="before_commit":
        assert marker["journal_exists"] and marker["journal_bytes"]>512
    else:
        assert not marker["journal_exists"]
    reopened=StreamStore(path)
    expected=1 if when=="before_commit" else 2
    assert reopened.head("cycle0")["accepted_updates"]==expected
    ack=reopened.append("cycle0",report["updates"][1],expected_spec_ref=report["spec_ref"])
    assert ack["receipt"]==report["receipts"][1]
    assert reopened.head("cycle0")["accepted_updates"]==2
    assert reopened.head("cycle0")["quality_status"]=="HELD"
    assert reopened.audit()["status"]=="PASS"


@pytest.mark.parametrize("identical", [False, True])
def test_competing_processes_one_parent_no_lost_update(path,tmp_path,identical):
    store,report,_=populated(path,stop=1)
    files=[]
    for i in range(2):
        r=deepcopy(report)
        if i==1 and not identical:r["updates"][1]["received_at_s"]+=.001
        p=tmp_path/f"r{i}.json";save_new(p,r);files.append(p)
    go=tmp_path/"go"
    script=textwrap.dedent('''
        import json, sys, time
        from pathlib import Path
        from ciw.toolpoint_stream_store import StreamStore
        from ciw.manufacturing_contracts import load
        store=StreamStore(sys.argv[1],timeout_s=5)
        report=load(Path(sys.argv[2])); flag=Path(sys.argv[3])
        while not flag.exists():time.sleep(.005)
        try:
            ack=store.append("cycle0",report["updates"][1],expected_spec_ref=report["spec_ref"])
            print(json.dumps({"ok":True,"ack":ack}))
        except Exception as exc:
            print(json.dumps({"ok":False,"reason":str(exc)}))
    ''')
    processes=[subprocess.Popen([sys.executable,"-c",script,str(path),str(p),str(go)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for p in files]
    go.write_text("go")
    outputs=[]
    for p in processes:
        out,err=p.communicate(timeout=15);assert p.returncode==0,err;outputs.append(json.loads(out))
    assert sum(o["ok"] for o in outputs)==(2 if identical else 1)
    if identical:assert outputs[0]["ack"]==outputs[1]["ack"]
    assert store.head("cycle0")["accepted_updates"]==2
    assert store.audit()["status"]=="PASS"


def test_lock_timeout_does_not_publish_receipt(path):
    store,report,_=populated(path,stop=1)
    contender=StreamStore(path,timeout_s=0)
    db=sqlite3.connect(path,isolation_level=None);db.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError,match="locked"):
            contender.append("cycle0",report["updates"][1],expected_spec_ref=report["spec_ref"])
    finally:db.rollback();db.close()
    assert store.head("cycle0")["accepted_updates"]==1


@pytest.mark.parametrize("bad", ["missing", "symlink", "hardlink", "directory", "empty", "foreign", "journal_symlink"])
def test_untrusted_or_unsupported_paths_fail_closed(tmp_path,bad):
    p=tmp_path/"db.sqlite3"
    if bad=="symlink":
        other=tmp_path/"real.sqlite3";initialize(other);p.symlink_to(other)
    elif bad=="hardlink":
        initialize(p);os.link(p,tmp_path/"alias.sqlite3")
    elif bad=="directory":p.mkdir()
    elif bad=="empty":p.touch()
    elif bad=="foreign":
        with sqlite3.connect(p) as db:db.execute("CREATE TABLE unrelated(x INTEGER)")
    elif bad=="journal_symlink":
        initialize(p);target=tmp_path/"dont_touch";target.write_text("valuable")
        Path(str(p)+"-journal").symlink_to(target)
    with pytest.raises((ValueError,OSError,sqlite3.Error)):
        StreamStore(p)


def test_initialize_never_overwrites_and_cleans_failed_publication(path,tmp_path,monkeypatch):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):initialize(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    target=tmp_path/"new.sqlite3"
    def fail(*args,**kwargs):raise OSError("link unavailable")
    monkeypatch.setattr(storage.os,"link",fail)
    with pytest.raises(OSError):initialize(target)
    assert not target.exists()
    assert not list(tmp_path.glob(".net-qc-init-*"))


@pytest.mark.parametrize("bad", [True,-1,31,float("nan"),float("inf"),"1"])
def test_timeout_contract(path,bad):
    with pytest.raises(ValueError):StreamStore(path,timeout_s=bad)


@pytest.mark.parametrize("name", ["", " ", "a\x00b", "a\nb", "x"*257])
def test_stream_identifier_contract(path,name):
    with pytest.raises(ValueError):StreamStore(path).register(name,demo()["spec"])


def test_budgets_remain_existing_journal_limits(path):
    report=demo();spec=deepcopy(report["spec"]);spec["limits"]["max_updates"]=1
    store=StreamStore(path);store.register("cycle0",spec)
    store.append("cycle0",report["updates"][0],expected_spec_ref=identity(spec))
    # Receipt changed because spec changed; use the new parent, but preserve the limit.
    update=deepcopy(report["updates"][1]);update["previous_receipt_ref"]=store.head("cycle0")["head_ref"]
    with pytest.raises(ValueError,match="budget"):
        store.append("cycle0",update,expected_spec_ref=identity(spec))
    assert store.head("cycle0")["accepted_updates"]==1


@pytest.mark.parametrize("case", CASES)
def test_codec_exact_roundtrip_no_sample_duplication(profile,case):
    report=demo(profile=profile,case=case);previous=None;total=0
    for update in report["updates"]:
        delta=codec.encode(previous,update,profile=report["spec"]["plan"]["schema"])
        assert codec.decode(previous,codec.loads(codec.dumps(delta)),profile=delta["profile"])==update
        assert canonical_json(codec.decode(previous,delta,profile=delta["profile"]))==canonical_json(update)
        total+=sum(len(c["samples"]) for c in delta["channels"])
        previous=update
    field="signals" if profile=="native" else "streams"
    assert total==sum(len(c["samples"]) for c in previous["observation"][field])


@pytest.mark.parametrize("mutation", ["base", "hash", "offset", "sensor", "duplicate", "header", "sample", "profile", "extra", "schema"])
def test_codec_corruption_refused(mutation):
    report=demo();previous,update=report["updates"][:2];profile=report["spec"]["plan"]["schema"]
    delta=codec.encode(previous,update,profile=profile)
    if mutation=="base":delta["previous_update_ref"]=None
    elif mutation=="hash":delta["update_ref"]=identity({})
    elif mutation=="offset":delta["channels"][0]["prior_count"]+=1
    elif mutation=="sensor":delta["channels"].pop()
    elif mutation=="duplicate":delta["channels"].append(deepcopy(delta["channels"][0]))
    elif mutation=="header":delta["observation_header"]["signals"]=[]
    elif mutation=="sample":delta["channels"][0]["samples"][0]["value"]+=1
    elif mutation=="profile":delta["profile"]="other"
    elif mutation=="extra":delta["command"]="release"
    else:delta["schema"]="wrong"
    with pytest.raises((ValueError,KeyError,TypeError)):
        codec.decode(previous,delta,profile=profile)


@pytest.mark.parametrize("raw", ['{} ', '{"x":1,"x":1}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":1e-999}', '[]'])
def test_stored_json_strict(raw):
    with pytest.raises(ValueError):codec.loads(raw)


def test_many_prefixes_store_less_payload_and_same_result(path):
    plan,obs=fixture(case="nominal")
    for signal in obs["signals"]:
        signal["samples"]=[{"time_s":i/1000,"value":100000+i,"expanded_uncertainty":1e-12} for i in range(81)]
    plan["phases"][0]["end_s"]=.08;plan["max_sample_gap_s"]=.002
    spec=make_spec(plan,obs,max_updates=16);journal=CaptureJournal(spec)
    store=StreamStore(path);store.register("dense",spec)
    for i,t in enumerate([.005*k for k in range(1,17)]):
        capture=prefix(plan,obs,t,final=i==15)
        update=journal.update(capture,received_at_s=t+.001,final=i==15)
        expected=journal.append(update)
        assert store.append("dense",update,expected_spec_ref=identity(spec))["receipt"]==expected
    head=store.head("dense");stats=head["storage_statistics"]
    assert stats["stored_sample_count"]==243
    assert stats["delta_json_bytes"]<stats["cumulative_update_json_bytes"]*.6
    assert store.export("dense")["result"]==journal.report()["result"]


def test_cli_full_lifecycle_and_quality_exit_codes(tmp_path,capsys):
    p=tmp_path/"cli.sqlite3";report=demo();spec=tmp_path/"spec.json";save_new(spec,report["spec"])
    assert main(["init",str(p)])==0;capsys.readouterr()
    assert main(["register",str(p),"c0",str(spec)])==2;capsys.readouterr()
    for i,update in enumerate(report["updates"]):
        f=tmp_path/f"u{i}.json";save_new(f,update)
        assert main(["append",str(p),"c0",str(f),"--expected-spec-ref",report["spec_ref"]])==2
        ack=json.loads(capsys.readouterr().out);assert ack["storage_status"]=="COMMITTED"
    assert main(["verify",str(p),"c0"])==0;capsys.readouterr()
    assert main(["head",str(p),"c0"])==2;capsys.readouterr()
    assert main(["audit",str(p)])==0;capsys.readouterr()
    out=tmp_path/"export.json"
    assert main(["export",str(p),"c0","--output",str(out)])==2;capsys.readouterr()
    assert load(out)["result"]==report["result"]
    assert main(["export",str(p),"c0","--output",str(out)])==1
    error=json.loads(capsys.readouterr().err);assert error["commit_outcome"]=="not_confirmed_by_this_response"
    # Read-only file publication failure cannot mutate accepted records.
    assert StreamStore(p).head("c0")["accepted_updates"]==3


def test_real_module_subprocess_resume_and_retry(tmp_path):
    p=tmp_path/"sub.sqlite3";report=demo();spec=tmp_path/"spec.json";save_new(spec,report["spec"])
    def run(*args):
        return subprocess.run([sys.executable,"-m","ciw.toolpoint_store_cli",*map(str,args)],capture_output=True,text=True,timeout=10)
    assert run("init",p).returncode==0
    assert run("register",p,"c0",spec).returncode==2
    parent="none"
    for i,u in enumerate(report["updates"]):
        c=tmp_path/f"c{i}.json";save_new(c,u["observation"])
        args=["append-capture",p,"c0",c,"--expected-spec-ref",report["spec_ref"],"--expected-head-ref",parent,"--received-at-s",u["received_at_s"]]
        if u["final"]:args.append("--final")
        first=run(*args);assert first.returncode==2,first.stderr
        retry=run(*args);assert retry.returncode==2,retry.stderr
        assert json.loads(first.stdout)==json.loads(retry.stdout)
        parent=json.loads(first.stdout)["receipt"]["receipt_ref"]
    verify_result=run("verify",p,"c0","--expected-head-ref",parent)
    assert verify_result.returncode==0,verify_result.stderr
    assert json.loads(verify_result.stdout)["quality_status"]=="HELD"


@pytest.mark.parametrize("alias", [True, 1.0])
def test_receipt_numeric_alias_cannot_bypass_exact_content_identity(path,alias):
    store,_,_=populated(path)
    raw=sql(path,"SELECT acknowledgement_json FROM entries WHERE sequence=1")[0][0]
    ack=json.loads(raw);ack["receipt"]["sequence"]=alias
    # Python dict equality would accept True == 1 == 1.0. The stored hash must not.
    mutate_sql(path,"entries","UPDATE entries SET acknowledgement_json=? WHERE sequence=1",(codec.dumps(ack),))
    with pytest.raises(ValueError,match="complete QC replay"):
        store.export("cycle0")


def test_different_start_time_cannot_create_parallel_clean_cycle_history(path):
    store,report,_=populated(path)
    changed=deepcopy(report["spec"]);changed["binding"]["started_at_s"]=10
    with pytest.raises(ValueError,match="fork"):
        store.register("alternate-start",changed)


@pytest.mark.parametrize("case",["nominal","health-recovery","late-unknown"])
def test_atomic_import_prior_checkpoint_and_idempotent_repeat(path,profile,case):
    report=demo(profile=profile,case=case);store=StreamStore(path)
    source_before=deepcopy(report)
    args={"expected_spec_ref":report["spec_ref"],"expected_head_ref":report["receipts"][-1]["receipt_ref"]}
    result=store.import_report("migrated",report,**args)
    assert result["storage_status"]=="COMMITTED"
    assert result["quality_status"]==report["result"]["status"]
    assert store.import_report("migrated",report,**args)==result
    assert report==source_before
    out=store.export("migrated")
    assert out["receipts"]==report["receipts"] and out["result"]==report["result"]
    assert store.verify("migrated")["status"]=="PASS"


def test_import_old_prefix_does_not_roll_back_existing_newer_hold(path):
    store,report,_=populated(path)
    old=CaptureJournal(report["spec"]);old.append(report["updates"][0]);source=old.report()
    current=store.head("cycle0")
    result=store.import_report("cycle0",source,expected_spec_ref=source["spec_ref"],
        expected_head_ref=source["receipts"][-1]["receipt_ref"])
    assert result["head_ref"]==current["head_ref"]
    assert result["quality_status"]=="HELD" and result["accepted_updates"]==3


def test_import_failure_after_second_write_rolls_back_registration_and_all_entries(path,monkeypatch):
    store=StreamStore(path);report=demo();original=store._admit
    def fail(*args,**kwargs):
        ack=original(*args,**kwargs)
        if ack["sequence"]==1:raise OSError("injected interruption during import")
        return ack
    monkeypatch.setattr(store,"_admit",fail)
    with pytest.raises(OSError):
        store.import_report("new",report,expected_spec_ref=report["spec_ref"],
            expected_head_ref=report["receipts"][-1]["receipt_ref"])
    assert sql(path,"SELECT count(*) FROM streams")==[(0,)]
    assert sql(path,"SELECT count(*) FROM entries")==[(0,)]
    assert store.audit()["streams"]==[]


def test_import_corrupt_report_never_registers_anything(path):
    report=demo();report["result"]["status"]="NO_PROCESS_HOLD_IDENTIFIED"
    report["result_ref"]=identity(report["result"])
    with pytest.raises(ValueError):
        StreamStore(path).import_report("forged",report,expected_spec_ref=report["spec_ref"],
            expected_head_ref=report["receipts"][-1]["receipt_ref"])
    assert StreamStore(path).audit()["streams"]==[]


@pytest.mark.parametrize("case",["health-recovery","high-nozzle","nominal"])
def test_reopened_store_to_existing_manufacturing_containment(path,profile,case):
    from ciw.manufacturing_example import example_request
    from ciw.manufacturing_runtime import evaluate as manufacture
    from ciw.toolpoint_stream_containment import evaluate as contain, gate, verify as check
    source=manufacture(example_request());store=StreamStore(path)
    for i,scenario in enumerate([case,"nominal"]):
        report=demo(profile=profile,case=scenario,cycle_index=i)
        store.import_report(f"cycle{i}",report,expected_spec_ref=report["spec_ref"],
            expected_head_ref=report["receipts"][-1]["receipt_ref"])
    reopened=StreamStore(path)
    request={"schema":"ciw.toolpoint-stream-containment-request.v1","manufacturing_report":source,
             "streams":[reopened.export("cycle0"),reopened.export("cycle1")],"assessed_at_s":12}
    report=contain(request);quality=report["result"]["quality"]
    assert quality["p004"]["state"]=="SCRAPPED"
    assert quality["p003"]["state"]=="ELIGIBLE_FOR_REVIEW"
    assert quality["a001"]["state"]==("ELIGIBLE_FOR_REVIEW" if case=="nominal" else "HELD")
    assert quality["p002"]["recorded_inspection"]=="PASS"
    assert check(report)["status"]=="PASS"
    result=gate(report,serials=["a001"],action="release_review")
    assert result["authority"]["action_authorized"] is False
    assert result["authority"]["shipment_release"]=="not_authorized"


@pytest.mark.parametrize("seed",range(12))
def test_packetization_reopen_does_not_change_result(profile,path,seed):
    rng=random.Random(seed);plan,obs=fixture(profile=profile,case="high-nozzle")
    cuts=sorted(rng.sample([0,.2,.4,.6],rng.randrange(5)))+[.8]
    spec=make_spec(plan,obs);journal=CaptureJournal(spec)
    store=StreamStore(path);store.register("packets",spec)
    for cut in cuts:
        final=cut==.8
        update=journal.update(prefix(plan,obs,cut,final=final),
            received_at_s=float(Decimal(str(cut))+Decimal(".01")),final=final)
        expected=journal.append(update)
        assert StreamStore(path).append("packets",update,expected_spec_ref=identity(spec))["receipt"]==expected
    assert StreamStore(path).export("packets")["result"]==journal.report()["result"]


def test_cli_import_old_checkpoint(tmp_path,capsys):
    p=tmp_path/"migrate.sqlite3";report=demo();src=tmp_path/"old.json";save_new(src,report)
    assert main(["init",str(p)])==0;capsys.readouterr()
    args=["import",str(p),"migrated",str(src),"--expected-spec-ref",report["spec_ref"],
          "--expected-head-ref",report["receipts"][-1]["receipt_ref"]]
    assert main(args)==2
    result=json.loads(capsys.readouterr().out);assert result["storage_status"]=="COMMITTED"
    assert main(args)==2
    assert json.loads(capsys.readouterr().out)==result


def test_import_keeps_original_occurrence_and_source_report_hash(path):
    report=demo();store=StreamStore(path)
    store.import_report("old",report,expected_spec_ref=report["spec_ref"],
        expected_head_ref=report["receipts"][-1]["receipt_ref"])
    provenance=StreamStore(path).provenance("old")
    row=provenance["imports"][0]
    assert row["source_report_ref"]==identity(report)
    assert row["source_execution_id"]==report["execution_id"]
    assert row["import_execution_id"]!=row["source_execution_id"]
    assert store.export("old")["execution_id"] not in {row["source_execution_id"],row["import_execution_id"]}
    assert store.head("old")["storage_statistics"]["imported_checkpoint_count"]==1
    assert store.provenance("old")==provenance


@pytest.mark.parametrize("field",["source_report_ref","source_execution_id","source_head_ref","source_result_ref","local_head_after_import","authority"])
def test_rehashed_import_provenance_corruption_fails_reconstruction(path,field):
    report=demo();store=StreamStore(path)
    store.import_report("old",report,expected_spec_ref=report["spec_ref"],
        expected_head_ref=report["receipts"][-1]["receipt_ref"])
    row=store.provenance("old")["imports"][0]
    if field=="source_execution_id":
        from ciw.core.identities import new_identity
        row[field]=new_identity("execution")
    elif field=="authority":row[field]["action_authorized"]=True
    else:row[field]=identity({"wrong":True})
    row["import_ref"]=identity({k:v for k,v in row.items() if k!="import_ref"})
    mutate_sql(path,"imports","UPDATE imports SET source_report_ref=?, record_json=?, import_ref=?",
        (row["source_report_ref"],codec.dumps(row),row["import_ref"]))
    with pytest.raises(ValueError):store.provenance("old")


@pytest.mark.parametrize("verb",["UPDATE","DELETE"])
def test_import_provenance_immutable_in_database(path,verb):
    report=demo();store=StreamStore(path)
    store.import_report("old",report,expected_spec_ref=report["spec_ref"],
        expected_head_ref=report["receipts"][-1]["receipt_ref"])
    statement="DELETE FROM imports" if verb=="DELETE" else "UPDATE imports SET source_report_ref=source_report_ref"
    with pytest.raises(sqlite3.IntegrityError,match="Immutable"):
        sql(path,statement)


def test_store_and_import_counts_have_hard_budgets(path,monkeypatch):
    report=demo();store=StreamStore(path)
    monkeypatch.setattr(storage,"MAX_STREAMS",1)
    store.register("first",report["spec"])
    other=demo(cycle_index=1)
    with pytest.raises(ValueError,match="budget"):
        store.register("second",other["spec"])
    monkeypatch.setattr(storage,"MAX_IMPORTS_PER_STREAM",1)
    store.import_report("first",report,expected_spec_ref=report["spec_ref"],
        expected_head_ref=report["receipts"][-1]["receipt_ref"])
    newer=store.export("first") # Different execution identity is another source occurrence.
    with pytest.raises(ValueError,match="budget"):
        store.import_report("first",newer,expected_spec_ref=newer["spec_ref"],
            expected_head_ref=newer["receipts"][-1]["receipt_ref"])
    assert len(store.provenance("first")["imports"])==1


def test_cli_provenance_read_does_not_change_storage(path,capsys):
    report=demo();store=StreamStore(path)
    store.import_report("old",report,expected_spec_ref=report["spec_ref"],
        expected_head_ref=report["receipts"][-1]["receipt_ref"])
    before=store.head("old")
    assert main(["provenance",str(path),"old"])==0
    assert json.loads(capsys.readouterr().out)["imports"][0]["source_report_ref"]==identity(report)
    assert store.head("old")==before
