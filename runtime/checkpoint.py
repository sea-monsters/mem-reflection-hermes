"""Runtime checkpoint persistence and best-effort recovery helpers."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..core.store import plugin_data_dir as _plugin_data_dir

logger = logging.getLogger(__name__)

__all__ = [
    "checkpoint_path",
    "clear_pending_stage",
    "clear_session_state",
    "load_checkpoint",
    "mark_pending_stage",
    "mark_stage_completed",
    "recover_pending_work",
    "snapshot_session_state",
    "write_checkpoint",
]


def _default_checkpoint() -> Dict[str, Any]:
    return {
        "session_states": {},
        "pending_reflections": {},
        "pending_compactions": {},
        "pending_curator_runs": {},
        "last_completed": {},
    }


def checkpoint_path() -> Path:
    """Return the runtime checkpoint file path."""
    return _plugin_data_dir() / "runtime-checkpoint.json"


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return str(value)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="runtime-checkpoint-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            logger.debug("Failed to remove temporary checkpoint file %s", tmp_name, exc_info=True)


def write_checkpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist checkpoint state atomically."""
    path = checkpoint_path()
    normalized = _default_checkpoint()
    for key, value in (payload or {}).items():
        normalized[key] = _json_safe(value)
    _atomic_write_json(path, normalized)
    return normalized


def load_checkpoint() -> Dict[str, Any]:
    """Load checkpoint state, backing up corrupt files and failing open."""
    path = checkpoint_path()
    if not path.exists():
        return _default_checkpoint()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("checkpoint root must be an object")
        merged = _default_checkpoint()
        for key, default_value in merged.items():
            value = data.get(key, default_value)
            merged[key] = value if isinstance(value, dict) else default_value
        return merged
    except Exception as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        try:
            os.replace(path, backup)
            logger.warning("Checkpoint file was corrupt and was moved to %s: %s", backup, exc)
        except Exception:
            logger.warning("Checkpoint file was corrupt and could not be backed up: %s", exc, exc_info=True)
        return _default_checkpoint()


def _mutate_checkpoint(mutator: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    payload = load_checkpoint()
    mutator(payload)
    return write_checkpoint(payload)


def snapshot_session_state(session_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Persist lightweight session state for diagnostics/recovery."""
    safe_state = _json_safe(dict(state or {}))

    def _apply(payload: Dict[str, Any]) -> None:
        payload["session_states"][session_id] = safe_state

    return _mutate_checkpoint(_apply)


def clear_session_state(session_id: str) -> Dict[str, Any]:
    """Remove persisted state for a completed session."""
    def _apply(payload: Dict[str, Any]) -> None:
        payload["session_states"].pop(session_id, None)

    return _mutate_checkpoint(_apply)


def _pending_bucket_name(stage: str) -> str:
    if stage == "reflection":
        return "pending_reflections"
    if stage == "compaction":
        return "pending_compactions"
    if stage == "curator":
        return "pending_curator_runs"
    raise ValueError(f"Unsupported checkpoint stage: {stage}")


def mark_pending_stage(session_id: str, stage: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a session stage as pending."""
    bucket_name = _pending_bucket_name(stage)
    safe_payload = _json_safe(dict(payload or {}))
    safe_payload.setdefault("session_id", session_id)
    safe_payload.setdefault("stage", stage)
    safe_payload.setdefault("updated_at", datetime.now(timezone.utc).isoformat())

    def _apply(data: Dict[str, Any]) -> None:
        data[bucket_name][session_id] = safe_payload

    return _mutate_checkpoint(_apply)


def clear_pending_stage(session_id: str, stage: str) -> Dict[str, Any]:
    """Clear a pending session stage."""
    bucket_name = _pending_bucket_name(stage)

    def _apply(data: Dict[str, Any]) -> None:
        data[bucket_name].pop(session_id, None)

    return _mutate_checkpoint(_apply)


def mark_stage_completed(session_id: str, stage: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Clear pending stage and record the latest successful completion."""
    bucket_name = _pending_bucket_name(stage)
    completed_at = datetime.now(timezone.utc).isoformat()
    record = {
        "stage": stage,
        "completed_at": completed_at,
    }
    if meta:
        record.update(_json_safe(dict(meta)))

    def _apply(data: Dict[str, Any]) -> None:
        data[bucket_name].pop(session_id, None)
        data["last_completed"][stage] = record

    return _mutate_checkpoint(_apply)


def _sort_pending_by_recency(
    pending: Dict[str, Dict[str, Any]],
) -> List[tuple[str, Dict[str, Any]]]:
    """Sort pending entries by updated_at descending (most recent first)."""
    items = list(pending.items())
    items.sort(key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
    return items


def _apply_cap(
    pending: Dict[str, Dict[str, Any]],
    max_pending_sessions: int,
) -> List[tuple[str, Dict[str, Any]]]:
    """Select pending entries respecting the cap, preferring most recent."""
    sorted_items = _sort_pending_by_recency(pending)
    if max_pending_sessions > 0:
        sorted_items = sorted_items[:max_pending_sessions]
    return sorted_items


def recover_pending_work(
    *,
    reflection_runner: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    compaction_runner: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    curator_runner: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    diagnostic_logger: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_pending_sessions: int = 0,
) -> Dict[str, int]:
    """Best-effort recovery for pending session-end work.

    When *max_pending_sessions* > 0, only the most recent N sessions
    (by ``updated_at`` timestamp) are recovered per stage, preventing
    unbounded recovery storms. A value of 0 means no cap.
    """
    payload = load_checkpoint()
    recovered = {"reflection": 0, "compaction": 0, "curator": 0, "diagnostic": 0}

    for session_id, entry in _apply_cap(payload["pending_reflections"], max_pending_sessions):
        messages = entry.get("messages") or []
        if not messages:
            if diagnostic_logger is not None:
                diagnostic_logger({
                    "type": "checkpoint_recovery",
                    "stage": "reflection",
                    "session_id": session_id,
                    "status": "skipped",
                    "reason": "missing_transcript_snapshot",
                })
                recovered["diagnostic"] += 1
            payload["pending_reflections"].pop(session_id, None)
            continue
        if reflection_runner is None:
            continue
        try:
            reflection_runner(session_id, entry)
            payload["pending_reflections"].pop(session_id, None)
            payload["last_completed"]["reflection"] = {
                "stage": "reflection",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "recovered": True,
            }
            recovered["reflection"] += 1
        except Exception:
            logger.warning("Pending reflection recovery failed for session %s", session_id, exc_info=True)

    for session_id, entry in _apply_cap(payload["pending_compactions"], max_pending_sessions):
        if compaction_runner is None:
            continue
        try:
            compaction_runner(session_id, entry)
            payload["pending_compactions"].pop(session_id, None)
            payload["last_completed"]["compaction"] = {
                "stage": "compaction",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "recovered": True,
            }
            recovered["compaction"] += 1
        except Exception:
            logger.warning("Pending compaction recovery failed for session %s", session_id, exc_info=True)

    for session_id, entry in _apply_cap(payload["pending_curator_runs"], max_pending_sessions):
        if curator_runner is None:
            continue
        try:
            curator_runner(session_id, entry)
            payload["pending_curator_runs"].pop(session_id, None)
            payload["last_completed"]["curator"] = {
                "stage": "curator",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "recovered": True,
            }
            recovered["curator"] += 1
        except Exception:
            logger.warning("Pending curator recovery failed for session %s", session_id, exc_info=True)

    write_checkpoint(payload)
    return recovered
