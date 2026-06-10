# Tasks: 澄清前项目预判 — 多角色并行 Fan-out

**Feature**: `001-clarify-fanout-roles` | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

**Tests**: 本特性容错与约束密集（三级降级、≤3 问硬约束、零回归），spec 的 Success Criteria 明确要求可在桩引擎下自动化验证（SC-005），故包含测试任务。

## User Stories（来自 spec Acceptance Scenarios）

- **US1 (P1)**：开启项目 → 多角色并行预判 + 综合，产出更全面的单个 Prediction（含多轮 prior_qa 透传）。Scenarios 1、5。
- **US2 (P1)**：未开启项目 → 行为与现状逐字节一致、零额外引擎调用。Scenario 2。
- **US3 (P2)**：三级容错降级（部分角色挂 / 全挂 / 综合挂）下澄清不中断。Scenarios 3、4、6。

---

## Phase 1: Setup

- [ ] T001 阅读并确认现状基线：`src/autocoder/core/orchestrator.py` 中 `_build_predict_prompt`/`_parse_prediction`/`_engine_predict`/`_predict_engine_spec`，以及 `tests/test_predict.py`、`tests/test_orchestrator.py` 现有断言，记录需保持兼容的行为契约（返回 Prediction 字段、≤3 问、空降级）

---

## Phase 2: Foundational（阻塞所有 US —— 抽取纯函数，消除循环依赖）

- [ ] T002 新建 `src/autocoder/core/predict.py`，从 orchestrator 抽出两个无状态纯函数：`build_predict_prompt(description, prior_qa, role=None)`（`role` 为 None 时输出与现状逐字一致；非 None 时追加「你是<role.name>，重点关注<role.focus>」框架）与 `parse_prediction(out) -> Prediction`（原 `_parse_prediction` 逻辑，含 `qs[:3]` 截断），保留对 `clarify.Prediction/Question` 的依赖
- [ ] T003 改 `src/autocoder/core/orchestrator.py`：删除内联 `_build_predict_prompt`/`_parse_prediction`，改为 `from autocoder.core.predict import build_predict_prompt, parse_prediction`，`_engine_predict` 单角色分支改调这两个函数；保持外部行为不变
- [ ] T004 [P] 新建 `tests/test_predict.py` 的纯函数级用例（或并入现有文件）：断言 `build_predict_prompt(desc, None)` 与旧 prompt 等价、`build_predict_prompt(desc, None, role)` 含角色措辞、`parse_prediction` 对裹 ```json```/前后文/超 3 问/非法 JSON 的处理与旧行为一致
- [ ] T005 运行 `pytest tests/test_predict.py tests/test_orchestrator.py -q` 确认抽取后回归全绿（这是 US2 零回归的基础保证）

---

## Phase 3: US1 — 多角色并行预判 + 综合（P1）🎯 MVP

**独立测试标准**：注入桩 `engine_capture`，对不同角色 prompt 返回不同 canned JSON、对综合 prompt 返回合并 JSON；断言最终 Prediction 合并了各角色的 modules/risks、问题去重且 ≤3、prior_qa 透传到每个角色 prompt。

- [ ] T006 [US1] 在 `src/autocoder/core/role_predict.py` 新建 `Role`（dataclass: name, focus）与 `DEFAULT_ROLES`（产品经理/架构师/测试工程师，focus 措辞见 data-model.md），及 `load_roles(fanout_cfg) -> list[Role]`（读 `roles` 覆盖项，缺省返回 DEFAULT_ROLES）
- [ ] T007 [US1] 在 `role_predict.py` 实现 `build_synthesis_prompt(description, role_preds) -> str`：输入各角色 Prediction 的 JSON 摘要，指令「不要再读项目、只归并去重、最多 3 问、按是否阻断实现/验收排序、输出与单角色相同 JSON schema」
- [ ] T008 [US1] 在 `role_predict.py` 实现 `fanout_predict(*, roles, description, prior_qa, spec, project_path, timeout, engine_capture, synth_timeout=None) -> Prediction`：用 `concurrent.futures.ThreadPoolExecutor(max_workers=len(roles))` 并行对每个角色调 `engine_capture(spec, project_path, build_predict_prompt(description, prior_qa, role), timeout)` → `parse_prediction`；汇集非空角色 Prediction；再调综合步 `engine_capture(..., build_synthesis_prompt(...), synth_timeout)` → `parse_prediction`（本任务先只做"全成功"主路径，降级留 US3）
- [ ] T009 [US1] 改 `orchestrator._engine_predict`：取匹配项目配置（按 project_path 反查 proj dict），读 `proj.get("clarify_fanout")`；`enabled` 且 `load_roles` 非空 → 调 `fanout_predict(...)` 传入 `self.engine_capture`、角色超时 `min(spec.timeout,180)`、综合超时 `min(spec.timeout,60)`；否则走单角色分支
- [ ] T010 [US1] 在 `config.yaml` 增加**注释掉的** `clarify_fanout` 示例块（连同成本注释：每轮 N+1 次引擎调用），演示如何开启但默认不对任何真实项目 live 启用；是否对某项目启用交由用户显式取消注释决定
- [ ] T011 [P] [US1] 新建 `tests/test_role_predict.py`：桩 `engine_capture` 按 prompt 中角色名分流返回不同 JSON、综合 prompt 返回合并 JSON；断言 `fanout_predict` 合并 modules/risks、问题去重 ≤3、并行调用次数 = N+1；断言 `build_predict_prompt(role=...)` 被各角色用到、`prior_qa` 进入每个角色 prompt；并用带 sleep 的桩断言各角色并发派发（墙钟 ≈ max + 综合，覆盖 SC-002）
- [ ] T012 [US1] 在 `tests/test_predict.py`（orchestrator 级）加用例：项目配置含 `clarify_fanout.enabled=true` 时 `_engine_predict` 走 fanout（断言 engine_capture 被调 N+1 次且结果为合成 Prediction），并断言合成 Prediction 含全部既有字段且类型不变（覆盖 FR-007）

