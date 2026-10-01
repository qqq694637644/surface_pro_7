# AI Loop

PowerLab Agent 是慢速研究、调查、实验、工程维护和复杂度决策层，不是实时 DVFS controller。

根目录 AGENTS.md 定义统一入口、事实优先级、Stage A–E、evidence identity 和自治边界。本文件只回答：

> Agent 在不同任务模式下，如何与 PowerLab runtime 形成长期闭环？

## 1. 先判断任务模式

### 1.1 仓库工程 / 代码维护

优先：

1. AGENTS.md
2. docs/PROJECT_STATUS.md
3. docs/PROJECT_MAP.md
4. 当前源码 / tests / Git / CI
5. 设计语义变更时读 PLAN2.md

这类任务**不要求**先把本地 agent-context 变成 healthy。

在非 SP7 开发机上：

- unsupported hardware；
- READ_ONLY / UNKNOWN；
- 本地旧 runtime DB schema mismatch；

都可能是正常开发环境现象，不是代码维护 blocker，也不授权 Agent reset runtime 或放宽硬件合同。

### 1.2 真实 SP7 运维 / 优化

优先：

1. AGENTS.md
2. docs/PROJECT_STATUS.md
3. `sp7-powerlab agent-context`
4. docs/LLM_BEHAVIOR.md
5. 按当前问题读 OPERATIONS / FIRST_RUN / PLAN2

### 1.3 首次部署 / 新电池

跟随 docs/FIRST_RUN.md 的 Stage A → B → C → D → E → STABLE 顺序。

## 2. agent-context 是动态摘要，不是万能事实源

真实 SP7 上：

```bash
sp7-powerlab agent-context
```

用于快速建立当前机器事实，包括：

- Git / schema / evidence semantics
- hardware contract
- Control Safety
- Learning Lifecycle
- Investigation state
- battery epoch / calibration
- Measurement Trust
- current evidence epoch / compatibility
- current verified envelopes
- Reference / Noise
- active trial / investigation
- Scheduler blockers
- usage coverage
- StableReadiness
- Net Benefit campaign/policy identity
- current Stage
- recommended next actions
- documentation map

它应该是**真实 runtime 调教 turn** 的第一动态入口。

它不是：

- 设计合同；
- 当前实现成熟度报告；
- destructive action 的批准；
- 非 SP7 开发机代码维护的 gate。

如果 agent-context 与 Markdown 在实时状态上冲突，以当前 OS/SQLite/CLI 为准；如果是设计语义冲突，回到 PLAN2 + implementation/tests 明确处理。

## 3. knowledge pack 只在需要历史时加载

```bash
sp7-powerlab-agent hourly
```

适用于：

- recent BAT/rollups
- thermal/demand
- control actions
- evidence decisions
- investigations
- UnexpectedPower
- trials
- feedback
- rejection memory
- coverage
- Net Benefit
- versions

不要每轮把全部 SQLite、journal、源码、文档和 knowledge pack 一起塞进模型。

先用 agent-context 定位当前缺口，再读取最小必要历史。

## 4. Agent cadence

### Event-driven

当出现：

- UnexpectedPower
- sustained drift
- thermal incident
- user complaint
- failed trial
- rollback integrity problem
- system/software compatibility change
- current policy identity change

触发调查或 evidence revalidation。

### Slow periodic review

用于：

- 检查长期 drift；
- 汇总当前 evidence；
- 判断 Stage D burn-in 是否足够；
- 判断 Stage E Net Benefit 是否仍代表当前 policy；
- 判断是否可以 STABLE；
- 判断复杂度是否值得保留。

### User-driven

用户主动要求：

- 研究
- 解释
- 实验
- 修改代码/配置
- review
- 部署/运维
- 删除复杂度

hourly timer 默认不启用。`sp7-powerlab hourly` 可以按需执行；只有用户明确希望固定 slow review 时才
enable timer，它也不是“每小时必须修改系统”的要求。

## 5. 每轮先问三个问题

### 5.1 测量可信吗？

Measurement Trust 不 READY 时：

- 不把短窗口 BAT W 当成真实收益；
- 不启动 candidate search；
- 优先解决 gauge cadence/quantization/integration consistency/minimum duration。

### 5.2 当前 evidence identity 兼容吗？

复用任何历史结果前检查：

- battery epoch
- hard evidence epoch
- relevant compatibility generation
- envelope content hash
- trial evidence_scope_key
- Stage E runtime policy fingerprint
- Net Benefit campaign

同名对象不能代替 identity 检查。

### 5.3 这是优化问题，还是调查/停止问题？

优先级：

