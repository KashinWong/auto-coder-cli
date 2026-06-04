from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class Config:
    adapters: dict
    workspace_dir: str
    concurrency_limit: int
    projects: dict
    engines: dict
    default_engine: str
    clarify_dimensions: list
    feishu: dict

    def engine_spec(self, name: str) -> dict:
        return self.engines[name]

    def match_project(self, text: str) -> Optional[str]:
        # 大小写不敏感：用户自然会写 Digital-Admin / Gomoku，而关键词常配小写，
        # 区分大小写会让英文项目名静默匹配失败 → project_path 空 → 澄清卡空白。
        low = text.lower()
        for key, proj in self.projects.items():
            for kw in proj.get("match_keywords", []):
                if kw.lower() in low:
                    return key
        return None


_REQUIRED = ["adapters", "workspace_dir", "concurrency", "projects", "engines"]


def load_config(path: str) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    for key in _REQUIRED:
        if key not in data:
            raise ValueError(f"config missing required key: {key}")
    engines = dict(data["engines"])
    default_engine = engines.pop("default", None)
    return Config(
        adapters=data["adapters"],
        workspace_dir=data["workspace_dir"],
        concurrency_limit=data["concurrency"]["limit"],
        projects=data["projects"],
        engines=engines,
        default_engine=default_engine,
        clarify_dimensions=data.get("clarify_dimensions", []),
        feishu=data.get("feishu", {}),
    )
