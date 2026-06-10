from pathlib import Path

from autocoder.adapters.notifier import Notifier
from autocoder.adapters.feishu.client import FeishuClient, render_card

_TPL_DIR = Path(__file__).resolve().parents[4] / "templates" / "cards"


class FeishuCardNotifier(Notifier):
    def __init__(self, feishu_config: dict, client: FeishuClient = None):
        self.chat_id = feishu_config.get("notify_chat_id", "")
        self.client = client or FeishuClient()

    def _send(self, card_name: str, **tokens):
        card = render_card(str(_TPL_DIR / f"{card_name}.json"), **tokens)
        return self.client.send_card(self.chat_id, card)

    def send_clarify(self, task, pred, round_no):
        elements = self._build_clarify_elements(task, pred)
        self._send("clarify", TASK_TITLE=task.task_title or task.description,
                   ROUND=round_no, FORM_ELEMENTS=elements)

    def _build_clarify_elements(self, task, pred):
        """按 AI 本轮动态问题拼飞书 form 子元素。每个问题 → 一段说明 +
        一个输入控件（text→input，select→multi_select_static），控件 name
        用 question.key，与回调 form_value 的键对齐，供 _merge_answers 配回。"""
        els = []
        # 顶部上下文：需求 + AI 预判摘要（范围/验收/模块/风险），只读。
        ctx = [f"**需求**：{task.description}"]
        if pred.scope_hint:
            ctx.append(f"**🎯 范围预判**：{pred.scope_hint}")
        if pred.acceptance_hint:
            ctx.append(f"**✅ 验收预判**：{pred.acceptance_hint}")
        if pred.modules:
            ctx.append("**🧩 涉及模块**：" + "、".join(pred.modules))
        if pred.risks:
            ctx.append("**⚠️ 风险点**\n" + "\n".join(f"- {r}" for r in pred.risks))
        if pred.ready_reason:
            ctx.append(f"_🤖 {pred.ready_reason}_")
        els.append({"tag": "markdown", "content": "\n\n".join(ctx)})
        els.append({"tag": "hr"})

        for q in pred.questions:
            els.append({"tag": "markdown", "content": f"**❓ {q.ask}**"})
            if q.type in ("single_select", "multi_select") and q.options:
                tag = ("multi_select_static" if q.type == "multi_select"
                       else "select_static")
                els.append({
                    "tag": tag, "name": q.key,
                    "placeholder": {"tag": "plain_text", "content": "请选择…"},
                    "options": [
                        {"text": {"tag": "plain_text", "content": o}, "value": o}
                        for o in q.options
                    ],
                })
                # 选项之外的兜底：允许填写下拉未覆盖的答案，与选中值合并。
                els.append({
                    "tag": "input", "name": f"{q.key}__other",
                    "placeholder": {"tag": "plain_text",
                                    "content": "选项以外？在此补充（选填）"},
                })
            else:
                els.append({
                    "tag": "input", "name": q.key,
                    "input_type": "multiline_text",
                    "placeholder": {"tag": "plain_text", "content": "在此回答…"},
                })

        els.append({"tag": "hr"})
        # 整卡级补充：让用户写问题之外、想额外交代的澄清细节。
        els.append({"tag": "markdown", "content": "**📝 其他补充说明（选填）**"})
        els.append({
            "tag": "input", "name": "__supplement",
            "input_type": "multiline_text",
            "placeholder": {"tag": "plain_text",
                            "content": "上面没问到、但你想补充的细节…"},
        })
        els.append({"tag": "hr"})
        els.append({
            "tag": "button", "name": "clarify_submit_btn",
            "form_action_type": "submit",
            "text": {"tag": "plain_text", "content": "提交澄清"},
            "type": "primary",
            "behaviors": [{"type": "callback", "value": {
                "ac_action": "clarify_submit", "record_id": task.record_id,
                "stage": "clarify", "_skill": "auto-coder-agent",
            }}],
        })
        return els

    def send_charter(self, task, summary):
        self._send("charter", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, CHARTER_SUMMARY=summary)

    def send_plan(self, task, plan_summary, task_count, branch, spec_content=""):
        self._send("plan", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, PLAN_SUMMARY=plan_summary,
                   TASKS_COUNT=task_count, BRANCH_NAME=branch,
                   SPEC_CONTENT=spec_content or "（spec 文件暂未生成）")

    def send_complete(self, task, branch, change_stats, duration, timeline):
        self._send("complete", TASK_TITLE=task.task_title or task.description,
                   PR_URL=branch, CHANGE_STATS=change_stats,
                   DURATION=duration, TIMELINE=timeline)

    def send_failure(self, task, stage, error, log_path, branch):
        # 失败阶段决定重试动作：立项/规划失败重跑 plan，其余(编码/测试/构建/推送)重跑 execute。
        if stage in ("立项", "规划"):
            retry_action, retry_stage = "retry_plan", "plan"
        else:
            retry_action, retry_stage = "retry_execute", "execute"
        self._send("failure", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, FAIL_STAGE=stage, ERROR_SUMMARY=error,
                   LOG_PATH=log_path, BRANCH_NAME=branch,
                   RETRY_ACTION=retry_action, RETRY_STAGE=retry_stage)

    def send_queued(self, task, position):
        self._send("queued", TASK_TITLE=task.task_title or task.description,
                   QUEUE_POSITION=position)

    def send_zombie_alert(self, task, status, kind, minutes):
        from autocoder.adapters.notifier import ZOMBIE_RETRY
        retry_action, stage = ZOMBIE_RETRY.get(kind, ("retry_execute", "execute"))
        started = (getattr(task, f"{kind}_started_at", None)
                   if kind != "clarify" else None) or "未记录"
        self._send("zombie", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, STATUS=status, KIND=kind,
                   MINUTES=minutes, STARTED_AT=started,
                   RETRY_ACTION=retry_action, STAGE=stage)
