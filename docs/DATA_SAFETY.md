# Data Safety Patterns

## Write-Then-Delete Swap

Mutations write the new file first, then delete the old file. If the write fails (disk full, permission error), the original file is preserved.

## Atomic In-Place Writes (v0.6.1)

When the new file path equals the old path (same-day updates, rank-only changes), the write goes to a `.tmp` file first, then `os.replace()` atomically swaps it — preventing file corruption on partial writes.

## Thread Safety (v1.0-beta3)

All public mutation methods on `MemoryStore` are guarded with `RLock` to prevent concurrent read/write races. Session-level state (`_session_messages`, `_turns_since_reflect`) uses `threading.Lock`. Graph adjacency rebuild and embedding cache operations are fully synchronized.

## Cache Consistency

`MemoryStore.update()` populates `_id_to_mem` with the new memory before calling `_invalidate_cache()`, ensuring the ID→memory mapping stays current even when the full cache is invalidated. `reorder()` uses the same `_write_memory` + `os.replace` pattern as other write paths.

## Error Logging

All silent failure paths (async write flush, stat recording, effectiveness loading, embedding fallback, graph operations) log at `logger.warning` level in production. Debug-level logging is reserved for non-critical cleanup operations (e.g., WAL checkpoint during close).

## Known Issues

### ONNX Fallback Cache Guard (Known, P3)

When ONNX is unavailable and sentence-transformers fallback is used, `_onnx_tokenizer` is set to `None`, causing every call to re-enter the critical section and re-load the model (~5s each). Impact: only when embeddings are enabled and ONNX model is unavailable.
