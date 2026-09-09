"""Deterministic routing/state tests; these are not subscription acceptance tests."""
import copy
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import time

import pytest
from agent import braia_routing as br


@pytest.fixture
def cfg():
    return br.validate_policy({
        "enabled": True, "schema_version": 1, "policy_version": "test-v1",
        "initial_provider": "anthropic", "manual_provider": None,
        "learning": {"min_samples": 2, "sustained_windows": 2, "cooldown_seconds": 100, "promotion_margin": .15},
        "providers": {
            "anthropic": {"orchestrator_model": "claude-normal", "models": {
                "claude-small": {"tier": "routine", "efforts": [], "default_effort": None},
                "claude-normal": {"tier": "normal", "efforts": ["medium", "high"], "default_effort": "medium"},
                "claude-large": {"tier": "complex", "efforts": ["high"], "default_effort": "high"}}},
            "openai-codex": {"orchestrator_model": "gpt-normal", "models": {
                "gpt-small": {"tier": "routine", "efforts": ["low"], "default_effort": "low"},
                "gpt-normal": {"tier": "normal", "efforts": ["medium", "high"], "default_effort": "medium"},
                "gpt-large": {"tier": "complex", "efforts": ["high"], "default_effort": "high"}}},
        }})


@pytest.fixture
def context():
    return {"task_type": "sdr-fields", "complexity": "normal", "acceptance_id": "fields-v1"}


def sample(store, cfg, context, provider, accepted=True, now=None, latency=10):
    model = cfg["providers"][provider]["orchestrator_model"]
    eid = store.begin(cfg, context, provider, model, now)
    store.finish(eid, "completed", latency)
    store.verdict(eid, "accepted" if accepted else "rejected", "a"*64, "test-suite")
    return eid


def evidence(store, cfg, context, now=None, count=4):
    now = now or time.time()
    for n in range(count):
        sample(store, cfg, context, "anthropic", False, now-20+n)
        sample(store, cfg, context, "openai-codex", True, now-20+n)


def test_enabled_config_validates_without_mutating(cfg):
    original = copy.deepcopy(cfg)
    br.validate_policy(cfg)["providers"].clear()
    assert cfg == original


@pytest.mark.parametrize("key,value", [("min_samples", True), ("min_samples", 1), ("window_days", 0), ("promotion_margin", float("nan")), ("sustained_windows", 1)])
def test_bad_learning_rejected(cfg, key, value):
    cfg["learning"][key] = value
    with pytest.raises(ValueError): br.validate_policy(cfg)


def test_task_models_family_capacity_effort_and_justification(cfg, context):
    route = {**context, "model": "claude-normal", "effort": "medium", "justification": "Defined fields with independent schema check"}
    assert br.validate_task({"routing": route}, "anthropic", cfg) == route
    for change in ({"model": "gpt-normal"}, {"model": "claude-small"}, {"effort": "low"}, {"justification": ""}):
        with pytest.raises(ValueError): br.validate_task({"routing": {**route, **change}}, "anthropic", cfg)
    route.update(model="claude-small", complexity="routine", effort=None)
    assert br.validate_task({"routing": route}, "anthropic", cfg)
    assert br.reasoning_for(cfg["providers"]["anthropic"]["models"]["claude-small"]) is None


def test_capable_fallback_never_downgrades(cfg):
    assert br.fallback_candidates(cfg, "anthropic", "claude-large", "normal") == [{"provider": "openai-codex", "model": "gpt-large"}]
    assert br.fallback_candidates(cfg, "anthropic", "claude-large", "complex", True) == []
    del cfg["providers"]["openai-codex"]["models"]["gpt-large"]
    assert br.fallback_candidates(cfg, "anthropic", "claude-large", "complex") == []


def test_unavailable_catalog_entry_not_selected(cfg, context):
    cfg["providers"]["openai-codex"]["models"]["gpt-large"]["availability"] = "unavailable"
    assert br.fallback_candidates(cfg, "anthropic", "claude-large", "complex") == []


def test_fallback_preserves_explicit_effort_or_fails_closed(cfg):
    assert br.fallback_candidates(cfg, "anthropic", "claude-normal", "normal", effort="high") == [
        {"provider": "openai-codex", "model": "gpt-normal"}
    ]
    for spec in cfg["providers"]["openai-codex"]["models"].values():
        spec["efforts"] = ["low"]
        spec["default_effort"] = "low"
    assert br.fallback_candidates(cfg, "anthropic", "claude-normal", "normal", effort="high") == []


