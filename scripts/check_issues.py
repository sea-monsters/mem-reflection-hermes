#!/usr/bin/env python3
"""Quick audit script for mem-reflection-hermes P0/P1/P2 issues."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_repo_file(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def parse_repo_file(relative: str):
    return ast.parse(read_repo_file(relative))

def check_p0():
    print("=== P0 Issues ===")
    issues = []

    # 1. register not in runtime_tools.py __all__
    content = read_repo_file("runtime_tools.py")
    if '"register"' not in content or "register_tools = register" not in content:
        issues.append("P0-1: 'register' not in runtime_tools.py public surface")
        print("  [FAIL] P0-1: 'register' not in runtime_tools.py public surface")
    else:
        print("  [OK] P0-1: 'register' in runtime_tools.py public surface")

    # 2. _bm25_search_scored is defined in the canonical search module
    search_tree = parse_repo_file("search.py")
    bm25_return = None
    for n in ast.walk(search_tree):
        if isinstance(n, ast.FunctionDef) and n.name == '_bm25_search_scored':
            bm25_return = ast.unparse(n.returns) if n.returns else None
    if bm25_return is None:
        issues.append("P0-2: _bm25_search_scored missing in search.py")
        print("  [FAIL] P0-2: _bm25_search_scored missing in search.py")
    else:
        print("  [OK] P0-2: _bm25_search_scored present in search.py")

    return issues


def check_p1():
    print("\n=== P1 Fixes ===")
    issues = []

    # 1. _safe_write in store.py
    content = read_repo_file("store.py")
    if 'def _safe_write' not in content:
        issues.append("P1-1: _safe_write missing in store.py")
        print("  [FAIL] P1-1: _safe_write missing")
    elif 'os.fsync' not in content:
        issues.append("P1-1: _safe_write missing os.fsync")
        print("  [FAIL] P1-1: _safe_write missing os.fsync")
    else:
        print("  [OK] P1-1: _safe_write with fsync present")

    # 2. _cosine_sim dim check
    content = read_repo_file("search.py")
    if 'len(a) != len(b)' not in content:
        issues.append("P1-2: _cosine_sim missing dimension check")
        print("  [FAIL] P1-2: _cosine_sim missing dimension check")
    else:
        print("  [OK] P1-2: _cosine_sim has dimension check")

    # 3. _MAX_REFLECT_TRANSCRIPT_CHARS
    content = read_repo_file("runtime_reflection.py")
    if '_MAX_REFLECT_TRANSCRIPT_CHARS' not in content:
        issues.append("P1-3: _MAX_REFLECT_TRANSCRIPT_CHARS missing")
        print("  [FAIL] P1-3: transcript truncation missing")
    else:
        print("  [OK] P1-3: transcript truncation present")

    # 4. delete OSError protection
    content = read_repo_file("store.py")
    if 'except OSError' not in content or 'path.unlink()' not in content:
        issues.append("P1-4: delete() missing OSError protection")
        print("  [FAIL] P1-4: delete() missing OSError protection")
    else:
        print("  [OK] P1-4: delete() has OSError protection")

    # 5. _async_write_memory alias
    content = read_repo_file("__init__.py")
    if '_async_write_memory' not in content:
        issues.append("P1-5: _async_write_memory alias missing")
        print("  [FAIL] P1-5: _async_write_memory alias missing")
    else:
        print("  [OK] P1-5: _async_write_memory alias present")

    return issues


def check_p2():
    print("\n=== P2 Issues (sample checks) ===")
    issues = []

    # Check a few key P2 issues
    checks = [
        ('store.py', 'k1, b = 1.5, 0.75', 'P2-1: BM25 k1/b parameters'),
        ('search.py', '_INTENT_PROTOTYPE_EMBEDDINGS', 'P2-4: Intent prototype embeddings'),
        ('graph.py', 'max_neighbors', 'P2-20: max_neighbors limit'),
        ('runtime_tools.py', 'default=str', 'P2-33: json.dumps default=str'),
    ]

    for filename, marker, desc in checks:
        content = read_repo_file(filename)
        if marker in content:
            print(f"  [OK] {desc}")
        else:
            issues.append(f"{desc} missing")
            print(f"  [FAIL] {desc} missing")

    return issues


if __name__ == '__main__':
    p0 = check_p0()
    p1 = check_p1()
    p2 = check_p2()

    total = len(p0) + len(p1) + len(p2)
    print(f"\n{'='*50}")
    print(f"Total issues found: {total}")
    print(f"  P0: {len(p0)} | P1: {len(p1)} | P2: {len(p2)}")

    if total > 0:
        print("\nIssues to fix:")
        for i in p0 + p1 + p2:
            print(f"  - {i}")
        sys.exit(1)
    else:
        print("\n[OK] All checks passed!")
        sys.exit(0)
