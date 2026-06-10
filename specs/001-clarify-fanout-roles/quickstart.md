# Quickstart: 启用多角色并行预判

## 开启（按项目）

编辑 `config.yaml`，在目标项目下加 `clarify_fanout`：

```yaml
projects:
  my-project:
    path: /Users/hjx/projects/my-project
    match_keywords: [my-project, 我的项目]
    engine: claude-code
    clarify_fanout:
      enabled: true
      # roles 省略 → 用内置默认：产品经理 / 架构师 / 测试工程师
```

自定义角色：

```yaml
    clarify_fanout:
      enabled: true
      roles:
        - name: 产品经理
          focus: 需求范围边界、用户价值、验收标准
        - name: 安全工程师
          focus: 鉴权、数据合规、注入与越权风险
```

## 关闭

删除 `clarify_fanout` 段，或设 `enabled: false`。行为回到单次单角色预判，无额外引擎调用。

## 成本提示

开启后，**每轮**澄清的引擎调用数 = 角色数 N + 1（综合步）。澄清最多 5 轮。并行执行下时延 ≈ 单角色 + 综合步，但 token 成本约 N+1 倍。建议仅对结构复杂、单视角易漏的项目开启。

## 验证

- 未开启项目：提交需求，确认预判仍是单次调用、卡片内容路径不变。
- 开启项目：提交需求，确认澄清卡的模块/风险更全、问题更切中，且问题数 ≤ 3。
- 故障演练：临时让某角色超时（或断网），确认澄清卡仍能发出（降级生效）。