def test_text_completion_is_not_quality(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    for p in cfg["providers"]:
        for _ in range(12):
            eid = s.begin(cfg, context, p, cfg["providers"][p]["orchestrator_model"])
            s.finish(eid, "completed", 1)
    assert s.choose(cfg, context, list(cfg["providers"])) == ("anthropic", "insufficient_evidence")
    with s.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM executions WHERE verdict IS NOT NULL").fetchone()[0] == 0


def test_sustained_promotion_persists_and_manual_wins(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    evidence(s, cfg, context)
    assert s.choose(cfg, context, list(cfg["providers"]))[0] == "openai-codex"
    assert br.Store(tmp_path).choose(cfg, context, list(cfg["providers"])) == ("openai-codex", "cooldown")
    cfg["manual_provider"] = "anthropic"
    assert s.choose(cfg, context, list(cfg["providers"])) == ("anthropic", "manual")
    cfg["manual_provider"] = None
    cfg["learning"]["enabled"] = False
    assert s.choose(cfg, context, list(cfg["providers"]))[0] == "anthropic"


def test_insufficient_samples_and_repeated_poll_do_not_promote(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    evidence(s, cfg, context, count=3)
    for _ in range(10): assert s.choose(cfg, context, list(cfg["providers"]))[0] == "anthropic"


@pytest.mark.parametrize("change", ["routine", "complexity", "acceptance", "policy", "model", "stale", "disconnected"])
def test_noncomparable_or_stale_evidence_does_not_promote(cfg, context, tmp_path, change):
    s = br.Store(tmp_path)
    evidence(s, cfg, context, time.time()-40*86400 if change == "stale" else None)
    available = list(cfg["providers"])
    if change == "routine": context["task_type"] = "architecture"
    if change == "complexity": context["complexity"] = "complex"
    if change == "acceptance": context["acceptance_id"] = "fields-v2"
    if change == "policy": cfg["policy_version"] = "test-v2"
    if change == "model": cfg["providers"]["anthropic"]["models"]["claude-normal"]["revision"] = "new"
    if change == "disconnected": available = ["anthropic"]
    assert s.choose(cfg, context, available)[0] == "anthropic"


def test_model_tier_and_effect_are_separate_and_not_cross_promoted(cfg, context, tmp_path):
    assert br.context_key({**context, "model": "claude-normal", "effort": "medium"}, cfg) != \
           br.context_key({**context, "model": "claude-large", "effort": "high"}, cfg)
    s = br.Store(tmp_path)
    now = time.time()
    for n in range(4):
        sample(s, cfg, context, "anthropic", False, now-20+n)
        explicit = {**context, "model": "gpt-normal", "effort": "high"}
        eid = s.begin(cfg, explicit, "openai-codex", "gpt-normal", now-20+n)
        s.finish(eid, "completed", 1)
        s.verdict(eid, "accepted", "a"*64, "test-suite")
    assert s.choose(cfg, context, list(cfg["providers"]), now)[0] == "anthropic"


def test_quality_beats_latency(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    for n in range(4):
        sample(s, cfg, context, "anthropic", True, latency=30)
        sample(s, cfg, context, "openai-codex", n % 2 == 0, latency=1)
    assert s.choose(cfg, context, list(cfg["providers"]))[0] == "anthropic"


def test_deterioration_demotes_after_cooldown(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    now = time.time()
    evidence(s, cfg, context, now)
    assert s.choose(cfg, context, list(cfg["providers"]), now)[0] == "openai-codex"
    for n in range(4):
        sample(s, cfg, context, "anthropic", True, now+10+n)
        sample(s, cfg, context, "openai-codex", False, now+10+n)
    assert s.choose(cfg, context, list(cfg["providers"]), now+50)[0] == "openai-codex"
    assert s.choose(cfg, context, list(cfg["providers"]), now+101)[0] == "anthropic"


def test_verdict_requires_known_completed_execution_and_digest(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    with pytest.raises(ValueError): s.verdict("unknown", "accepted", "a"*64, "checker")
    eid = s.begin(cfg, context, "anthropic", "claude-normal")
    with pytest.raises(ValueError): s.verdict(eid, "accepted", "a"*64, "checker")
    s.finish(eid, "completed", 1)
    with pytest.raises(ValueError): s.verdict(eid, "accepted", "looks good", "checker")
    s.verdict(eid, "accepted", "a"*64, "checker")
    s.verdict(eid, "accepted", "a"*64, "checker")
    with pytest.raises(ValueError): s.verdict(eid, "rejected", "a"*64, "checker")


def test_concurrent_writes_and_promotions_atomic(cfg, context, tmp_path):
    def write(n):
        return sample(br.Store(tmp_path), cfg, context, "anthropic", now=time.time()+n/10000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(write, range(40)))
    assert len(set(ids)) == 40
    with br.Store(tmp_path).tx() as db:
        assert db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 40


def test_profile_isolation_and_sensitive_text_absent(cfg, context, tmp_path):
    one, two = tmp_path/'one', tmp_path/'two'
    one.mkdir(); two.mkdir()
    ctx = {**context, "justification": "CLIENT_SECRET_NEVER_PERSIST", "goal": "CLIENT_SECRET_NEVER_PERSIST"}
    evidence(br.Store(one), cfg, ctx)
    assert br.Store(two).choose(cfg, ctx, list(cfg["providers"]))[0] == "anthropic"
    assert b"CLIENT_SECRET" not in (one/'braia-routing.sqlite3').read_bytes()
    assert context["task_type"].encode() not in (one/'braia-routing.sqlite3').read_bytes()


def agent(tmp_path):
    return SimpleNamespace(provider="anthropic", model="claude-normal", _delegate_depth=0,
                           _braia_profile=str(tmp_path), _active_children=[], _braia_route_batch_safe=True)


@pytest.fixture
def host_parent(tmp_path):
    from hermes_state import SessionDB
    from run_agent import AIAgent
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="host-conversation", source="cli", model="claude-normal")

    def make(sid="host-conversation", depth=0):
        a = AIAgent.__new__(AIAgent)  # real host object; no provider transport
        a.__dict__.update(vars(agent(tmp_path)))
        a.session_id = sid
        a._session_db = db
        a._delegate_depth = depth
        return a
    yield make
    db.close()


def review_message(tmp_path, execution_id):
    artifact = tmp_path / "async-check.json"
    artifact.write_text('{"expected":3,"observed":3,"passed":true}')
    return json.dumps({"execution_id": execution_id, "verdict": "accepted",
                       "evidence_path": str(artifact),
                       "evidence_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                       "verifier": "async-test-v1"})


def run_owned_child(cfg, context, tmp_path, parent, body=None):
    child = agent(tmp_path)
    child._delegate_depth = 1
    child.braia_task_context = context
    br.track_child(child, parent)
    @br.routed_conversation
    def run(a):
        return body(a) if body else {"completed": True, "final_response": "3"}
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_switch", side_effect=switch):
        result = run(child)
    entry = {"task_index": 0, "status": "completed", "summary": "3"}
    br.publish_child_evidence(child, parent, entry)
    br.finish_child(child, parent)
    return result, entry


def test_async_review_new_host_parent_same_session(cfg, context, tmp_path, host_parent):
    old = host_parent()
    result, entry = run_owned_child(cfg, context, tmp_path, old)
    new = host_parent()
    assert new is not old and not hasattr(new, "_braia_child_executions")
    with patch.object(br, "load_policy", return_value=cfg):
        assert br.control_action("review", review_message(tmp_path, result["braia_routing"]["execution_id"]), new)["status"] == "recorded"


def test_async_formatter_exposes_execution_and_actual_route():
    from tools.process_registry import format_process_notification
    meta = {"execution_id": "a" * 32, "attempt_ids": ["b" * 32, "a" * 32],
            "provider": "openai-codex", "model": "gpt-normal", "quality": "unreviewed", "continuation": True}
    entry = {"task_index": 0, "status": "completed", "summary": "3", "braia_routing": meta}
    for evt in ({"type": "async_delegation", "is_batch": True, "results": [entry]},
                {"type": "async_delegation", **entry}):
        formatted = format_process_notification(evt)
        assert meta["execution_id"] in formatted
        assert meta["provider"] in formatted and meta["model"] in formatted
        assert "unreviewed" in formatted


@pytest.mark.parametrize("intruder", ["session", "profile", "child", "child-row", "branch", "unknown-session", "no-db", "db-profile-mismatch", "unknown-id"])
def test_async_review_rejects_untrusted_owner(cfg, context, tmp_path, host_parent, intruder):
    parent = host_parent()
    result, _ = run_owned_child(cfg, context, tmp_path, parent)
    eid = result["braia_routing"]["execution_id"]
    other = host_parent()
    if intruder == "session":
        other._session_db.create_session(session_id="different", source="cli")
        other.session_id = "different"
    elif intruder == "profile":
        import shutil
        from hermes_state import SessionDB
        profile = tmp_path / "different-profile"
        profile.mkdir()
        # Even a copy of routing history must not confer the original owner.
        shutil.copyfile(tmp_path / "braia-routing.sqlite3", profile / "braia-routing.sqlite3")
        other._braia_profile = str(profile)
        other._session_db = SessionDB(db_path=profile / "state.db")
        other._session_db.create_session(session_id=other.session_id, source="cli")
    elif intruder == "child":
        other._delegate_depth = 1  # same session_id cannot bypass depth
    elif intruder in {"child-row", "branch"}:
        other.session_id = "descendant"
        other._session_db.create_session(session_id="descendant", source="delegate" if intruder == "child-row" else "cli", parent_session_id=parent.session_id)
    elif intruder == "unknown-session":
        other.session_id = "model-claimed-session"
    elif intruder == "no-db":
        other._session_db = None
    elif intruder == "db-profile-mismatch":
        other._braia_profile = str(tmp_path / "wrong")
    elif intruder == "unknown-id":
        eid = "f" * 32
    # Old in-memory map and JSON session claims cannot establish authority.
    other._braia_child_executions = {eid: str(tmp_path)}
    data = json.loads(review_message(tmp_path, eid))
    data.update(owner_session_id=parent.session_id, owner=br.session_owner(parent), profile=str(tmp_path))
    try:
        with patch.object(br, "load_policy", return_value=cfg), pytest.raises(ValueError):
            br.control_action("review", json.dumps(data), other)
    finally:
        if intruder == "profile":
            other._session_db.close()
    with br.Store(tmp_path).tx() as db:
        assert db.execute("SELECT verdict FROM executions").fetchone()[0] is None


def test_async_owner_survives_db_reopen_and_compression(cfg, context, tmp_path, host_parent):
    from hermes_state import SessionDB
    parent = host_parent()
    result, _ = run_owned_child(cfg, context, tmp_path, parent)
    owner = br.session_owner(parent)
    parent._session_db.end_session(parent.session_id, "compression")
    parent._session_db.create_session(session_id="compressed", source="cli", parent_session_id=parent.session_id)
    new = host_parent("compressed")
    reopened = SessionDB(db_path=tmp_path / "state.db")
    new._session_db = reopened
    try:
        assert br.session_owner(new) == owner
        with patch.object(br, "load_policy", return_value=cfg):
            assert br.control_action("review", review_message(tmp_path, result["braia_routing"]["execution_id"]), new)["status"] == "recorded"
    finally:
        reopened.close()


@pytest.mark.parametrize("length", [96, 120])
def test_async_owner_long_compression_chain(cfg, context, tmp_path, host_parent, length):
    parent = host_parent()
    result, _ = run_owned_child(cfg, context, tmp_path, parent)
    owner = br.session_owner(parent)
    db = parent._session_db
    previous = parent.session_id
    for n in range(length):
        db.end_session(previous, "compression")
        sid = f"compression-{n}"
        db.create_session(session_id=sid, source="cli", parent_session_id=previous)
        previous = sid
    if length <= 100:
        assert db.get_compression_tip(parent.session_id) == previous
    else:
        # Native tip traversal is bounded. Review authority must remain valid
        # beyond that bound by checking each immediate continuation edge.
        assert db.get_compression_tip(parent.session_id) != previous
    new = host_parent(previous)
    assert br.session_owner(new) == owner
    with patch.object(br, "load_policy", return_value=cfg):
        assert br.control_action("review", review_message(tmp_path, result["braia_routing"]["execution_id"]), new)["status"] == "recorded"


def test_async_owner_compression_cycle_fails_closed(tmp_path, host_parent):
    parent = host_parent()
    db = parent._session_db
    db.end_session(parent.session_id, "compression")
    db.create_session(session_id="cycle", source="cli", parent_session_id=parent.session_id)
    db.end_session("cycle", "compression")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE sessions SET parent_session_id=? WHERE id=?", ("cycle", parent.session_id))
    assert br.session_owner(parent) is None
    # Exercise the independent seen guard even if host tip resolution returns
    # a common value for a damaged cyclic graph.
    with patch.object(db, "get_compression_tip", return_value="cycle"):
        assert br.session_owner(parent) is None


@pytest.mark.parametrize("failure", [False, True])
def test_async_owner_preserved_through_fallback_and_exception(cfg, context, tmp_path, host_parent, failure):
    parent = host_parent()
    captured = []
    def body(child):
        assert br.route_fallback(child, "rate_limit")
        captured.append(child)
        if failure:
            raise RuntimeError("transport failed")
        return {"completed": True, "final_response": "3"}
    if failure:
        with pytest.raises(RuntimeError, match="transport failed"):
            run_owned_child(cfg, context, tmp_path, parent, body)
    else:
        run_owned_child(cfg, context, tmp_path, parent, body)
    child = captured[0]
    entry = {}
    br.publish_child_evidence(child, parent, entry)
    meta = entry["braia_routing"]
    assert len(meta["attempt_ids"]) == 2
    assert meta["provider"] == "openai-codex" and meta["continuation"]
    with br.Store(tmp_path).tx() as db:
        rows = db.execute("SELECT owner,status FROM executions ORDER BY created").fetchall()
        assert {r["owner"] for r in rows} == {br.session_owner(parent)}
        assert [r["status"] for r in rows] == ["rate_limit", "runtime_error" if failure else "completed"]
    with patch.object(br, "load_policy", return_value=cfg):
        if failure:
            with pytest.raises(ValueError, match="completed known"):
                br.control_action("review", review_message(tmp_path, meta["execution_id"]), host_parent())
        else:
            assert br.control_action("review", review_message(tmp_path, meta["execution_id"]), host_parent())["status"] == "recorded"


def test_async_migration_preserves_legacy_without_inventing_owner(cfg, context, tmp_path, host_parent):
    # Exact pre-async execution layout, populated before upgraded Store opens.
    path = tmp_path / "braia-routing.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE executions (id TEXT PRIMARY KEY, bucket TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL, finished REAL, latency REAL, status TEXT, verdict TEXT, evidence TEXT, verifier TEXT, rework INTEGER DEFAULT 0, usage REAL)")
        db.execute("INSERT INTO executions(id,bucket,provider,model,created,status) VALUES(?,?,?,?,?,?)", ("a" * 32, "legacy", "anthropic", "claude-normal", time.time(), "completed"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: br.Store(tmp_path), range(8)))
    with stores[0].tx() as db:
        row = db.execute("SELECT * FROM executions").fetchone()
        assert row["id"] == "a" * 32 and row["owner"] is None and row["status"] == "completed"
        assert sum(r[1] == "owner" for r in db.execute("PRAGMA table_info(executions)")) == 1
    parent = host_parent()
    parent._braia_child_executions = {"a" * 32: str(tmp_path)}
    with patch.object(br, "load_policy", return_value=cfg), pytest.raises(ValueError, match="owned"):
        br.control_action("review", review_message(tmp_path, "a" * 32), parent)
    # Explicit trusted Python verifier remains compatible for legacy records.
    br.record_verdict(tmp_path, "a" * 32, "accepted", "b" * 64, "external-verifier")


def test_async_native_event_preserves_metadata_before_durable_delivery():
    from queue import Queue
    from tools import async_delegation as ad
    from tools import process_registry as pr
    meta = {"execution_id": "c" * 32, "provider": "anthropic", "model": "claude-small", "quality": "unreviewed"}
    entry = {"summary": "3", "status": "completed", "braia_routing": meta}
    queue = Queue()
    with patch.object(pr.process_registry, "completion_queue", queue), patch.object(ad, "_persist_completion") as persist:
        ad._push_completion_event({"delegation_id": "unit-single"}, entry, "completed")
        evt = queue.get_nowait()
        assert persist.call_args.args[0]["braia_routing"] == meta
        assert meta["execution_id"] in pr.format_process_notification(evt)
        ad._push_batch_completion_event({"delegation_id": "unit-batch"}, {"results": [entry]}, "completed")
        evt = queue.get_nowait()
        assert persist.call_args.args[0]["results"][0]["braia_routing"] == meta
        assert meta["execution_id"] in pr.format_process_notification(evt)


def test_async_native_child_exception_exposes_id_and_releases_lease(cfg, context, tmp_path, host_parent):
    import weakref
    from tools.delegate_tool import _run_single_child
    parent = host_parent()
    child = MagicMock()
    child.__dict__.update(vars(agent(tmp_path)))
    child._delegate_depth = 1
    child._braia_turn_lock = None
    child.braia_task_context = context
    child._delegate_parent_ref = weakref.ref(parent)
    child._parent_session_id = parent.session_id
    child._credential_pool.acquire_lease.return_value = "fixture-lease"
    child._credential_pool.current.return_value = MagicMock(id="fixture-lease")
    @br.routed_conversation
    def run(a):
        assert br.route_fallback(a, "rate_limit")
        raise RuntimeError("synthetic transport failure")
    child.run_conversation.side_effect = lambda **kwargs: run(child)
    parent._active_children.append(child)
    br.track_child(child, parent)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_switch", side_effect=switch):
        result = _run_single_child(0, "isolated exception", child, parent)
    assert result["status"] == "error"
    assert result["braia_routing"]["provider"] == "openai-codex"
    assert len(result["braia_routing"]["attempt_ids"]) == 2
    child._credential_pool.release_lease.assert_called_once_with("fixture-lease")
    child.close.assert_called_once()
    assert child._delegate_parent_ref() is parent and child._parent_session_id == parent.session_id
    assert not parent._active_children and not parent._braia_inflight_children


def switch(a, provider, model, cfg, profile, effort=None):
    a.provider, a.model = provider, model


def test_same_request_route_then_child_model_selection(cfg, context, tmp_path):
    a = agent(tmp_path)
    evidence(br.Store(tmp_path), cfg, context)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch):
        result = br.control_action("route", json.dumps(context), a)
    assert result["active_provider"] == a.provider == "openai-codex"
    assert a.model == "gpt-normal"
    route = {**context, "model": "gpt-normal", "effort": "medium", "justification": "Verified field extraction"}
    assert br.validate_task({"routing": route}, a.provider, cfg)["model"] == "gpt-normal"
    route["model"] = "claude-normal"
    with pytest.raises(ValueError): br.validate_task({"routing": route}, a.provider, cfg)


@pytest.mark.parametrize("field,value", [("_active_children", [object()]), ("_braia_route_batch_safe", False), ("_delegate_depth", 1)])
def test_route_does_not_interrupt_work(cfg, context, tmp_path, field, value):
    a = agent(tmp_path); setattr(a, field, value)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_switch") as sw:
        with pytest.raises(ValueError): br.control_action("route", json.dumps(context), a)
        sw.assert_not_called()


def test_review_ownership_and_real_evidence(cfg, context, tmp_path, host_parent):
    a = host_parent()
    s = br.Store(tmp_path)
    eid = s.begin(cfg, context, "anthropic", "claude-normal"); s.finish(eid, "completed", 1)
    artifact = tmp_path/'test-report.json'; artifact.write_text('{"checks_passed": 4}')
    data = {"execution_id": eid, "verdict": "accepted", "evidence_path": str(artifact), "evidence_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "verifier": "parent-review"}
    with patch.object(br, "load_policy", return_value=cfg):
        with pytest.raises(ValueError): br.control_action("review", json.dumps(data), a)
        # Only host child construction/execution can grant ownership.
        result, _ = run_owned_child(cfg, context, tmp_path, a)
        data["execution_id"] = result["braia_routing"]["execution_id"]
        altered = {**data, "evidence_sha256": "b"*64}
        with pytest.raises(ValueError): br.control_action("review", json.dumps(altered), a)
        assert br.control_action("review", json.dumps(data), a)["status"] == "recorded"


def test_fallback_bounded_no_task_or_tool_replay(cfg, context, tmp_path):
    a = agent(tmp_path)
    s = br.Store(tmp_path)
    eid = s.begin(cfg, context, "anthropic", "claude-normal")
    a._braia_execution = {"id": eid, "ids": [eid], "cfg": cfg, "context": context, "profile": tmp_path, "store": s, "started": time.monotonic(), "alternatives": [{"provider": "openai-codex", "model": "gpt-normal"}]}
    with patch.object(br, "_switch", side_effect=switch) as sw:
        assert br.route_fallback(a, "content_policy_blocked") is False
        assert br.route_fallback(a, None) is False
        assert br.route_fallback(a, "rate_limit") is True
        assert br.route_fallback(a, "timeout") is False
        assert sw.call_count == 1
    assert len(a._braia_execution["ids"]) == 2


def test_fallback_switch_receives_explicit_effort(cfg, context, tmp_path):
    a = agent(tmp_path)
    s = br.Store(tmp_path)
    explicit = {**context, "model": "claude-normal", "effort": "high"}
    eid = s.begin(cfg, explicit, "anthropic", "claude-normal")
    a._braia_execution = {"id": eid, "ids": [eid], "cfg": cfg, "context": explicit,
                          "profile": tmp_path, "store": s, "started": time.monotonic(),
                          "alternatives": br.fallback_candidates(
                              cfg, "anthropic", "claude-normal", "normal", effort="high")}
    with patch.object(br, "_switch", side_effect=switch) as sw:
        assert br.route_fallback(a, "rate_limit") is True
    assert sw.call_args.args[-1] == "high"


def test_conversation_hook_records_unreviewed_and_blocks_overlap(cfg, context, tmp_path):
    a = agent(tmp_path); a.braia_task_context = context
    seen = []
    @br.routed_conversation
    def run(a):
        seen.append(a.provider)
        with pytest.raises(ValueError, match="concurrent"): run(a)
        return {"completed": True, "final_response": "generated, not accepted"}
    evidence(br.Store(tmp_path), cfg, context)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch):
        result = run(a)
    assert seen == ["openai-codex"]
    assert result["braia_routing"]["quality"] == "unreviewed"
    with br.Store(tmp_path).tx() as db:
        row = db.execute("SELECT * FROM executions WHERE id=?", (result["braia_routing"]["execution_id"],)).fetchone()
        assert row["verdict"] is None


def test_disconnected_preference_preserved_with_capability_floor(cfg, context, tmp_path):
    cfg.update(initial_model="claude-normal", initial_tier="normal")
    del cfg["providers"]["anthropic"]
    cfg = br.validate_policy(cfg)
    assert cfg["initial_provider"] == "anthropic"
    choice = br._start_choice(cfg, "anthropic", "claude-normal", "normal", True)
    assert choice[:2] == ("openai-codex", "gpt-normal")
    assert choice[3] is True
    cfg["initial_tier"] = "complex"
    with pytest.raises(ValueError): br._start_choice(cfg, "anthropic", "claude-normal", "normal", True)


def test_hermes_dynamic_schema_and_child_constructor(cfg, context, tmp_path):
    dt = pytest.importorskip("tools.delegate_tool")
    run_agent = pytest.importorskip("run_agent")
    p = MagicMock()
    p.provider = "anthropic"; p.model = "claude-normal"; p.base_url = "https://api.anthropic.com"
    p.api_key = "test-key"; p.api_mode = "anthropic_messages"; p._delegate_depth = 0
    p._active_children = []; p._session_db = None; p.enabled_toolsets = []
    p._braia_profile = str(tmp_path); p._fallback_chain = [{"provider": "openrouter", "model": "wrong-family"}]
    route = {**context, "complexity": "routine", "model": "claude-small", "effort": None, "justification": "Bounded routine"}
    with patch.object(br, "load_policy", return_value=cfg), patch.object(dt, "_load_config", return_value={}), patch.object(run_agent, "AIAgent") as ctor:
        schema = dt._build_dynamic_schema_overrides()
        assert "routing" in schema["parameters"]["properties"]["tasks"]["items"]["required"]
        assert "route" in schema["parameters"]["properties"]["action"]["enum"]
        child = dt._build_child_agent(task_index=0, goal="test child", context=None, toolsets=None, model="claude-small", max_iterations=5, task_count=1, parent_agent=p, routing_spec=route)
        assert ctor.call_args.kwargs["model"] == "claude-small"
        assert ctor.call_args.kwargs["reasoning_config"] is None
        assert ctor.call_args.kwargs["fallback_model"] == []
        assert child.braia_task_context == route
    with patch.object(br, "load_policy", return_value=None):
        schema = dt._build_dynamic_schema_overrides()
        assert "routing" not in schema["parameters"]["properties"]["tasks"]["items"]["properties"]


def test_hermes_dispatch_same_request_route_and_family(cfg, context, tmp_path):
    dt = pytest.importorskip("tools.delegate_tool")
    run_agent = pytest.importorskip("run_agent")
    p = agent(tmp_path)
    evidence(br.Store(tmp_path), cfg, context)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch):
        result = json.loads(run_agent.AIAgent._dispatch_delegate_task(p, {"action": "route", "message": json.dumps(context)}))
    assert result["active_provider"] == p.provider == "openai-codex"
    tasks = [{"goal": "extract and independently verify fields", "routing": {**context, "model": "gpt-normal", "effort": "medium", "justification": "Known schema"}}]
    with patch.object(dt, "delegate_task", return_value="forwarded") as dispatch:
        assert run_agent.AIAgent._dispatch_delegate_task(p, {"tasks": tasks}) == "forwarded"
        assert dispatch.call_args.kwargs["tasks"] == tasks
        assert dispatch.call_args.kwargs["parent_agent"].provider == "openai-codex"


def test_hermes_invalid_later_task_rejected_before_any_construction(cfg, context, tmp_path):
    dt = pytest.importorskip("tools.delegate_tool")
    p = agent(tmp_path)
    route = {**context, "model": "claude-normal", "effort": "medium", "justification": "Known schema"}
    tasks = [{"goal": "Extract fields from sample one", "routing": route},
             {"goal": "Extract fields from sample two", "routing": {**route, "model": "gpt-normal"}}]
    with patch.object(br, "load_policy", return_value=cfg), patch.object(dt, "_load_config", return_value={}), patch.object(dt, "_build_child_preserving_parent_tools") as build:
        result = json.loads(dt.delegate_task(tasks=tasks, parent_agent=p))
        assert "active parent provider" in result["error"]
        build.assert_not_called()


def _process_sample(args):
    profile, cfg, context = args
    return sample(br.Store(profile), cfg, context, "anthropic")


def test_cross_process_writes_and_atomic_promotion(cfg, context, tmp_path):
    profile = tmp_path/'process'; profile.mkdir()
    with ProcessPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(_process_sample, [(profile, cfg, context)] * 12))
    assert len(set(ids)) == 12
    s = br.Store(tmp_path)
    evidence(s, cfg, context)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: br.Store(tmp_path).choose(cfg, context, list(cfg["providers"])), range(8)))
    assert all(result[0] == "openai-codex" for result in results)
    with s.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM promotions").fetchone()[0] == 1


def test_model_not_found_is_availability_not_quality(cfg, context, tmp_path):
    a = agent(tmp_path)
    s = br.Store(tmp_path)
    eid = s.begin(cfg, context, "anthropic", "claude-normal")
    a._braia_execution = {"id": eid, "ids": [eid], "cfg": cfg, "context": context, "profile": tmp_path, "store": s, "started": time.monotonic(), "alternatives": [{"provider": "openai-codex", "model": "gpt-normal"}]}
    with patch.object(br, "_switch", side_effect=switch):
        assert br.route_fallback(a, "model_not_found") is True
    with s.tx() as db:
        row = db.execute("SELECT status,verdict FROM executions WHERE id=?", (eid,)).fetchone()
        assert tuple(row) == ("model_not_found", None)


@pytest.mark.parametrize("status_code,error_type", [(429, "rate_limit"), (404, "model_not_found")])
def test_native_conversation_loop_dynamic_fallback_after_tool_does_not_replay(cfg, context, tmp_path, monkeypatch, status_code, error_type):
    ra = pytest.importorskip("run_agent")
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tool = {"type": "function", "function": {"name": "todo", "description": "test", "parameters": {"type": "object", "properties": {}}}}
    with patch.object(ra, "get_tool_definitions", return_value=[tool]), patch.object(ra, "check_toolset_requirements", return_value={}), patch.object(ra, "OpenAI"):
        a = ra.AIAgent(api_key="test-key", provider="openrouter", model="test/model", base_url="https://openrouter.ai/api/v1/", quiet_mode=True, skip_context_files=True, skip_memory=True)
    a.provider = "anthropic"; a.model = "claude-normal"; a._braia_profile = str(tmp_path)
    a._cached_system_prompt = "Test agent"; a._use_prompt_caching = False
    a.compression_enabled = False; a.save_trajectories = False; a._credential_pool = None
    a.valid_tool_names = {"todo"}
    def response(content, calls=None):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls), finish_reason="tool_calls" if calls else "stop")], model="test/model", usage=None)
    call = SimpleNamespace(id="once", type="function", function=SimpleNamespace(name="todo", arguments="{}"))
    http = httpx.Response(status_code, request=httpx.Request("POST", "https://example.invalid"))
    exc = (openai.RateLimitError if status_code == 429 else openai.NotFoundError)("rate limited" if status_code == 429 else "model not found", response=http, body={"error": {"code": error_type, "message": "model not found" if status_code == 404 else "rate limit"}})
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch), patch.object(a, "_interruptible_api_call", side_effect=[response("Doing one operation", [call]), exc, response("Finished")]) as request, patch.object(a, "_execute_tool_calls", wraps=a._execute_tool_calls) as tool_call, patch.object(a, "_persist_session"), patch.object(a, "_save_trajectory"), patch.object(a, "_cleanup_task_resources"):
        result = a.run_conversation("Perform one operation and report")
    assert result["final_response"] == "Finished"
    assert request.call_count == 3
    assert tool_call.call_count == 1
    assert result["braia_routing"]["provider"] == "openai-codex"
    assert len(result["braia_routing"]["attempt_ids"]) == 2


