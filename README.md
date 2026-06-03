# auto-coder-cli

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
