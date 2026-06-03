import json
from dataclasses import dataclass, field
from inspect import signature
from typing import Callable, Optional


@dataclass
class Question:
    """AI 判定本轮需澄清的一个问题。type: text | single_select | multi_select。"""
    key: str
    ask: str
    type: str = "text"
    options: list = field(default_factory=list)


@dataclass
class Prediction:
    modules: list
    risks: list
    scope_hint: str = ""        # AI 预判的范围边界（做什么/不做什么）
    acceptance_hint: str = ""   # AI 预判的验收标准建议
    ready: bool = False         # AI 判定信息是否已足够立项
    ready_reason: str = ""      # AI 对 ready 与否的一句话说明
    questions: list = field(default_factory=list)  # list[Question]，本轮该问什么


# 引擎可能抽风（连续判 not ready 却问不出新东西），用硬上限兜底防死循环。
_MAX_ROUNDS = 5


class ClarifyOrchestrator:
    def __init__(self, predict_fn: Callable):
        self._predict_fn = predict_fn
        # predict_fn 兼容两种签名：(desc, path) 与 (desc, path, prior_qa)。
        # 老的测试桩/简单实现只取两参，新的增量预判需要历轮问答。
        try:
            self._predict_arity = len(signature(predict_fn).parameters)
        except (TypeError, ValueError):
            self._predict_arity = 2

    def predict(self, description: str, project_path: str,
                prior_qa: Optional[list] = None) -> Prediction:
        if self._predict_arity >= 3:
            return self._predict_fn(description, project_path, prior_qa)
        return self._predict_fn(description, project_path)

    def ready_to_charter(self, pred: Optional[Prediction], round_no: int,
                         trivial: bool = False) -> bool:
        """是否可立项。轮次由 AI 决断（pred.ready），仅以 _MAX_ROUNDS 兜底。"""
        if round_no >= _MAX_ROUNDS:
            return True
        if trivial:
            return True
        if pred is None:
            return False
        # AI 判定够了，或它已问不出任何问题（无 pending）→ 立项。
        return bool(pred.ready) or not pred.questions

    def synthesize(self, description: str, qa: list) -> str:
        """把累积问答拼成需求陈述。qa: list[{ask, answer}]。"""
        lines = [f"## 需求\n{description}", ""]
        for item in qa or []:
            ask = (item.get("ask") or "").strip()
            answer = item.get("answer")
            if isinstance(answer, list):
                answer = "、".join(str(v) for v in answer)
            answer = (answer or "").strip()
            if ask and answer:
                lines.append(f"- **{ask}**: {answer}")
        return "\n".join(lines)

    # ---- progress 编解码（无状态 advance 的累积状态载体）------------------
    @staticmethod
    def encode_progress(round_no: int, qa: list, pending: list) -> str:
        """把澄清进度编码进 progress 字段。pending: list[Question 的 dict]。"""
        return json.dumps({"round": round_no, "qa": qa, "pending": pending},
                          ensure_ascii=False)

    @staticmethod
    def decode_progress(progress: Optional[str]) -> dict:
        """解码 progress。向后兼容旧格式 'round:N' 与空值。"""
        if not progress:
            return {"round": 1, "qa": [], "pending": []}
        if progress.startswith("round:"):
            try:
                return {"round": int(progress.split(":")[1]), "qa": [], "pending": []}
            except (ValueError, IndexError):
                return {"round": 1, "qa": [], "pending": []}
        try:
            data = json.loads(progress)
        except (ValueError, TypeError):
            return {"round": 1, "qa": [], "pending": []}
        if not isinstance(data, dict):
            return {"round": 1, "qa": [], "pending": []}
        data.setdefault("round", 1)
        data.setdefault("qa", [])
        data.setdefault("pending", [])
        return data