def test_complex_work_can_use_configured_normal_orchestrator(cfg, context, tmp_path):
    context["complexity"] = "complex"
    a = agent(tmp_path)
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch):
        result = br.control_action("route", json.dumps(context), a)
    assert result["orchestrator_model"] == "claude-normal"
    assert "claude-large" in result["models"]


def test_provider_failures_after_latest_acceptance_prevent_speed_promotion(cfg, context, tmp_path):
    s = br.Store(tmp_path); now = time.time()
    for n in range(4):
        sample(s, cfg, context, "anthropic", True, now-20+n, 10)
        sample(s, cfg, context, "openai-codex", True, now-20+n, 1)
    for n in range(20):
        eid = s.begin(cfg, context, "openai-codex", "gpt-normal", now-10+n/10)
        s.finish(eid, "rate_limit", 1)
    assert s.choose(cfg, context, list(cfg["providers"]), now)[0] == "anthropic"


def test_detached_background_child_blocks_route_until_actual_completion(cfg, context, tmp_path):
    a = agent(tmp_path); child = SimpleNamespace()
    br.track_child(child, a)
    a._active_children = []  # exactly what native async dispatch does
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch) as sw:
        with pytest.raises(ValueError, match="active children"): br.control_action("route", json.dumps(context), a)
        sw.assert_not_called()
        import threading
        child._braia_turn_lock = threading.Lock(); child._braia_turn_lock.acquire()
        br.finish_child(child, a)
        with pytest.raises(ValueError, match="active children"): br.control_action("route", json.dumps(context), a)
        child._braia_turn_lock.release(); br.finish_child(child, a)
        assert br.control_action("route", json.dumps(context), a)["status"] == "routed"


