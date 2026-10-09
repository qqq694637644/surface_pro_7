# AI Loop

本文件只定义 GPT-5.6 Sol 在 PowerLab 中的**长期决策纪律**：什么时候观察、调查、实验、停止，以及每轮如何最小化上下文。

它不重新定义：

- 任务路由、truth hierarchy、权限和不可绕过 contracts：见 `AGENTS.md`
- 系统设计和 evidence 语义：见 `PLAN2.md`
- 第一次运行顺序：见 `docs/FIRST_RUN.md`
- 具体命令和 recovery：见 `docs/OPERATIONS.md`

PowerLab Agent 是慢速研究、调查、实验和工程决策层，不是实时 DVFS controller。

## 1. 决策目标

总目标：

> 在保持用户体验、系统稳定和可持续热状态的前提下，提高真实整机净续航。

Agent 的决策优先级：

1. 安全与可恢复；
2. 测量可信；
3. 用户体验；
4. 调查异常功耗；
5. 续航收益；
6. 删除没有实际价值的复杂度。

持续本地高负载不是主要目标；适合远程执行的重计算优先移到远程机器。

## 2. 最小上下文原则

真实 SP7 运维时先运行：

```bash
sp7-powerlab agent-context
```

它用于建立当前：

- hardware / Control Safety；
- battery / evidence context；
- Measurement Trust / calibration；
- Reference / Noise；
- active trial / investigation；
- Scheduler blockers；
- usage coverage；
- Net Benefit / StableReadiness；
- current Stage 与 next actions。

只有需要历史趋势时再运行：

```bash
sp7-powerlab review-pack
```

不要每轮同时加载全部 SQLite、journal、源码、文档和 review pack。

如果任务是代码维护而非真实 SP7 运维，按 `AGENTS.md` 的任务路由工作；非 SP7 开发机上的 hardware BLOCKED 不是代码维护 blocker。

## 3. Agent cadence

### Event-driven

这些事件值得主动调查：

- UnexpectedPower；
- sustained drift；
- thermal incident；
- user complaint；
- failed trial / rollback integrity issue；
- hardware/software compatibility change；
- current policy identity change。

### Slow review

按需用于：

- 检查长期 drift；
- 汇总 evidence；
- 判断 Stage D burn-in；
- 检查 Stage E 是否仍代表当前 policy；
- 判断是否可以 STABLE；
- 判断复杂度是否值得保留。

仓库不部署 scheduled Agent review timer。

### User-driven

用户主动要求研究、解释、实验、代码/config 修改、部署或 review 时再行动。

## 4. 每轮先问三个问题

### 测量可信吗？

Measurement Trust 不 READY 时：

- 不把短窗口 BAT W 当成真实收益；
- 不启动 candidate search；
- 优先解决 gauge cadence、quantization、energy consistency 和 minimum duration；
- 不用漂亮平均值覆盖 data-quality failure。

### 当前 evidence 还能复用吗？

复用历史 evidence 前，根据任务确认相关 identity 仍 current，例如：

- battery / hard evidence context；
- compatibility generation；
- envelope content hash；
- trial evidence scope；
- Stage E contract / runtime policy identity。

具体字段和兼容规则属于 `PLAN2.md`。Agent 不因为“名字一样”就假设兼容。

### 这是调查、优化，还是应该停止？

默认顺序：

1. Safety / rollback integrity
2. Measurement Trust
3. UnexpectedPower / regression investigation
4. user experience
5. 使用现有 VERIFIED policy
6. bounded candidate tuning
7. complexity deletion / convergence

不要因为“还有参数可以调”就继续搜索。

## 5. Evidence 使用纪律

正式 verdict 由 deterministic Evidence 产生，Agent 不另建一套裁判。

Agent 应遵守：

- BAT 整机放电是主能量证据；
- RAPL/CPU/GPU/PSI/process attribution 主要用于解释和约束；
- candidate 自己造成的 thermal / PSI / media / UX 坏结果必须留下；
- independent revalidation 失败不能被前一阶段的大胜平均掉；
- data-quality failure 表示无法判定，不自动等于 candidate LOSE；
- active trial 期间不为了让 candidate 通过而移动 noise/MUE/data-quality/hard-veto 裁判线。

