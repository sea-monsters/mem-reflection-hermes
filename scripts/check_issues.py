#!/usr/bin/env python3
"""Quick audit script for mem-reflection-hermes P0/P1/P2 issues."""

import ast
import sys

def check_p0():
    print("=== P0 Issues ===")
    issues = []

    # 1. register not in tools.py __all__
    with open('../tools/handlers.py', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == '__all__':
                    names = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                    if 'register' not in names:
                        issues.append("P0-1: 'register' not in tools.py __all__")
                        print("  [FAIL] P0-1: 'register' not in tools.py __all__")
                    else:
                        print("  [OK] P0-1: 'register' in tools.py __all__")

    # 2. _bm25_search_scored duplicate with mismatched return types
    with open('../core.py', encoding='utf-8') as f:
        core_tree = ast.parse(f.read())
    with open('../__init__.py', encoding='utf-8') as f:
        init_tree = ast.parse(f.read())

    core_return = None
    init_return = None
    for n in ast.walk(core_tree):
        if isinstance(n, ast.FunctionDef) and n.name == '_bm25_search_scored':
            core_return = ast.unparse(n.returns) if n.returns else None
    for n in ast.walk(init_tree):
        if isinstance(n, ast.FunctionDef) and n.name == '_bm25_search_scored':
            init_return = ast.unparse(n.returns) if n.returns else None

    if core_return != init_return:
        issues.append(f"P0-2: _bm25_search_scored return type mismatch: core={core_return}, init={init_return}")
        print(f"  [FAIL] P0-2: _bm25_search_scored return type mismatch: core={core_return}, init={init_return}")
    else:
        print(f"  [OK] P0-2: _bm25_search_scored return types match")

    return issues


def check_p1():
    print("\n=== P1 Fixes ===")
    issues = []

    # 1. _safe_write in core.py
    with open('../core.py', encoding='utf-8') as f:
        content = f.read()
    if 'def _safe_write' not in content:
        issues.append("P1-1: _safe_write missing in core.py")
        print("  [FAIL] P1-1: _safe_write missing")
    elif 'os.fsync' not in content:
        issues.append("P1-1: _safe_write missing os.fsync")
        print("  [FAIL] P1-1: _safe_write missing os.fsync")
    else:
        print("  [OK] P1-1: _safe_write with fsync present")

    # 2. _cosine_sim dim check
    with open('../search/embed.py', encoding='utf-8') as f:
        content = f.read()
    if 'len(a) != len(b)' not in content:
        issues.append("P1-2: _cosine_sim missing dimension check")
        print("  [FAIL] P1-2: _cosine_sim missing dimension check")
    else:
        print("  [OK] P1-2: _cosine_sim has dimension check")

    # 3. _MAX_REFLECT_TRANSCRIPT_CHARS
    with open('../reflection/engine.py', encoding='utf-8') as f:
        content = f.read()
    if '_MAX_REFLECT_TRANSCRIPT_CHARS' not in content:
        issues.append("P1-3: _MAX_REFLECT_TRANSCRIPT_CHARS missing")
        print("  [FAIL] P1-3: transcript truncation missing")
    else:
        print("  [OK] P1-3: transcript truncation present")

    # 4. delete OSError protection
    with open('../__init__.py', encoding='utf-8') as f:
        content = f.read()
    if 'except OSError' not in content or 'path.unlink()' not in content:
        issues.append("P1-4: delete() missing OSError protection")
        print("  [FAIL] P1-4: delete() missing OSError protection")
    else:
        print("  [OK] P1-4: delete() has OSError protection")

    # 5. _async_write_memory alias
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
        ('../core.py', 'k1, b = 1.5, 0.75', 'P2-1: BM25 k1/b parameters'),
        ('../search/embed.py', '_INTENT_PROTOTYPE_EMBEDDINGS', 'P2-4: Intent prototype embeddings'),
        ('../graph/ahe_graph.py', 'max_neighbors', 'P2-20: max_neighbors limit'),
        ('../tools/handlers.py', 'default=str', 'P2-33: json.dumps default=str'),
    ]

    for filename, marker, desc in checks:
        try:
            with open(filename, encoding='utf-8') as f:
                content = f.read()
            if marker in content:
                print(f"  [OK] {desc}")
            else:
                issues.append(f"{desc} missing")
                print(f"  [FAIL] {desc} missing")
        except FileNotFoundError:
            issues.append(f"{filename} not found")
            print(f"  [FAIL] {filename} not found")

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
