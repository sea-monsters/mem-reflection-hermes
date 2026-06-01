# Data Safety Patterns

## Write-Then-Delete Swap

Mutations write the new file first, then delete the old file. If the write fails (disk full, permission error), the original file is preserved.

## Atomic In-Place Writes (v0.6.1)

When the new file path equals the old path (same-day updates, rank-only changes), the write goes to a `.tmp` file first, then `os.replace()` atomically swaps it — preventing file corruption on partial writes.

## Cache Consistency

`MemoryStore.update()` and `reorder()` call `_invalidate_cache()` before `_update_cache_for_put()` to prevent duplicate entries, and explicitly set `_index_dirty = True` + `_cached_index = ""` for same-path updates.

## Known Issues

### Duplicate Memory Scanning (Fixed in v0.6.0)

When `cwd` is `~`, `_project_memories_dir()` resolved to the same path as `_user_memories_dir()`, causing every memory to appear twice. **Fixed** with path resolution comparison.

### ONNX Fallback Cache Guard (Known, P3)

When ONNX is unavailable and sentence-transformers fallback is used, `_onnx_tokenizer` is set to `None`, causing every call to re-enter the critical section and re-load the model (~5s each). Impact: only when embeddings are enabled and ONNX model is unavailable.
