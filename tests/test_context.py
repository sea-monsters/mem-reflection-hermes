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

_spec_store = importlib.util.spec_from_file_location("_store", str(_REPO / "store.py"))
_store = importlib.util.module_from_spec(_spec_store)
sys.modules["_store"] = _store
_spec_store.loader.exec_module(_store)
MemoryStore = _store.MemoryStore
MemoryFrontmatter = _store.MemoryFrontmatter
LoadedMemory = _store.LoadedMemory
SkillFrontmatter = _store.SkillFrontmatter
LoadedSkill = _store.LoadedSkill

_spec_search = importlib.util.spec_from_file_location("_search", str(_REPO / "search.py"))
_search = importlib.util.module_from_spec(_spec_search)
sys.modules["_search"] = _search
_spec_search.loader.exec_module(_search)
SearchIndex = _search.SearchIndex

_spec_context = importlib.util.spec_from_file_location("_context", str(_REPO / "context.py"))
_context = importlib.util.module_from_spec(_spec_context)
sys.modules["_context"] = _context
_spec_context.loader.exec_module(_context)
build_context = _context.build_context


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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_format_memory_truncation(self, temp_store):
        """Long memory bodies are truncated to fit max_tokens."""
        store = temp_store
        fm = MemoryFrontmatter.new(source="test")
        store.put("user", fm, "a" * 1000)
        mem = store.get(fm.id)
        formatted = _context._format_memory(mem, max_tokens=10)
        # Should be shorter than full body
        assert len(formatted) < len(mem.body)
        assert "[general]" in formatted

    def test_format_skill_basic(self, temp_store, temp_skills_dir):
        """Skill formatting includes name and description."""
        skill_path = temp_skills_dir / "skill.md"
        skill_path.write_text(
            "---\nname: MySkill\ndescription: Does useful things\n---\n"
        )
        skill = _store._read_skill_file(skill_path, "user")
        formatted = _context._format_skill(skill)
        assert "MySkill" in formatted
        assert "Does useful things" in formatted


# =====================================================================
# v1.1: Built-in memory block & compacted episode block
# =====================================================================


class TestV11ContextBlocks:
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

