# auto-coder-oss 设计文档

**日期**：2026-06-02
**状态**：已批准架构，待 spec 评审

## 1. 目标

一个 agent 驱动的自动化编码工作流，把"一句话需求"推进到"一个可审查的代码分支"：

```
拉取需求 → 澄清对话 → 立项 → spec-kit 规划 → 人工审批 → 隔离 worktree 执行引擎 → 推分支
```

核心价值：**澄清先行**（编码前先把范围/模块/验收/约束/风险问清楚）、**多需求并行**（每个需求独占一个 git worktree，互不干扰）、**人工卡点**（立项与方案两道人工审批闸）。

本项目是某私有 hermes/飞书工作流的开源移植版：剥离所有私有依赖与敏感数据，用 Python 重写，提供开箱即用的 CLI 体验，飞书作为可选 adapter。

## 2. 设计原则

- **核心逻辑与 I/O 边界解耦**：状态机、worktree、引擎调度、规划编排是纯核心，不知道任务存在哪、通知发到哪、按钮点击从哪来。
- **三个可插拔接口**：`TaskStore`（任务存取）、`Notifier`（出站 UI）、`EventRouter`（入站决策→恢复推进）。每个接口都有一个零外部依赖的默认实现，飞书实现是可选项。
- **开箱即跑**：默认配置（JSON 存储 + CLI 通知 + CLI 路由）不依赖任何外部服务，clone 下来配好项目路径即可跑通完整闭环。
- **配置外置**：所有环境相关值（路径、token、chat_id、模型名）在 `config.yaml` / `.env`，仓库只含 `.example` 模板。

## 3. 状态机

每个需求（一条任务记录）走 7 状态：

```
待开始 → 澄清中 → 待立项 → 规划中 → 待审批 → 进行中 → 已完成
                    ↘ 已搁置（驳回）          ↘ 已停滞（失败留现场）
```

合法转移表（移植自原 `state-machine.sh`，纯函数、无 I/O）：

```
待开始 → 澄清中
澄清中 → 待立项 | 澄清中（再问一轮）
待立项 → 规划中 | 澄清中 | 已搁置
已搁置 → 待开始
规划中 → 待审批
待审批 → 进行中 | 规划中 | 澄清中
进行中 → 已完成 | 已停滞
```

回滚规则：
- `reject_charter` → 已搁置（此时还没 worktree，无副作用）
- `revise_charter` / `rechat` → 澄清中（不动 git）
- `revise_plan` → 规划中（**保留** worktree）
- `reunderstand` → 澄清中（**删除** worktree，从头澄清）

状态权威：任何转移前必须问状态机 `can_transition(from, to)`，不允许就拒绝并记日志。

## 4. 架构

```
            ┌─────────────────────────────────────────┐
            │              核心 (portable)              │
            │  StateMachine · Worktree · EngineRunner   │
            │  Planner · ClarifyOrchestrator            │
            │  ConcurrencyGate · Orchestrator           │
            └───────┬───────────┬───────────┬──────────┘
                    │           │           │
         ┌──────────▼──┐  ┌─────▼──────┐  ┌─▼──────────────┐
         │  TaskStore  │  │  Notifier  │  │  EventRouter   │
         └──────┬──────┘  └─────┬──────┘  └──────┬─────────┘
                │               │                │
        ┌───────┴───┐    ┌──────┴────┐    ┌──────┴────────┐
        │JsonStore  │    │CliNotifier│    │ CliRouter     │ ← 默认
        │FeishuStore│    │FeishuCard │    │ FeishuWebhook │ ← 可选
        └───────────┘    └───────────┘    └───────────────┘
```

### 4.1 TaskStore（任务存取接口）

```python
class TaskStore(ABC):
    def fetch_pending(self) -> list[Task]: ...        # 拉取「待开始」，按优先级排序
    def get(self, record_id: str) -> Task: ...
    def update_status(self, record_id, status): ...
    def update_summary(self, record_id, summary, progress): ...
    def set_clarify_pointer(self, record_id, relpath): ...
    def set_queue_position(self, record_id, position): ...
    def complete(self, record_id, branch, summary, timeline): ...
```

