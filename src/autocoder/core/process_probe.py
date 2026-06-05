"""后台 worker 存活探测。

monitor 判定僵尸任务的双信号之一：用 `pgrep -f` 查某个 record 的
plan/execute 后台 worker 进程是否还在跑。worker 由 orchestrator._launch_bg
以 `python -m autocoder.cli <kind> <record_id>` 形式启动（脱离父进程组），
其完整命令行含子命令名与 record_id，故可按这两者精确匹配。
"""
import subprocess


def _matches(ps_line: str, record_id: str, kind: str) -> bool:
    """判断一行 ps/pgrep 命令行是否属于该 record 的指定 worker。

    纯函数，便于单测。要求命令行同时含 `autocoder.cli`、子命令 kind、
    以及 record_id，避免把 dispatch-feishu 等无关进程误判为 worker。
    """
    if "autocoder.cli" not in ps_line:
        return False
    if record_id not in ps_line:
        return False
    # kind 作为独立 token 出现（前后是空白），避免 'plan' 命中 'planner' 之类。
    tokens = ps_line.split()
    return kind in tokens


def worker_alive(record_id: str, kind: str) -> bool:
    """kind ∈ {'execute','plan'}。该 record 的后台 worker 是否在跑。"""
    r = subprocess.run(
        ["pgrep", "-f", f"autocoder.cli.*{kind}.*{record_id}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return False
    # pgrep -f 用正则粗筛后，再用 _matches 精确校验每个命中进程的完整命令行。
    for pid in r.stdout.split():
        cmd = subprocess.run(
            ["ps", "-p", pid.strip(), "-o", "command="],
            capture_output=True, text=True,
        )
        if _matches(cmd.stdout.strip(), record_id, kind):
            return True
    return False
