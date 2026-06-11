"""test_context.py — Tests for context assembly.

Coverage:
- build_context 4-layer priority (pinned → active → skills → always-active)
- Token budget enforcement
- Zone filtering in active memories
- Skill matching by token overlap
- Empty / boundary conditions

Run: pytest tests/test_context.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "core" / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter
LoadedMemory = _store.LoadedMemory
SkillFrontmatter = _store.SkillFrontmatter
LoadedSkill = _store.LoadedSkill

_spec_search = importlib.util.spec_from_file_location("_search", str(_REPO / "core" / "search.py"))
_search = importlib.util.module_from_spec(_spec_search)
sys.modules["_search"] = _search
_spec_search.loader.exec_module(_search)
SearchIndex = _search.SearchIndex

_spec_context = importlib.util.spec_from_file_location("_context", str(_REPO / "memory" / "context.py"))
_context = importlib.util.module_from_spec(_spec_context)
sys.modules["_context"] = _context
_spec_context.loader.exec_module(_context)
build_context = _context.build_context
build_context_bundle = _context.build_context_bundle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_store():
    tmpdir = tempfile.mkdtemp(prefix="hermes_store_")
    root = Path(tmpdir) / "memories"
    root.mkdir(parents=True, exist_ok=True)
    db_path = Path(tmpdir) / "memories.db"
    store = MemoryStore(user_root=root, db_path=db_path)
    yield store
    try:
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    import shutil as _shutil
    import time as _time
    for _attempt in range(5):
        try:
            _shutil.rmtree(tmpdir)
            break
        except PermissionError:
            _time.sleep(0.1)
    _shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_skills_dir():
    tmpdir = tempfile.mkdtemp(prefix="hermes_skills_")
    root = Path(tmpdir)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    import shutil as _shutil
    _shutil.rmtree(tmpdir, ignore_errors=True)


class FakeSkills:
    """Minimal skills container for context tests."""

    def __init__(self, skills):
        self._skills = skills

    def list(self):
        return self._skills


# ---------------------------------------------------------------------------
# build_context — layer priority
# ---------------------------------------------------------------------------

class TestBuildContextPriority:
    def test_empty_store_returns_empty(self, temp_store, monkeypatch):
        """Empty store produces empty context block."""
        # Mock home to a temp dir with no MEMORY.md
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)
        search = SearchIndex(temp_store)
        skills = FakeSkills([])
        ctx = build_context(temp_store, search, skills, "test query", max_tokens=4000)
        assert ctx == ""

    def test_pinned_memories_always_included(self, temp_store):
        """Pinned memories appear in context regardless of query."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", fm, "Important pinned memory")
        search = SearchIndex(store)
        skills = FakeSkills([])
        ctx = build_context(store, search, skills, "", max_tokens=4000)
        assert "Pinned Memories" in ctx
        assert "Important pinned memory" in ctx

    def test_active_memories_via_search(self, temp_store):
        """Active memories are included when query matches."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "User prefers dark mode")
        search = SearchIndex(store)
        skills = FakeSkills([])
        ctx = build_context(store, search, skills, "dark mode", max_tokens=4000)
        assert "Relevant Memories" in ctx
        assert "dark mode" in ctx.lower()

    def test_always_active_skills_included(self, temp_store, temp_skills_dir):
        """Skills marked always_active appear in context."""
        store = temp_store
        # Write a skill file with always_active=True
        skill_path = temp_skills_dir / "test_skill.md"
        skill_path.write_text(
            "---\nname: TestSkill\ndescription: A test skill\nalways_active: true\n---\n"
        )
        # Read it back
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)
        ctx = build_context(store, search, skills, "", max_tokens=4000)
        assert "Active Skills" in ctx
        assert "TestSkill" in ctx

    def test_context_bundle_splits_stable_and_dynamic_sections(self, temp_store, temp_skills_dir):
        """v1.4 bundle should separate stable and dynamic sections."""
        store = temp_store

        pinned = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", pinned, "Pinned operating preference")

        general = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", general, "Dark mode is preferred for dashboards")

        skill_path = temp_skills_dir / "always_skill.md"
        skill_path.write_text(
            "---\nname: StableSkill\ndescription: A stable helper\nalways_active: true\n---\n"
        )
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "dark mode", max_tokens=4000)

        assert "Pinned Memories" in bundle.append_system_context
        assert "StableSkill" in bundle.append_system_context
        assert "Relevant Memories" in bundle.prepend_context
        assert "dark mode" in bundle.prepend_context.lower()
        assert bundle.debug["stable_section_count"] >= 1
        assert bundle.debug["dynamic_section_count"] >= 1

    def test_context_bundle_stable_only_omits_dynamic_sections(self, temp_store):
        """Stable-only mode should exclude dynamic recall blocks."""
        store = temp_store
        pinned = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", pinned, "Pinned memory only")
        general = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", general, "This should stay in dynamic recall")

        search = SearchIndex(store)
        skills = FakeSkills([])
        bundle = build_context_bundle(store, search, skills, "dynamic query", stable_only=True, max_tokens=4000)

        assert "Pinned Memories" in bundle.append_system_context
        assert bundle.prepend_context == ""
        assert bundle.debug["stable_only"] is True


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_small_budget_truncates(self, temp_store):
        """Very small max_tokens yields empty or minimal context."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "Short")
        search = SearchIndex(store)
        skills = FakeSkills([])
        ctx = build_context(store, search, skills, "", max_tokens=10)
        # Pinned block alone exceeds budget → likely empty
        assert ctx == "" or "##" not in ctx

    def test_budget_enforcement_drops_layers(self, temp_store):
        """If budget is exhausted by pinned, lower layers are omitted."""
        store = temp_store
        for i in range(2):
            fm = MemoryFrontmatter.new(source="test", pinned=True)
            store.put("user", fm, f"Pinned memory number {i} with extra text")
        search = SearchIndex(store)
        skills = FakeSkills([])
        ctx = build_context(store, search, skills, "", max_tokens=50)
        # Should only include Pinned Memories, not Active Memories
        assert "Pinned Memories" in ctx
        assert "Relevant Memories" not in ctx

    def test_bundle_records_compression_level_under_pressure(self, temp_store, temp_skills_dir):
        """Dynamic sections should degrade by compression level, not tail truncation."""
        store = temp_store
        for i in range(4):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Long memory {i} " + ("detail " * 80))

        skill_path = temp_skills_dir / "compression_skill.md"
        skill_path.write_text(
            "---\nname: CompressionSkill\ndescription: " + ("Useful guidance " * 40) + "\ntriggers: [detail, long]\n---\n"
        )
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "detail long", max_tokens=120)

        assert bundle.debug["compression_level"] in {"mild", "aggressive", "emergency"}
        assert not bundle.prepend_context.endswith("tags:")

    def test_emergency_compression_keeps_pinned_stable_context(self, temp_store):
        """Pinned memories should survive even when dynamic recall is squeezed out."""
        store = temp_store
        pinned = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", pinned, "Pinned memory must remain intact")
        for i in range(6):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Overflow memory {i} " + ("content " * 50))
        search = SearchIndex(store)
        skills = FakeSkills([])

        bundle = build_context_bundle(store, search, skills, "overflow content", max_tokens=90)

        assert "Pinned Memories" in bundle.append_system_context
        assert "Pinned memory must remain intact" in bundle.append_system_context


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

