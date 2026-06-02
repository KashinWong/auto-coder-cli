from autocoder.adapters.notifier import CliNotifier
from autocoder.models import Task


def _task():
    return Task(record_id="r1", description="加登录", priority="重要紧急",
                task_title="加登录按钮")


def test_send_clarify_prints_dimensions(capsys):
    CliNotifier().send_clarify(_task(), modules=["auth.py"],
                               risks=["可能影响会话"], round_no=1)
    out = capsys.readouterr().out
    assert "加登录" in out
    assert "auth.py" in out
    assert "可能影响会话" in out
    assert "1/3" in out


def test_send_plan_prints_branch_and_count(capsys):
    CliNotifier().send_plan(_task(), plan_summary="改 3 个文件",
                            task_count=5, branch="feature/auto-r1")
    out = capsys.readouterr().out
    assert "feature/auto-r1" in out
    assert "5" in out
    assert "改 3 个文件" in out


def test_send_complete_prints_stats(capsys):
    CliNotifier().send_complete(_task(), branch="feature/auto-r1",
                                change_stats="3 files changed",
                                duration="4 分钟", timeline="t1 → t2")
    out = capsys.readouterr().out
    assert "3 files changed" in out
    assert "4 分钟" in out


def test_send_failure_prints_stage_and_error(capsys):
    CliNotifier().send_failure(_task(), stage="测试", error="2 个用例失败",
                               log_path="/tmp/x.log", branch="feature/auto-r1")
    out = capsys.readouterr().out
    assert "测试" in out
    assert "2 个用例失败" in out
    assert "/tmp/x.log" in out
