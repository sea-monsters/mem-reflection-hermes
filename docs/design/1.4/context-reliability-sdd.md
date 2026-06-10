# v1.4 SDD: Context Reliability

**Version**: v1.4  
**Date**: 2026-06-09  
**Status**: Completed  
**Scope**: Phase 1 implementation for structured context injection, timeout protection, and backward-compatible host fallback

## 1. Purpose

This SDD defines the first implementation slice of v1.4 for `mem-reflection-hermes`.

The immediate goal is to improve context injection reliability without changing the existing storage model or breaking the current host contract.

## 2. Problem

The current implementation in `memory/context.py` and `runtime/hooks.py` has three practical issues:

1. Context is assembled as a single string, so stable and dynamic context cannot be split for prompt-cache-friendly injection.
2. `pre_llm_call` has token truncation, but no deadline or fallback path when recall is slow.
3. The host-facing API is string-only, which makes it hard to evolve context assembly while preserving compatibility.

## 3. Design Goals

- Add a structured context bundle abstraction.
- Preserve `build_context()` as a backward-compatible string API.
- Keep `pre_llm_call()` compatible with the current host return contract.
- Introduce timeout-based fail-open behavior for context assembly.
- Add debug metadata so later v1.4 phases can build on it.

## 4. Non-Goals

- No search-ranking changes in this phase.
- No entity indexing in this phase.
- No checkpoint recovery in this phase.
- No host protocol expansion that requires immediate external changes.

## 5. Proposed Design

### 5.1 ContextBundle

Add a lightweight structured result in `memory/context.py`:

```python
@dataclass
class ContextBundle:
    prepend_context: str = ""
    append_system_context: str = ""
    debug: Dict[str, Any] = field(default_factory=dict)
```

Meaning:

- `prepend_context`: dynamic recall content, mainly relevant memories.
- `append_system_context`: stable context, mainly pinned memories, active skills, and optional static guidance.
- `debug`: token usage, included sections, compression notes.

### 5.2 Assembly Rules

Initial split:

- `append_system_context`
  - pinned memories
  - always-active skills
- `prepend_context`
  - relevant memories
  - triggered skills
  - compacted episode summaries

This split is intentionally conservative. It keeps existing semantics mostly intact while establishing a clear stable/dynamic boundary.

### 5.3 Compatibility Layer

`build_context()` remains public and returns a single string:

```python
bundle = build_context_bundle(...)
return join_nonempty(bundle.append_system_context, bundle.prepend_context)
```

This allows existing tests and callers to keep working unchanged.

### 5.4 Timeout and Fallback

`runtime/hooks.py::_pre_llm_call()` wraps context assembly in a bounded execution window.

New config key:

```yaml
memory:
  recall_timeout_ms: 1500
```

Behavior:

- Success path: inject assembled context as before.
- Timeout/failure path: assemble stable-only fallback if possible.
- If fallback also fails: skip injection without failing the hook.

### 5.5 Host Contract

Current host return shape remains:

```python
{"context": "..."}
```

The structured bundle exists internally first. Host-level split injection can be added in a later compatible phase.

## 6. Files Affected

- `memory/context.py`
- `runtime/hooks.py`
- `tests/test_context.py`
- `tests/test_reflection.py` if hook coverage needs extension
- `docs/dev/1.4/DEVELOPMENT_PROGRESS.md`

## 7. Acceptance Criteria

- `build_context()` output remains string-compatible for existing callers.
- `build_context_bundle()` returns stable/dynamic sections plus debug metadata.
- `pre_llm_call()` does not block indefinitely on context assembly.
- Timeout path returns stable fallback or no context, but never raises to host.
- Existing context tests continue to pass after adaptation.

## 8. Progress Notes

- 2026-06-09: SDD created.
- 2026-06-09: Phase 1 implementation started with ContextBundle + hook timeout scope.
- 2026-06-09: `ContextBundle` implemented with stable/dynamic split and debug metadata.
- 2026-06-09: `build_context()` preserved as compatibility wrapper.
- 2026-06-09: `pre_llm_call()` updated to use timeout-protected context assembly with stable-only fallback.
- 2026-06-09: Targeted verification passed on context tests, reflection tests, and host-contract smoke test.
- 2026-06-09: Scope completed and recorded in `docs/dev/1.4/DEVELOPMENT_PROGRESS.md`.
