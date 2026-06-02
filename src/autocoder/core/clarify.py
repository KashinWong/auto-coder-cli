from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Prediction:
    modules: list
    risks: list


# 需求自描述时，留空的澄清回答不应再追问。这些维度若有实质内容即视为已覆盖。
_KEY_DIMENSIONS = ["scope", "acceptance"]


class ClarifyOrchestrator:
    def __init__(self, predict_fn: Callable[[str, str], Prediction]):
        self._predict_fn = predict_fn

    def predict(self, description: str, project_path: str) -> Prediction:
        return self._predict_fn(description, project_path)

    def ready_to_charter(self, form: Optional[dict], round_no: int,
                         trivial: bool = False) -> bool:
        if round_no >= 3:
            return True
        if trivial:
            return True
        if not form:
            return False
        # 关键维度都有实质内容即可立项
        return all(form.get(k, "").strip() for k in _KEY_DIMENSIONS)

    def synthesize(self, description: str, form: dict) -> str:
        lines = [f"## 需求\n{description}", ""]
        labels = {
            "scope": "范围边界", "modules": "涉及模块", "acceptance": "验收标准",
            "constraints": "优先级/约束", "risk_reply": "风险回应",
        }
        for key, label in labels.items():
            val = (form or {}).get(key, "").strip()
            if val:
                lines.append(f"- **{label}**: {val}")
        return "\n".join(lines)
