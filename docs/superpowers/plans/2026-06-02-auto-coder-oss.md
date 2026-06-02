# auto-coder-oss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把私有 hermes/飞书编码工作流移植成开源 Python 包：可插拔 TaskStore/Notifier/EventRouter，零依赖 CLI 默认闭环，飞书为可选 adapter。

**Architecture:** 纯核心（状态机/worktree/引擎调度/规划编排）通过三个抽象接口与 I/O 解耦。默认实现 JsonTaskStore + CliNotifier + CliRouter 在单进程内同步驱动完整闭环；飞书实现封装原 bash 逻辑作为可选项。

**Tech Stack:** Python 3.10+，pytest，PyYAML，subprocess（引擎调度），可选 requests（飞书）+ FastAPI（webhook）。

**设计文档:** `docs/specs/2026-06-02-auto-coder-oss-design.md`

**工作目录:** `~/projects/auto-coder-oss`（git 已初始化，remote origin 已配，首个 commit 589bba0 为设计文档）

---

## 关键约定（所有任务共享）

- **包根:** `src/autocoder/`，用 `pip install -e .` 安装后可 `import autocoder`。
- **测试运行:** 项目根目录 `pytest tests/ -v`。
- **每个任务自成一个 commit。** commit message 用 `feat:` / `test:` / `chore:` 前缀。
- **不 push。** 所有 commit 留本地，push 等用户明确指示。
- **状态常量:** 用中文字符串字面量（`"待开始"` 等），与原系统一致，便于飞书 adapter 复用。

---

### Task 1: 项目骨架与打包配置

**Files:**
- Create: `pyproject.toml`
- Create: `src/autocoder/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_packaging.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "auto-coder-oss"
version = "0.1.0"
description = "Agent 驱动的自动化编码工作流"
requires-python = ">=3.10"
dependencies = ["PyYAML>=6.0"]

[project.optional-dependencies]
feishu = ["requests>=2.28", "fastapi>=0.100", "uvicorn>=0.23"]
dev = ["pytest>=7.0"]

[project.scripts]
auto-coder = "autocoder.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: 写包入口 `src/autocoder/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: 写 `tests/__init__.py`（空文件）**

```python
```

- [ ] **Step 4: 写打包冒烟测试 `tests/test_packaging.py`**

```python
def test_version_importable():
    import autocoder
    assert autocoder.__version__ == "0.1.0"
```

- [ ] **Step 5: 安装并运行测试**

Run: `cd ~/projects/auto-coder-oss && pip install -e ".[dev]" && pytest tests/test_packaging.py -v`
Expected: PASS（1 passed）

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/autocoder/__init__.py tests/__init__.py tests/test_packaging.py
git commit -m "chore: project skeleton and packaging config"
```

---

### Task 2: 数据模型（Task / Decision）

**Files:**
- Create: `src/autocoder/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: 写失败测试 `tests/test_models.py`**

```python
from autocoder.models import Task, Decision


def test_task_defaults():
    t = Task(record_id="r1", description="加个登录按钮", priority="重要紧急")
    assert t.record_id == "r1"
    assert t.status == "待开始"
    assert t.summary == ""
    assert t.spec_dir is None


def test_task_branch_name():
    t = Task(record_id="abc", description="x", priority="p")
    assert t.branch_name() == "feature/auto-abc"


def test_decision_holds_action_and_form():
    d = Decision(action="clarify_submit", record_id="r1",
                 form={"scope": "只改前端"})
    assert d.action == "clarify_submit"
    assert d.form["scope"] == "只改前端"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_models.py -v`
Expected: FAIL（ModuleNotFoundError: autocoder.models）

- [ ] **Step 3: 实现 `src/autocoder/models.py`**

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Task:
    record_id: str
    description: str
    priority: str
    status: str = "待开始"
    summary: str = ""
    progress: str = ""
    project: Optional[str] = None
    task_title: Optional[str] = None
    base_branch: Optional[str] = None
    engine: Optional[str] = None
    spec_dir: Optional[str] = None
    clarify_pointer: Optional[str] = None
    queue_position: Optional[int] = None
    plan_generated_at: Optional[str] = None
    approved_at: Optional[str] = None
    completed_at: Optional[str] = None
    branch_info: Optional[str] = None

    def branch_name(self) -> str:
        return f"feature/auto-{self.record_id}"


@dataclass
class Decision:
    action: str
    record_id: str
    stage: str = ""
    form: dict = field(default_factory=dict)
    input_text: str = ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_models.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/models.py tests/test_models.py
git commit -m "feat: add Task and Decision data models"
```

---

### Task 3: 状态机（纯函数，移植 state-machine.sh）

**Files:**
- Create: `src/autocoder/core/__init__.py`
- Create: `src/autocoder/core/state_machine.py`
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: 写失败测试 `tests/test_state_machine.py`**

```python
import pytest
from autocoder.core import state_machine as sm


@pytest.mark.parametrize("frm,to", [
    ("待开始", "澄清中"),
    ("澄清中", "待立项"),
    ("澄清中", "澄清中"),
    ("待立项", "规划中"),
    ("待立项", "澄清中"),
    ("待立项", "已搁置"),
    ("已搁置", "待开始"),
    ("规划中", "待审批"),
    ("待审批", "进行中"),
    ("待审批", "规划中"),
    ("待审批", "澄清中"),
    ("进行中", "已完成"),
    ("进行中", "已停滞"),
])
def test_allowed_transitions(frm, to):
    assert sm.can_transition(frm, to) is True


@pytest.mark.parametrize("frm,to", [
    ("待开始", "待审批"),
    ("已完成", "进行中"),
    ("澄清中", "进行中"),
])
def test_forbidden_transitions(frm, to):
    assert sm.can_transition(frm, to) is False


@pytest.mark.parametrize("action,target", [
    ("reject_charter", "已搁置"),
    ("revise_charter", "澄清中"),
    ("rechat", "澄清中"),
    ("revise_plan", "规划中"),
    ("reunderstand", "澄清中"),
])
def test_rollback_target(action, target):
    assert sm.rollback_target(action) == target


def test_rollback_target_unknown():
    assert sm.rollback_target("nope") is None


def test_rollback_drops_worktree():
    assert sm.rollback_drops_worktree("reunderstand") is True
    assert sm.rollback_drops_worktree("revise_plan") is False
    assert sm.rollback_drops_worktree("reject_charter") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_state_machine.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/__init__.py`（空）**

```python
```

- [ ] **Step 4: 实现 `src/autocoder/core/state_machine.py`**

```python
_TRANSITIONS = {
    ("待开始", "澄清中"),
    ("澄清中", "待立项"),
    ("澄清中", "澄清中"),
    ("待立项", "规划中"),
    ("待立项", "澄清中"),
    ("待立项", "已搁置"),
    ("已搁置", "待开始"),
    ("规划中", "待审批"),
    ("待审批", "进行中"),
    ("待审批", "规划中"),
    ("待审批", "澄清中"),
    ("进行中", "已完成"),
    ("进行中", "已停滞"),
}

_ROLLBACK_TARGET = {
    "reject_charter": "已搁置",
    "revise_charter": "澄清中",
    "rechat": "澄清中",
    "revise_plan": "规划中",
    "reunderstand": "澄清中",
}


def can_transition(frm: str, to: str) -> bool:
    return (frm, to) in _TRANSITIONS


def rollback_target(action: str):
    return _ROLLBACK_TARGET.get(action)


def rollback_drops_worktree(action: str) -> bool:
    return action == "reunderstand"
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_state_machine.py -v`
Expected: PASS（全部 parametrize 用例 passed）

- [ ] **Step 6: Commit**

```bash
git add src/autocoder/core/__init__.py src/autocoder/core/state_machine.py tests/test_state_machine.py
git commit -m "feat: port state machine transition/rollback logic"
```

---

### Task 4: Worktree 与并发闸（移植 worktree.sh）

**Files:**
- Create: `src/autocoder/core/worktree.py`
- Test: `tests/test_worktree.py`

- [ ] **Step 1: 写失败测试 `tests/test_worktree.py`**

```python
from autocoder.core import worktree as wt


