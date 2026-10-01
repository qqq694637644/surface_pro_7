# PowerLab Project Map

这是 PowerLab 的静态导航地图。它回答“系统由什么组成、想查某件事应该看哪里”。

它不保存当前机器状态，也不判断当前阶段。实时状态请运行：

sp7-powerlab agent-context

AI 首次进入仓库先读根目录 AGENTS.md。

## 1. 总体架构

主控制链：

~~~
Linux telemetry
    |
    v
TelemetryCollector
    |
    +--> DemandObserver
    +--> ThermalObserver
    |
    v
BatteryLifeController
    |
    v
VERIFIED Envelope
    |
    v
transactional HWP actuator
    |
    v
intel_pstate / Intel HWP
~~~

测量 / 学习链：

~~~
BAT + telemetry
    |
    v
Measurement Trust
    |
    v
current hard evidence epoch
    |
    v
clean Power rollups
    |
    +--> FrozenReferenceBaseline
    +--> RecentNoiseDistribution
    |
    v
CandidateScheduler
    |
    v
A/B/A + independent revalidation
    |
    v
deterministic EvidenceDecision
    |
    v
VERIFIED winner / reject / equivalent / inconclusive
~~~

异常链：

~~~
UnexpectedPower / sustained drift
    |
    v
Investigation
    |
    v
Attribution
    |
    +--> EXPECTED_WORKLOAD_CHANGE
    +--> INSUFFICIENT_EVIDENCE
    +--> SUSPECTED_REGRESSION
    +--> ACTIONABLE_WASTE
    +--> CONFIRMED_CONFIG_REGRESSION
                         |
                         v
                 reopen optimization
~~~

收敛链：

~~~
Stage D validation / real-usage burn-in
        +
Stage E bounded Net Benefit campaign
        |
        v
StableReadiness
        |
        v
STABLE -> monitor / drift / NO_CHANGE
~~~

AI 不在 10 秒级实时控制回路中。AI 读取聚合事实、调查异常、设计/审查实验、修改代码和配置、判断是否应继续保留复杂度。

## 2. 运行时三个正交状态

### Control Safety State

回答：现在能不能写机器。

状态：

- CONTROL_ALLOWED
- READ_ONLY
- DEGRADED
- EMERGENCY

主要实现：

- src/sp7_powerlab/lifecycle.py
- src/sp7_powerlab/controller.py
- src/sp7_powerlab/hardware.py
- src/sp7_powerlab/actuators/hwp.py
- src/sp7_powerlab/helper.py

### Learning Lifecycle

回答：系统现在应该积累哪类证据，Scheduler 是否应该工作。

状态：

- CALIBRATING
- BASELINE_OBSERVATION
- COARSE_OPTIMIZATION
- VALIDATING
- STABLE
- REOPENED

主要实现：

- src/sp7_powerlab/lifecycle.py
- src/sp7_powerlab/longterm.py
- src/sp7_powerlab/scheduler.py

### Investigation Status

回答：是否正在调查异常功耗或 drift。

状态：

- IDLE
- INVESTIGATING

主要实现：

- src/sp7_powerlab/unexpected_power.py
- src/sp7_powerlab/attribution.py
- src/sp7_powerlab/lifecycle.py

这三个状态可以同时存在。例如：

~~~
Control = CONTROL_ALLOWED
Learning = STABLE
Investigation = INVESTIGATING
~~~

此时可以继续安全使用 verified envelope，但 Scheduler 不应因为一次异常直接开始调参。

## 3. Evidence identity 地图

不同层有不同 identity，不能用“名字一样”代替兼容性判断：

- battery epoch：电池 identity/capacity context
- hard evidence epoch：battery/kernel/power-driver/calibration/thermal/core semantics 边界
- compatibility generation：browser/Mesa/media 等局部兼容边界
- envelope content hash：natural Reference/Noise 的 policy revision identity
- evidence_scope_key：hard epoch + compatibility + baseline + workload/reference strata + candidate
- runtime policy fingerprint：Stage E 的实际 treatment identity
- Net Benefit campaign：有 OPEN/COMPLETE/INVALID 生命周期的 bounded validation session

历史数据可以做 prior/diagnosis，但只有满足对应 identity contract 才能进入当前 promotion、trusted
coverage 或 STABLE readiness。

## 4. 源码地图

### src/sp7_powerlab/agent_context.py

AI 动态入口。

聚合：