def test_first_turn_explicit_model_beats_initial_preference(cfg, tmp_path):
    cfg["manual_provider"] = "anthropic"
    a = agent(tmp_path); a.provider = "openai-codex"; a.model = "gpt-normal"
    a.requested_provider = "openai-codex"
    @br.routed_conversation
    def run(a): return {"completed": True, "provider_seen": a.provider}
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch", side_effect=switch):
        result = run(a)
    assert result["provider_seen"] == "openai-codex"
    assert result["braia_routing"]["decision"] == "manual_session"


def test_initial_disconnected_fallback_preserves_explicit_effort(cfg, context, tmp_path):
    cfg.update(initial_model="claude-normal", initial_tier="normal")
    del cfg["providers"]["anthropic"]
    cfg = br.validate_policy(cfg)
    a = agent(tmp_path)
    a._delegate_depth = 1
    a.braia_task_context = {**context, "model": "claude-normal", "effort": "high"}
    captured = []
    def capture_switch(agent, provider, model, policy, profile, effort=None):
        captured.append(effort)
        switch(agent, provider, model, policy, profile, effort)
    @br.routed_conversation
    def run(agent):
        return {"completed": True}
    with patch.object(br, "load_policy", return_value=cfg), \
         patch.object(br, "_runtime", return_value={}), \
         patch.object(br, "_switch", side_effect=capture_switch):
        result = run(a)
    assert result["braia_routing"]["provider"] == "openai-codex"
    assert captured[0] == "high"


