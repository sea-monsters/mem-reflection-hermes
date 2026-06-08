"""test_core_data.py — Core data models, frontmatter, lineage, effectiveness.

Tests:
- Frontmatter serialization round-trip
- CJK content preservation
- MemoryEffectiveness factor/decay calculations
- Lineage chain operations (depth, root, cycle detection)
- Safe write atomicity

Run: pytest tests/test_core_data.py -v
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import pytest

from core.store import (
    MemoryFrontmatter,
    MemoryEffectiveness,
    LoadedMemory,
    parse_frontmatter,
    serialize_frontmatter,
    _lineage_depth,
    _lineage_root,
    _lineage_cycle_check,
    _classify_update_intent,
    _safe_write,
)


class TestFrontmatter:
    def test_roundtrip(self):
        fm = MemoryFrontmatter(
            id="test-123",
            created="2026-01-15T10:00:00+00:00",
            source="reflection",
            confidence="high",
            pinned=True,
            tags=["python", "backend"],
            supersedes=["old-456"],
            supersedes_reason="updated preference",
            zone="work",
            rank=5,
        )
        data = {
            "id": fm.id,
            "created": fm.created,
            "source": fm.source,
            "confidence": fm.confidence,
            "pinned": fm.pinned,
            "tags": fm.tags,
            "supersedes": fm.supersedes,
            "supersedes_reason": fm.supersedes_reason,
            "zone": fm.zone,
            "rank": fm.rank,
        }
        body = "Test body content"
        serialized = serialize_frontmatter(data, body)
        parsed_data, parsed_body = parse_frontmatter(serialized)
        assert parsed_data is not None
        assert parsed_data["id"] == fm.id
        assert parsed_data["source"] == fm.source
        assert parsed_data["confidence"] == fm.confidence
        assert parsed_data["tags"] == fm.tags
        assert parsed_data["zone"] == fm.zone
        assert parsed_data["rank"] == 5

    def test_roundtrip_preserves_created(self):
        fm = MemoryFrontmatter(
            id="test-ts",
            created=datetime.now(timezone.utc).isoformat(),
            source="test",
            confidence="medium",
            tags=["a"],
        )
        body = ""
        data = {
            "id": fm.id,
            "created": fm.created,
            "source": fm.source,
            "confidence": fm.confidence,
            "tags": fm.tags,
        }
        serialized = serialize_frontmatter(data, body)
        parsed_data, parsed_body = parse_frontmatter(serialized)
        assert parsed_data is not None
        assert parsed_data["created"].isoformat() == fm.created

    def test_cjk_body_in_memory(self):
        """CJK content in body survives frontmatter round-trip."""
        fm = MemoryFrontmatter(
            id="cjk-test",
            created=datetime.now(timezone.utc).isoformat(),
            source="test",
            confidence="medium",
        )
        body = "这是一个中文记忆内容，包含日本語テストと한국어"
        data = {
            "id": fm.id,
            "created": fm.created,
            "source": fm.source,
            "confidence": fm.confidence,
        }
        serialized = serialize_frontmatter(data, body)
        assert body in serialized


class TestEffectivenessDataclass:
    def test_factor_range(self):
        # loaded=0 -> factor=1.0
        eff = MemoryEffectiveness(loaded=0, referenced=0)
        assert eff.factor() == 1.0

        # loaded=10, referenced=0 -> factor=0.5
        eff = MemoryEffectiveness(loaded=10, referenced=0)
        assert eff.factor() == 0.5

        # loaded=10, referenced=10 -> factor=1.0
        eff = MemoryEffectiveness(loaded=10, referenced=10)
        assert eff.factor() == 1.0

        # loaded=10, referenced=5 -> factor=0.75
        eff = MemoryEffectiveness(loaded=10, referenced=5)
        assert eff.factor() == pytest.approx(0.75)

    def test_decay_floor(self):
        """decay_factor never goes below 0.3."""
        now = datetime.now(timezone.utc)
        # 365 days ago -> well past half-life
        last = (now - timedelta(days=365)).isoformat()
        eff = MemoryEffectiveness(loaded=1, referenced=1, last_event_at=last)
        decay = eff.decay_factor(now)
        assert decay >= 0.3, f"Decay should have floor of 0.3, got {decay}"

    def test_decay_no_event(self):
        """No last_event_at -> decay=1.0."""
        eff = MemoryEffectiveness(loaded=5, referenced=3)
        assert eff.decay_factor() == 1.0

    def test_combined_factor_decay(self):
        """factor * decay_factor produces expected combined value."""
        now = datetime.now(timezone.utc)
        last = (now - timedelta(days=30)).isoformat()
        eff = MemoryEffectiveness(loaded=10, referenced=5, last_event_at=last)
        combined = eff.factor() * eff.decay_factor(now)
        # factor = 0.75, decay ~ 0.5
        assert 0.3 < combined < 0.5


class _DictStore:
    """Minimal store-like object for lineage tests (has .get() method)."""

    def __init__(self, mems: Dict[str, LoadedMemory]):
        self._mems = mems

    def get(self, mem_id: str):
        return self._mems.get(mem_id)

    def list(self):
        return list(self._mems.values())


class TestLineage:
    def _make_store(self, chain_ids):
        """Create a store-like object with supersedes chain."""
        mems = {}
        for i, mid in enumerate(chain_ids):
            supersedes = [chain_ids[i + 1]] if i + 1 < len(chain_ids) else []
            fm = MemoryFrontmatter(
                id=mid,
                created=datetime.now(timezone.utc).isoformat(),
                source="test",
                confidence="medium",
                supersedes=supersedes,
            )
            mems[mid] = LoadedMemory(
                frontmatter=fm,
                body=f"body {mid}",
                source_path=Path(f"/tmp/{mid}.md"),
                scope="user",
            )
        return _DictStore(mems)

    def test_chain_depth(self):
        store = self._make_store(["a", "b", "c"])
        # a->b->c: depth from a is 2
        depth = _lineage_depth(store, "a")
        assert depth == 2

    def test_chain_depth_single(self):
        store = self._make_store(["a", "b"])
        depth = _lineage_depth(store, "a")
        assert depth == 1

    def test_root(self):
        store = self._make_store(["a", "b", "c"])
        root = _lineage_root(store, "a")
        assert root == "c"

    def test_cycle_detection(self):
        mems = {}
        for mid, sup in [("a", ["b"]), ("b", ["a"])]:
            fm = MemoryFrontmatter(
                id=mid, created=datetime.now(timezone.utc).isoformat(),
                source="test", confidence="medium", supersedes=sup,
            )
            m = LoadedMemory(frontmatter=fm, body=f"body {mid}",
                             source_path=Path(f"/tmp/{mid}.md"), scope="user")
            mems[mid] = m
        store = _DictStore(mems)
        result = _lineage_cycle_check(store, "a")
        assert result is not None and len(result) > 0

    def test_no_cycle(self):
        store = self._make_store(["a", "b", "c"])
        result = _lineage_cycle_check(store, "a")
        assert result is None


class TestSafeWrite:
    def test_atomic_write(self, temp_dir):
        """_safe_write creates file with correct content."""
        path = temp_dir / "test_atomic.md"
        content = "---\nid: test\n---\nHello world"
        _safe_write(path, content)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == content

    def test_concurrent_writes(self, temp_dir):
        """Two concurrent _safe_write calls don't corrupt."""
        path = temp_dir / "concurrent.md"
        errors = []

        def writer(content):
            try:
                for _ in range(20):
                    _safe_write(path, content)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer, args=("content_a",))
        t2 = threading.Thread(target=writer, args=("content_b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        # File should contain one or the other, never a mix
        final = path.read_text(encoding="utf-8")
        assert final in ("content_a", "content_b")


class TestClassifyIntent:
    def test_correction_detected(self):
        intent = _classify_update_intent("No, I actually prefer light mode", "existing memory")
        # Should detect some intent
        assert intent is not None

    def test_append_detected(self):
        intent = _classify_update_intent("Also remember that I use vim", "existing memory")
        # Should be some intent, not necessarily correction
        assert intent is not None
