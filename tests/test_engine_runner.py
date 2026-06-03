import pytest
from autocoder.core.engine_runner import (run_engine, run_engine_capture,
                                          EngineResult)


def test_run_engine_success(tmp_path):
    # prompt 作为末位参数传入；echo 直接回显它并写入日志。
    log = tmp_path / "engine.log"
    spec = {"command": "echo", "args": [], "timeout": 10, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt text", str(log))
    assert result == EngineResult.SUCCESS
    assert "prompt text" in log.read_text()


def test_run_engine_does_not_hang_on_open_pipe(tmp_path):
    # 关键回归：引擎读 stdin 时不能挂死。`sh -c cat` 会读 stdin 直到 EOF
    # （末位 prompt 落到 $0，不当作文件名）；因为我们传 stdin=DEVNULL，
    # 它立刻收到 EOF 而非永久阻塞。若 stdin 是开放管道则会卡到超时。
    log = tmp_path / "engine.log"
    spec = {"command": "sh", "args": ["-c", "cat"], "timeout": 5, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.SUCCESS


def test_run_engine_timeout(tmp_path):
    log = tmp_path / "engine.log"
    spec = {"command": "sh", "args": ["-c", "sleep 30"], "timeout": 1, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.TIMEOUT


def test_run_engine_failure(tmp_path):
    log = tmp_path / "engine.log"
    spec = {"command": "false", "args": [], "timeout": 5, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.FAILURE


def test_run_engine_capture_returns_stdout(tmp_path):
    # echo 把 prompt 打到 stdout，capture 应原样返回。
    spec = {"command": "echo", "args": [], "timeout": 10, "env": {}}
    out = run_engine_capture(spec, str(tmp_path), "hello json", timeout=10)
    assert "hello json" in out


def test_run_engine_capture_timeout_returns_empty(tmp_path):
    spec = {"command": "sh", "args": ["-c", "sleep 30"], "timeout": 30, "env": {}}
    out = run_engine_capture(spec, str(tmp_path), "prompt", timeout=1)
    assert out == ""


def test_run_engine_capture_failure_returns_empty(tmp_path):
    spec = {"command": "false", "args": [], "timeout": 5, "env": {}}
    out = run_engine_capture(spec, str(tmp_path), "prompt", timeout=5)
    assert out == ""
