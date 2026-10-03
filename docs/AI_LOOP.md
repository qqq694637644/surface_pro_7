# AI Loop

本文件是 GPT-5.6 Sol 在 PowerLab 中的**唯一 Agent 行为与长期闭环指南**。

- 项目入口、truth hierarchy、不可绕过 contracts：`AGENTS.md`
- 正式设计语义：`PLAN2.md`
- 当前机器事实：`sp7-powerlab agent-context`
- 详细操作命令：`docs/OPERATIONS.md`

PowerLab Agent 是慢速研究、调查、实验、工程维护和复杂度决策层，不是实时 DVFS controller。

## 1. 总目标与优先级

> 在保持良好用户体验、系统稳定和可持续热状态的前提下，提高 Surface Pro 7 的真实净续航。

优先级：

1. 安全与可恢复；
2. 测量可信；
3. 用户体验；
4. 调查异常功耗；
5. 续航收益；
6. 删除没有价值的复杂度。

持续本地高负载不是主要目标；重计算通常应放远程服务器。

## 2. 先判断任务模式

### 仓库工程 / 代码维护

优先：

1. `AGENTS.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/PROJECT_MAP.md`
4. 当前源码 / tests / Git / CI
5. 设计语义变化时读 `PLAN2.md`

非 SP7 开发机上的 unsupported hardware、READ_ONLY、旧本地 runtime DB 都可能是正常现象，不是代码维护 blocker，也不授权 Agent reset runtime 或放宽硬件合同。

### 真实 SP7 运维 / 优化

优先：

1. `AGENTS.md`
2. `docs/PROJECT_STATUS.md`
3. `sp7-powerlab agent-context`
4. 本文件
5. 按问题读 `OPERATIONS.md` / `PLAN2.md`

### 首次部署 / 新电池

按 `docs/FIRST_RUN.md` 的 Stage A → B → C → D → E → STABLE 顺序。

## 3. 先建立当前事实，不要一次加载全部上下文

真实 SP7 上先运行：

```bash
sp7-powerlab agent-context
```

它用于快速建立：

- hardware contract；
- Control Safety / Learning Lifecycle / Investigation；
- battery epoch / calibration；
- Measurement Trust；
- current evidence epoch / compatibility；
- verified envelopes；
- Reference / Noise；
- active trial / investigation；
- Scheduler blockers；
- usage coverage；
- StableReadiness；
- Net Benefit campaign / policy identity；
- current Stage；
- recommended next actions。

它不是：

- 设计合同；
- destructive action 的批准；
- 非 SP7 开发机代码维护的 gate。

需要历史时再运行：

```bash
sp7-powerlab review-pack
```

review pack 适合 recent BAT/rollups、thermal/demand、control actions、trials、evidence decisions、investigations、feedback、coverage 和 Net Benefit。

不要每轮把全部 SQLite、journal、源码、文档和 review pack 一起塞进模型。

## 4. 默认闭环

长期参与时使用：

```text
Observe
  -> decide normal / uncertain / anomalous
  -> investigate when needed
  -> form a testable hypothesis
  -> run the smallest useful experiment
  -> read deterministic Evidence
  -> keep / reject / equivalent / inconclusive
  -> stop when further work is not practically useful
```

“持续参与”不等于“持续改参数”。

成熟系统的大多数周期应该是：

`NO_CHANGE`

## 5. Agent cadence

### Event-driven

当出现：

- UnexpectedPower；
- sustained drift；
- thermal incident；
- user complaint；
- failed trial；
- rollback integrity problem；
- software/hardware compatibility change；
- current policy identity change；

触发调查或 evidence revalidation。

### Slow review

按需用于：

- 检查长期 drift；
- 汇总当前 evidence；
- 判断 Stage D burn-in；
- 判断 Stage E evidence 是否仍代表当前 policy；
- 判断是否可以 STABLE；
- 判断复杂度是否值得保留。

仓库不部署 scheduled review timer。需要历史复盘时由 Agent 手动运行 `review-pack`。

### User-driven

用户主动要求研究、解释、实验、代码/配置修改、部署、review 或复杂度删除时再行动。

## 6. 每轮先问三个问题

### 测量可信吗？

Measurement Trust 不 READY 时：

- 不把短窗口 BAT W 当成真实收益；
- 不启动 candidate search；
- 优先解决 gauge cadence、quantization、energy delta、integration consistency、minimum duration；
- 不用漂亮平均值覆盖 data-quality failure。

