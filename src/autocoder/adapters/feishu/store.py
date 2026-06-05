import json
import subprocess
from dataclasses import fields as _dc_fields
from datetime import datetime

from autocoder.adapters.store import TaskStore
from autocoder.models import Task

_PRIORITY_ORDER = ["重要紧急", "紧急不重要", "重要不紧急", "不紧急不重要"]

# bitable 字段名 → Task 属性
_FIELD_MAP = {
    "任务描述": "description",
    "重要紧急程度": "priority",
    "进展": "status",
    "任务情况总结": "summary",
    "最新进展记录": "progress",
    "澄清记录": "clarify_pointer",
    "排队位置": "queue_position",
    "实际完成日期": "completed_at",
}
# Task 属性 → bitable 字段名（反向映射，用于写回）
_ATTR_TO_FIELD = {v: k for k, v in _FIELD_MAP.items()}

# 未单独建列的 Task 属性统一打包进这一个 JSON 文本列兜底。
# 含规划阶段写回、执行阶段读取的元数据（project/engine/spec_dir/base_branch
# /task_title 等）——这些字段无独立列，若不兜底会在进程间丢失。
_META_FIELD = "元数据"
_MAPPED_ATTRS = set(_FIELD_MAP.values()) | {"record_id"}
_META_ATTRS = [f.name for f in _dc_fields(Task) if f.name not in _MAPPED_ATTRS]


