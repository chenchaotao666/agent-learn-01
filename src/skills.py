import hashlib
from pathlib import Path

import yaml
from pydantic import Field

from src.config import ROOT
from src.schemas import Schema


class SkillMetadata(Schema):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1)
    metadata: dict[str, str]


class Skills:
    def __init__(self, root: Path | None = None):
        self.root = root or ROOT / "agent_skills"

    def catalog(self):
        results = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            raw = path.read_text(encoding="utf-8")
            parts = raw.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise ValueError(f"Skill metadata invalid: {path.name}")
            meta = SkillMetadata.model_validate(yaml.safe_load(parts[1]))
            if meta.name != path.parent.name:
                raise ValueError("Skill 名称必须与目录名一致")
            results.append(meta.model_dump())
        return results

    def load(self, name):
        names = {m["name"] for m in self.catalog()}
        if name not in names:
            raise ValueError("Skill 不存在")
        path = self.root / name
        raw = (path / "SKILL.md").read_text(encoding="utf-8")
        resources = {}
        for file in sorted((path / "references").glob("*.md")):
            resources[file.name] = file.read_text(encoding="utf-8")
        import json
        content = raw + json.dumps(resources, sort_keys=True, ensure_ascii=False)
        return {"name": name, "version": next(m["metadata"]["version"] for m in self.catalog() if m["name"] == name),
                "hash": hashlib.sha256(content.encode()).hexdigest(), "instructions": raw.split("---", 2)[2].strip(),
                "resources": resources}

    def select(self, task):
        if "翻译" in task or "translate" in task.lower():
            return self.load("procurement-translation")
        if any(word in task for word in ("预算", "采购", "审批")):
            return self.load("budget-review")
        return None