- **JsonTaskStore（默认）**：任务存 `workspace/tasks/<id>/task.json`，pending 队列由本地文件 + 优先级字段排序。
- **FeishuBaseStore（可选）**：封装原 `base-sync.sh` 的 lark-cli 调用（建临时视图→过滤→排序→列记录→删视图）。字段 ID、base_token、table_id 从 config 读。

### 4.2 Notifier（出站 UI 接口）

```python
class Notifier(ABC):
    def send_clarify(self, task, modules, risks, round_no) -> None: ...
    def send_charter(self, task, summary) -> None: ...
    def send_plan(self, task, plan_summary, task_count, branch) -> None: ...
    def send_complete(self, task, branch, change_stats, duration, timeline) -> None: ...
    def send_failure(self, task, stage, error, log_path, branch) -> None: ...
```

- **CliNotifier（默认）**：终端富文本输出（澄清问题、方案摘要等）。
- **FeishuCardNotifier（可选）**：封装原 `cards.sh`——渲染 `templates/cards/*.json` 模板、用飞书 app 凭证取 tenant_access_token、POST `im/v1/messages`。凭证从 `.env` 读，绝不硬编码。

### 4.3 EventRouter（入站决策接口）

这是移植中最关键的抽象。原系统里，用户点飞书卡片按钮 → 飞书回调到 hermes 网关 → 网关合成 `/auto-coder-agent <json>` → 启动 agent 会话推进。**hermes 网关是外部依赖，开源用户没有。**

```python
class EventRouter(ABC):
    # 阻塞等待用户对某需求的下一个决策，返回 (action, payload)
    def await_decision(self, record_id: str, stage: str) -> Decision: ...
```

- **CliRouter（默认）**：在终端同步交互——打印选项（如"立项 / 改 / 再聊 / 拒"），读用户输入，直接返回决策。dispatch/execute 在同一进程内同步驱动，无需网关、无需回调。
- **FeishuWebhookRouter（可选）**：一个极简 FastAPI 服务接住飞书卡片回调，把 `ac_action`/`record_id`/`_form` 投递给等待中的编排器（替代 hermes 网关那一层）。文档标注此为进阶用法。

> 默认 CLI 路径让整个闭环在单进程内同步跑通，这是"开箱即用"的关键。飞书路径是异步事件驱动，需要常驻服务。

## 5. 核心模块（可移植，无 I/O 依赖）

| 模块 | 移植自 | 职责 |
|------|--------|------|
| `state_machine.py` | state-machine.sh | 转移表、回滚目标、worktree-drop 规则（纯函数） |
| `worktree.py` | worktree.sh | `feature/auto-<id>` 分支创建/删除、活跃计数、并发闸 |
| `engine_runner.py` | engine-runner.sh | 调编码引擎（subprocess，`stdin=DEVNULL`，`timeout=`）、跑测试、跑构建 |
| `planner.py` | auto-coder-agent SKILL §规划 | spec-kit 编排（specify→plan→tasks，产出 spec.md/plan.md/tasks.md） |
| `clarify.py` | auto-coder-agent SKILL §澄清 | 5 维澄清编排（范围/模块/验收/约束/风险），≤3 轮收敛 |
| `orchestrator.py` | dispatch.sh + execute.sh | dispatch（拉取→澄清→立项→规划→审批闸）与 execute（引擎→测试→构建→提交→推分支）主流程 |

### 5.1 移植中必须保真的"坑"（来自原项目实战）

1. **引擎 stdin 必须为 DEVNULL**：`claude --print` 继承开放管道时会等永不到来的 EOF 而挂死。Python 用 `subprocess.run(..., stdin=subprocess.DEVNULL)`。
2. **record 级锁防重复执行**：同一需求被触发两次会让两个引擎操作同一 worktree。用文件锁（`fcntl.flock` 或原子 mkdir），锁龄超 timeout 视为 stale 可回收。
3. **引擎超时**：用 `subprocess` 的 `timeout=` 参数（替代原 macOS perl-alarm fallback）。默认 1800s。
4. **worktree 一分支一需求硬规则**：分支名 `feature/auto-<record_id>`，并发上限默认 3。
5. **spec-kit 不阻塞用户**：规划阶段绝不等用户输入；要么自行决策，要么回滚到澄清中。
6. **base_branch 必须匹配远端**：worktree 从 `origin/<base_branch>` 建，分支名错会静默失败。