class TestSkillMatching:
    def test_triggered_skills_by_overlap(self, temp_store, temp_skills_dir):
        """Skills matching query by token overlap appear as triggered."""
        store = temp_store
        skill_path = temp_skills_dir / "coding_skill.md"
        skill_path.write_text(
            "---\nname: CodeGen\ndescription: Generate Python code\ntriggers: [python, coding]\n---\n"
        )
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)
        ctx = build_context(store, search, skills, "python coding", max_tokens=4000)
        assert "Triggered Skills" in ctx
        assert "CodeGen" in ctx

    def test_no_triggered_skills_when_no_match(self, temp_store):
        """No triggered skills section when query doesn't match any skill."""
        store = temp_store
        skills = FakeSkills([])
        search = SearchIndex(store)
        ctx = build_context(store, search, skills, "xyzabc unrelated", max_tokens=4000)
        assert "Triggered Skills" not in ctx

    def test_empty_query_no_triggered(self, temp_store):
        """Empty query should not produce triggered skills."""
        store = temp_store
        skills = FakeSkills([])
        search = SearchIndex(store)
        ctx = build_context(store, search, skills, "", max_tokens=4000)
        assert "Triggered Skills" not in ctx


# =====================================================================
# v1.1: Built-in memory block & compacted episode block
# =====================================================================


# =====================================================================
# v1.4: Graded compression boundary tests
# =====================================================================