**Checkpoint**：开启项目能并行多角色预判并合成出 ≤3 问的 Prediction。

---

## Phase 4: US2 — 未开启零回归（P1）

**独立测试标准**：未配置 `clarify_fanout` 的项目，`_engine_predict` 仅调用 `engine_capture` 一次，返回值与抽取前单角色路径完全一致。

- [ ] T013 [US2] 在 `tests/test_predict.py` 加回归用例：项目无 `clarify_fanout`（或 `enabled:false`）时，桩 `engine_capture` 计数断言恰好 1 次调用，且 Prediction 与现有 `test_engine_predict_parses_json` 路径一致
- [ ] T014 [US2] 运行 `pytest tests/test_orchestrator.py tests/test_clarify.py -q` 确认澄清编排（多轮、ready 判定、charter）行为不受影响

**Checkpoint**：关闭路径零回归、零额外成本，得到自动化保证。

---

## Phase 5: US3 — 三级容错降级（P2）

**独立测试标准**：注入故障桩，分别验证「部分角色返回空串」「全部角色返回空串」「综合步返回空串」三种情形下 `fanout_predict` 不抛异常并给出符合预期的降级结果。

- [ ] T015 [US3] 在 `role_predict.py` 实现 `merge_predictions(role_preds) -> Prediction`（确定性兜底）：modules/risks 保序并集去重；scope_hint/acceptance_hint 取首个非空（角色顺序优先）；ready = 所有非空角色 ready 或无人提问；questions 按 ask 文本去重保序、截断 3
- [ ] T016 [US3] 完善 `fanout_predict` 降级分支：单角色 future 异常/空串 → 该角色记空并丢弃（不抛）；非空角色为空集 → 返回 `Prediction([],[])`；综合步空串/解析失败 → 调 `merge_predictions(role_preds)`
- [ ] T017 [P] [US3] 在 `tests/test_role_predict.py` 加三组故障用例：部分角色空（用剩余角色合成）、全角色空（返回空 Prediction）、综合空（走 merge_predictions 且 ≤3 问）；断言均不抛异常
- [ ] T018 [US3] 加 `merge_predictions` 单元用例：重叠 modules/risks/questions 去重、ready 保守判定、questions 截断至 3

**Checkpoint**：三级容错齐备，澄清流程在任意故障下不中断（满足 FR-008/009/010、SC-003）。

---

## Phase 6: Polish & 文档

- [ ] T019 [P] 更新 `specs/001-clarify-fanout-roles/quickstart.md`（如实现中字段名有调整）并核对 `config.yaml` 示例与实现一致
- [ ] T020 [P] 在项目 README 或 `config.yaml` 注释补「多角色预判」成本与开启建议（仅复杂项目开启；每轮 N+1 次引擎调用、最多 5 轮）
- [ ] T021 运行全量 `pytest -q` 确认整库绿；按 CLAUDE.md 自动修复上限 3 次，超出提示人工介入
- [ ] T022 生成 `specs/001-clarify-fanout-roles/smoke-test.md`（按全局规范：每个 spec tasks 完成后须产出冒烟测试文档），含开启/关闭两条手动验证路径与一条故障演练

---

## Dependencies

- **Phase 2 (T002–T005)** 阻塞所有 US：抽取纯函数是单/多角色共用与避免循环依赖的前提。
- **US1 (Phase 3)** 依赖 Phase 2；是 MVP。
- **US2 (Phase 4)** 依赖 Phase 2（其零回归正由 T003 抽取保证）；可与 US1 并行验证。
- **US3 (Phase 5)** 依赖 US1 的 `fanout_predict` 骨架（T008）。
- **Phase 6** 依赖全部前序。

## Parallel Execution 机会

- T004 与 T002/T003 实现可交错；T011、T017、T019、T020 标 [P]（独立文件）。
- US1 与 US2 的测试任务可并行编写（不同测试文件 / 不同断言）。

## Implementation Strategy

- **MVP = Phase 2 + Phase 3（US1）**：跑通"开启项目的多角色并行 + 综合主路径"，即可演示核心价值。
- **增量**：再加 US2 回归护栏、US3 容错降级、Phase 6 文档与冒烟。
- 每个 Phase 末尾的 pytest 即其独立验收。
