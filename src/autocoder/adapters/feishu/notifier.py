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

    def send_clarify(self, task, modules, risks, round_no):
        self._send("clarify", TASK_TITLE=task.task_title or task.description,
                   ROUND=round_no, RECORD_ID=task.record_id,
                   MODULE_BLOCK="\n".join(f"- {m}" for m in modules),
                   RISK_BLOCK="\n".join(f"- {r}" for r in risks))

    def send_charter(self, task, summary):
        self._send("charter", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, CHARTER_SUMMARY=summary)

    def send_plan(self, task, plan_summary, task_count, branch):
        self._send("plan", TASK_TITLE=task.task_title or task.description,
                   RECORD_ID=task.record_id, PLAN_SUMMARY=plan_summary,
                   TASKS_COUNT=task_count, BRANCH_NAME=branch)

    def send_complete(self, task, branch, change_stats, duration, timeline):
        self._send("complete", TASK_TITLE=task.task_title or task.description,
                   PR_URL=branch, CHANGE_STATS=change_stats,
                   DURATION=duration, TIMELINE=timeline)

    def send_failure(self, task, stage, error, log_path, branch):
        self._send("failure", TASK_TITLE=task.task_title or task.description,
                   FAIL_STAGE=stage, ERROR_SUMMARY=error,
                   LOG_PATH=log_path, BRANCH_NAME=branch)
