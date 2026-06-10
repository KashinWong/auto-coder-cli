# Implementation Plan: 澄清前项目预判 — 多角色并行 Fan-out

**Branch**: `001-clarify-fanout-roles` | **Date**: 2026-06-10 | **Spec**: [spec.md](./spec.md)

## Summary

把 `Orchestrator._engine_predict` 的单次单角色预判，升级为**可选的**编排层并行多角色预判：用线程池让产品经理 / 架构师 / 测试工程师各自读项目产出结构化预判，再用一次"不扫项目"的综合调用收敛成单个 `Prediction`，综合失败则用确定性兜底合并。通过项目级开关 opt-in，未开启的项目走原路径、零额外成本。复用现有 `predict_fn` 注入缝与 `run_engine_capture` 契约，澄清编排核心与 `Prediction` 数据结构均不改。

## Technical Context

- **语言/运行时**: Python 3（项目现有），标准库 `concurrent.futures`、`dataclasses`、`json`、`re`。
- **引擎**: 现有 `run_engine_capture`（`claude --print`，失败/超时返回空串）。
- **接入缝**: `ClarifyOrchestrator(predict_fn=...)`，默认 `predict_fn=self._engine_predict`。本特性只改 `_engine_predict` 内部分支，不改 `ClarifyOrchestrator`。
- **并发模型**: `ThreadPoolExecutor`。每个角色一次 `subprocess.run`（阻塞在 I/O，GIL 释放），线程并行成立。
- **配置载体**: `config.yaml` 的 `projects.<key>` 下新增 `clarify_fanout` 段。
- **数据结构**: 沿用 `clarify.Prediction` / `clarify.Question`，字段不变。
- **未决项**: 无 NEEDS CLARIFICATION（spec 已定）。

## Constitution Check

项目无 `.specify/memory/constitution.md`。改以全局 CLAUDE.md 规范替代约束：本项目为 Python，Java 专项规则不适用；遵守"先理解全局、给系统性方案而非补丁"、"2+ 文件改动走 Spec 流程"（本次正在执行）。无违例。

## 设计

### 模块划分

| 文件 | 角色 | 改动 |
|------|------|------|
| `src/autocoder/core/predict.py`（**新建**） | 预判 prompt 构建 + 输出解析的纯函数 | 从 orchestrator 抽出 `build_predict_prompt` / `parse_prediction`，二者由单次与多角色共用 |
| `src/autocoder/core/role_predict.py`（**新建**） | 多角色 fan-out 编排 | `Role`、`load_roles`、`fanout_predict`、`merge_predictions` |
| `src/autocoder/core/orchestrator.py`（**改**） | 接入分支 | `_engine_predict` 内按项目配置选择 单次 / 多角色；删除内联的 `_build_predict_prompt`/`_parse_prediction`，改为 import |
| `config.yaml`（**改**） | 项目级开关与角色 | 在目标项目下加 `clarify_fanout` 段（示例 + 注释成本） |
| `tests/...`（**新建/改**） | 覆盖开关、并行、三级降级、≤3 问约束 | 注入桩 engine_capture，无需真实引擎 |

> 为什么抽 `predict.py`：单角色与综合步都要"构建预判 prompt / 解析同一 JSON schema"。把它们留在 orchestrator 会被 role_predict 反向依赖 orchestrator，形成环。抽成无状态纯函数，两边各自 import，依赖单向（orchestrator → role_predict → predict）。

### 数据与契约

详见 [data-model.md](./data-model.md) 与下方配置契约。`Prediction` 不变。

#### 配置契约（config.yaml）

```yaml
projects:
  <project-key>:
    path: /abs/path/to/project
    match_keywords: [...]
    engine: claude-code
    clarify_fanout:            # 整段缺省 / enabled:false → 走现有单次预判，零额外调用
      enabled: true
      roles:                   # 选填；缺省用内置默认三角色
        - name: 产品经理
          focus: 需求范围边界、用户价值、验收标准
        - name: 架构师
          focus: 涉及模块、集成点、技术风险
        - name: 测试工程师
          focus: 边界条件、异常路径、验收用例
```

内置默认角色定义在 `role_predict.DEFAULT_ROLES`，因此只写 `clarify_fanout: {enabled: true}` 即可启用三角色。

### 控制流

