from autocoder.adapters.store import JsonTaskStore
from autocoder.models import Task


def test_add_and_fetch_pending(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="需求一", priority="重要紧急"))
    store.add(Task(record_id="r2", description="需求二", priority="不紧急不重要"))
    pending = store.fetch_pending()
    ids = [t.record_id for t in pending]
    assert ids == ["r1", "r2"]  # 按优先级排序，重要紧急在前


def test_get_roundtrip(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p"))
    t = store.get("r1")
    assert t.description == "x"


def test_update_status_persists(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p"))
    store.update_status("r1", "澄清中")
    assert store.get("r1").status == "澄清中"
    # 新 store 实例也能读到（已落盘）
    assert JsonTaskStore(str(tmp_path)).get("r1").status == "澄清中"


def test_completed_not_in_pending(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p"))
    store.update_status("r1", "已完成")
    assert store.fetch_pending() == []


def test_fetch_by_status_filters(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p", status="待审批"))
    store.add(Task(record_id="r2", description="y", priority="p", status="进行中"))
    store.add(Task(record_id="r3", description="z", priority="p", status="待审批"))
    ids = sorted(t.record_id for t in store.fetch_by_status("待审批"))
    assert ids == ["r1", "r3"]
    assert store.fetch_by_status("已完成") == []

    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p"))
    store.set_clarify_pointer("r1", "specs/001-x/clarify.md")
    store.set_queue_position("r1", 2)
    t = store.get("r1")
    assert t.clarify_pointer == "specs/001-x/clarify.md"
    assert t.queue_position == 2