def test_new_parent_turn_does_not_switch_while_detached_child_active(cfg, tmp_path):
    a = agent(tmp_path); a.provider = "openai-codex"; a.model = "gpt-normal"
    a._braia_last_route = (a.provider, a.model)
    br.track_child(SimpleNamespace(), a)
    @br.routed_conversation
    def run(a): return {"completed": True, "provider_seen": a.provider}
    with patch.object(br, "load_policy", return_value=cfg), patch.object(br, "_runtime", return_value={}), patch.object(br, "_switch") as sw:
        result = run(a)
        sw.assert_not_called()
    assert result["provider_seen"] == "openai-codex"


def test_fallback_continuations_cannot_promote_as_fast_complete_tasks(cfg, context, tmp_path):
    s = br.Store(tmp_path)
    for _ in range(4):
        sample(s, cfg, context, "anthropic", True, latency=100)
        a = agent(tmp_path)
        eid = s.begin(cfg, context, "anthropic", "claude-normal")
        a._braia_execution = {"id": eid, "ids": [eid], "cfg": cfg, "context": dict(context), "profile": tmp_path, "store": s, "started": time.monotonic()-99, "alternatives": [{"provider": "openai-codex", "model": "gpt-normal"}]}
        with patch.object(br, "_switch", side_effect=switch):
            assert br.route_fallback(a, "rate_limit")
        final_id = a._braia_execution["id"]
        s.finish(final_id, "completed", 1)
        s.verdict(final_id, "accepted", "a"*64, "checker")
    assert s.choose(cfg, context, list(cfg["providers"]))[0] == "anthropic"
    with s.tx() as db:
        assert db.execute("SELECT COUNT(DISTINCT bucket) FROM executions").fetchone()[0] == 2


