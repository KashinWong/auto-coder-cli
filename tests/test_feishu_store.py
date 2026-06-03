import json

from autocoder.adapters.feishu.store import (
    FeishuBaseStore, _META_FIELD, _META_ATTRS,
)
from autocoder.models import Task


def _store():
    return FeishuBaseStore({"base_token": "bt", "table_id": "tbl"})


def test_meta_attrs_cover_unmapped_planning_fields():
    # 规划阶段写回、执行阶段读取的元数据必须落在兜底列里。
    for attr in ("project", "engine", "spec_dir", "base_branch", "task_title"):
        assert attr in _META_ATTRS


def test_save_packs_unmapped_fields_into_meta_json():
    store = _store()
    captured = {}
    store._upsert = lambda rid, payload: captured.update(payload)

    task = Task(record_id="r1", description="加功能", priority="重要紧急",
                status="规划中", project="gomoku-h5", engine="claude-code",
                spec_dir="specs/001-feature", base_branch="master")
    store._save(task)

    # 有独立列的字段走各自列
    assert captured["任务描述"] == "加功能"
    assert captured["进展"] == ["规划中"]
    # 无独立列的元数据打包进 JSON 兜底列
    meta = json.loads(captured[_META_FIELD])
    assert meta["project"] == "gomoku-h5"
    assert meta["engine"] == "claude-code"
    assert meta["spec_dir"] == "specs/001-feature"
    assert meta["base_branch"] == "master"


def test_parse_record_restores_meta_round_trip():
    store = _store()
    meta = {"project": "gomoku-h5", "engine": "claude-code",
            "spec_dir": "specs/001-feature", "base_branch": "master"}
    fields = {
        "任务描述": "加功能",
        "重要紧急程度": "重要紧急",
        "进展": "规划中",
        _META_FIELD: json.dumps(meta, ensure_ascii=False),
    }
    task = store._parse_record("r1", fields)
    assert task.project == "gomoku-h5"
    assert task.engine == "claude-code"
    assert task.spec_dir == "specs/001-feature"
    assert task.base_branch == "master"


def test_parse_record_tolerates_missing_or_garbage_meta():
    store = _store()
    # 缺兜底列：元数据字段退化为 None，不报错
    t1 = store._parse_record("r1", {"任务描述": "x", "重要紧急程度": "重要紧急",
                                    "进展": "待开始"})
    assert t1.project is None
    # 兜底列是垃圾串：吞掉异常，不污染
    t2 = store._parse_record("r2", {"任务描述": "x", "重要紧急程度": "重要紧急",
                                    _META_FIELD: "not json"})
    assert t2.engine is None