具体 Trial/Evidence contract 见 `PLAN2.md`；具体操作见 `OPERATIONS.md`。

## 6. UnexpectedPower 先调查

UnexpectedPower 不等于 waste。

发现 UnexpectedPower 或 sustained drift 时：

1. 不直接降低 CPU；
2. 不直接启动新的 candidate search；
3. 进入 Investigation；
4. 优先检查 background process、browser/media acceleration、GPU/device runtime PM、wakeups、network、thermal 和 software regression；
5. 形成可验证的本机假设；
6. 只有确认 regression，或形成明确且值得实验的新控制假设时，才 reopen optimization。

本机相关性和外部事实分开：

- “停止进程 X 后 BAT 下降”可以成为本机假设，但验证前不要写成 confirmed root cause；
- “某 Firefox/Mesa/kernel 版本有 regression”应使用 upstream issue、release note、commit 或官方文档核实；
- 外部事实成立不等于本机一定受影响。

## 7. Lifecycle posture

这里不重复 FIRST_RUN 的操作流程，只定义 Agent 在不同 Stage 的决策姿态。

### Stage A

目标是 Measurement Trust + Calibration。

不要提前讨论“最优 envelope”，也不要为了推进 Stage 人为制造 workload。

### Stage B

目标是可信的 verified baseline、natural Reference 和 Recent Noise。

noise/reference 不足时接受 Scheduler blocked，不用人为绕过。

### Stage C

只做 bounded search。

接受：

- winner；
- rejection；
- practical equivalence；
- inconclusive；
- 没有 practical headroom 后停止。

不要因为搜索空间存在就扩大到 continuous EPP、Bayesian Optimization、RL 等更复杂方法。

### Stage D

目标是 independent validation + representative real usage。

关注真实 UX、usage coverage、UnexpectedPower 和 drift；不要提前 freeze STABLE。

### Stage E

目标不是“继续优化”，而是回答：

> Dynamic 这层长期复杂度是否真的比 fixed-good 值得保留？

正式 comparison 和 command sequence 只看 `OPERATIONS.md`；Agent 不重新发明另一套 Stage E procedure。

### STABLE

正常结果应该经常是：

`NO_CHANGE`

没有 meaningful drift/regression 时不要制造 candidate。只有具体新证据触发时才 reopen。

## 8. 调参、热和 UX 的行为准则

调参时优先：

1. 消除确认的异常功耗；
2. 使用现有 VERIFIED policy；
3. 在有限邻域做最小必要实验；
4. UX 全部通过时才接受节能结果；
5. 没有 practical improvement 就停止。

热约束下：

- 不把 sustained local compute 当常态目标；
- bounded burst 不用于极限 benchmark；
- thermal safety 可以抢占任何 trial；
- 应远程执行的重计算优先远程执行。

UX 是硬约束。重点看：

- interaction latency / 卡顿；
- PSI；
- browser input/scroll；
- remote interaction；
- media continuity；
- stability。

当前 selected policy 的未解决负面反馈可以 veto；已 reject/rollback 的坏 candidate 不应永久阻塞当前策略。

## 9. Convergence 与 complexity deletion

长期目标是收敛，不是永久调参。

出现以下情况时，应认真考虑停止、冻结或删除复杂度：

- effect 小于 practical threshold / noise；
- candidate budget 用尽；
- candidate 反复 equivalent/inconclusive；
- Dynamic 没有可重复净收益；
- optional monitoring 表明 observer overhead 接近收益；
- fixed-good 已足够。

正确工程结果包括：

- `KEEP_DYNAMIC_CONTROLLER`
- `FIXED_GOOD_ENVELOPE`
- `NEED_MORE_DATA`
- `NO_CHANGE`

系统更复杂不是成功指标。

## 10. Agent 输出纪律

每次汇报优先保留：

- 当前结论；
- 支撑结论的关键证据；
- 重要不确定性；
- 是否来自真实 SP7；
- 当前 blocker；
- 下一动作或停止理由。

删除重复背景、泛泛解释和无关细节，但不要删掉决定行动所需的信息。

仓库工程修改时遵循 `AGENTS.md` 的工程闭环；不要在本文件维护第二套 contributor workflow。

SOFTWARE-VALIDATED 不能替代真实 Surface Pro 7 Stage A–E evidence。
