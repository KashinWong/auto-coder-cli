from abc import ABC, abstractmethod

from autocoder.models import Task


class Notifier(ABC):
    @abstractmethod
    def send_clarify(self, task: Task, modules: list, risks: list, round_no: int) -> None: ...
    @abstractmethod
    def send_charter(self, task: Task, summary: str) -> None: ...
    @abstractmethod
    def send_plan(self, task: Task, plan_summary: str, task_count: int, branch: str) -> None: ...
    @abstractmethod
    def send_complete(self, task: Task, branch: str, change_stats: str,
                      duration: str, timeline: str) -> None: ...
    @abstractmethod
    def send_failure(self, task: Task, stage: str, error: str,
                     log_path: str, branch: str) -> None: ...


def _title(task: Task) -> str:
    return task.task_title or task.description


class CliNotifier(Notifier):
    """把每张「卡片」渲染成终端富文本。"""

    def send_clarify(self, task, modules, risks, round_no):
        print(f"\n===== 📋 需求澄清 · {_title(task)} (第 {round_no}/3 轮) =====")
        print(f"需求描述: {task.description}")
        print("🧩 涉及模块（预判）:")
        for m in modules:
            print(f"  - {m}")
        print("⚠️ 风险点:")
        for r in risks:
            print(f"  - {r}")
        print("请就以下维度补充: 范围边界 / 模块 / 验收标准 / 优先级约束 / 风险回应")

    def send_charter(self, task, summary):
        print(f"\n===== 🏗️ 立项确认 · {_title(task)} =====")
        print(summary)
        print("操作: 立项 / 改 / 再聊 / 拒")

    def send_plan(self, task, plan_summary, task_count, branch):
        print(f"\n===== 📐 方案审批 · {_title(task)} =====")
        print(f"分支: {branch}")
        print(f"任务数: {task_count}")
        print(f"方案摘要:\n{plan_summary}")
        print("操作: 批准 / 退·改方案 / 退·重新理解")

    def send_complete(self, task, branch, change_stats, duration, timeline):
        print(f"\n===== ✅ 已完成 · {_title(task)} =====")
        print(f"分支: {branch}")
        print(f"改动: {change_stats}")
        print(f"耗时: {duration}")
        print(f"时间线: {timeline}")

    def send_failure(self, task, stage, error, log_path, branch):
        print(f"\n===== ❌ 失败 · {_title(task)} =====")
        print(f"阶段: {stage}")
        print(f"错误: {error}")
        print(f"日志: {log_path}")
        print(f"分支(留现场): {branch}")
