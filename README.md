# auto-coder-cli

Agent 驱动的自动化编码工作流：把"一句话需求"推进到"一个可审查的代码分支"。

```
拉取需求 → AI 澄清对话 → 立项 → spec-kit 规划 → 人工审批 → 隔离 worktree 执行引擎 → 推分支
```

每个需求走一条 7 状态的状态机，人在两个卡点（立项、方案）把关，编码引擎（Claude Code / Codex 等）在独立 git worktree 里干活。既能在终端单机跑通整条闭环，也能挂到飞书做团队协作 UI。

## 核心特性

- **AI 自适应澄清**：编码前由引擎读项目代码、自行判断"该问什么、问几轮"，每轮最多 3 个结构化问题（单选/多选/文本），问够了自动收尾——不再是固定维度、固定轮数
- **多需求并行**：每个需求独占一个 git worktree（`feature/auto-<id>`），互不干扰，并发上限可配
- **人工卡点**：立项、方案两道人工审批闸，引擎不会未经批准就改代码
- **可插拔架构**：TaskStore / Notifier / EventRouter 三个接口，默认零外部依赖（本地 JSON + 终端交互），飞书为可选 adapter

## 两种运行模式

这是理解本项目的关键。同一套核心状态机，有两种驱动方式：

| | **CLI 模式（默认）** | **飞书模式** |
|---|---|---|
| 适用 | 个人本机、快速验证 | 团队协作、异步审批 |
| 任务存储 | 本地 JSON 文件 | 飞书多维表格（Bitable） |
| UI | 终端富文本 | 飞书交互卡片 |
| 驱动方式 | **单进程阻塞**：一个 `dispatch` 进程从头同步跑到尾，每个卡点用 `input()` 等你回答 | **无状态推进**：每次点卡片按钮 = 一次独立的 `advance` 进程，处理一步、发下一张卡、退出 |
| 状态持久化 | 进程内内存 | 全部落多维表格，进程间不共享内存 |

CLI 模式适合一个人试。飞书模式是设计的主场景——下面单独一节详述。

## 快速开始（CLI 模式）

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml   # 编辑：填入你的项目路径、引擎、关键词

auto-coder add "给项目加一个登录按钮" --priority 重要紧急
auto-coder dispatch                  # 终端交互：澄清 → 立项 → 规划 → 审批（同一进程同步跑完）
auto-coder execute <record_id>       # 引擎编码 → 测试 → 构建 → 提交 → 推分支
auto-coder status                    # 查看所有需求状态
```

`dispatch` 会在终端里逐轮抛出 AI 的澄清问题，你回答后它判断够不够立项；立项、方案两个卡点输入选项编号即可。

## 状态机

```
待开始 → 澄清中 → 待立项 → 规划中 → 待审批 → 进行中 → 已完成
                    ↘ 已搁置（驳回）          ↘ 已停滞（失败留现场）
```

每个卡点对应一组决策动作（飞书卡片按钮 / CLI 选项）：

| 阶段 | stage | 动作（ac_action） | 效果 |
|---|---|---|---|
| 澄清 | `clarify` | `clarify_submit` | 提交本轮答案，AI 判断够没够 → 收尾立项 或 再问一轮 |
| 立项 | `charter` | `approve_charter` | 建 worktree，后台启动规划 |
| | | `revise_charter` / `rechat` | 退回澄清第 1 轮重聊 |
| | | `reject_charter` | 搁置 |
| 方案 | `plan` | `approve_plan` | 并发闸放行 → 后台执行编码 |
| | | `revise_plan` | 退回重新规划 |
| | | `reunderstand` | 删 worktree，退回澄清重来 |

## 架构

```
核心（状态机 / worktree / 引擎调度 / spec-kit 规划）── 通过三接口与 I/O 解耦
  ├─ TaskStore   任务存取   默认 JsonTaskStore │ 可选 FeishuBaseStore（多维表格）
  ├─ Notifier    出站 UI    默认 CliNotifier   │ 可选 FeishuCardNotifier（交互卡片）
  └─ EventRouter 入站决策   默认 CliRouter     │ 可选 FeishuWebhookRouter
```

CLI 模式用 `EventRouter.await_decision()` 同步等输入；飞书模式不用 router，而是靠外部网关把卡片点击翻译成 `auto-coder advance` 调用（见下）。

## 飞书模式接入（完整指南）

飞书模式没有自己的常驻服务——它依赖一个**已订阅飞书事件的网关**（本项目实战用的是 hermes/openclaw）把卡片点击转发进来。整条链路是无状态的：

```
飞书群里点卡片按钮
  → 网关收到 card.action 回调，读按钮 value 里的 _skill 字段
  → 网关合成一次 skill 调用：/auto-coder-agent <tag> <json>
  → auto-coder-agent skill（薄壳，不做推理）解析 json，调：
      auto-coder advance <record_id> <ac_action> --stage <stage> [--form <json>] [--input <text>]
  → CLI 起一个新进程：读多维表格当前状态 → 处理这一步 → 发下一张卡 → 退出