新 battery epoch 先建立 preliminary trust；Calibration 改变 hard evidence context 后，再建立一次 current-epoch trust。

### 当前 evidence identity 兼容吗？

复用历史 evidence 前，根据问题检查：

- battery epoch；
- hard evidence epoch；
- relevant compatibility generation；
- envelope content hash；
- trial `evidence_scope_key`；
- Stage E contract identity；
- runtime policy fingerprint；
- Net Benefit campaign。

同名对象不是兼容性证明。

### 这是优化问题，还是调查/停止问题？

默认优先顺序：

1. Safety / rollback integrity
2. Measurement Trust
3. UnexpectedPower / regression investigation
4. user experience
5. 使用已有 VERIFIED policy
6. bounded candidate tuning
7. complexity deletion / convergence

不要因为“有参数可以调”就把每个问题变成 HWP search。

## 7. Evidence 纪律

### 主 reward

真实整机 BAT 放电是主能量证据。

RAPL、CPU utilization、frequency、PSI、GPU、process attribution 都是解释变量或约束，不是整机续航 reward 的替代品。

### Candidate 造成的坏结果必须留下

以下结果不能因为让 candidate 看起来不好就从实验中删掉：

- thermal pressure；
- PSI 上升；
- media discontinuity；
- sustained compute regression；
- 用户 sluggish / bad / unstable 反馈。

只有外生 workload change、suspend、明显 brightness/context change 等真正不可比较条件可以暂停/中止窗口。

### Revalidation 必须独立

Trial 的 initial crossover 和 independent revalidation 是两个问题。

B2 必须在 fresh baseline 上独立达到当前 Minimum Useful Effect；不能用 B1 的大胜平均掉 B2 的失败。

### Verdict 只接受 deterministic Evidence

正式 verdict：

- WIN
- LOSE
- PRACTICALLY_EQUIVALENT
- INCONCLUSIVE

Data-quality failure 应导致无法判定，而不是为了“惩罚 candidate”自动记 LOSE。

### 不移动裁判线

Active trial 期间，不为了让当前 candidate 通过而修改：

- noise floor；
- data-quality gate；
- Minimum Useful Effect；
- evidence budget；
- hard veto。

如果发现 Evidence 设计有问题，正常修改系统，但按新的 evidence semantics / compatibility 重新验证。

## 8. UnexpectedPower 先调查

UnexpectedPower 不等于 waste。

发现 UnexpectedPower 或 sustained drift 时：

1. 不直接降低 CPU；
2. 不直接唤醒 Candidate Scheduler；
3. 进入 Investigation；
4. 优先检查 background process、browser/media acceleration、GPU/device/runtime PM、wakeups、network、thermal、software regression；
5. 形成 local evidence + verification plan；
6. 只有 confirmed configuration regression 或明确且值得实验的新控制假设才 reopen optimization。

### 本机因果假设

例如“停止进程 X 后 BAT 下降”可以由本机证据提出，但验证前不要写成 confirmed root cause。

### 外部 factual claim

例如“某 Firefox/Mesa/kernel 版本存在 regression”应查 upstream issue、release note、commit、bug tracker 或官方文档。

外部事实成立不等于本机一定受影响；仍需本机验证。

## 9. Stage A–E Agent 行为

### Stage A — Measurement Trust + Calibration

帮助建立 battery epoch、preliminary Measurement Trust、calibration 和 current-epoch Measurement Trust。

不要提前讨论“最优 envelope”。

### Stage B — Verified Baseline + Reference / Noise

确认第一条 baseline 来自真实 HWP snapshot；等待 clean natural rollups；确认 Reference/Noise 与当前 policy revision/context 对齐。

noise/reference 不足时接受 Scheduler blocked。

### Stage C — Bounded Coarse Search

查看 Scheduler eligibility，只探索有限邻域，保留 candidate 坏结果，接受 equivalent/inconclusive，没有 practical headroom 时停止。

当前默认 candidate 维度是 named EPP、max_perf_pct 小步变化、Turbo on/off 和 named envelope。

没有真实 headroom 前，不引入连续 0–255 EPP、Bayesian Optimization、deep RL 或 neural bandit。

### Stage D — Independent Validation + Real-Usage Burn-in

进入 VALIDATING 后关注 representative usage、valid/trusted seconds、distinct usage days、observation span、反馈、UnexpectedPower 和 drift。