def test_native_failed_manual_switch_does_not_record_preference(tmp_path):
    ra = pytest.importorskip("run_agent")
    helpers = pytest.importorskip("agent.agent_runtime_helpers")
    a = agent(tmp_path)
    with patch.object(helpers, "switch_model", side_effect=ValueError("client rebuild failed")):
        with pytest.raises(ValueError): ra.AIAgent.switch_model(a, "gpt-normal", "openai-codex")
    assert not hasattr(a, "_braia_manual_route")
    def native_switch(a, model, provider, *args):
        a.model, a.provider = model, provider
    with patch.object(helpers, "switch_model", side_effect=native_switch):
        ra.AIAgent.switch_model(a, "gpt-normal", "openai-codex")
    assert a._braia_manual_route == ("openai-codex", "gpt-normal")


@pytest.mark.parametrize("error_class", [ValueError, RuntimeError, KeyboardInterrupt])
def test_second_constructor_failure_releases_unstarted_children(cfg, context, tmp_path, monkeypatch, error_class):
    dt = pytest.importorskip("tools.delegate_tool")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    a = agent(tmp_path)
    orphan = SimpleNamespace(_subagent_id="first-unstarted", tool_progress_callback=None, close=MagicMock())
    calls = []
    def builder(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise error_class("second constructor failed")
        br.track_child(orphan, a)
        a._active_children.append(orphan)
        return orphan
    route = {**context, "model": "claude-normal", "effort": "medium", "justification": "Known fields"}
    tasks = [{"goal": "Extract fields from first independent record", "routing": route},
             {"goal": "Extract fields from second independent record", "routing": route}]
    with patch.object(br, "load_policy", return_value=cfg), patch.object(dt, "_load_config", return_value={}), patch.object(dt, "_build_child_preserving_parent_tools", side_effect=builder):
        if error_class is ValueError:
            result = json.loads(dt.delegate_task(tasks=tasks, parent_agent=a))
            assert "second constructor failed" in result["error"]
        else:
            with pytest.raises(error_class, match="second constructor failed"):
                dt.delegate_task(tasks=tasks, parent_agent=a)
    assert a._braia_inflight_children == {}
    assert a._active_children == []
    orphan.close.assert_called_once()