1. Safety / rollback integrity
2. Measurement Trust
3. UnexpectedPower / regression investigation
4. user experience
5. 使用已有 VERIFIED policy
6. bounded candidate tuning
7. complexity deletion / convergence

不要因为“有参数可以调”就把每个问题转成 HWP search。

## 6. Stage A–E Agent 行为

### Stage A — Measurement Trust + Calibration

Agent 应帮助：

- 建 battery epoch；
- 观察真实 Discharging；
- preliminary Measurement Trust；
- calibration；
- current-epoch Measurement Trust。

不要提前讨论“最优 envelope”。

### Stage B — Verified Baseline + Reference / Noise

Agent 应：

- 确认第一条 baseline 来自真实 HWP snapshot；
- 等待 clean natural rollups；
- 确认 Reference/Noise 按 envelope content hash 隔离；
- 判断 noise / MUE 是否足以支持实验。

### Stage C — Bounded Coarse Search

Agent 应：

- 看 Scheduler eligibility；
- 只探索有限邻域；
- 保留 candidate 造成的坏结果；
- 接受 equivalent / inconclusive；
- 没有 practical headroom 时停止。

### Stage D — Independent Validation + Real-Usage Burn-in

Agent 应：

- 切到 VALIDATING；
- 收集 representative usage；
- 看 total valid/trusted seconds；
- 看 distinct usage days / observation span；
- 处理用户反馈、UnexpectedPower 和 drift；
- 不提前 freeze STABLE。

### Stage E — End-to-End Net Benefit / Complexity Selection

Agent 应：

- 使用同一 bounded OPEN campaign；
- 为 MONITORING 和 DYNAMIC_CONTROLLER 分别运行 A1-B1-B2-A2；
- 检查 runtime policy fingerprint；
- 检查 actual runtime mode 和 hourly background units；
- 拒绝 Charging/resume/gap 后重连 block；
- 拒绝跨时间/context/policy 拼接；
- MONITORING 只解释 observer overhead；最终决定保留 Dynamic 还是回到 Fixed-good。

### STABLE

只有 deterministic readiness 全部通过后进入。

STABLE 的正常循环是：

```
agent-context
  -> no meaningful drift/regression
  -> NO_CHANGE
```

不是继续制造 candidate。

## 7. UnexpectedPower 闭环

```
UnexpectedPower / Drift
  -> Investigation
  -> local attribution
  -> testable hypothesis
  -> verification
  -> classification
```

优先调查：

- background process
- browser/media acceleration
- GPU/device/runtime PM
- wakeups
- network
- thermal
- software/driver regression

只有 confirmed configuration regression 或新的高价值控制假设才 reopen optimization。

外部事实（例如某 kernel/Firefox/Mesa regression）需要可核实来源；本机是否受影响仍需本机验证。

## 8. Structured actions 是便利协议

可选 decision contract：

- NO_CHANGE
- NEED_MORE_DATA
- INVESTIGATE_POWER_SPIKE
- INVESTIGATE_THERMAL_EVENT
- PROPOSE_POWER_FIX
- PROPOSE_ENVELOPE_TRIAL
- ROLLBACK_TRIAL
- PROMOTE_ENVELOPE
- PROPOSE_MANUAL_RECALIBRATION

拥有 Bash/workspace 的 Agent 不被限制只能使用这些 action。

但无论使用哪种接口，都不能绕过：

- thermal safety
- transactional HWP / read-back / rollback
- current evidence identity
- active trial deterministic Evidence contract
- STABLE readiness
- explicit authorization for destructive runtime reset

## 9. 不要制造永久忙碌

长期目标是收敛。

如果：

- measurement noise 吃掉 candidate effect；
- 搜索邻域已耗尽；
- candidate 反复 equivalent/inconclusive；
- dynamic controller 没实际净收益；
- monitoring overhead 接近收益；
- fixed-good 已足够；

Agent 应建议停止、冻结或删除复杂度。

正确工程结果包括：

- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA
- NO_CHANGE

“系统越复杂”不是成功指标。

## 10. 工程变更后的 Agent 闭环

Agent 修改以下任何一项时：

- Evidence semantics
- lifecycle/readiness
- Stage order
- evidence identity
- Agent entry/context
- schema
- trial protocol
- Scheduler
- control/safety
- Net Benefit
- deployment

必须同时检查受影响的：

- PLAN2
- AGENTS
- PROJECT_MAP
- PROJECT_STATUS
- FIRST_RUN / OPERATIONS
- LLM_BEHAVIOR
- tests
- agent-context output

架构级变更完成后运行：

```bash
bash scripts/quality-gate.sh
```

软件测试通过只能说明 SOFTWARE-VALIDATED；不能替代真实 Surface Pro 7 的 Stage A–E evidence。