```

因为每次点击都是独立进程、内存不共享，**累积的澄清问答全部持久化在多维表格的「最新进展记录」字段里**（JSON：`{round, qa, pending}`），下一次 `advance` 读回来续上。

### 步骤

**1. 装可选依赖**
```bash
pip install -e ".[feishu]"
```

**2. 建飞书企业自建应用**，开通：发消息、多维表格读写、接收 `card.action_trigger` 事件。把 `FEISHU_APP_ID/SECRET` 填进 `.env`（见 `.env.example`，不入仓库）。

**3. 建多维表格**作为任务看板，至少包含这些字段（字段名要和 `src/autocoder/adapters/feishu/store.py` 的 `_FIELD_MAP` 对齐）：

| 字段名 | 类型 | 用途 |
|---|---|---|
| 任务描述 | 文本 | 需求原文 |
| 重要紧急程度 | 单选 | 优先级（重要紧急 / …） |
| 进展 | 单选 | 状态机当前状态（待开始 / 澄清中 / …） |
| 任务情况总结 | 文本 | 澄清产物（立项依据） |
| 最新进展记录 | 文本 | 澄清累积问答 JSON |
| 澄清记录 | 文本 | spec 目录指针 |

> 任务存取通过 `lark-cli` 命令行工具读写多维表格（不走 `FEISHU_*` env，需另行配置 lark-cli 的鉴权）。

**4. config.yaml** 切到飞书 adapter：
```yaml
adapters:
  store: feishu        # 任务存多维表格
  notifier: feishu     # 出站发交互卡片
  router: cli          # 入站由网关转发，不用常驻 router
feishu:
  base_token: YOUR_BASE_TOKEN
  table_id: YOUR_TABLE_ID
  notify_chat_id: oc_xxx   # 发卡的目标群
```

**5. 部署 hermes 转发 skill**（薄壳，本仓库不含，需放到网关侧）。它只做机械转发，不做任何 LLM 推理：解析卡片 json → 调 `auto-coder advance`。卡片按钮的 `behaviors.value` 里带 `ac_action`/`record_id`/`stage`/`_skill`，网关靠 `_skill` 路由到这个 skill。

**6. 触发第一张卡**。多维表格里加一条「待开始」需求后，跑：
```bash
set -a && source .env && set +a
auto-coder dispatch-feishu     # 取一条待开始 → AI 预判 → 发澄清卡 → 立即退出（不阻塞）
```
之后整条流程都由群里点卡片驱动，无需再开任何常驻进程。可挂到 cron / 飞书消息触发 `dispatch-feishu`。

### ⚠️ 飞书模式两条铁律

- **SAME-APP 规则**：交互卡片按钮回调只会回到「发卡的那个 app」。发卡（FeishuCardNotifier）与接收回调（网关订阅）必须是同一个飞书 app，否则所有按钮点击失联。
- **发卡失败必须不翻状态**：`dispatch_feishu` 先发卡成功、再把状态翻「澄清中」。若顺序反了，发卡失败会把任务孤立在「澄清中」——既没有卡可点，又因 `fetch_pending` 只取「待开始」而无法重新分发。

## 引擎注意事项

- 引擎以子进程调用，`stdin` 强制为 `DEVNULL`——否则 `claude --print` 会继承开放管道、等永不到来的 EOF 而挂死。
- 后台阶段（plan / execute）用 `python -m autocoder.cli` 起子进程（复用当前 venv 解释器），**不要**依赖裸 `auto-coder` 在 PATH 里。
- 引擎超时默认 1800s，可在 `engines.<name>.timeout` 调整。
- 同一需求并发触发由 record 级文件锁拦截。
- `projects.<name>.base_branch` 必须是目标仓库真实存在的远程分支（`origin/<base_branch>` 要能解析）——写错会在建 worktree 时报 `not a valid object name`。

## 配置速查

见 `config.example.yaml`。关键项：

- `adapters`：选 store / notifier / router 实现
- `projects`：每个受管项目的路径、引擎、匹配关键词、base 分支、测试/构建命令、知识库
- `engines`：编码引擎命令（如 `claude --print`）、超时、模型环境变量

敏感凭证（飞书 app）放 `.env`，不入仓库。

## 开发

```bash
pytest tests/ -v
```

## License

MIT
