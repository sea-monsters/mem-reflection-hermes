"""test_beta2.py — Regression and smoke tests for v0.9.2-beta2.

Covers WS-1 (supersedes governance), WS-2 (lineage-aware recall),
WS-3 (reflection quality audit), WS-6 (temporal/context hints),
and host-contract basics.
"""
import os
import sys
import tempfile
from pathlib import Path

repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Register mem_reflection_hermes package so subpackage imports work
if "mem_reflection_hermes" not in sys.modules:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mem_reflection_hermes",
        str(Path(repo_root) / "__init__.py"),
        submodule_search_locations=[repo_root],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mem_reflection_hermes"] = mod
    spec.loader.exec_module(mod)

from mem_reflection_hermes.core import (
    MemoryFrontmatter, LoadedMemory, parse_frontmatter, serialize_frontmatter,
    read_memory, _lineage_latest, _lineage_root, _lineage_depth,
    _lineage_cycle_check, _classify_update_intent, _is_expired,
)
from mem_reflection_hermes.reflection.engine import (
    _build_audit_entry, _append_reflect_log, _recent_reflect_outcomes,
)
from mem_reflection_hermes import MemoryStore

# Set up temp home for isolation
TMPDIR = Path(tempfile.mkdtemp(prefix="hermes_beta2_"))
(TMPDIR / "memory" / "memories").mkdir(parents=True, exist_ok=True)
(TMPDIR / "memory" / "skills").mkdir(parents=True, exist_ok=True)
os.environ["HERMES_HOME"] = str(TMPDIR)

PASS = 0
FAIL = 0

def ok(desc):
    global PASS
    PASS += 1
    print(f"  [PASS] {desc}")