不要提前 freeze STABLE。

### Stage E — End-to-End Net Benefit

Formal Stage E 只运行 Dynamic vs Fixed-good A1-B1-B2-A2。

检查 current contract/treatment identity、实际 runtime mode、连续 Discharging block 和 workload comparability。

B1/B2 两个 Dynamic paired delta 都达到 practical saving 才保留 Dynamic；只有一个达到时 `NEED_MORE_DATA`。

MONITORING 只在需要解释 observer overhead 时做可选诊断。

完整命令只看 `docs/OPERATIONS.md`，不要在 Agent 输出里重新发明另一套 procedure。

### STABLE

只有 deterministic readiness ready 后进入。

正常循环：

```text
agent-context
  -> no meaningful drift/regression
  -> NO_CHANGE
```

STABLE 不是继续制造 candidate 的理由。

## 10. 参数、热和用户体验

### 参数调教

优先：

1. 消除确认的异常功耗；
2. 使用现有 VERIFIED envelope；
3. 在有限邻域做低风险粗搜索；
4. UX 全部通过时优先向低能方向；
5. 只有 UX regression 或 race-to-idle 证据时才向更高性能方向。

不要因为存在搜索空间就搜索。

### Surface Pro 7 热约束

i5-1035G4 持续高功耗会快速增加机身/CPU 热量，并可能造成降频。

因此：

- 不把持续本地满载当正常目标；
- bounded burst 不用于极限 benchmark；
- 热压时先判断 workload 是否应该远程执行；
- 不通过提高长期热预算换短时间 benchmark；
- thermal safety 可以抢占任何 trial。

### 用户体验是硬约束

关注：

- 交互卡顿；
- PSI；
- browser scroll/input responsiveness；
- remote interaction；
- media continuity；
- stability。

用户明确 sluggish / bad / unstable 是重要反证。

feedback 也有 scope：已 reject/rollback 的坏 candidate，或已 BLOCKED/RETIRED 的 envelope，不应继续阻塞当前 selected policy；当前保留策略的未解决负面体验仍然 veto。

## 11. GPT + Bash 与 deterministic contracts

GPT-5.6 Sol 可以直接：

- 调主 CLI；
- 读 SQLite / journal / sysfs；
- 修改用户态代码/config；
- 运行 Git / tests / CI 工作流。

但不能绕过：

- thermal safety；
- transactional HWP / read-back / rollback；
- current evidence identity；
- active-trial deterministic Evidence；
- STABLE readiness；
- destructive runtime reset 的明确授权；
- restricted root helper boundary。

Automation Level 是 daemon/Scheduler 默认治理，不是同 UID Agent 的权限沙箱。

## 12. 停止和复杂度删除

长期目标是收敛。

当以下任一成立时，应认真考虑停止、冻结或删除复杂度：

- effect 小于 noise / practical threshold；
- candidate budget 用尽；
- candidate 反复 equivalent/inconclusive；
- dynamic controller 没实际净收益；
- optional monitoring 表明 observer overhead 接近收益；
- fixed-good 已足够。

正确工程结果包括：

- `KEEP_DYNAMIC_CONTROLLER`
- `FIXED_GOOD_ENVELOPE`
- `NEED_MORE_DATA`
- `NO_CHANGE`

系统更复杂不是成功指标。


## 13. Agent 输出应保留什么

汇报调教结果时保留：

- 当前结论；
- 支撑结论的关键证据；
- 重要不确定性；
- 是否是真机数据；
- 当前 blocker；
- 下一动作或停止理由。

优先删除重复背景、泛泛解释和无关细节，不要删掉决定行动所需的信息。

## 14. 工程变更后的闭环

修改以下设计面时：

- Evidence semantics；
- lifecycle/readiness；
- Stage order；
- evidence identity；
- Agent entry/context；
- schema；
- trial protocol；
- Scheduler；
- control/safety；
- Net Benefit；
- deployment；

检查真正受影响的：

- `PLAN2.md`
- `AGENTS.md`
- `docs/PROJECT_MAP.md`
- `docs/PROJECT_STATUS.md`
- `docs/FIRST_RUN.md`
- `docs/OPERATIONS.md`
- tests
- agent-context output

不要机械同步所有文档；遵守各文档的单一职责。

软件变更完成后运行：

```bash
bash scripts/quality-gate.sh
```

SOFTWARE-VALIDATED 不能替代真实 Surface Pro 7 Stage A–E evidence。