- Git/schema/version
- hardware contract
- control safety
- learning lifecycle
- battery epoch
- calibration
- measurement trust
- evidence epoch/reference/noise
- active trial
- active investigation
- Scheduler eligibility
- usage coverage
- net benefit
- current runtime stage
- recommended next actions

CLI：

sp7-powerlab agent-context

### src/sp7_powerlab/telemetry.py

负责低层 telemetry 收集和分层采样。

包含：

- BAT
- CPU/load/frequency/HWP
- PSI
- RAPL
- thermal
- brightness
- network
- GPU/device hints
- process attribution
- ActivityWatch enrichment

STABLE 下昂贵 attribution 会降频；trial/calibration 或 Diagnostic Burst 会提高采样密度。

### src/sp7_powerlab/measurement.py

Measurement Trust 基础。

负责：

- BAT power integration
- battery energy delta
- consistency check
- battery gauge quantum
- power/energy update cadence
- minimum arm duration 的 gauge 约束
- MinimalMeter

### src/sp7_powerlab/evidence.py

Evidence Engine 和经验噪声模型。

负责：

- hard strata key
- relevant compatibility generation / evidence_scope_key
- FrozenReferenceBaseline
- RecentNoiseDistribution
- Minimum Useful Effect
- noise-constrained minimum arm duration
- CrossoverEpisode 构造
- WIN / LOSE / PRACTICALLY_EQUIVALENT / INCONCLUSIVE

这是实验判决的唯一事实源。

### src/sp7_powerlab/evaluation.py

只比较实验约束和 outcome。

例如：

- PSI
- thermal
- media continuity
- sustained compute
- brightness/workload comparability

它不再产生最终 winner verdict。

### src/sp7_powerlab/experiments.py

Trial 状态机。

核心协议：

~~~
A1 baseline
B1 candidate
A2 baseline
    |
    v
initial provisional evidence
    |
    v
A3 fresh baseline
B2 candidate
    |
    v
independent revalidation
~~~

Candidate 造成的坏结果必须保留。

离开可比窗口时恢复 baseline，不允许把 candidate 长期留在机器上“等待以后凑数据”。

### src/sp7_powerlab/scheduler.py

Candidate Scheduler。

只搜索有限、可解释、低风险邻域：

- max_perf_pct 小步变化
- named EPP
- Turbo on/off
- verified envelope 邻域

负责：

- measurement/noise/headroom gate
- automation level gate
- weekly trial budget
- daily candidate exposure budget
- negative feedback cooldown
- thermal cooldown
- low battery gate
- finite retry
- CandidateFrontier 以 evidence_scope_key 为 identity，不以裸 candidate content hash 覆盖历史
- stop rules

默认优先向低能方向搜索；提高性能主要用于 UX rescue 或有证据支持的 race-to-idle 假设。

### src/sp7_powerlab/controller.py

确定性 Battery-Life Controller。

负责：

- 从 demand/thermal/override 选择 verified envelope
- minimum dwell
- actual HWP state reconcile
- ControlSafetyState gate
- thermal preemption
- transaction failure 后降级

Controller 不调用 LLM。

### src/sp7_powerlab/service.py

主 runtime orchestrator。

负责把 telemetry、demand、thermal、lifecycle、controller、trial tick、rollup、NoiseTracker、Drift、
UnexpectedPower、Scheduler 和 hardware refresh 串成一个服务循环。这里也是 sample-time envelope content
hash 冻结、rollup reference eligibility、actuator rebind/reconnect policy 等跨模块运行时合同的连接点。

### src/sp7_powerlab/envelopes.py

Envelope registry。

区分：

- config 中的 candidate 参数
- 当前 hard epoch 下真正 VERIFIED 的 envelope
- NEEDS_REVALIDATION
- BLOCKED

配置文件不是验证事实。

### src/sp7_powerlab/actuators/hwp.py

HWP 事务执行。

职责：

- snapshot
- apply
- read-back
- rollback

普通控制和 trial 都不能留下半套 HWP 参数。

### src/sp7_powerlab/helper.py

root helper。

只暴露有限 HWP inspect/snapshot/apply/restore。

不是 root shell。

### src/sp7_powerlab/thermal.py

本地 thermal pressure observer。

validated thermal safety provider 当前是 thermald；PowerLab thermal observer 用于：

- pressure state
- trial outcome
- preemption
- heat-soak / cooldown 判断

### src/sp7_powerlab/hardware.py

硬件契约和系统 fingerprint。

检查：

