from abc import ABC, abstractmethod

from autocoder.models import Decision

_CLARIFY_FIELDS = ["scope", "modules", "acceptance", "constraints", "risk_reply"]
_CLARIFY_PROMPTS = {
    "scope": "🎯 范围边界（做什么/不做什么）",
    "modules": "🧩 涉及模块补充/纠正",
    "acceptance": "✅ 验收标准",
    "constraints": "⚙️ 优先级/约束",
    "risk_reply": "⚠️ 对风险点的回应",
}


class EventRouter(ABC):
    @abstractmethod
    def await_decision(self, record_id: str, stage: str) -> Decision: ...


class CliRouter(EventRouter):
    """终端同步交互。dispatch/execute 在同一进程内驱动，无需网关/回调。"""

    def await_decision(self, record_id: str, stage: str) -> Decision:
        if stage == "clarify":
            form = {}
            for field in _CLARIFY_FIELDS:
                form[field] = input(f"{_CLARIFY_PROMPTS[field]}: ").strip()
            return Decision(action="clarify_submit", record_id=record_id,
                            stage=stage, form=form)

        if stage == "charter":
            choice = input("选择 [1]立项 [2]改 [3]再聊 [4]拒: ").strip()
            action = {"1": "approve_charter", "2": "revise_charter",
                      "3": "rechat", "4": "reject_charter"}.get(choice, "approve_charter")
            note = ""
            if action == "revise_charter":
                note = input("修改说明: ").strip()
            return Decision(action=action, record_id=record_id, stage=stage,
                            input_text=note)

        if stage == "plan":
            choice = input("选择 [1]批准 [2]退·改方案 [3]退·重新理解: ").strip()
            action = {"1": "approve_plan", "2": "revise_plan",
                      "3": "reunderstand"}.get(choice, "approve_plan")
            note = ""
            if action == "revise_plan":
                note = input("改方案说明: ").strip()
            return Decision(action=action, record_id=record_id, stage=stage,
                            input_text=note)

        raise ValueError(f"unknown stage: {stage}")
