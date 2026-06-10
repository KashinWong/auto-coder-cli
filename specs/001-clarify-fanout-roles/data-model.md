# Data Model: 多角色并行预判

## Role（新增，role_predict.Role）

预判视角的定义，来自配置或内置默认。

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 角色名，如「产品经理」。会嵌入该角色的 prompt，并用于测试桩按角色派发 |
| focus | str | 该角色的关注点描述，嵌入 prompt 指导其侧重 |

- `DEFAULT_ROLES`：产品经理（范围/价值/验收）、架构师（模块/集成点/风险）、测试工程师（边界/异常/验收用例）。
- 不变式：name 非空；roles 列表为空时调用方回退单角色路径。

## RoleFinding（概念实体，无独立类）

单角色的预判产出。物理上**复用 `clarify.Prediction`**：每个角色返回一个 `Prediction`，承载该视角下的 modules/risks/scope_hint/acceptance_hint/ready/questions。空 `Prediction([],[])` 表示该角色失败/超时，被合成丢弃。

## Prediction（既有，clarify.Prediction —— 不改）

合成后供澄清卡使用的单一结果。字段与现状完全一致：

| 字段 | 类型 | 不变式 |
|------|------|--------|
| modules | list[str] | 合成后并集去重 |
| risks | list[str] | 合成后并集去重 |
| scope_hint | str | 取最具体/首个非空 |
| acceptance_hint | str | 取最具体/首个非空 |
| ready | bool | 保守：非空角色普遍 ready 且无阻断问题才 True |
| ready_reason | str | 综合说明 |
| questions | list[Question] | **≤ 3**（三重保证：synth prompt / parse 截断 / merge 兜底） |

## 配置实体（config.yaml）

`projects.<key>.clarify_fanout`：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| enabled | bool | （缺省=false） | 是否启用多角色 fan-out |
| roles | list[{name, focus}] | DEFAULT_ROLES | 选填，覆盖默认角色集 |

## 状态/流程

无新增持久化状态。多角色仅发生在 `_engine_predict` 一次调用内（一轮澄清预判），产出单个 `Prediction` 后即与现状汇流（`send_clarify` / progress 编码不变）。多轮澄清通过既有 `prior_qa` 透传给每个角色。