def test_branch_name():
    assert wt.branch_name("abc") == "feature/auto-abc"


def test_count_active():
    porcelain = (
        "worktree /repo\nHEAD x\nbranch refs/heads/main\n\n"
        "worktree /repo/.worktrees/feature/auto-r1\nHEAD y\n"
        "branch refs/heads/feature/auto-r1\n\n"
        "worktree /repo/.worktrees/feature/auto-r2\nHEAD z\n"
        "branch refs/heads/feature/auto-r2\n"
    )
    assert wt.count_active(porcelain) == 2


def test_count_active_none():
    assert wt.count_active("worktree /repo\nbranch refs/heads/main\n") == 0


def test_gate_open():
    assert wt.gate_open(2, 3) is True
    assert wt.gate_open(3, 3) is False
    assert wt.gate_open(4, 3) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_worktree.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/worktree.py`**

```python
import subprocess
from pathlib import Path


def branch_name(record_id: str) -> str:
    return f"feature/auto-{record_id}"


def count_active(porcelain_text: str) -> int:
    return sum(
        1 for line in porcelain_text.splitlines()
        if line.startswith("branch refs/heads/feature/auto-")
    )


def gate_open(active: int, limit: int) -> bool:
    return active < limit


def active_count(project_path: str) -> int:
    result = subprocess.run(
        ["git", "-C", project_path, "worktree", "list", "--porcelain"],
        capture_output=True, text=True,
    )
    return count_active(result.stdout)


def create(project_path: str, base_branch: str, record_id: str) -> str:
    branch = branch_name(record_id)
    worktree_path = str(Path(project_path) / ".worktrees" / branch)
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", project_path, "fetch", "origin", base_branch],
        capture_output=True, text=True,
    )
    add_from_remote = subprocess.run(
        ["git", "-C", project_path, "worktree", "add", worktree_path,
         "-b", branch, f"origin/{base_branch}"],
        capture_output=True, text=True,
    )
    if add_from_remote.returncode != 0:
        fallback = subprocess.run(
            ["git", "-C", project_path, "worktree", "add", worktree_path, branch],
            capture_output=True, text=True,
        )
        if fallback.returncode != 0:
            raise RuntimeError(f"worktree create failed: {add_from_remote.stderr}")
    return worktree_path


