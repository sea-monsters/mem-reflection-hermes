"""SkillStore and skill file I/O for mem-reflection-hermes.

Separated from core/store.py so the main store module can focus on
memory storage while SkillStore evolves independently.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Set

from .models import LoadedSkill, SkillFrontmatter, _load_frontmatter_file

logger = logging.getLogger(__name__)


def _read_skill_file(path: Path, scope: str) -> Optional[LoadedSkill]:
    """Read a SKILL.md file with YAML frontmatter."""
    try:
        metadata, body = _load_frontmatter_file(path)
        fm = SkillFrontmatter(
            name=metadata.get("name", path.parent.name),
            description=metadata.get("description", ""),
            triggers=metadata.get("triggers", []),
            version=metadata.get("version"),
            license=metadata.get("license"),
            always_active=bool(metadata.get("always_active", False)),
        )
        return LoadedSkill(frontmatter=fm, body=body,
                           source_path=path, scope=scope)
    except Exception as e:
        logger.warning("Failed to read skill %s: %s", path, e)
        return None


class SkillStore:
    """File-based skill store with lazy caching (skills are static per session)."""

    def __init__(self, user_root: Path, project_root: Optional[Path] = None):
        self.user_root = user_root
        self.project_root = project_root
        self._cache: Optional[List[LoadedSkill]] = None
        self._disabled: Set[str] = set()

    def invalidate_cache(self) -> None:
        self._cache = None

    def disable_project_skill(self, name: str) -> None:
        self._disabled.add(name)
        self.invalidate_cache()

    def enable_project_skill(self, name: str) -> None:
        self._disabled.discard(name)
        self.invalidate_cache()

    def list(self) -> List[LoadedSkill]:
        if self._cache is not None:
            return self._cache
        user_skills = self._scan(self.user_root, "user")
        project_skills = self._scan(self.project_root, "project") if self.project_root else []
        project_skills = [s for s in project_skills
                          if s.frontmatter.name not in self._disabled]
        project_names = {s.frontmatter.name for s in project_skills}
        user_skills = [s for s in user_skills
                       if s.frontmatter.name not in project_names]
        out = user_skills + project_skills
        out.sort(key=lambda s: s.frontmatter.name)
        self._cache = out
        return out

    def get(self, name: str) -> Optional[LoadedSkill]:
        if self.project_root:
            p = self.project_root / name / "SKILL.md"
            if p.exists():
                return _read_skill_file(p, "project")
        p = self.user_root / name / "SKILL.md"
        if p.exists():
            return _read_skill_file(p, "user")
        return None

    @staticmethod
    def _scan(root: Optional[Path], scope: str) -> List[LoadedSkill]:
        out: List[LoadedSkill] = []
        if root is None or not root.exists():
            return out
        for d in root.iterdir():
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            s = _read_skill_file(skill_md, scope)
            if s:
                out.append(s)
        return out