class FeishuBaseStore(TaskStore):
    """用 lark-cli 读写飞书多维表格（Base）。"""

    def __init__(self, feishu_config: dict):
        self.base_token = feishu_config["base_token"]
        self.table_id = feishu_config["table_id"]
        self.field_ids = feishu_config.get("field_ids", {})

    def _run_cli(self, *args, check=True) -> dict:
        import os
        cmd = [
            "lark-cli", "base",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            *args,
            "--format", "json",
        ]
        env = {**os.environ, "LARK_CLI_NO_PROXY": "1"}
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0 and check:
            raise RuntimeError(f"lark-cli failed: {proc.stderr[:200]}")
        return json.loads(proc.stdout)

    def _parse_record(self, record_id: str, fields_dict: dict) -> Task:
        """把 bitable 字段 dict 转成 Task 对象。"""
        kwargs = {"record_id": record_id, "description": ""}
        for field_name, attr in _FIELD_MAP.items():
            raw = fields_dict.get(field_name)
            if raw is None:
                continue
            # select 字段是数组 ["选项名"]，取第一个
            if isinstance(raw, list) and raw and isinstance(raw[0], str):
                kwargs[attr] = raw[0]
            elif isinstance(raw, str):
                kwargs[attr] = raw
            # user/datetime/其他类型暂不映射
        # 兜底列：未单独建列的元数据从「元数据」JSON 还原。
        meta_raw = fields_dict.get(_META_FIELD)
        if isinstance(meta_raw, list) and meta_raw:
            meta_raw = meta_raw[0]
        if isinstance(meta_raw, str) and meta_raw.strip():
            try:
                meta = json.loads(meta_raw)
            except (ValueError, TypeError):
                meta = {}
            for attr in _META_ATTRS:
                if meta.get(attr) is not None:
                    kwargs[attr] = meta[attr]
        return Task(**kwargs)

    def add(self, task: Task) -> str:
        """在 bitable 里创建一条新记录。bitable 自动生成 record_id，返回它。"""
        payload = {
            "任务描述": task.description,
            "重要紧急程度": [task.priority] if task.priority else ["一般"],
            "进展": ["待开始"],
        }
        proc = subprocess.run([
            "lark-cli", "base", "+record-upsert",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            "--json", json.dumps(payload, ensure_ascii=False),
        ], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"lark-cli failed: {proc.stderr[:200]}")
        resp = json.loads(proc.stdout)
        record = resp.get("data", {}).get("record", {})
        ids = record.get("record_id_list", [])
        if not ids:
            raise RuntimeError(f"upsert succeeded but no record_id returned: {resp}")
        return ids[0]

    def _save(self, task: Task) -> None:
        """把 Task 的所有非空字段写回 bitable。
        有独立列的走各自列；其余元数据打包进「元数据」JSON 列兜底。"""
        payload = {}
        for attr, field_name in _ATTR_TO_FIELD.items():
            val = getattr(task, attr, None)
            if val is not None:
                if attr in ("priority", "status"):
                    payload[field_name] = [val]
                else:
                    payload[field_name] = val
        meta = {attr: getattr(task, attr, None) for attr in _META_ATTRS
                if getattr(task, attr, None) is not None}
        if meta:
            payload[_META_FIELD] = json.dumps(meta, ensure_ascii=False)
        if payload:
            self._upsert(task.record_id, payload)

    def _list_all(self) -> list:
        """拉全表记录，展平 select 数组，转成 Task 列表（不过滤状态）。"""
        resp = self._run_cli("+record-list", "--limit", "200")
        data = resp.get("data", {})
        rows = data.get("data", [])
        field_names = data.get("fields", [])
        record_ids = data.get("record_id_list", [])

        tasks = []
        for row, rid in zip(rows, record_ids):
            fields_dict = dict(zip(field_names, row))
            fields_dict_mapped = {}
            for field_name, val in fields_dict.items():
                if isinstance(val, list) and val and isinstance(val[0], str):
                    fields_dict_mapped[field_name] = val[0]
                else:
                    fields_dict_mapped[field_name] = val
            tasks.append(self._parse_record(rid, fields_dict_mapped))
        return tasks

    def fetch_pending(self) -> list:
        """过滤 进展==待开始，按优先级排序。"""
        pending = [t for t in self._list_all() if t.status == "待开始"]

        def rank(t: Task) -> int:
            return _PRIORITY_ORDER.index(t.priority) if t.priority in _PRIORITY_ORDER else 99

        return sorted(pending, key=rank)

    def fetch_by_status(self, status: str) -> list:
        return [t for t in self._list_all() if t.status == status]

    def get(self, record_id: str) -> Task:
        resp = self._run_cli("+record-get", "--record-id", record_id)
        data = resp.get("data", {})
        rows = data.get("data", [])
        field_names = data.get("fields", [])
        record_ids = data.get("record_id_list", [])
        if not rows:
            raise FileNotFoundError(f"record {record_id} not found")
        row = rows[0]
        fields_dict = dict(zip(field_names, row))
        # 把 select 数组展平为字符串
        for k, v in fields_dict.items():
            if isinstance(v, list) and v and isinstance(v[0], str):
                fields_dict[k] = v[0]
        return self._parse_record(record_id, fields_dict)

    def _upsert(self, record_id: str, payload: dict):
        """用字段名（而非字段 ID）写回 bitable。"""
        subprocess.run([
            "lark-cli", "base", "+record-upsert",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            "--record-id", record_id,
            "--json", json.dumps(payload, ensure_ascii=False),
        ], capture_output=True, text=True, check=True)

    def _update_field(self, record_id: str, attr: str, value):
        field_name = _ATTR_TO_FIELD.get(attr)
        if not field_name:
            return
        # select 字段需要包在数组里
        if attr in ("priority", "status"):
            value = [value] if value else None
        self._upsert(record_id, {field_name: value})

    def update_status(self, record_id, status):
        self._update_field(record_id, "status", status)

    def update_summary(self, record_id, summary, progress):
        self._upsert(record_id, {
            "任务情况总结": summary,
            "最新进展记录": progress,
        })

    def set_clarify_pointer(self, record_id, relpath):
        self._update_field(record_id, "clarify_pointer", relpath)

    def set_queue_position(self, record_id, position):
        self._update_field(record_id, "queue_position", str(position))

    def complete(self, record_id, branch, summary, timeline):
        self._upsert(record_id, {
            "进展": ["已完成"],
            "实际完成日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "任务情况总结": summary,
            "最新进展记录": timeline,
        })
