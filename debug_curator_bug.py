#!/usr/bin/env python3
"""
Debug script for curator prune bug.

Scenario: User creates mems, after a few days old mems disappear.
Hypothesis: scan_for_stale() or merge_similar() incorrectly archives active entries.

Key areas of investigation:
1. Type mismatch: _load_effectiveness() calls eff.get() on MemoryEffectiveness dataclass
2. Missing method: list_active_effectiveness() not on real MemoryStore
3. Aggressive similarity merge threshold
"""

import sys
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---------------------------------------------------------------------------
# Import the curator module
# ---------------------------------------------------------------------------
from memory.curator import (
    _DEFAULT_CFG,
    _curator_config,
    scan_for_stale,
    archive_expired,
    _load_effectiveness,
    _run_curator,
    scan_for_similar,
    merge_similar,
)

from core.store import MemoryEffectiveness

# ---------------------------------------------------------------------------
# Problem 1: _load_effectiveness uses .get() on non-dict objects
# ---------------------------------------------------------------------------
print("=" * 72)
print("PROBLEM 1: _load_effectiveness type mismatch")
print("=" * 72)

# Simulate what happens when _load_effectiveness tries to access 
# a MemoryEffectiveness dataclass as if it were a dict
eff = MemoryEffectiveness(loaded=5, referenced=2, accessed=3, 
                           last_event_at=datetime.now(timezone.utc).isoformat())

print(f"MemoryEffectiveness object: {eff}")
print(f"type: {type(eff)}")
print(f"Has .get() method: {hasattr(eff, 'get')}")
print()

try:
    last_access = eff.get("last_accessed", 0)
    print(f"eff.get('last_accessed', 0) = {last_access}")
except AttributeError as e:
    print(f"❌ BUG: eff.get('last_accessed', 0) raises: {e}")
    print("   MemoryEffectiveness is a dataclass, not a dict.")
    print("   Fields are: loaded, referenced, accessed, last_event_at")
    print("   No 'last_accessed' field, no 'get()' method!")
print()

try:
    score = eff.get("effectiveness", 0.5)
    print(f"eff.get('effectiveness', 0.5) = {score}")
except AttributeError as e:
    print(f"❌ BUG: eff.get('effectiveness', 0.5) raises: {e}")
    print("   MemoryEffectiveness dataclass lacks .get() method entirely!")
print()

# Show the correct way to access MemoryEffectiveness fields
print("✓ Correct access: eff.accessed =", eff.accessed)
print(f"✓ Correct access: eff.last_event_at = {eff.last_event_at}")
print(f"✓ factor() method: {eff.factor()}")
print(f"✓ decay_factor(): {eff.decay_factor()}")

# ---------------------------------------------------------------------------
# Problem 2: list_active_effectiveness() doesn't exist on MemoryStore
# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("PROBLEM 2: list_active_effectiveness() missing from MemoryStore")
print("=" * 72)

from core.store import MemoryStore

try:
    ms = MemoryStore(user_root=Path(tempfile.mkdtemp()))
    ms.list_active_effectiveness()
    print("list_active_effectiveness() exists on MemoryStore")
except AttributeError as e:
    print(f"❌ BUG: MemoryStore has no 'list_active_effectiveness()' method: {e}")
    print("   Real MemoryStore has 'effectiveness()' method, not 'list_active_effectiveness()'")
    print("   So _load_effectiveness() ALWAYS returns None in production!")
    print("   This makes criteria 2 and 3 in scan_for_stale() dead code!")
print()

# ---------------------------------------------------------------------------
# Problem 3: scan_for_stale's elif logic — criterion 3 is dead code
# ---------------------------------------------------------------------------
print("=" * 72)
print("PROBLEM 3: scan_for_stale() — Criterion 3 logic analysis")
print("=" * 72)