- Surface Pro 7 DMI
- i5-1035G4
- intel_pstate/HWP
- Turbo control
- battery
- RAPL
- package thermal sensor
- thermald
- ownership conflicts

### src/sp7_powerlab/unexpected_power.py

UnexpectedPower detector。

只说明：

> 实际整机功耗明显偏离当前合理预期。

它不直接把事件叫 waste，也不直接触发 Scheduler。

### src/sp7_powerlab/attribution.py

把 investigation 的本机证据组织成 hypothesis 和 verification plan。

本机因果假设不要求互联网 source；涉及外部软件事实时应由 Agent 补可核查来源。

### src/sp7_powerlab/longterm.py

长期收敛层。

负责：

- UsageCoverage
- StableReadiness
- DriftDetector
- runtime policy fingerprint
- explicit Level-1 Dynamic runtime code identity
- separate Stage E measurement-contract code identity
- actual runtime mode validation
- MinimalMeter A1-B1-B2-A2 paired comparison
- brightness/active/media/remote/network/temperature comparability veto
- fixed baseline content hash / campaign coherence gate
- Net Benefit campaign OPEN/COMPLETE/INVALID lifecycle + max span
- recommendation -> selected policy fingerprint mapping；STABLE 校验 selected policy 仍是当前 runtime
- minutes gained per charge
- Net Benefit assessment

`UsageCoverage` 的 total valid time 包含真实但脏的 transitional usage；只有 `reference_eligible=true`、
frozen envelope content hash 仍与当前 VERIFIED record 匹配且有 FrozenReference 的窗口进入 trusted
coverage。StableReadiness 还要求 minimum usage/trusted seconds、distinct usage days 和 observation span。

### src/sp7_powerlab/minimal_meter_cli.py

Stage E 的低开销 capture 入口。

负责在 capture-time 固定 provenance，并验证实际 runtime mode：

- FIXED_GOOD：service 停止 + actual HWP 每点保持同一 VERIFIED envelope
- DYNAMIC_CONTROLLER：live CONTROL_ALLOWED service + Automation Level 1
- MONITORING：可选诊断模式，live service + Automation Level 0 + fixed HWP

同时采集 brightness、active/media/remote fraction 所需信号、basic network、package temperature 和
actual HWP snapshot，并冻结 runtime policy fingerprint。trial/calibration、hard context 或 policy 变化会让
run INVALID。每个正式 block 还必须是一段连续 Discharging observation；若遗留 scheduled-review unit
仍存在，它们必须保持 inactive。campaign 有独立 DB lifecycle，不允许跨周复用裸字符串拼结果。

正式 STABLE evidence 只要求 Dynamic vs Fixed-good；MONITORING 不参与 readiness。Level 2+ 的
Scheduler/Agent/自动学习是按需能力，不是 formal Stage E treatment。

最终允许系统得出：

- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

### src/sp7_powerlab/lifecycle.py

Control / Learning / Investigation 状态协调器。

### src/sp7_powerlab/storage.py

SQLite schema 和持久化 API。

当前 schema 版本不要从本文件猜；运行 sp7-powerlab agent-context 或读 SCHEMA_VERSION。

高频原始 SQLite 不进 Git。

### src/sp7_powerlab/knowledge.py

构建按需 review pack。它只组织历史事实，不调用 LLM，也不提供 action DSL。

CLI：`sp7-powerlab review-pack [--output PATH]`。

GPT-5.6 Sol 直接通过 Bash 使用主 CLI、SQLite、日志、sysfs 和源码；仓库不再维护第二套 Agent command language。

### src/sp7_powerlab/runtime_audit.py

FIXED_GOOD 的 one-shot 物理事实审计：main service、legacy scheduled units、thermald、ownership、live hard
identity、verified fixed baseline 和 actual HWP snapshot。

### src/sp7_powerlab/cli.py

人类和 Agent 的主 CLI 入口。

## 5. 配置地图

### config/powerlab.toml

系统默认运行参数：

- collector cadence
- controller dwell
- experiment limits
- evidence semantics
- automation level
- scheduler budgets
- stable coverage
- Net Benefit campaign/comparability limits
- drift thresholds

不要把这些数值复制到 PLAN2；运行时以当前 config 为准。

### config/machine.toml

真机 calibration 和 machine-specific 结果。

例如：

- battery epoch
- calibration valid/version
- calibrated baselines
- thermal sensor/path

这是机器相关状态，不应该由仓库默认值假装成真机结论。

### config/envelopes.toml

候选 envelope 参数。