def remove(project_path: str, record_id: str) -> None:
    branch = branch_name(record_id)
    worktree_path = str(Path(project_path) / ".worktrees" / branch)
    subprocess.run(
        ["git", "-C", project_path, "worktree", "remove", "--force", worktree_path],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", project_path, "branch", "-D", branch],
        capture_output=True, text=True,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_worktree.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/core/worktree.py tests/test_worktree.py
git commit -m "feat: port worktree lifecycle and concurrency gate"
```

---

### Task 5: 引擎调度（移植 engine-runner.sh，含 stdin=DEVNULL 与 timeout）

**Files:**
- Create: `src/autocoder/core/engine_runner.py`
- Test: `tests/test_engine_runner.py`

- [ ] **Step 1: 写失败测试 `tests/test_engine_runner.py`**

```python
import pytest
from autocoder.core.engine_runner import run_engine, EngineResult


def test_run_engine_success(tmp_path):
    log = tmp_path / "engine.log"
    spec = {"command": "printf", "args": ["done"], "timeout": 10, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt text", str(log))
    assert result == EngineResult.SUCCESS
    assert "done" in log.read_text()


def test_run_engine_does_not_hang_on_open_pipe(tmp_path):
    # 关键回归：引擎继承开放管道时不能挂死。cat 会读 stdin 直到 EOF；
    # 因为我们传 stdin=DEVNULL，它立刻收到 EOF 而非永久阻塞。
    log = tmp_path / "engine.log"
    spec = {"command": "cat", "args": [], "timeout": 5, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.SUCCESS


def test_run_engine_timeout(tmp_path):
    log = tmp_path / "engine.log"
    spec = {"command": "sleep", "args": ["30"], "timeout": 1, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.TIMEOUT


def test_run_engine_failure(tmp_path):
    log = tmp_path / "engine.log"
    spec = {"command": "false", "args": [], "timeout": 5, "env": {}}
    result = run_engine(spec, str(tmp_path), "prompt", str(log))
    assert result == EngineResult.FAILURE
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine_runner.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/engine_runner.py`**

```python
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
    proc = subprocess.run(command, cwd=cwd, shell=True)
    return proc.returncode == 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_engine_runner.py -v`
Expected: PASS（4 passed，timeout 用例约 1 秒）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/core/engine_runner.py tests/test_engine_runner.py
git commit -m "feat: port engine runner with stdin=DEVNULL and timeout"
```

---

## 自查（覆盖 Task 1-5）

- **Spec 覆盖:** §3 状态机→Task 3；§5 worktree→Task 4；§5 engine_runner→Task 5；数据模型→Task 2；骨架→Task 1。✅
- **占位符扫描:** 无 TBD/TODO；每个代码步骤都有完整代码。✅
- **类型一致性:** `Task.branch_name()`、`EngineResult` 枚举、`run_engine(spec, working_dir, prompt, log_file)` 签名在测试与实现中一致。✅

后续任务（adapters、orchestrator、CLI、飞书实现、配置、README）将在下一批补充，保持同样的 TDD 颗粒度。

---

### Task 6: 配置加载与校验

**Files:**
- Create: `src/autocoder/config.py`
- Create: `config.example.yaml`
- Create: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
import pytest
from autocoder.config import load_config, Config

SAMPLE = """
adapters:
  store: json
  notifier: cli
  router: cli
workspace_dir: ./workspace/tasks
concurrency:
  limit: 3
projects:
  demo:
    path: /tmp/demo
    engine: claude-code
    match_keywords: ["demo"]
    base_branch: main
    test_command: "true"
    build_command: "true"
engines:
  claude-code:
    command: claude
    args: ["--print"]
    timeout: 1800
    env: {}
  default: claude-code
clarify_dimensions:
  - {key: scope, label: "范围", source: fixed}
"""


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_load_basic(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    assert isinstance(cfg, Config)
    assert cfg.adapters["store"] == "json"
    assert cfg.concurrency_limit == 3
    assert cfg.projects["demo"]["base_branch"] == "main"
    assert cfg.default_engine == "claude-code"


def test_engine_spec_lookup(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    spec = cfg.engine_spec("claude-code")
    assert spec["command"] == "claude"
    assert spec["timeout"] == 1800


def test_match_project_by_keyword(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    assert cfg.match_project("给 demo 加功能") == "demo"
    assert cfg.match_project("无关需求") is None


def test_missing_required_key_raises(tmp_path):
    bad = SAMPLE.replace("workspace_dir: ./workspace/tasks", "")
    with pytest.raises(ValueError, match="workspace_dir"):
        load_config(_write(tmp_path, bad))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/config.py`**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class Config:
    adapters: dict
    workspace_dir: str
    concurrency_limit: int
    projects: dict
    engines: dict
    default_engine: str
    clarify_dimensions: list
    feishu: dict

    def engine_spec(self, name: str) -> dict:
        return self.engines[name]

    def match_project(self, text: str) -> Optional[str]:
        for key, proj in self.projects.items():
            for kw in proj.get("match_keywords", []):
                if kw in text:
                    return key
        return None


_REQUIRED = ["adapters", "workspace_dir", "concurrency", "projects", "engines"]


def load_config(path: str) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    for key in _REQUIRED:
        if key not in data:
            raise ValueError(f"config missing required key: {key}")
    engines = dict(data["engines"])
    default_engine = engines.pop("default", None)
    return Config(
        adapters=data["adapters"],
        workspace_dir=data["workspace_dir"],
        concurrency_limit=data["concurrency"]["limit"],
        projects=data["projects"],
        engines=engines,
        default_engine=default_engine,
        clarify_dimensions=data.get("clarify_dimensions", []),
        feishu=data.get("feishu", {}),
    )
```

- [ ] **Step 4: 写 `config.example.yaml`（全占位符，无任何真实值）**

```yaml
adapters:
  store: json          # json | feishu
  notifier: cli        # cli | feishu
  router: cli          # cli | feishu_webhook

workspace_dir: ./workspace/tasks

concurrency:
  limit: 3

projects:
  example-project:
    path: /path/to/your/project
    engine: claude-code
    match_keywords: ["关键词1", "关键词2"]
    base_branch: main
    test_command: "npm test"
    build_command: "npm run build"
    knowledge:
      - path: /path/to/your/project/docs/
        label: 技术文档
        strategy: all
        include: "*.md"

engines:
  claude-code:
    command: claude
    args: ["--print", "--dangerously-skip-permissions"]
    timeout: 1800
    env:
      CLAUDE_MODEL: your-model-name
  codex:
    command: codex
    args: ["--quiet", "--auto-edit"]
    timeout: 1800
    env: {}
  default: claude-code

clarify_dimensions:
  - {key: scope,       label: "🎯 范围边界",    source: fixed}
  - {key: modules,     label: "🧩 涉及模块",    source: agent}
  - {key: acceptance,  label: "✅ 验收标准",    source: fixed}
  - {key: constraints, label: "⚙️ 优先级/约束", source: fixed}
  - {key: risk,        label: "⚠️ 风险点",      source: agent}

# 仅当 adapters.store/notifier = feishu 时需要
feishu:
  base_token: YOUR_BASE_TOKEN
  table_id: YOUR_TABLE_ID
  notify_chat_id: YOUR_CHAT_ID
  field_ids:
    description: fldXXXXXXXX
    priority: fldXXXXXXXX
```

- [ ] **Step 5: 写 `.env.example`**

```
# 仅当使用飞书 adapter 时需要（飞书开放平台「企业自建应用」凭证）
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_config.py -v`
Expected: PASS（4 passed）

- [ ] **Step 7: Commit**

```bash
git add src/autocoder/config.py config.example.yaml .env.example tests/test_config.py
git commit -m "feat: add config loader and example templates"
```

---

### Task 7: TaskStore 接口 + JsonTaskStore（默认实现）

**Files:**
- Create: `src/autocoder/adapters/__init__.py`
- Create: `src/autocoder/adapters/store.py`
- Test: `tests/test_json_store.py`

- [ ] **Step 1: 写失败测试 `tests/test_json_store.py`**

```python
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


def test_set_pointers(tmp_path):
    store = JsonTaskStore(str(tmp_path))
    store.add(Task(record_id="r1", description="x", priority="p"))
    store.set_clarify_pointer("r1", "specs/001-x/clarify.md")
    store.set_queue_position("r1", 2)
    t = store.get("r1")
    assert t.clarify_pointer == "specs/001-x/clarify.md"
    assert t.queue_position == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_json_store.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/adapters/__init__.py`（空）**

```python
```

- [ ] **Step 4: 实现 `src/autocoder/adapters/store.py`**

```python
import json
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from autocoder.models import Task

_PRIORITY_ORDER = ["重要紧急", "紧急不重要", "重要不紧急", "不紧急不重要"]


class TaskStore(ABC):
    @abstractmethod
    def fetch_pending(self) -> list: ...
    @abstractmethod
    def get(self, record_id: str) -> Task: ...
    @abstractmethod
    def update_status(self, record_id: str, status: str) -> None: ...
    @abstractmethod
    def update_summary(self, record_id: str, summary: str, progress: str) -> None: ...
    @abstractmethod
    def set_clarify_pointer(self, record_id: str, relpath: str) -> None: ...
    @abstractmethod
    def set_queue_position(self, record_id: str, position: int) -> None: ...
    @abstractmethod
    def complete(self, record_id, branch, summary, timeline) -> None: ...


class JsonTaskStore(TaskStore):
    """每个任务存 <workspace_dir>/<record_id>/task.json。"""

    def __init__(self, workspace_dir: str):
        self.root = Path(workspace_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, record_id: str) -> Path:
        return self.root / record_id / "task.json"

    def _save(self, task: Task) -> None:
        p = self._path(task.record_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2))

    def add(self, task: Task) -> None:
        self._save(task)

    def get(self, record_id: str) -> Task:
        data = json.loads(self._path(record_id).read_text())
        return Task(**data)

    def _all(self) -> list:
        tasks = []
        for d in self.root.iterdir():
            f = d / "task.json"
            if f.exists():
                tasks.append(Task(**json.loads(f.read_text())))
        return tasks

    def fetch_pending(self) -> list:
        pending = [t for t in self._all() if t.status == "待开始"]

        def rank(t: Task) -> int:
            return _PRIORITY_ORDER.index(t.priority) if t.priority in _PRIORITY_ORDER else 99

        return sorted(pending, key=rank)

    def _update(self, record_id: str, **fields) -> None:
        t = self.get(record_id)
        for k, v in fields.items():
            setattr(t, k, v)
        self._save(t)

    def update_status(self, record_id: str, status: str) -> None:
        self._update(record_id, status=status)

    def update_summary(self, record_id: str, summary: str, progress: str) -> None:
        self._update(record_id, summary=summary, progress=progress)

    def set_clarify_pointer(self, record_id: str, relpath: str) -> None:
        self._update(record_id, clarify_pointer=relpath)

    def set_queue_position(self, record_id: str, position: int) -> None:
        self._update(record_id, queue_position=position)

    def complete(self, record_id, branch, summary, timeline) -> None:
        from datetime import datetime
        self._update(
            record_id,
            status="已完成",
            branch_info=branch,
            summary=summary,
            progress=timeline,
            completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_json_store.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add src/autocoder/adapters/__init__.py src/autocoder/adapters/store.py tests/test_json_store.py
git commit -m "feat: add TaskStore interface and JsonTaskStore"
```

---

### Task 8: Notifier 接口 + CliNotifier（默认实现）

**Files:**
- Create: `src/autocoder/adapters/notifier.py`
- Test: `tests/test_cli_notifier.py`

- [ ] **Step 1: 写失败测试 `tests/test_cli_notifier.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cli_notifier.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/adapters/notifier.py`**

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cli_notifier.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/adapters/notifier.py tests/test_cli_notifier.py
git commit -m "feat: add Notifier interface and CliNotifier"
```

---

### Task 9: EventRouter 接口 + CliRouter（默认实现）

**Files:**
- Create: `src/autocoder/adapters/router.py`
- Test: `tests/test_cli_router.py`

- [ ] **Step 1: 写失败测试 `tests/test_cli_router.py`**

```python
import builtins
from autocoder.adapters.router import CliRouter
from autocoder.models import Decision


def test_clarify_decision_collects_form(monkeypatch):
    # 模拟用户依次输入 5 个澄清维度的回答
    answers = iter(["只改前端", "auth.py", "能登录即可", "无 deadline", "无风险"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    d = CliRouter().await_decision("r1", stage="clarify")
    assert isinstance(d, Decision)
    assert d.action == "clarify_submit"
    assert d.form["scope"] == "只改前端"
    assert d.form["modules"] == "auth.py"


def test_charter_approve(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "1")
    d = CliRouter().await_decision("r1", stage="charter")
    assert d.action == "approve_charter"


def test_charter_reject(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "4")
    d = CliRouter().await_decision("r1", stage="charter")
    assert d.action == "reject_charter"


def test_plan_approve(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "1")
    d = CliRouter().await_decision("r1", stage="plan")
    assert d.action == "approve_plan"


def test_plan_revise_collects_input(monkeypatch):
    answers = iter(["2", "把第三个任务拆开"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    d = CliRouter().await_decision("r1", stage="plan")
    assert d.action == "revise_plan"
    assert d.input_text == "把第三个任务拆开"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cli_router.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/adapters/router.py`**

```python
from abc import ABC, abstractmethod

from autocoder.models import Decision

_CLARIFY_FIELDS = ["scope", "modules", "acceptance", "constraints", "risk_reply"]
_CLARIFY_PROMPTS = {
    "scope": "🎯 范围边界（做什么/不做什么）",
    "modules": "🧩 涉及模块补充/纠正",
    "acceptance": "✅ 验收标准",
    "constraints": "⚙️ 优先级/约束",
    "risk_reply": "⚠️ 对风险点的回应",
}


class EventRouter(ABC):
    @abstractmethod
    def await_decision(self, record_id: str, stage: str) -> Decision: ...


class CliRouter(EventRouter):
    """终端同步交互。dispatch/execute 在同一进程内驱动，无需网关/回调。"""

    def await_decision(self, record_id: str, stage: str) -> Decision:
        if stage == "clarify":
            form = {}
            for field in _CLARIFY_FIELDS:
                form[field] = input(f"{_CLARIFY_PROMPTS[field]}: ").strip()
            return Decision(action="clarify_submit", record_id=record_id,
                            stage=stage, form=form)

        if stage == "charter":
            choice = input("选择 [1]立项 [2]改 [3]再聊 [4]拒: ").strip()
            action = {"1": "approve_charter", "2": "revise_charter",
                      "3": "rechat", "4": "reject_charter"}.get(choice, "approve_charter")
            note = ""
            if action == "revise_charter":
                note = input("修改说明: ").strip()
            return Decision(action=action, record_id=record_id, stage=stage,
                            input_text=note)

        if stage == "plan":
            choice = input("选择 [1]批准 [2]退·改方案 [3]退·重新理解: ").strip()
            action = {"1": "approve_plan", "2": "revise_plan",
                      "3": "reunderstand"}.get(choice, "approve_plan")
            note = ""
            if action == "revise_plan":
                note = input("改方案说明: ").strip()
            return Decision(action=action, record_id=record_id, stage=stage,
                            input_text=note)

        raise ValueError(f"unknown stage: {stage}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cli_router.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/adapters/router.py tests/test_cli_router.py
git commit -m "feat: add EventRouter interface and CliRouter"
```

---

### Task 10: 澄清编排（ClarifyOrchestrator）

澄清阶段：用引擎对目标项目做只读预判（涉及模块 + 风险），产出供 Notifier 展示的两个列表；并在收到澄清回答后判断是否可立项。引擎调用复用 Task 5 的 `run_engine`。为可测试，预判逻辑接受一个可注入的 `predict_fn`（默认走引擎），测试用假函数。

**Files:**
- Create: `src/autocoder/core/clarify.py`
- Test: `tests/test_clarify.py`

- [ ] **Step 1: 写失败测试 `tests/test_clarify.py`**

```python
from autocoder.core.clarify import ClarifyOrchestrator, Prediction


def test_predict_uses_injected_fn():
    fake = lambda desc, project_path: Prediction(
        modules=["auth.py", "session.py"], risks=["会话兼容性"])
    orch = ClarifyOrchestrator(predict_fn=fake)
    pred = orch.predict("加登录", "/tmp/proj")
    assert pred.modules == ["auth.py", "session.py"]
    assert pred.risks == ["会话兼容性"]


def test_ready_to_charter_when_form_filled():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    form = {"scope": "只改前端", "acceptance": "能登录",
            "constraints": "无", "modules": "auth", "risk_reply": "无"}
    assert orch.ready_to_charter(form, round_no=1) is True


def test_ready_to_charter_forced_at_round_3():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    assert orch.ready_to_charter({}, round_no=3) is True


def test_not_ready_when_empty_form_early_round():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 多维度留空且非末轮且需求非自描述 → 再问一轮
    assert orch.ready_to_charter({"scope": "", "acceptance": ""}, round_no=1) is False


def test_empty_form_but_trivial_task_charters_round_1():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 无 _form 但需求自描述（短、单文件文档改动）→ 立即立项
    assert orch.ready_to_charter(None, round_no=1, trivial=True) is True


def test_synthesize_statement():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    form = {"scope": "只改前端登录页", "acceptance": "点击能跳转"}
    stmt = orch.synthesize("加登录按钮", form)
    assert "加登录按钮" in stmt
    assert "只改前端登录页" in stmt
    assert "点击能跳转" in stmt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_clarify.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/clarify.py`**

```python
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Prediction:
    modules: list
    risks: list


# 需求自描述时，留空的澄清回答不应再追问。这些维度若有实质内容即视为已覆盖。
_KEY_DIMENSIONS = ["scope", "acceptance"]


class ClarifyOrchestrator:
    def __init__(self, predict_fn: Callable[[str, str], Prediction]):
        self._predict_fn = predict_fn

    def predict(self, description: str, project_path: str) -> Prediction:
        return self._predict_fn(description, project_path)

    def ready_to_charter(self, form: Optional[dict], round_no: int,
                         trivial: bool = False) -> bool:
        if round_no >= 3:
            return True
        if trivial:
            return True
        if not form:
            return False
        # 关键维度都有实质内容即可立项
        return all(form.get(k, "").strip() for k in _KEY_DIMENSIONS)

    def synthesize(self, description: str, form: dict) -> str:
        lines = [f"## 需求\n{description}", ""]
        labels = {
            "scope": "范围边界", "modules": "涉及模块", "acceptance": "验收标准",
            "constraints": "优先级/约束", "risk_reply": "风险回应",
        }
        for key, label in labels.items():
            val = (form or {}).get(key, "").strip()
            if val:
                lines.append(f"- **{label}**: {val}")
        return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_clarify.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/core/clarify.py tests/test_clarify.py
git commit -m "feat: add clarify orchestrator"
```

---

### Task 11: 规划编排（Planner）

规划阶段：在 worktree 内创建 `specs/NNN-xxx/` 目录，按 spec-kit 约定产出 `spec.md`/`plan.md`/`tasks.md`。本项目里规划由编码引擎完成（给引擎一个"按 spec-kit 产出规格文件"的 prompt），因此 Planner 的纯逻辑部分是：算下一个 spec 目录编号、统计 tasks 数、写 meta。引擎调用本身复用 Task 5，端到端在 orchestrator 串联。

**Files:**
- Create: `src/autocoder/core/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: 写失败测试 `tests/test_planner.py`**

```python
from pathlib import Path
from autocoder.core.planner import next_spec_dir, count_tasks, slugify


def test_next_spec_dir_empty(tmp_path):
    assert next_spec_dir(str(tmp_path)) == "specs/001-feature"


def test_next_spec_dir_increments(tmp_path):
    (tmp_path / "specs" / "001-foo").mkdir(parents=True)
    (tmp_path / "specs" / "002-bar").mkdir(parents=True)
    assert next_spec_dir(str(tmp_path)) == "specs/003-feature"


def test_next_spec_dir_with_slug(tmp_path):
    assert next_spec_dir(str(tmp_path), "加登录按钮") == "specs/001-加登录按钮"


def test_count_tasks(tmp_path):
    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text(
        "# Tasks\n\n"
        "- [ ] T001 第一项\n"
        "- [ ] T002 第二项\n"
        "- [ ] T003 第三项\n"
    )
    assert count_tasks(str(tasks_md)) == 3


def test_count_tasks_missing_file(tmp_path):
    assert count_tasks(str(tmp_path / "nope.md")) == 0


def test_slugify():
    assert slugify("加登录按钮") == "加登录按钮"
    assert slugify("Add Login Button") == "add-login-button"
    assert slugify("") == "feature"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_planner.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/planner.py`**

```python
import re
from pathlib import Path


def slugify(title: str) -> str:
    if not title:
        return "feature"
    s = title.strip().lower()
    # 英文转 kebab；非 ASCII（中文）原样保留
    if re.fullmatch(r"[\x00-\x7f]+", s):
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s or "feature"
    return title.strip()


def next_spec_dir(worktree_path: str, title: str = "") -> str:
    specs = Path(worktree_path) / "specs"
    max_n = 0
    if specs.exists():
        for d in specs.iterdir():
            m = re.match(r"(\d{3})-", d.name)
            if d.is_dir() and m:
                max_n = max(max_n, int(m.group(1)))
    n = f"{max_n + 1:03d}"
    return f"specs/{n}-{slugify(title)}"


def count_tasks(tasks_md_path: str) -> int:
    p = Path(tasks_md_path)
    if not p.exists():
        return 0
    return sum(
        1 for line in p.read_text().splitlines()
        if re.match(r"\s*- \[[ x]\] ", line)
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_planner.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/autocoder/core/planner.py tests/test_planner.py
git commit -m "feat: add planner spec-dir numbering and task counting"
```

---

### Task 12: Orchestrator（dispatch + execute 主流程）

把所有部件串成两条主流程：
- **dispatch_one**：拉一条 pending → 澄清循环（预判→发卡→等决策→判定立项）→ 立项（建 worktree）→ 规划（引擎产出 spec/plan/tasks + 写 meta）→ 方案审批 → 批准则触发 execute。
- **execute**：record 级锁 → 跑引擎 → 测试 → 构建 → commit → push → 完成/失败通知 + 状态落库。

为可测，Orchestrator 接受注入的 store/notifier/router 以及 `engine_runner`、`worktree` 模块（默认用真实模块，测试传 fake）。execute 的 git/push 用可注入的 `git_ops` 以便测试。

**Files:**
- Create: `src/autocoder/core/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: 写失败测试 `tests/test_orchestrator.py`**

```python
import threading
from pathlib import Path

from autocoder.core.orchestrator import Orchestrator
from autocoder.core.engine_runner import EngineResult
from autocoder.adapters.store import JsonTaskStore
from autocoder.models import Task, Decision


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send_clarify(self, *a, **k): self.calls.append("clarify")
    def send_charter(self, *a, **k): self.calls.append("charter")
    def send_plan(self, *a, **k): self.calls.append("plan")
    def send_complete(self, *a, **k): self.calls.append("complete")
    def send_failure(self, *a, **k): self.calls.append("failure")


class ScriptedRouter:
    """按 stage 返回预设决策。"""
    def __init__(self, decisions: dict):
        self._d = decisions

    def await_decision(self, record_id, stage):
        return self._d[stage]


def _config(tmp_path, project_path):
    class Cfg:
        workspace_dir = str(tmp_path / "ws")
        concurrency_limit = 3
        default_engine = "fake"
        projects = {"demo": {
            "path": str(project_path), "engine": "fake",
            "match_keywords": ["demo"], "base_branch": "main",
            "test_command": "true", "build_command": "true",
        }}
        engines = {"fake": {"command": "true", "args": [], "timeout": 5, "env": {}}}
        feishu = {}
        def engine_spec(self, name): return self.engines[name]
        def match_project(self, text):
            return "demo" if "demo" in text else None
    return Cfg()


def test_dispatch_happy_path_to_planning(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="重要紧急"))
    notifier = FakeNotifier()

    router = ScriptedRouter({
        "clarify": Decision("clarify_submit", "r1", "clarify",
                            form={"scope": "明确范围", "acceptance": "明确验收"}),
        "charter": Decision("reject_charter", "r1", "charter"),  # 立项卡选「拒」提前收尾
    })

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: str(tmp_path / "wt")),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    orch = Orchestrator(cfg, store, notifier, router,
                        worktree=fake_wt,
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction(["m.py"], ["风险"]))
    orch.dispatch_one()

    assert "clarify" in notifier.calls
    assert "charter" in notifier.calls
    assert store.get("r1").status == "已搁置"  # reject_charter → 已搁置


def test_dispatch_no_pending_is_noop(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        predict_fn=lambda d, p: None)
    orch.dispatch_one()  # 不抛异常
    assert notifier.calls == []


def test_execute_runs_engine_and_completes(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001 做事\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="做事", engine="fake",
                   base_branch="main", spec_dir="specs/001-x",
                   status="进行中"))
    notifier = FakeNotifier()

    fake_git = type("G", (), {
        "add_commit_push": staticmethod(lambda wt, msg, br: (True, "1 file changed")),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert "complete" in notifier.calls
    assert store.get("r1").status == "已完成"


def test_execute_engine_failure_sets_stalled(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="t", engine="fake",
                   base_branch="main", spec_dir="specs/001-x", status="进行中"))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        engine_run=lambda *a: EngineResult.FAILURE,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")
    assert "failure" in notifier.calls
    assert store.get("r1").status == "已停滞"


def test_execute_record_lock_blocks_duplicate(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status="进行中"))
    orch = Orchestrator(cfg, store, FakeNotifier(), ScriptedRouter({}))
    # 手动占锁
    assert orch._acquire_lock("r1") is True
    assert orch._acquire_lock("r1") is False  # 第二次拿不到
    orch._release_lock("r1")
    assert orch._acquire_lock("r1") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/core/orchestrator.py`**

```python
import time
from datetime import datetime
from pathlib import Path

from autocoder.core import state_machine as sm
from autocoder.core import worktree as wt_mod
from autocoder.core import planner
from autocoder.core.clarify import ClarifyOrchestrator
from autocoder.core.engine_runner import run_engine, run_command, EngineResult


def _default_engine_run(spec, working_dir, prompt, log_file):
    return run_engine(spec, working_dir, prompt, log_file)


class _DefaultGit:
    @staticmethod
    def add_commit_push(worktree_path, commit_msg, branch):
        import subprocess
        subprocess.run(["git", "-C", worktree_path, "add", "-A"], check=False)
        stat = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--cached", "--stat"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()
        change_stats = stat[-1] if stat else ""
        subprocess.run(["git", "-C", worktree_path, "commit", "-m", commit_msg],
                       capture_output=True, text=True)
        push = subprocess.run(
            ["git", "-C", worktree_path, "push", "-u", "origin", branch],
            capture_output=True, text=True,
        )
        return push.returncode == 0, change_stats


class Orchestrator:
    def __init__(self, config, store, notifier, router,
                 worktree=wt_mod, git_ops=None, engine_run=None,
                 predict_fn=None, resolve_worktree=None):
        self.cfg = config
        self.store = store
        self.notifier = notifier
        self.router = router
        self.wt = worktree
        self.git = git_ops or _DefaultGit
        self.engine_run = engine_run or _default_engine_run
        self.clarify = ClarifyOrchestrator(
            predict_fn=predict_fn or self._engine_predict)
        self._resolve_worktree = resolve_worktree
        self._lock_root = Path(config.workspace_dir) / ".locks"

    # ---- locking (record-level, atomic mkdir) -------------------------
    def _lock_path(self, record_id):
        return self._lock_root / f"{record_id}.lock"

    def _acquire_lock(self, record_id) -> bool:
        self._lock_root.mkdir(parents=True, exist_ok=True)
        p = self._lock_path(record_id)
        try:
            p.mkdir()
            return True
        except FileExistsError:
            age = time.time() - p.stat().st_mtime
            if age < 1800:
                return False
            # stale, reclaim
            p.rmdir()
            p.mkdir()
            return True

    def _release_lock(self, record_id):
        p = self._lock_path(record_id)
        if p.exists():
            p.rmdir()

    # ---- prediction via engine (default) ------------------------------
    def _engine_predict(self, description, project_path):
        from autocoder.core.clarify import Prediction
        # 默认实现：不深入跑引擎做结构化预判（留给飞书/agent 场景增强），
        # CLI 默认给空预判，由用户在澄清回答里补全。
        return Prediction([], [])

    def _set_status(self, record_id, to):
        cur = self.store.get(record_id).status
        if sm.can_transition(cur, to):
            self.store.update_status(record_id, to)
        else:
            raise RuntimeError(f"非法状态转移 {cur} -> {to}")

    # ---- dispatch -----------------------------------------------------
    def dispatch_one(self):
        pending = self.store.fetch_pending()
        if not pending:
            return
        task = pending[0]
        rid = task.record_id
        self._set_status(rid, "澄清中")

        project_key = self.cfg.match_project(task.description)
        project_path = (self.cfg.projects[project_key]["path"]
                        if project_key else "")

        round_no = 1
        while True:
            pred = self.clarify.predict(task.description, project_path)
            self.notifier.send_clarify(task, pred.modules, pred.risks, round_no)
            decision = self.router.await_decision(rid, "clarify")
            trivial = not decision.form
            if self.clarify.ready_to_charter(decision.form, round_no, trivial):
                break
            if round_no >= 3:
                break
            round_no += 1

        summary = self.clarify.synthesize(task.description, decision.form)
        self._set_status(rid, "待立项")
        self.notifier.send_charter(task, summary)

        charter = self.router.await_decision(rid, "charter")
        if charter.action == "reject_charter":
            self._set_status(rid, "已搁置")
            return
        if charter.action in ("revise_charter", "rechat"):
            self._set_status(rid, "澄清中")
            return  # CLI 模式下交回用户重新触发；飞书模式由回调继续
        # approve_charter：建 worktree，进规划
        if not project_key:
            self.notifier.send_failure(task, "立项", "无法匹配目标项目", "", "")
            return
        proj = self.cfg.projects[project_key]
        wtpath = self.wt.create(proj["path"], proj["base_branch"], rid)
        self._set_status(rid, "规划中")
        self._plan(task, project_key, proj, wtpath, summary)

        # 方案审批
        plan_decision = self.router.await_decision(rid, "plan")
        if plan_decision.action == "approve_plan":
            active = self.wt.active_count(proj["path"])
            if self.wt.gate_open(active, self.cfg.concurrency_limit):
                self.store.update_status(rid, "进行中") if sm.can_transition(
                    self.store.get(rid).status, "进行中") else None
                self.execute(rid)
            else:
                self.store.set_queue_position(rid, active)
        elif plan_decision.action == "revise_plan":
            self._set_status(rid, "规划中")
        elif plan_decision.action == "reunderstand":
            self.wt.remove(proj["path"], rid)
            self._set_status(rid, "澄清中")

    def _plan(self, task, project_key, proj, worktree_path, requirement):
        spec_dir = planner.next_spec_dir(worktree_path, task.task_title or "")
        Path(worktree_path, spec_dir).mkdir(parents=True, exist_ok=True)
        engine_name = proj.get("engine", self.cfg.default_engine)
        spec = self.cfg.engine_spec(engine_name)
        prompt = (f"按 spec-kit 约定，在 {spec_dir}/ 下产出 spec.md、plan.md、tasks.md。\n"
                  f"需求：\n{requirement}\n严格按需求范围，不额外加功能。")
        log = str(Path(self.cfg.workspace_dir) / task.record_id / "plan.log")
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        self.engine_run(spec, worktree_path, prompt, log)
        tasks_md = str(Path(worktree_path, spec_dir, "tasks.md"))
        n = planner.count_tasks(tasks_md)
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 写回 task 元数据
        t = self.store.get(task.record_id)
        t.project = project_key
        t.engine = engine_name
        t.spec_dir = spec_dir
        t.base_branch = proj["base_branch"]
        t.plan_generated_at = now
        self.store._save(t)
        self.store.set_clarify_pointer(task.record_id, f"{spec_dir}/clarify.md")
        self._set_status(task.record_id, "待审批")
        self.notifier.send_plan(t, requirement, n, t.branch_name())

    # ---- execute ------------------------------------------------------
    def execute(self, record_id):
        if not self._acquire_lock(record_id):
            return
        try:
            self._execute_inner(record_id)
        finally:
            self._release_lock(record_id)

    def _resolve_wt(self, task):
        if self._resolve_worktree:
            return self._resolve_worktree(task)
        proj = self.cfg.projects[task.project]
        return str(Path(proj["path"]) / ".worktrees" / task.branch_name())

    def _execute_inner(self, record_id):
        task = self.store.get(record_id)
        start = time.time()
        wtpath = self._resolve_wt(task)
        spec = self.cfg.engine_spec(task.engine)
        log = str(Path(self.cfg.workspace_dir) / record_id / "execute.log")
        Path(log).parent.mkdir(parents=True, exist_ok=True)

        tasks_md = str(Path(wtpath, task.spec_dir, "tasks.md"))
        prompt = (f"按任务清单逐条实现，完成后通过测试。\n"
                  f"{Path(tasks_md).read_text() if Path(tasks_md).exists() else ''}")
        result = self.engine_run(spec, wtpath, prompt, log)
        if result != EngineResult.SUCCESS:
            stage = "编码超时" if result == EngineResult.TIMEOUT else "编码"
            self.notifier.send_failure(task, stage, "引擎执行失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return

        proj = self.cfg.projects.get(task.project, {})
        if not run_command(proj.get("test_command", ""), wtpath):
            self.notifier.send_failure(task, "测试", "测试失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return
        if not run_command(proj.get("build_command", ""), wtpath):
            self.notifier.send_failure(task, "构建", "构建失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return

        branch = task.branch_name()
        commit_msg = f"feat: {task.task_title}\n\nAuto-generated by auto-coder\nTask: {record_id}"
        pushed, change_stats = self.git.add_commit_push(wtpath, commit_msg, branch)
        if not pushed:
            self.notifier.send_failure(task, "推送", "push 失败", log, branch)
            self.store.update_status(record_id, "已停滞")
            return

        duration = f"{int((time.time() - start) // 60)} 分钟"
        timeline = f"{task.plan_generated_at or ''} 规划 → {datetime.now():%m-%d %H:%M} 已推送"
        self.store.complete(record_id, branch, "已完成并推送分支", timeline)
        self.notifier.send_complete(task, branch, change_stats, duration, timeline)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `pytest tests/ -v`
Expected: PASS（全部通过）

- [ ] **Step 6: Commit**

```bash
git add src/autocoder/core/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add orchestrator wiring dispatch and execute flows"
```

---

### Task 13: CLI 入口（dispatch / execute / status / add）

**Files:**
- Create: `src/autocoder/cli.py`
- Create: `src/autocoder/factory.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试 `tests/test_cli.py`**

```python
import json
from pathlib import Path
from autocoder.cli import main


SAMPLE_CFG = """
adapters: {store: json, notifier: cli, router: cli}
workspace_dir: {ws}
concurrency: {{limit: 3}}
projects:
  demo:
    path: /tmp/demo
    engine: claude-code
    match_keywords: ["demo"]
    base_branch: main
    test_command: "true"
    build_command: "true"
engines:
  claude-code: {{command: claude, args: [], timeout: 1800, env: {{}}}}
  default: claude-code
clarify_dimensions: []
"""


def _cfg(tmp_path):
    ws = tmp_path / "ws"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CFG.format(ws=str(ws)))
    return str(cfg), str(ws)


def test_add_creates_task(tmp_path, capsys):
    cfg_path, ws = _cfg(tmp_path)
    rc = main(["add", "给 demo 加功能", "--priority", "重要紧急",
               "--config", cfg_path])
    assert rc == 0
    files = list(Path(ws).glob("*/task.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["description"] == "给 demo 加功能"
    assert data["status"] == "待开始"


def test_status_lists_tasks(tmp_path, capsys):
    cfg_path, ws = _cfg(tmp_path)
    main(["add", "需求A", "--config", cfg_path])
    capsys.readouterr()
    rc = main(["status", "--config", cfg_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "需求A" in out
    assert "待开始" in out


def test_unknown_command_returns_error(tmp_path):
    cfg_path, _ = _cfg(tmp_path)
    rc = main(["frobnicate", "--config", cfg_path])
    assert rc != 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/factory.py`（按 config 装配 adapters）**

```python
from autocoder.config import load_config
from autocoder.adapters.store import JsonTaskStore
from autocoder.adapters.notifier import CliNotifier
from autocoder.adapters.router import CliRouter


def build(config_path: str):
    cfg = load_config(config_path)
    store = _build_store(cfg)
    notifier = _build_notifier(cfg)
    router = _build_router(cfg)
    return cfg, store, notifier, router


def _build_store(cfg):
    kind = cfg.adapters.get("store", "json")
    if kind == "json":
        return JsonTaskStore(cfg.workspace_dir)
    if kind == "feishu":
        from autocoder.adapters.feishu.store import FeishuBaseStore
        return FeishuBaseStore(cfg.feishu)
    raise ValueError(f"unknown store adapter: {kind}")


def _build_notifier(cfg):
    kind = cfg.adapters.get("notifier", "cli")
    if kind == "cli":
        return CliNotifier()
    if kind == "feishu":
        from autocoder.adapters.feishu.notifier import FeishuCardNotifier
        return FeishuCardNotifier(cfg.feishu)
    raise ValueError(f"unknown notifier adapter: {kind}")


def _build_router(cfg):
    kind = cfg.adapters.get("router", "cli")
    if kind == "cli":
        return CliRouter()
    if kind == "feishu_webhook":
        from autocoder.adapters.feishu.webhook import FeishuWebhookRouter
        return FeishuWebhookRouter(cfg.feishu)
    raise ValueError(f"unknown router adapter: {kind}")
```

- [ ] **Step 4: 实现 `src/autocoder/cli.py`**

```python
import argparse
import sys
import uuid

from autocoder.factory import build
from autocoder.core.orchestrator import Orchestrator
from autocoder.models import Task


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="auto-coder")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="新增需求")
    p_add.add_argument("description")
    p_add.add_argument("--priority", default="重要不紧急")

    sub.add_parser("dispatch", help="拉取并处理一条 pending 需求")

    p_exec = sub.add_parser("execute", help="执行指定需求")
    p_exec.add_argument("record_id")

    sub.add_parser("status", help="列出所有需求状态")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 1

    cfg, store, notifier, router = build(args.config)

    if args.cmd == "add":
        rid = uuid.uuid4().hex[:12]
        store.add(Task(record_id=rid, description=args.description,
                       priority=args.priority))
        print(f"已添加需求 {rid}: {args.description}")
        return 0

    if args.cmd == "dispatch":
        Orchestrator(cfg, store, notifier, router).dispatch_one()
        return 0

    if args.cmd == "execute":
        Orchestrator(cfg, store, notifier, router).execute(args.record_id)
        return 0

    if args.cmd == "status":
        for t in store.fetch_pending() or []:
            print(f"[{t.status}] {t.record_id}  {t.description}  ({t.priority})")
        # fetch_pending 只列待开始；status 应列全部
        if hasattr(store, "_all"):
            for t in store._all():
                if t.status != "待开始":
                    print(f"[{t.status}] {t.record_id}  {t.description}")
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_cli.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add src/autocoder/cli.py src/autocoder/factory.py tests/test_cli.py
git commit -m "feat: add CLI entrypoint and adapter factory"
```

---

### Task 14: 飞书 adapter（可选实现）

封装原项目的飞书逻辑为三个可选 adapter。**网络调用全部用 mock 测试，不打真实飞书。** webhook 路由仅做最小骨架（接收回调→投递给等待中的编排器），标注为进阶用法。

**Files:**
- Create: `src/autocoder/adapters/feishu/__init__.py`
- Create: `src/autocoder/adapters/feishu/client.py`
- Create: `src/autocoder/adapters/feishu/notifier.py`
- Create: `src/autocoder/adapters/feishu/store.py`
- Create: `src/autocoder/adapters/feishu/webhook.py`
- Copy: `templates/cards/*.json`（从原项目模板脱敏后复制）
- Test: `tests/test_feishu_notifier.py`

- [ ] **Step 1: 写失败测试 `tests/test_feishu_notifier.py`**

```python
from autocoder.adapters.feishu.client import render_card


def test_render_card_substitutes_tokens(tmp_path):
    tpl = tmp_path / "clarify.json"
    tpl.write_text('{"title": "{{TASK_TITLE}}", "round": "{{ROUND}}"}')
    out = render_card(str(tpl), TASK_TITLE="加登录", ROUND="1")
    assert '"title": "加登录"' in out
    assert '"round": "1"' in out


def test_render_card_escapes_newlines(tmp_path):
    tpl = tmp_path / "c.json"
    tpl.write_text('{"body": "{{SUMMARY}}"}')
    out = render_card(str(tpl), SUMMARY="第一行\n第二行")
    # 换行被 JSON 转义，渲染结果仍是合法 JSON
    import json
    json.loads(out)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_feishu_notifier.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/autocoder/adapters/feishu/__init__.py`（空）**

```python
```

- [ ] **Step 4: 实现 `src/autocoder/adapters/feishu/client.py`**

```python
import json
import os
import time
from pathlib import Path

import requests

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"


def render_card(template_path: str, **tokens) -> str:
    """读卡片模板，替换 {{TOKEN}}。值做 JSON 转义以免换行/引号破坏卡片。"""
    content = Path(template_path).read_text()
    for key, val in tokens.items():
        escaped = json.dumps(str(val), ensure_ascii=False)[1:-1]
        content = content.replace("{{" + key + "}}", escaped)
    return content


class FeishuClient:
    """用 hermes 网关同款 app 凭证发交互卡片。凭证从环境变量读，不硬编码。

    SAME-APP 规则：交互卡片按钮回调只会回到「发卡的那个 app」。若用别的
    app 发卡，所有按钮点击都会失联。务必用接收事件的同一 app 凭证。
    """

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET")
        self._token = None
        self._token_exp = 0

    def _tenant_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp:
            return self._token
        resp = requests.post(_TOKEN_URL, json={
            "app_id": self.app_id, "app_secret": self.app_secret})
        data = resp.json()
        self._token = data["tenant_access_token"]
        self._token_exp = now + 6000  # ~100min
        return self._token

    def send_card(self, chat_id: str, card_json: str) -> bool:
        token = self._tenant_token()
        resp = requests.post(_MSG_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "interactive",
                  "content": card_json})
        return resp.json().get("code") == 0
```

- [ ] **Step 5: 实现 `src/autocoder/adapters/feishu/notifier.py`**

```python
from pathlib import Path

from autocoder.adapters.notifier import Notifier
from autocoder.adapters.feishu.client import FeishuClient, render_card

_TPL_DIR = Path(__file__).resolve().parents[3] / "templates" / "cards"


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
                   TASK_COUNT=task_count, BRANCH_NAME=branch)

    def send_complete(self, task, branch, change_stats, duration, timeline):
        self._send("complete", TASK_TITLE=task.task_title or task.description,
                   PR_URL=branch, CHANGE_STATS=change_stats,
                   DURATION=duration, TIMELINE=timeline)

    def send_failure(self, task, stage, error, log_path, branch):
        self._send("failure", TASK_TITLE=task.task_title or task.description,
                   FAIL_STAGE=stage, ERROR_SUMMARY=error,
                   LOG_PATH=log_path, BRANCH_NAME=branch)
```

- [ ] **Step 6: 实现 `src/autocoder/adapters/feishu/store.py`（封装 lark-cli，骨架 + 文档说明）**

```python
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
```

- [ ] **Step 7: 实现 `src/autocoder/adapters/feishu/webhook.py`（最小骨架）**

```python
import queue

from autocoder.adapters.router import EventRouter
from autocoder.models import Decision


class FeishuWebhookRouter(EventRouter):
    """进阶用法：替代 hermes 网关，用一个常驻 HTTP 服务接住飞书卡片回调。

    原系统里按钮点击经飞书事件订阅回到网关，网关合成命令调起 agent。开源
    场景无网关，需自建：跑 serve() 起 FastAPI 接 /feishu/callback，把
    回调里的 ac_action/record_id/_form 投进队列；await_decision 阻塞取队列。

    NOTE: 这要求 dispatch 以常驻服务模式运行（非一次性 CLI 调用）。
    """

    def __init__(self, feishu_config: dict):
        self._queues: dict[str, queue.Queue] = {}

    def _q(self, record_id: str) -> queue.Queue:
        return self._queues.setdefault(record_id, queue.Queue())

    def deliver(self, record_id: str, decision: Decision):
        self._q(record_id).put(decision)

    def await_decision(self, record_id: str, stage: str) -> Decision:
        return self._q(record_id).get()

    def serve(self, host="0.0.0.0", port=8000):  # pragma: no cover
        from fastapi import FastAPI, Request
        import uvicorn
        app = FastAPI()

        @app.post("/feishu/callback")
        async def callback(req: Request):
            body = await req.json()
            value = body.get("action", {}).get("value", {})
            self.deliver(value.get("record_id", ""), Decision(
                action=value.get("ac_action", ""),
                record_id=value.get("record_id", ""),
                stage=value.get("stage", ""),
                form=body.get("action", {}).get("form_value", {})))
            return {"code": 0}

        uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 8: 复制并脱敏卡片模板**

从原项目 `~/.hermes/skills/auto-coder/templates/cards/*.json` 复制到
`templates/cards/`。这些模板**本身不含敏感数据**（只有 `{{TOKEN}}` 占位和
`"_skill": "auto-coder-agent"` 路由键）。逐一检查无硬编码 chat_id/token 后提交。

Run: `cp ~/.hermes/skills/auto-coder/templates/cards/*.json templates/cards/ && grep -rn "oc_\|ZveEb\|tblg\|cli_a" templates/cards/ || echo "无敏感残留"`
Expected: 输出「无敏感残留」（若有命中则手动改成占位）

- [ ] **Step 9: 运行测试确认通过**

Run: `pytest tests/test_feishu_notifier.py -v`
Expected: PASS（2 passed）

- [ ] **Step 10: Commit**

```bash
git add src/autocoder/adapters/feishu/ templates/cards/ tests/test_feishu_notifier.py
git commit -m "feat: add optional Feishu adapters (notifier/store/webhook)"
```

---

### Task 15: README（中文使用说明）

**Files:**
- Create: `README.md`

- [ ] **Step 1: 写 `README.md`**

````markdown
# auto-coder-oss

Agent 驱动的自动化编码工作流：把"一句话需求"推进到"一个可审查的代码分支"。

```
拉取需求 → 澄清对话 → 立项 → spec-kit 规划 → 人工审批 → 隔离 worktree 执行引擎 → 推分支
```

## 核心特性

- **澄清先行**：编码前先把范围/模块/验收/约束/风险问清楚（≤3 轮）
- **多需求并行**：每个需求独占一个 git worktree（`feature/auto-<id>`），互不干扰
- **人工卡点**：立项、方案两道人工审批闸
- **可插拔架构**：TaskStore / Notifier / EventRouter 三个接口，默认零外部依赖（本地 JSON + 终端交互），飞书为可选 adapter

## 快速开始

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml   # 编辑：填入你的项目路径、引擎、关键词
auto-coder add "给项目加一个登录按钮" --priority 重要紧急
auto-coder dispatch                  # 终端交互：澄清 → 立项 → 规划 → 审批
auto-coder execute <record_id>       # 引擎编码 → 测试 → 构建 → 提交 → 推分支
auto-coder status                    # 查看所有需求状态
```

## 配置

见 `config.example.yaml`。关键项：

- `adapters`：选 store/notifier/router 实现（默认全 cli/json）
- `projects`：每个受管项目的路径、引擎、匹配关键词、base 分支、测试/构建命令
- `engines`：编码引擎命令（如 `claude --print`）、超时、模型环境变量

敏感凭证（飞书 app）放 `.env`（见 `.env.example`），不入仓库。

## 状态机

```
待开始 → 澄清中 → 待立项 → 规划中 → 待审批 → 进行中 → 已完成
                    ↘ 已搁置（驳回）          ↘ 已停滞（失败留现场）
```

## 架构

```
核心（状态机/worktree/引擎调度/规划）── 通过三接口与 I/O 解耦
  ├─ TaskStore   任务存取   默认 JsonTaskStore │ 可选 FeishuBaseStore
  ├─ Notifier    出站 UI    默认 CliNotifier   │ 可选 FeishuCardNotifier
  └─ EventRouter 入站决策   默认 CliRouter     │ 可选 FeishuWebhookRouter
```

## 飞书 adapter（进阶，可选）

默认 CLI 模式在单进程内同步跑通闭环，无需任何外部服务。

若要用飞书交互卡片做 UI：
1. `pip install -e ".[feishu]"`
2. 在飞书开放平台创建企业自建应用，填 `.env`（`FEISHU_APP_ID/SECRET`）
3. config.yaml 设 `adapters.notifier: feishu`、`store: feishu`，填 `feishu.base_token/table_id/notify_chat_id/field_ids`
4. **入站回调**：飞书卡片按钮回调需要一个常驻服务接收（替代私有网关）。用 `FeishuWebhookRouter.serve()` 起一个 FastAPI 服务，并在飞书后台配置事件回调地址。

> ⚠️ **SAME-APP 规则**：交互卡片按钮回调只会回到「发卡的那个 app」。发卡与接收回调必须是同一个飞书 app，否则所有按钮点击失联。
> `FeishuBaseStore.fetch_pending/get` 是骨架，需按你的多维表格字段结构补全（字段 ID 配在 `feishu.field_ids`）。

## 引擎注意事项

- 引擎以子进程调用，`stdin` 强制为 `DEVNULL`——否则 `claude --print` 会继承开放管道、等永不到来的 EOF 而挂死。
- 引擎超时默认 1800s，可在 `engines.<name>.timeout` 调整。
- 同一需求并发触发由 record 级文件锁拦截。

## 开发

```bash
pytest tests/ -v
```

## License

MIT
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Chinese README"
```

---

## 最终自查（覆盖全 15 任务）

**1. Spec 覆盖：**
- §3 状态机 → Task 3 ✅
- §4.1 TaskStore → Task 7（接口+Json）、Task 14（飞书）✅
- §4.2 Notifier → Task 8（接口+Cli）、Task 14（飞书）✅
- §4.3 EventRouter → Task 9（接口+Cli）、Task 14（webhook）✅
- §5 core 模块 → Task 4（worktree）、5（engine）、10（clarify）、11（planner）、12（orchestrator）✅
- §6 配置 → Task 6 ✅
- §7 目录结构 → Task 1 骨架 + 各任务 ✅
- §8 CLI 闭环 → Task 13 ✅
- §9 测试策略 → 每个任务含 TDD ✅
- README → Task 15 ✅

**2. 占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码。`config.example.yaml`/`.env.example`/卡片模板全为占位符，无真实 token/路径。✅

**3. 类型一致性：**
- `Task` 字段（record_id/description/priority/status/project/engine/spec_dir/...）在 models、store、orchestrator、cli 间一致 ✅
- `Decision(action, record_id, stage, form, input_text)` 在 router、orchestrator 间一致 ✅
- `run_engine(spec, working_dir, prompt, log_file) -> EngineResult` 在 engine_runner、orchestrator 间一致 ✅
- `Notifier` 五个 send_* 签名在接口、Cli、Feishu 三处一致 ✅
- `worktree.create/remove/active_count/gate_open` 在 worktree、orchestrator、测试间一致 ✅
- 状态字符串字面量全程用中文，与状态机转移表一致 ✅

**脱敏确认：** 全程使用泛化占位符；唯一从原项目复制的是卡片模板（Task 14 Step 8 含 grep 校验无敏感残留）。`.gitignore` 屏蔽 config.yaml/.env/workspace。
