import os
import subprocess
from enum import Enum


class EngineResult(Enum):
    SUCCESS = 0
    FAILURE = 1
    TIMEOUT = 2


def run_engine(spec: dict, working_dir: str, prompt: str, log_file: str) -> EngineResult:
    """调编码引擎。spec: {command, args, timeout, env}。

    stdin 必须是 DEVNULL：claude --print 继承开放管道时会等永不到来的
    EOF 而挂死，直到超时。prompt 作为最后一个位置参数传入。
    """
    cmd = [spec["command"], *spec.get("args", []), prompt]
    env = {**os.environ, **{k: str(v) for k, v in spec.get("env", {}).items()}}
    timeout = spec.get("timeout", 1800)

    with open(log_file, "w") as log:
        try:
            proc = subprocess.run(
                cmd,
                cwd=working_dir,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return EngineResult.TIMEOUT

    return EngineResult.SUCCESS if proc.returncode == 0 else EngineResult.FAILURE


def run_command(command: str, cwd: str) -> bool:
    """跑 shell 命令（测试/构建用）。空命令视为跳过=成功。"""
    if not command:
        return True
    proc = subprocess.run(command, cwd=cwd, shell=True, stdin=subprocess.DEVNULL)
    return proc.returncode == 0
