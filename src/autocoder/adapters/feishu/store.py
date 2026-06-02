import json
import subprocess

from autocoder.adapters.store import TaskStore
from autocoder.models import Task


class FeishuBaseStore(TaskStore):
    """用 lark-cli 读写飞书多维表格（Base）。需先 `lark-cli` 登录并有
    base:record 读写权限。字段 ID 从 config.feishu.field_ids 读。

    注意：lark-cli 在 cron/bot 上下文下不要加 --as user。
    """

    def __init__(self, feishu_config: dict):
        self.base_token = feishu_config["base_token"]
        self.table_id = feishu_config["table_id"]
        self.field_ids = feishu_config.get("field_ids", {})

    def _upsert(self, record_id: str, payload: dict):
        subprocess.run([
            "lark-cli", "base", "+record-upsert",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            "--record-id", record_id,
            "--json", json.dumps(payload, ensure_ascii=False),
        ], capture_output=True, text=True)

    def fetch_pending(self) -> list:
        # 见 README「飞书 adapter」：建临时视图→过滤 进展=待开始→排序→列记录→删视图
        raise NotImplementedError(
            "FeishuBaseStore.fetch_pending 需按你的表结构实现，见 README")

    def get(self, record_id: str) -> Task:
        raise NotImplementedError("见 README 飞书 adapter 说明")

    def update_status(self, record_id, status):
        self._upsert(record_id, {"进展": status})

    def update_summary(self, record_id, summary, progress):
        self._upsert(record_id, {"任务情况总结": summary, "最新进展记录": progress})

    def set_clarify_pointer(self, record_id, relpath):
        self._upsert(record_id, {"澄清记录": relpath})

    def set_queue_position(self, record_id, position):
        self._upsert(record_id, {"排队位置": str(position)})

    def complete(self, record_id, branch, summary, timeline):
        from datetime import datetime
        self._upsert(record_id, {
            "进展": "已完成",
            "实际完成日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "任务情况总结": summary, "最新进展记录": timeline})
