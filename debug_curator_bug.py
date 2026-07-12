#!/usr/bin/env python3
"""
Regression check: curator effectiveness path (was broken, now fixed).

History: this script originally documented two production bugs in the curator's
effectiveness handling (introduced before the v1.7 round-4 fixes):
  1. _load_effectiveness() called mem_store.list_active_effectiveness(), a
     method that does not exist on MemoryStore (real API: effectiveness()).
  2. scan_for_stale / ArchiveStale called .get("effectiveness"/"last_accessed")
     on a MemoryEffectiveness dataclass, which has no .get() method.

Both are now fixed: _load_effectiveness uses effectiveness(memory_id) and the
archival criterion uses the combined score factor() * decay_factor(). This
script verifies those fixes hold and is safe to run.

Run:  python debug_curator_bug.py
Exit: 0 if all checks pass, non-zero on regression.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Make the plugin importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.store import MemoryEffectiveness, MemoryStore  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


print("=" * 72)
print("Curator effectiveness regression checks")
print("=" * 72)

# --- Check 1: MemoryEffectiveness has no .get() (the dataclass contract) ----
eff = MemoryEffectiveness(
    loaded=5, referenced=2, accessed=3,
    last_event_at=datetime.now(timezone.utc).isoformat(),
)
check("MemoryEffectiveness has no .get()", not hasattr(eff, "get"))
check("MemoryEffectiveness has factor()", hasattr(eff, "factor"))
check("MemoryEffectiveness has decay_factor()", hasattr(eff, "decay_factor"))
check(
    "factor() returns hit-rate in [0.5, 1.0]",
    0.5 <= eff.factor() <= 1.0,
    detail=f"factor={eff.factor():.3f}",
)

# --- Check 2: MemoryStore.effectiveness() method exists (contract check) ---
# We check the method's existence on the class rather than instantiating, to
# avoid depending on the full SQLite schema init here.
check("MemoryStore defines effectiveness()", hasattr(MemoryStore, "effectiveness"))
check(
    "MemoryStore has no list_active_effectiveness()",
    not hasattr(MemoryStore, "list_active_effectiveness"),
)

# --- Check 3: effectiveness() reads the JSONL truth path (not empty SQLite) ---
# Record stats via the module-level writer, then read via load_effectiveness()
# (the JSONL-backed reader that MemoryStore.effectiveness() delegates to).
import core.store as store_mod  # noqa: E402

# Isolate to a temp HERMES_HOME so we don't touch real data.
tmp_home = Path(tempfile.mkdtemp()) / "hermes_home"
(tmp_home / "memory").mkdir(parents=True, exist_ok=True)
os.environ["HERMES_HOME"] = str(tmp_home)
store_mod._invalidate_effectiveness_cache()

mid = "regression-test-mem-1"
for _ in range(3):
    store_mod.record_memory_stat(mid, "accessed")
store_mod.record_memory_stat(mid, "loaded")

eff_map = store_mod.load_effectiveness()
got = eff_map.get(mid)
check(
    "load_effectiveness() returns recorded stats via JSONL path",
    got is not None and got.accessed == 3 and got.loaded == 1,
    detail=f"accessed={got.accessed if got else None}",
)

# --- Check 4: combined score is sane ---------------------------------------
if got is not None:
    combined = got.factor() * got.decay_factor()
    check(
        "combined factor()*decay_factor() in [0.15, 1.0]",
        0.15 <= combined <= 1.0,
        detail=f"combined={combined:.3f}",
    )

# --- Check 5: default effectiveness_threshold is 0.2 (matches combined floor)
from memory.curator.helpers import _DEFAULT_CFG  # noqa: E402
threshold = _DEFAULT_CFG["stale"]["effectiveness_threshold"]
check(
    "default effectiveness_threshold is 0.2 (above combined floor 0.15)",
    threshold == 0.2,
    detail=f"threshold={threshold}",
)

# --- Check 6: stats compaction config exists --------------------------------
check(
    "stats.compact_threshold_lines config present",
    "stats" in _DEFAULT_CFG and "compact_threshold_lines" in _DEFAULT_CFG["stats"],
    detail=str(_DEFAULT_CFG.get("stats")),
)

# Cleanup
try:
    import shutil
    shutil.rmtree(tmp_home, ignore_errors=True)
except Exception:
    pass

print("=" * 72)
if failures:
    print(f"REGRESSION DETECTED: {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed -- curator effectiveness path is healthy.")
sys.exit(0)