```
_engine_predict(description, project_path, prior_qa)
  ├─ project_path 空 → Prediction([],[])                    # 现状不变
  ├─ spec 取不到 → Prediction([],[])                         # 现状不变
  ├─ 查 project 配置 clarify_fanout
  │   ├─ enabled 且 roles 非空 →
  │   │     fanout_predict(roles, desc, prior_qa, spec, path, timeout, engine_capture)
  │   └─ 否则 ↓
  └─ 单次：parse_prediction(engine_capture(spec, path, build_predict_prompt(desc, prior_qa), timeout))
```

```
fanout_predict(...)
  1. ThreadPoolExecutor(max_workers=len(roles))：
        每角色 → parse_prediction(engine_capture(spec, path,
                    build_predict_prompt(desc, prior_qa, role=role), role_timeout))
        单角色异常/空串 → 记空 Prediction（丢弃，不抛出）
  2. role_preds = [非空的角色 Prediction]
  3. role_preds 为空 → 返回 Prediction([],[])               # FR-009 全挂降级
  4. 综合步：synth_out = engine_capture(spec, path,
                build_synthesis_prompt(desc, role_preds), synth_timeout)
        # 综合 prompt 明确"不要再读项目，只归并去重"
     ├─ synth_out 解析成功 → 该 Prediction（已 ≤3 问）
     └─ 解析失败/空 → merge_predictions(role_preds)          # FR-010 确定性兜底
```

### 关键约束落地

- **≤3 问（FR-006/SC-004）**：`build_synthesis_prompt` 要求最多 3 问；`parse_prediction` 已有 `qs[:3]` 截断；`merge_predictions` 兜底也 `[:3]`。三处保证无论走哪条路都 ≤3。
- **并行时延（FR-004/SC-002）**：线程池并发跑角色，墙钟 ≈ max(单角色) + 综合步，而非求和。
- **容错三级（FR-008/009/010）**：单角色失败丢弃；全失败空预判；综合失败确定性合并。任何分支都不向上抛异常（沿用"预判绝不阻断澄清"语义）。
- **零回归（FR-001/SC-001）**：未配置项目不进入 fanout 分支，调用次数与内容路径与改前一致。

### 综合步 prompt 要点

- 输入：原需求 + 各角色 JSON（modules/risks/scope/acceptance/ready/questions）。
- 指令：不得再读项目；合并去重 modules/risks；scope_hint/acceptance_hint 取最具体的综合；ready = 各角色普遍认为足够且无真正阻断性问题；questions 去重并按"是否真阻断实现/验收"排序后最多 3 个；输出与单角色相同的 JSON schema（复用 `parse_prediction`）。

### merge_predictions（确定性兜底）

- modules：各角色并集，保序去重。
- risks：各角色并集，保序去重。
- scope_hint / acceptance_hint：按角色顺序取第一个非空（产品经理优先）。
- ready：所有非空角色都 ready 才 ready（保守，倾向继续澄清）；若无人提问则视为 ready。
- ready_reason：拼接简述或取首个非空。
- questions：按 `ask` 文本去重，保序，截断至 3。

## Phase 0 — Research

见 [research.md](./research.md)。要点：线程 vs 进程（选线程，subprocess I/O 阻塞）、综合步用引擎 vs 纯 Python（用引擎+兜底，取质量与鲁棒兼得）、prompt 抽取避免循环依赖。

## Phase 1 — Design Artifacts

- [data-model.md](./data-model.md)：Role / RoleFinding / Prediction 字段与不变式。
- 配置契约：见上方 config.yaml 段。
- quickstart：见 [quickstart.md](./quickstart.md)。

## 风险与权衡

- **成本放大**：每轮澄清引擎调用 1 → N+1（N 角色 + 1 综合）。开关默认关闭、按项目开启缓解（FR-013）。
- **综合步质量**：LLM 合并可能仍漏/重；以 `parse_prediction` 截断 + 确定性兜底保证下限。
- **多轮叠加**：澄清最多 5 轮，开启项目成本随轮次叠加；文档需提示仅对复杂项目开启。

## 测试策略

- 注入桩 `engine_capture(spec, path, prompt, timeout)`：按 prompt 中角色名/综合标记返回不同 canned JSON，断言并行结果合成正确、问题 ≤3。
- 故障注入：桩对特定角色返回 `""`（失败）、对综合 prompt 返回 `""`（综合失败）→ 断言降级路径。
- 回归：未配置 `clarify_fanout` 的项目，断言 `engine_capture` 仅被调用 1 次且结果等于单角色路径。