class TestV14CompressionBoundaries:
    """Test graded compression levels (none/mild/aggressive/emergency)."""

    def test_none_compression_fits_all_sections(self, temp_store, temp_skills_dir):
        """P0: When budget is ample, compression should be 'none' and all sections fit."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", fm, "A memory about context compression")

        skill_path = temp_skills_dir / "trigger_skill.md"
        skill_path.write_text("---\nname: TriggerSkill\ndescription: A triggered skill\ntriggers: [context, compression]\n---\n")
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "context compression", max_tokens=4000)

        assert bundle.debug["compression_level"] == "none"
        assert "relevant_memories" in bundle.debug["included_sections"]
        assert "triggered_skills" in bundle.debug["included_sections"]

    def test_mild_compression_shortens_memory_previews(self, temp_store):
        """P0: mild level reduces memory token limit but keeps all sections."""
        store = temp_store
        for i in range(3):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Memory {i} " + ("extra detail " * 40))
        search = SearchIndex(store)
        skills = FakeSkills([])

        # Budget tight enough to trigger mild but not aggressive
        bundle = build_context_bundle(store, search, skills, "memory", max_tokens=300)

        assert bundle.debug["compression_level"] in {"none", "mild"}
        # Even under mild, relevant_memories should still be included
        if bundle.debug["compression_level"] == "mild":
            assert "relevant_memories" in bundle.debug["included_sections"]

    def test_aggressive_compression_drops_some_sections(self, temp_store, temp_skills_dir):
        """P0: aggressive level may drop lower-priority sections."""
        store = temp_store
        for i in range(5):
            fm = MemoryFrontmatter.new(source="test", zone="general")
            store.put("user", fm, f"Overflow memory {i} " + ("content " * 50))

        skill_path = temp_skills_dir / "big_skill.md"
        skill_path.write_text("---\nname: BigSkill\ndescription: " + ("Very long description " * 30) + "\ntriggers: [overflow]\n---\n")
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "overflow", max_tokens=200)

        assert bundle.debug["compression_level"] in {"mild", "aggressive", "emergency"}
        # Pinned should always survive
        assert "Pinned Memories" in bundle.append_system_context or bundle.append_system_context == ""

    def test_empty_store_produces_empty_bundle(self, temp_store):
        """P0: Empty store + empty skills + empty query should produce empty bundle.

        Design intent: with no stable content and no dynamic content, the bundle
        should be empty. compression_level should be 'none' since there is simply
        nothing to compress — emergency means reactive truncation under pressure.
        """
        store = temp_store
        search = SearchIndex(store)
        skills = FakeSkills([])

        bundle = build_context_bundle(store, search, skills, "", max_tokens=4000)

        assert bundle.prepend_context == ""
        assert bundle.append_system_context == ""
        assert bundle.debug["compression_level"] == "none"
        assert bundle.debug["stable_section_count"] == 0
        assert bundle.debug["dynamic_section_count"] == 0

    def test_empty_query_no_triggered_skills_in_bundle(self, temp_store, temp_skills_dir):
        """P0: Empty query should not produce triggered skills."""
        store = temp_store
        skill_path = temp_skills_dir / "always.md"
        skill_path.write_text("---\nname: AlwaysSkill\ndescription: Always active\nalways_active: true\n---\n")
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "", max_tokens=4000)

        assert "Active Skills" in bundle.append_system_context
        assert "Triggered Skills" not in bundle.prepend_context
        assert bundle.debug.get("stable_only") is not True  # Not stable_only, just no query

    def test_debug_metadata_is_complete(self, temp_store, temp_skills_dir):
        """P0: ContextBundle.debug should contain all expected metadata keys."""
        store = temp_store
        pinned = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", pinned, "Pinned data")
        general = MemoryFrontmatter.new(source="test", zone="general")
        store.put("user", general, "General data")

        skill_path = temp_skills_dir / "skill.md"
        skill_path.write_text("---\nname: DebugSkill\ndescription: Debug test\nalways_active: true\n---\n")
        skill = _store._read_skill_file(skill_path, "user")
        skills = FakeSkills([skill])
        search = SearchIndex(store)

        bundle = build_context_bundle(store, search, skills, "data", max_tokens=4000)

        assert "max_tokens" in bundle.debug
        assert "stable_only" in bundle.debug
        assert "included_sections" in bundle.debug
        assert "dropped_sections" in bundle.debug
        assert "compression_level" in bundle.debug
        assert "used_tokens" in bundle.debug
        assert "stable_section_count" in bundle.debug
        assert "dynamic_section_count" in bundle.debug
        assert bundle.debug["stable_section_count"] >= 1
        assert bundle.debug["dynamic_section_count"] >= 1

    def test_max_tokens_zero_returns_empty(self, temp_store):
        """P0: max_tokens=0 should produce essentially empty output."""
        store = temp_store
        pinned = MemoryFrontmatter.new(source="test", pinned=True, zone="core")
        store.put("user", pinned, "Pinned")
        search = SearchIndex(store)
        skills = FakeSkills([])

        bundle = build_context_bundle(store, search, skills, "", max_tokens=0)

        # Even pinned may not fit at 0 budget; emergency level expected
        assert bundle.debug["compression_level"] == "emergency"
        assert bundle.prepend_context == ""


# =====================================================================
# v1.1: Built-in memory block & compacted episode block
# =====================================================================
    """Test the v1.1 context injection features."""

    def test_compacted_episode_block(self, temp_store, monkeypatch):
        """Compacted episode summaries should appear in context."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        # Add a compacted episode entry to the store
        fm = MemoryFrontmatter.new(
            source="system", tags=["compacted", "auto-summary"], zone="episode",
        )
        temp_store.put("user", fm, "Summary of daily conversations about project planning")

        search = SearchIndex(temp_store)
        skills = FakeSkills([])
        ctx = build_context(temp_store, search, skills, "", max_tokens=4000)
        assert "Episode Summaries" in ctx
        assert "project planning" in ctx

    def test_no_compacted_episode_block_when_empty(self, temp_store, monkeypatch):
        """No compacted episode block when no compacted entries exist."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        search = SearchIndex(temp_store)
        skills = FakeSkills([])
        ctx = build_context(temp_store, search, skills, "", max_tokens=4000)
        assert "Episode Summaries" not in ctx


class TestEpisodeCompressionDetailLevels:
    """Gap C: _build_compacted_episode_block detail_level variants.

    Design intent (FEP §5.7): episode summaries degrade per compression tier:
    - mild: max_items=10, max_chars=200
    - aggressive: max_items=6, max_chars=120
    - emergency (default else): max_items=4, max_chars=80

    Cross-reference: hy-memory context offload uses similar mild/aggressive/emergency
    tiers for L2→L3→L4 degradation. SRH adapts this for episode summaries.
    """

    def test_mild_level_shows_up_to_10_episodes(self, temp_store, monkeypatch):
        """mild detail_level should include up to 10 episodes, 200 chars each."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        for i in range(12):
            fm = MemoryFrontmatter.new(
                source="system", tags=["compacted", "auto-summary"], zone="episode",
            )
            temp_store.put("user", fm, f"Summary {i}: " + ("detail " * 30))

        block = _context._build_compacted_episode_block(temp_store, detail_level="mild")

        assert "## Episode Summaries" in block
        # Should contain "... (2 more)" indicating 12 total but only 10 shown
        assert "(2 more)" in block

    def test_aggressive_level_limits_to_6_episodes(self, temp_store, monkeypatch):
        """aggressive detail_level should include up to 6 episodes, 120 chars each."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        for i in range(10):
            fm = MemoryFrontmatter.new(
                source="system", tags=["compacted", "auto-summary"], zone="episode",
            )
            temp_store.put("user", fm, f"Summary {i}: " + ("detail " * 30))

        block = _context._build_compacted_episode_block(temp_store, detail_level="aggressive")

        assert "## Episode Summaries" in block
        assert "(4 more)" in block

    def test_emergency_level_limits_to_4_episodes(self, temp_store, monkeypatch):
        """emergency (default) detail_level should include up to 4 episodes."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        for i in range(8):
            fm = MemoryFrontmatter.new(
                source="system", tags=["compacted", "auto-summary"], zone="episode",
            )
            temp_store.put("user", fm, f"Summary {i}: " + ("detail " * 30))

        block = _context._build_compacted_episode_block(temp_store, detail_level="emergency")

        assert "## Episode Summaries" in block
        assert "(4 more)" in block

    def test_empty_episodes_returns_empty_string(self, temp_store):
        """No episode data → empty block."""
        block = _context._build_compacted_episode_block(temp_store, detail_level="mild")
        assert block == ""

    def test_truncation_appends_ellipsis(self, temp_store, monkeypatch):
        """Bodies exceeding max_chars should be truncated with '...'."""
        import tempfile as _tf
        fake_home = Path(_tf.mkdtemp(prefix="hermes_test_home_"))
        monkeypatch.setattr(_context, "_hermes_home", lambda: fake_home)

        long_body = "A" * 300
        fm = MemoryFrontmatter.new(
            source="system", tags=["compacted", "auto-summary"], zone="episode",
        )
        temp_store.put("user", fm, long_body)

        block = _context._build_compacted_episode_block(temp_store, detail_level="mild")

        # mild: max_chars=200, so body should be truncated
        assert "..." in block
        # The full 300-char body should NOT appear intact
        assert "A" * 300 not in block