TOML 中存在一个 envelope 不等于它在当前机器/epoch 已 VERIFIED。

### config/thermal.toml

thermal model 参数。

## 6. SQLite 事实模型

关键概念：

- battery_epochs
- calibration_runs
- evidence_epochs
- compatibility_tags
- power_rollups（包含 frozen envelope content hash / reference eligibility）
- reference_baselines
- recent_noise_distributions
- arm_measurements
- crossover_episodes
- evidence_decisions
- candidate_frontier（evidence_scope_key 主键；同 candidate content 可保留多个实验 scope）
- trials
- feedback
- rejections
- control_safety_history
- learning_lifecycle_history
- unexpected_power_events
- investigations
- net_benefit_results
- net_benefit_campaigns
- minimal_meter_runs

不要直接依赖表结构猜业务语义；优先读对应模块和 PLAN2。

## 7. CLI 导航

### “现在机器和项目处于什么状态？”

sp7-powerlab agent-context

### “硬件能不能控制？”

sp7-powerlab doctor

sp7-powerlab safety status

### “calibration 到哪了？”

sp7-powerlab calibrate status

### “测量可信了吗？”

sp7-powerlab evidence trust

sp7-powerlab evidence gauge

### “Evidence/Noise 怎么样？”

sp7-powerlab evidence status

sp7-powerlab evidence noise

### “现在为什么不能继续搜索？”

sp7-powerlab scheduler status

sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT

### “Learning 是否可以进入 STABLE？”

sp7-powerlab lifecycle status

sp7-powerlab lifecycle readiness

sp7-powerlab lifecycle coverage

先完成 Stage D validation/burn-in，再完成 Stage E Net Benefit；readiness 通过后才 freeze STABLE。

### “有异常功耗吗？”

sp7-powerlab unexpected-power list

sp7-powerlab investigation list

### “调查某个事件”

sp7-powerlab investigation inspect <id>

sp7-powerlab investigation attribute <id>

### “PowerLab 自己到底值不值得？”

sp7-powerlab net-benefit history

sp7-powerlab net-benefit summary

### “实验状态”

sp7-powerlab trial status

### “当前 envelope”

sp7-powerlab envelope list

## 8. 常见调查路线

### 电池掉得快

1. agent-context
2. observe power
3. unexpected-power list
4. investigation / attribution
5. 看 process/RAPL/GPU/network/device attribution
6. 如果是外部软件 regression，再查 upstream
7. 只有确认新控制假设后才考虑 Scheduler/trial

### 用户说卡

1. 记录 feedback
2. 检查 current envelope
3. 看 CPU/IO PSI 和 thermal
4. 判断是否是 envelope 过紧、后台负载或 thermal throttling
5. 只有 envelope 过紧时才做 UX rescue candidate

### 浏览器/视频功耗高

优先检查：

- media_playing
- CPU/RAPL
- GPU decode hint
- browser/Mesa compatibility tags
- hardware acceleration
- upstream regression

不要先假设“CPU 上限太高”。

### 远程工作

REMOTE_EFFICIENT 的判断不应依赖某个低 CPU ssh 进程一定进入 top-N。

重点看：

- user active
- remote hint
- local compute pressure
- network
- media
- interaction latency

## 9. 文档地图

- ../AGENTS.md：AI 第一个读
- ../README.md：人类入口
- ../PLAN2.md：设计合同
- PROJECT_STATUS.md：实现成熟度和真机验证状态
- LLM_BEHAVIOR.md：AI 调教纪律
- FIRST_RUN.md：首次真机部署
- OPERATIONS.md：日常维护
- DEPLOYMENT.md：systemd/root helper
- AI_LOOP.md：Agent 与 PowerLab runtime 交互

按任务读，不要机械加载全部文档：仓库工程优先 PROJECT_STATUS + PROJECT_MAP；真实 SP7 运维优先
PROJECT_STATUS + agent-context + LLM_BEHAVIOR/OPERATIONS；设计变更再读 PLAN2。

## 10. 什么时候读 PLAN2

以下情况必须读相关 PLAN2 章节：

- 修改 Evidence semantics
- 修改 trial protocol
- 修改 lifecycle
- 修改 Scheduler 搜索/预算
- 修改 hard epoch / compatibility
- 修改 thermal/control safety
- 修改 STABLE / Net Benefit 定义
- 引入新的 actuator 或新的自动探索维度

普通日志调查、CLI 使用或小 bug 不需要把整个 PLAN2 塞进上下文。
