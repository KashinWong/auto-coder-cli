# Research: 多角色并行预判

## R1. 并发模型 —— 线程 vs 进程 vs asyncio

- **Decision**: `concurrent.futures.ThreadPoolExecutor`，每角色一个 task。
- **Rationale**: 每个角色调用就是一次 `subprocess.run(claude ...)`，纯 I/O 阻塞，等待期间 GIL 释放，多线程即可真并行；引擎是独立子进程，本身不占用 Python CPU。线程模型最简单、与现有同步代码无缝（`run_engine_capture` 是同步函数）。
- **Alternatives**: 多进程（ProcessPool）—— 没必要，子进程已是 claude，再 fork Python 纯浪费且序列化麻烦；asyncio —— 需把 `subprocess.run` 改 `asyncio.create_subprocess_exec`，侵入现有同步栈，收益为零。

## R2. 综合步 —— 引擎调用 vs 纯 Python 合并

- **Decision**: 优先用一次"不扫项目"的引擎调用做综合；失败/超时回退到确定性 `merge_predictions`。
- **Rationale**: 语义级去重/排序/ready 判定，LLM 比规则强（"两个角色问的是不是同一件事"靠文本规则很难判准）。但 LLM 会失败，所以必须有确定性兜底，保证可用性下限与 ≤3 问硬约束。两者结合 = 质量与鲁棒兼得。
- **Alternatives**: 纯 Python 合并 —— 鲁棒但去重粗糙（只能按字符串）；纯 LLM 无兜底 —— 综合一挂就丢掉全部多角色成果，违反 FR-010。

## R3. prompt 构建 / 解析的归属 —— 避免循环依赖

- **Decision**: 把 `build_predict_prompt` 与 `parse_prediction` 从 `orchestrator` 抽到新模块 `core/predict.py`，作为无状态纯函数；`orchestrator` 与 `role_predict` 均 import 之。
- **Rationale**: 多角色与单角色都要复用同一套"构 prompt + 解析同一 JSON schema"。若留在 orchestrator，则 `role_predict` 需反向 import orchestrator → 循环依赖。抽到底层模块后依赖单向：`orchestrator → role_predict → predict`。
- **Alternatives**: 把函数作为回调注入 role_predict —— 可行但签名臃肿、可读性差；在 role_predict 里复制一份 —— 违反 DRY，schema 变更要改两处。

## R4. 角色配置形态

- **Decision**: `projects.<key>.clarify_fanout = {enabled: bool, roles?: [{name, focus}]}`；`roles` 缺省用 `role_predict.DEFAULT_ROLES`（产品经理/架构师/测试工程师）。
- **Rationale**: opt-in 默认关闭满足"零回归 + 成本可控"；角色可覆盖满足"可配置"（FR-003）；缺省内置降低使用门槛（只写 `enabled: true` 即用）。
- **Alternatives**: 全局开关 —— 不满足"按项目"；必须显式列角色 —— 使用门槛高。

## R5. 超时分配

- **Decision**: 角色超时 = 现有预判上限 `min(spec.timeout, 180)`；综合步超时更短（如 `min(spec.timeout, 60)`，因不扫项目）。
- **Rationale**: 角色仍需读项目，沿用 180s 上限；综合只读文本，给短超时避免拖慢发卡；并行下墙钟 ≈ 角色 + 综合。
- **Alternatives**: 给综合同样 180s —— 不必要地放大尾延迟。