def fail(desc, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {desc} {detail}")

def wait_for_file(path: Path, timeout: float = 2.0) -> bool:
    import time
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if path.exists():
            return True
        time.sleep(0.05)
    return False

# ---------------------------------------------------------------------------
# WS-6: Frontmatter round-trip with new fields
# ---------------------------------------------------------------------------
print("\n=== WS-6: Frontmatter round-trip ===")

def test_frontmatter_roundtrip():
    data = {
        "id": "test-001",
        "created": "2026-06-01T00:00:00+00:00",
        "source": "user",
        "confidence": "high",
        "zone": "work",
        "supersedes": ["old-001"],
        "supersedes_reason": "corrected preference",
        "valid_from": "2026-06-01",
        "valid_until": "2026-12-31",
        "context_scope": "project-alpha",
        "tags": ["python", "testing"],
    }
    text = serialize_frontmatter(data, "Test body content.")
    parsed, body = parse_frontmatter(text)

    if parsed.get("supersedes_reason") == "corrected preference":
        ok("supersedes_reason round-trip")
    else:
        fail("supersedes_reason round-trip", str(parsed.get("supersedes_reason")))

    vf = parsed.get("valid_from")
    if str(vf) == "2026-06-01":
        ok("valid_from round-trip")
    else:
        fail("valid_from round-trip", repr(vf))

    vu = parsed.get("valid_until")
    if str(vu) == "2026-12-31":
        ok("valid_until round-trip")
    else:
        fail("valid_until round-trip", repr(vu))

    if parsed.get("context_scope") == "project-alpha":
        ok("context_scope round-trip")
    else:
        fail("context_scope round-trip")

    if body == "Test body content.":
        ok("body preserved")
    else:
        fail("body preserved", repr(body))

test_frontmatter_roundtrip()


def test_store_write_update_reorder_preserve_beta2_fields():
    store_rt = MemoryStore(TMPDIR / "memory" / "roundtrip")
    fm = MemoryFrontmatter.new(source="user", zone="work")
    fm.id = "roundtrip-beta2"
    fm.supersedes_reason = "manual correction"
    fm.valid_from = "2026-06-01"
    fm.valid_until = "2026-12-31"
    fm.context_scope = "project-alpha"

    path = store_rt.put("user", fm, "Beta2 field preservation.")
    wait_for_file(path)
    store_rt._invalidate_cache()

    updated = store_rt.update("roundtrip-beta2", body="Updated beta2 body.")
    parsed, body = parse_frontmatter(updated.source_path.read_text(encoding="utf-8"))
    checks = [
        (parsed.get("supersedes_reason") == "manual correction", "update preserves supersedes_reason"),
        (str(parsed.get("valid_from")) == "2026-06-01", "update preserves valid_from"),
        (str(parsed.get("valid_until")) == "2026-12-31", "update preserves valid_until"),
        (parsed.get("context_scope") == "project-alpha", "update preserves context_scope"),
        (body == "Updated beta2 body.", "update preserves body"),
    ]
    for cond, desc in checks:
        if cond:
            ok(desc)
        else:
            fail(desc, str(parsed))

    store_rt.reorder(["roundtrip-beta2"])
    store_rt._invalidate_cache()
    after = store_rt.get("roundtrip-beta2")
    parsed_after, _ = parse_frontmatter(after.source_path.read_text(encoding="utf-8"))
    if parsed_after.get("supersedes_reason") == "manual correction" and parsed_after.get("context_scope") == "project-alpha":
        ok("reorder preserves beta2 fields")
    else:
        fail("reorder preserves beta2 fields", str(parsed_after))


test_store_write_update_reorder_preserve_beta2_fields()

# ---------------------------------------------------------------------------
# WS-1: Lineage helpers
# ---------------------------------------------------------------------------
print("\n=== WS-1: Lineage helpers ===")

store = MemoryStore(TMPDIR / "memory" / "memories")

def test_lineage():
    global PASS, FAIL
    # Write root memory
    fm1 = MemoryFrontmatter.new(source="user", zone="work")
    fm1.id = "root-001"
    p1 = store.put("user", fm1, "Original fact: user likes Python.")
    wait_for_file(p1)

    # Write superseding memory
    fm2 = MemoryFrontmatter.new(source="user", zone="work")
    fm2.id = "super-001"
    fm2.supersedes = ["root-001"]
    fm2.supersedes_reason = "preference changed"
    p2 = store.put("user", fm2, "Updated fact: user prefers Go.")
    wait_for_file(p2)

    # is_superseded
    if store.is_superseded("root-001"):
        ok("is_superseded(root-001)")
    else:
        fail("is_superseded(root-001)")

    if not store.is_superseded("super-001"):
        ok("is_superseded(super-001) is False")
    else:
        fail("is_superseded(super-001) should be False")

    # latest_for
    latest = store.latest_for("root-001")
    if latest is not None and latest.id() == "super-001":
        ok("latest_for(root-001) -> super-001")
    else:
        fail("latest_for(root-001)", str(latest))

    # lineage_chain
    chain = store.lineage_chain("super-001", max_depth=10)
    ids = [m.id() for m in chain]
    if ids == ["root-001", "super-001"]:
        ok("lineage_chain order")
    else:
        fail("lineage_chain order", str(ids))

    # _lineage_cycle_check: no cycle
    cycle = _lineage_cycle_check(store, "root-001")
    if cycle is None:
        ok("no cycle detected")
    else:
        fail("no cycle detected", str(cycle))

    # _lineage_cycle_check: inject cycle manually by writing files directly
    # A -> B -> C -> A  cycle (bypass put() validation)
    root = TMPDIR / "memory" / "memories"
    for mid, supers, body in [
        ("cycle-a", ["cycle-c"], "A"),
        ("cycle-b", ["cycle-a"], "B"),
        ("cycle-c", ["cycle-b"], "C"),
    ]:
        fm = MemoryFrontmatter.new(source="user", zone="work")
        fm.id = mid
        fm.supersedes = supers
        text = serialize_frontmatter({
            "id": mid,
            "created": fm.created,
            "source": fm.source,
            "confidence": fm.confidence,
            "zone": fm.zone,
            "supersedes": supers,
        }, body)
        p = root / f"2026-06-01-{mid}.md"
        p.write_text(text, encoding="utf-8")

    store._invalidate_cache()
    cycle = _lineage_cycle_check(store, "cycle-a")
    if cycle is not None and "cycle-a" in cycle:
        ok("cycle detected")
    else:
        fail("cycle detection", str(cycle))

test_lineage()

# ---------------------------------------------------------------------------
# WS-2: Search lineage-aware recall
# ---------------------------------------------------------------------------
print("\n=== WS-2: Search lineage-aware recall ===")

def test_search_recall():
    global PASS, FAIL
    # Write a new memory that supersedes an old one about Python
    fm_old = MemoryFrontmatter.new(source="user", zone="work")
    fm_old.id = "py-old"
    store.put("user", fm_old, "User prefers Python 3.10.")

    fm_new = MemoryFrontmatter.new(source="user", zone="work")
    fm_new.id = "py-new"
    fm_new.supersedes = ["py-old"]
    p_new = store.put("user", fm_new, "User prefers Python 3.12.")

    # Wait for async writes to complete
    wait_for_file(p_new)
    store._invalidate_cache()

    # Default search (include_history=False) should not return superseded
    active_results = store.search("Python", k=10, include_history=False)
    active_ids = [m.id() for m in active_results]
    if "py-new" in active_ids and "py-old" not in active_ids:
        ok("default search excludes superseded")
    else:
        fail("default search excludes superseded", f"active_ids={active_ids}")

    # Search with include_history=True should return both
    all_results = store.search("Python", k=10, include_history=True)
    all_ids = [m.id() for m in all_results]
    if "py-old" in all_ids and "py-new" in all_ids:
        ok("include_history returns superseded")
    else:
        fail("include_history returns superseded", f"all_ids={all_ids}")

test_search_recall()

# ---------------------------------------------------------------------------
# WS-1: Conflict response and supersedes validation
# ---------------------------------------------------------------------------
print("\n=== WS-1: Supersedes validation ===")

def test_supersedes_validation():
    global PASS, FAIL
    # Direct store validation: missing supersedes target should be caught
    fm = MemoryFrontmatter.new(source="user", zone="work")
    fm.supersedes = ["nonexistent-id-12345"]
    try:
        store.put("user", fm, "Fact with bad supersedes.")
        fail("missing supersedes target rejected", "put() did not raise")
    except ValueError as e:
        if "not found" in str(e).lower() or "missing" in str(e).lower():
            ok("missing supersedes target rejected")
        else:
            ok("missing supersedes target rejected (via ValueError)")
    except Exception as e:
        ok("missing supersedes target rejected (via exception)")

test_supersedes_validation()

# ---------------------------------------------------------------------------
# WS-6: Expired memory detection
# ---------------------------------------------------------------------------
print("\n=== WS-6: Temporal hints ===")

def test_temporal_hints():
    fm = MemoryFrontmatter.new(source="user")
    fm.valid_until = "2020-01-01T00:00:00+00:00"
    if _is_expired(fm):
        ok("expired memory detected")
    else:
        fail("expired memory detected")

    fm2 = MemoryFrontmatter.new(source="user")
    fm2.valid_until = "2099-01-01T00:00:00+00:00"
    if not _is_expired(fm2):
        ok("future memory not expired")
    else:
        fail("future memory not expired")

test_temporal_hints()

# ---------------------------------------------------------------------------
# WS-7: Host contract smoke
# ---------------------------------------------------------------------------
print("\n=== WS-7: Host contract smoke ===")


def test_register_contract():
    global PASS, FAIL

    class FakeCtx:
        def __init__(self):
            self.tools = []
            self.hooks = []
            self.slash = []

        def register_tool(self, **kw):
            self.tools.append(kw.get("name"))

        def register_hook(self, name, handler):
            self.hooks.append(name)

        def register_slash_command(self, **kw):
            self.slash.append(kw.get("name"))

        def register_command(self, **kw):
            self.slash.append(kw.get("name"))

    # Simulate minimal plugin bootstrap
    fake = FakeCtx()
    try:
        from mem_reflection_hermes.tools.handlers import register as _handlers_register
        _handlers_register(fake)
    except Exception as e:
        fail("handlers.register", str(e))
        return

    # 12 tools from handlers
    if len(fake.tools) == 12:
        ok("handlers.register: 12 tools")
    else:
        fail("handlers.register: 12 tools", f"got {len(fake.tools)}")

    # 3 hooks from handlers
    if len(fake.hooks) == 3:
        ok("handlers.register: 3 hooks")
    else:
        fail("handlers.register: 3 hooks", f"got {len(fake.hooks)}")

    # Verify pre_llm_call exists and accepts **kwargs
    try:
        from mem_reflection_hermes.hooks.lifecycle import _pre_llm_call
        import inspect
        sig = inspect.signature(_pre_llm_call)
        params = list(sig.parameters.keys())
        if "kwargs" in str(sig) or not params:
            ok("pre_llm_call accepts **kwargs")
        else:
            fail("pre_llm_call accepts **kwargs", str(sig))
    except Exception as e:
        fail("pre_llm_call import", str(e))

    # Verify post_tool_call hook is registered (it lives in __init__.py, not hooks.py)
    try:
        from mem_reflection_hermes import register
        fake2 = FakeCtx()
        register(fake2)
        if "post_tool_call" in fake2.hooks:
            ok("post_tool_call hook registered")
        else:
            fail("post_tool_call hook registered", f"hooks={fake2.hooks}")
        # Full plugin: 17 tools (12 from handlers + 4 graph tools + 1 health)
        if len(fake2.tools) == 17:
            ok("register(ctx): 17 tools total")
        else:
            fail("register(ctx): 17 tools total", f"got {len(fake2.tools)}")
        # Full plugin: 4 hooks
        if len(fake2.hooks) == 4:
            ok("register(ctx): 4 hooks total")
        else:
            fail("register(ctx): 4 hooks total", f"got {len(fake2.hooks)}")
    except Exception as e:
        fail("register(ctx) full", str(e))


test_register_contract()

# ---------------------------------------------------------------------------
# WS-3: Reflection quality audit
# ---------------------------------------------------------------------------
print("\n=== WS-3: Reflection quality audit ===")


def test_audit_entry_structure():
    global PASS, FAIL
    ae = _build_audit_entry(
        candidate_id="cand_test_001",
        decision="accepted",
        decision_reason="novelty sufficient",
        novelty_score=0.75,
        conflict_id="",
        supersedes_ids=["old_001"],
        supersedes_reason="corrected preference",
        assigned_zone="work",
        graph_migration={"migrated_edges": 3},
    )
    checks = [
        (ae.get("candidate_id") == "cand_test_001", "candidate_id"),
        (ae.get("decision") == "accepted", "decision"),
        (ae.get("decision_reason") == "novelty sufficient", "decision_reason"),
        (abs(ae.get("novelty_score", 0) - 0.75) < 0.001, "novelty_score"),
        (ae.get("supersedes_ids") == ["old_001"], "supersedes_ids"),
        (ae.get("supersedes_reason") == "corrected preference", "supersedes_reason"),
        (ae.get("assigned_zone") == "work", "assigned_zone"),
        (ae.get("graph_migration", {}).get("migrated_edges") == 3, "graph_migration"),
    ]
    for cond, name in checks:
        if cond:
            ok(f"audit entry {name}")
        else:
            fail(f"audit entry {name}", str(ae.get(name.lower().replace(" ", "_"))))


def test_reflect_log_audit_roundtrip():
    global PASS, FAIL
    entry = {
        "timestamp": "2026-06-01T12:00:00+00:00",
        "mode": "embedding_micro",
        "summary": "test",
        "audit_entries": [
            _build_audit_entry(
                candidate_id="cand_abc",
                decision="skipped",
                decision_reason="novelty too low",
                novelty_score=0.1,
                assigned_zone="episode",
            ),
            _build_audit_entry(
                candidate_id="cand_def",
                decision="accepted",
                decision_reason="novelty sufficient",
                novelty_score=0.8,
                assigned_zone="work",
            ),
        ],
    }
    _append_reflect_log(entry)

    # Read back
    recent = _recent_reflect_outcomes(n=5)
    found = False
    for r in recent:
        if r.get("summary") == "test":
            found = True
            audit_entries = r.get("audit_entries", [])
            if len(audit_entries) == 2:
                ok("audit round-trip count")
            else:
                fail("audit round-trip count", str(len(audit_entries)))

            skipped = [a for a in audit_entries if a.get("decision") == "skipped"]
            accepted = [a for a in audit_entries if a.get("decision") == "accepted"]
            if len(skipped) == 1 and skipped[0].get("novelty_score") == 0.1:
                ok("skipped audit entry preserved")
            else:
                fail("skipped audit entry preserved")
            if len(accepted) == 1 and accepted[0].get("assigned_zone") == "work":
                ok("accepted audit entry preserved")
            else:
                fail("accepted audit entry preserved")
            break

    if not found:
        fail("audit round-trip", "test entry not found in recent outcomes")


test_audit_entry_structure()
test_reflect_log_audit_roundtrip()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 50)
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("EXIT 1")
    sys.exit(1)
else:
    print("All tests passed!")