# Even if _load_effectiveness worked, criterion 3 has an elif relationship
# with criterion 2 that limits its reach
print()
print("Code path analysis for scan_for_stale() Criterion 3:")
print()
print("  if not is_stale:")
print("      eff = _load_effectiveness(...)")
print("      last_access = eff.get('last_accessed', 0) if eff else 0")
print()
print("      if last_access > 0 and (now - last_access) > stale_days*86400:")
print("          is_stale = True       # Criterion 2")
print()
print("      elif eff:                  # Only enters if last_access == 0")
print("          score = eff.get('effectiveness', 0.5)")
print("          if score < eff_threshold:")
print("              is_stale = True   # Criterion 3")
print()
print("  Consequence: Criterion 3 ONLY fires when last_access == 0")
print("  AND eff is truthy. If last_access > 0 but not stale,")
print("  criterion 3 is SKIPPED due to elif.")
print()

# ---------------------------------------------------------------------------
# Problem 4: Other phases that could incorrectly archive active memories
# ---------------------------------------------------------------------------
print("=" * 72)
print("PROBLEM 4: Similarity Merge — potential false positives")
print("=" * 72)

from tests.test_memory_curator import MockStore, MockMemory

# Simulate: user has many memories that naturally share tokens
store = MockStore()
store.memories["user_pref"] = MockMemory(
    "user_pref", 
    "The user prefers Python for backend development and data analysis tasks",
    zone="general",
)
store.memories["work_tool"] = MockMemory(
    "work_tool",
    "The user uses VS Code for development work with Python backend tools",
    zone="work",
)
store.memories["old_mem"] = MockMemory(
    "old_mem",
    "The user mentioned using Python for some development tasks last week",
    zone="general",
)
store.memories["project_info"] = MockMemory(
    "project_info",
    "Current project uses Python with FastAPI for the backend API development",
    zone="general",
)

similar = scan_for_similar(store)
print(f"Similar pairs found (threshold={_DEFAULT_CFG['similarity']['bm25_threshold']}):")
for a, b, score in similar:
    print(f"  {a} <-> {b} : score={score}")
    print(f"    body a: '{store.memories[a].body[:60]}...'")
    print(f"    body b: '{store.memories[b].body[:60]}...'")
    
print()

# Now test merge_similar
store2 = MockStore()
store2.memories["pref1"] = MockMemory(
    "pref1",
    "User prefers Python for backend development and data analysis",
    tags=["python"],
)
store2.memories["pref2"] = MockMemory(
    "pref2",
    "User uses Python for development and data work",
    tags=["python", "dev"],
)
print(f"Before merge: {len(store2.memories)} active memories")
merged = merge_similar(store2)
print(f"Merged: {merged}")
print(f"After merge: {len(store2.memories)} active memories")
print(f"Deleted: {store2.deleted}")
if merged > 0:
    print("⚠️  Potential issue: merge_similar archived one of the memories!")
print()

# ---------------------------------------------------------------------------
# Summary of all bugs found
# ---------------------------------------------------------------------------
print("=" * 72)
print("SUMMARY OF BUGS FOUND")
print("=" * 72)
print()
print("1. TYPE MISMATCH in _load_effectiveness() → scan_for_stale()")
print("   File: memory/curator.py, lines ~395-400")
print("   _load_effectiveness returns a MemoryEffectiveness dataclass")
print("   but scan_for_stale calls .get() on it like a dict.")
print("   This crashes silently (except caught) → always returns None.")
print()
print("2. MISSING METHOD on MemoryStore")
print("   File: memory/curator.py, line ~413")
print("   Calls mem_store.list_active_effectiveness()")
print("   But MemoryStore only has .effectiveness() method.")
print("   This silently fails → _load_effectiveness always returns None.")
print()
print("3. DEAD CODE: Criterion 3 in scan_for_stale")
print("   The elif relationship means criterion 3 only fires when")
print("   last_access == 0. This is probably not the intended logic.")
print()
print("4. AGGRESSIVE SIMILARITY MERGE (if this is 'Phase 4 step')")
print("   bm25_threshold=0.6 + merge_threshold=0.7 can cause")
print("   false positive merge/archive of related but distinct memories.")
print("   Reducing merge_threshold to 0.85 would be more conservative.")
print()
print("5. The user-suggested fix 'Phase 4 step is too aggressive, reduce it'")
print("   likely refers to the similarity thresholds (Phase 3 in code)")
print("   or stale_days (Phase 1 in code). Both can cause active mems to")
print("   be archived when thresholds are too permissive.")
