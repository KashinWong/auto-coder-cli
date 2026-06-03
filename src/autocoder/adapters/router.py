from abc import ABC, abstractmethod

from autocoder.models import Decision


class EventRouter(ABC):
    @abstractmethod
    def await_decision(self, record_id: str, stage: str,
                       questions: list = None) -> Decision: ...


class CliRouter(EventRouter):
    """终端同步交互。dispatch/execute 在同一进程内驱动，无需网关/回调。"""

    def await_decision(self, record_id: str, stage: str,
                       questions: list = None) -> Decision:
        if stage == "clarify":
            # 按 AI 本轮动态问题逐个询问；form 以 question.key 为键，
            # 与飞书卡片 form_value 的键保持一致，供下游 _merge_answers 配回。
            form = {}
            for q in questions or []:
                prompt = q.ask
                if q.options:
                    prompt += "（候选：" + " / ".join(q.options) + "）"
                form[q.key] = input(f"{prompt}: ").strip()
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