## 6. 配置

`config.example.yaml`（仓库内，全占位符）：

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
    match_keywords: ["keyword1", "keyword2"]
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
  default: claude-code

clarify_dimensions:
  - { key: scope,       label: "🎯 范围边界",   source: fixed }
  - { key: modules,     label: "🧩 涉及模块",   source: agent }
  - { key: acceptance,  label: "✅ 验收标准",   source: fixed }
  - { key: constraints, label: "⚙️ 优先级/约束", source: fixed }
  - { key: risk,        label: "⚠️ 风险点",     source: agent }

# 仅 feishu adapter 用
feishu:
  base_token: YOUR_BASE_TOKEN
  table_id: YOUR_TABLE_ID
  notify_chat_id: YOUR_CHAT_ID
  field_ids:
    description: fldXXXXXX
    priority: fldXXXXXX
    # ...
```

`.env.example`（仅 feishu adapter 用）：

```
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxx
```

`.gitignore`：`config.yaml`、`.env`、`workspace/`。

## 7. 目录结构

```
auto-coder-oss/
├── README.md                  # 中文使用说明
├── pyproject.toml             # 入口 auto-coder = autocoder.cli:main
├── config.example.yaml
├── .env.example
├── .gitignore
├── src/autocoder/
│   ├── __init__.py
│   ├── config.py              # 加载+校验 config.yaml / .env
│   ├── models.py              # Task / Decision 等 dataclass
│   ├── cli.py                 # auto-coder dispatch/execute/status
│   ├── core/
│   │   ├── state_machine.py
│   │   ├── worktree.py
│   │   ├── engine_runner.py
│   │   ├── planner.py
│   │   ├── clarify.py
│   │   └── orchestrator.py
│   └── adapters/
│       ├── store.py           # TaskStore ABC + JsonTaskStore
│       ├── notifier.py        # Notifier ABC + CliNotifier
│       ├── router.py          # EventRouter ABC + CliRouter
│       └── feishu/
│           ├── __init__.py
│           ├── store.py       # FeishuBaseStore
│           ├── notifier.py    # FeishuCardNotifier
│           └── webhook.py     # FeishuWebhookRouter (FastAPI)
├── templates/cards/           # 飞书卡片 JSON 模板（仅 feishu）
│   ├── clarify.json
│   ├── charter.json
│   ├── plan.json
│   ├── complete.json
│   └── failure.json
└── tests/
    ├── test_state_machine.py
    ├── test_worktree.py
    ├── test_engine_runner.py
    ├── test_json_store.py
    ├── test_cli_notifier.py
    └── test_orchestrator.py
```

## 8. CLI 默认闭环（无飞书）

```bash
# 安装
pip install -e .
cp config.example.yaml config.yaml   # 编辑填入你的项目路径
auto-coder dispatch                  # 拉取 pending → 终端打印澄清问题
                                     # → 终端答 → 立项确认 → 规划 → 展示方案 → 输入"通过"
auto-coder execute <record_id>       # 引擎 → 测试 → 构建 → 提交 → 推分支
auto-coder status                    # 查看队列与各需求状态
```

JsonTaskStore 下，新需求通过 `auto-coder add "<需求描述>"` 或直接写 `workspace/tasks/` 加入队列（spec 阶段细化）。

## 9. 测试策略

- 纯核心（state_machine、worktree 的纯函数、并发闸）→ 单元测试，移植原 5 个 `.test.sh` 的断言。
- adapters → 针对 JsonTaskStore / CliNotifier 的单元测试（飞书 adapter 用 mock HTTP，不打真实飞书）。
- engine_runner → 用 `sleep 60` 之类的假命令验证 timeout 与 stdin=DEVNULL 不挂。
- orchestrator → 用 fake adapters（in-memory store + 脚本化 router）跑一条端到端假需求。

## 10. 非目标（YAGNI）

- 不内置 web UI（飞书 webhook 已是进阶项）。
- 不支持 GitHub/GitLab 之外的代码托管自动建 PR（先只推分支，留扩展点）。
- 不做多租户/权限系统。
- 不重现 hermes 的 agent 会话管理；CLI 路径同步驱动，飞书路径靠用户自备常驻 webhook。
```

