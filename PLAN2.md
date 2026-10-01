# Surface Pro 7 PowerLab — PLAN2

PLAN2 是 PowerLab 的正式设计合同。

它只定义系统必须保持的语义和验收边界，不保存实时机器状态，也不记录当前实现进度。

- 当前机器事实：运行 `sp7-powerlab agent-context`
- 当前实现成熟度：`docs/PROJECT_STATUS.md`
- 源码/CLI 导航：`docs/PROJECT_MAP.md`
- Agent 行为纪律：`docs/LLM_BEHAVIOR.md`

---

## 1. 目标与范围

目标硬件：

- Microsoft Surface Pro 7
- Intel Core i5-1035G4
- Linux
- intel_pstate / Intel HWP

唯一优化目标：

> 在用户体验良好、系统稳定、热状态可持续的前提下，降低真实整机 BAT 放电，提高实际续航。

这不是最低 CPU 功耗目标。整机续航同时受 display、GPU、Wi-Fi、browser/media、后台进程、设备、电源管理、thermal 和 PowerLab 自身开销影响。

### 使用假设

Surface Pro 7 持续高本地功耗会快速积热并可能降频。

本项目不把持续本地重计算作为主要 workload。长编译、训练、批处理和长时间计算通常放远程服务器。

本机重点优化：

- 日常交互
- 浏览/阅读
- 轻量开发
- 媒体
- 远程工作
- idle/background
- 软件/设备异常功耗

### 成功允许“更简单”

正确结果可以是：

- fixed-good verified envelope 已足够好；
- dynamic controller 有价值，但高开销 monitoring 没价值；
- full PowerLab 有明确净收益；
- 多个 envelope 可以合并；
- Scheduler 已没有值得继续搜索的 headroom。

系统必须知道何时停止。

---

## 2. 核心架构与不变量

主运行链：

~~~
Telemetry
  -> Demand / Thermal
  -> Deterministic Controller
  -> VERIFIED Envelope
  -> transactional HWP
  -> real BAT / UX / thermal outcome
~~~

学习链：

~~~
Telemetry / MinimalMeter
  -> Measurement Trust
  -> Frozen Reference + Recent Noise
  -> Evidence Engine
  -> finite Candidate Scheduler
  -> controlled Trial
  -> EvidenceDecision
~~~

异常链：

~~~
UnexpectedPower / Drift
  -> Investigation
  -> Attribution
  -> verification
  -> expected/no change
     or confirmed regression/reopen
~~~

### 不变量

1. **BAT 整机放电是主能量证据。**
   RAPL、CPU、PSI、GPU、process、network 是 attribution/constraint，不替代整机续航 reward。

2. **快控制与慢控制分离。**
   Linux scheduler / HWP 负责瞬时 DVFS；PowerLab 只表达 EPP、max_perf_pct、Turbo、named envelope 等慢意图。

3. **Safety 独立于学习。**
   validated thermal safety、transaction/read-back/rollback、battery/suspend/ownership gate 不能因为 AI/Scheduler 想继续实验而关闭。

4. **Active trial 不能移动裁判线。**
   不得为了当前 candidate 临时放宽 data-quality、noise、MUE、evidence budget 或 UX/thermal/media veto。

5. **Candidate 造成的坏结果必须保留。**
   thermal pressure、PSI、media discontinuity、stability、用户卡顿都是真实 outcome，不能作为“不再 comparable”被过滤。

6. **AI 不进入实时控制主链。**
   AI 可以调查、解释、设计实验、修改代码/配置和维护 Git，但不做每 10 秒 DVFS 微调。

---

## 3. Measurement Trust、Calibration 与 Noise

### 3.1 Measurement Trust

在优化之前先证明测量可信。

至少覆盖：

- BAT power integration
- battery energy delta
- power_now / energy_now cadence
- gauge quantization
- suspend/gap handling
- charging/discharging handling
- integration-vs-energy consistency
- minimum useful arm duration

仅有效 Discharging 区间进入 reward。

无效区间包括：

- Charging / AC
- suspend gap
- resume grace
- 缺失或异常样本
- 超过 max gap

`energy_now` endpoint delta 用于一致性检查。短窗口因 gauge 量化不变化时，不制造虚假精度。

长时间 Stage A 观察可以包含 Charging、suspend/resume 或采样 gap，但这些边界必须把数据切成
独立的 contiguous valid-discharge window。每个达到 gauge/config 最小时长的 window 单独比较：

~~~
integral(power_now)  vs  energy_now(first) - energy_now(last)
~~~

不能因为全局 observation 被打断、全局 endpoint delta 不可用，就只累计多个短 Discharging
片段的 valid seconds 后宣布 Measurement Trust READY。READY 至少要求配置数量的可检查 window，
且已检查 window 不得出现 consistency mismatch。

Measurement Trust 输出：

- READY
- BLOCKED

Scheduler 在 BLOCKED 时不探索。

### 3.2 新 battery epoch 顺序

新电池或新 battery epoch：

~~~
battery epoch
  -> preliminary Measurement Trust
  -> calibration
  -> new/current hard evidence epoch
  -> Measurement Trust again
  -> verified baseline
  -> natural reference/noise
~~~

旧坏电池数据不能直接与新电池 evidence 合并。

### 3.3 Calibration

阶段：

- cold_idle
- normal_interactive
- media
- bounded_burst

Calibration 只使用有效 Discharging 数据。

bounded_burst 用于短时 thermal inertia / RAPL / throttling 观察，不是 sustained benchmark。

### 3.4 Frozen Reference 与 Recent Noise

必须同时维护两类基线：

**FrozenReferenceBaseline**

回答：

> 和当前 hard epoch 刚稳定时相比，是否发生长期退化？

建立后默认冻结，不能随 recent power 变高自动漂移。

**RecentNoiseDistribution**

回答：

> 最近自然波动有多大？

用于 MUE、drift 和 experiment budget，可维护例如 24h / 7d / 30d。

自然 Frozen Reference / Recent Noise 的输入必须比普通 usage telemetry 更干净：整个 rollup 内
power source 保持 Discharging、没有 resume grace、没有超限 sample gap、battery/evidence epoch
单一，并达到 minimum valid-discharge fraction。被 Charging/resume/gap 切碎的 transitional
rollup 可以保留作普通 telemetry，但不能作为一个完整 reference/noise observation。

### 3.5 避免 bucket explosion

个人单机数据只使用少量主分层，例如：

- hard evidence epoch
- effective envelope
- active/idle
- media/non-media
- remote/local

自然 Frozen Reference / Recent Noise 必须按稳定的 brightness bucket 隔离，因为屏幕功耗可能大于 CPU candidate effect。
trial 自身仍把 brightness 当 comparison constraint，而不是把所有连续变量都加入 hard trial strata。
thermal start、network/workload、data quality、compatibility 等继续优先作为 comparison constraints/covariates，避免笛卡尔积 bucket。

### 3.6 Minimum Useful Effect

概念上：

~~~
MUE = max(practical threshold, empirical noise requirement)
~~~

收益长期小于 MUE 时：

`PRACTICALLY_EQUIVALENT`

是正式、正常的结论。

minimum arm duration 同时受到：

- configured minimum
- gauge resolution
- empirical noise
- experiment budget

约束。

---

## 4. Evidence 与 Trial Contract

### 4.1 三层数据语义

**ArmMeasurement**

一个连续 baseline/candidate arm 的真实测量，至少保留：

- start/end
- valid duration
- integrated BAT Wh
- energy delta / consistency
- PSI
- thermal
- media
- workload/comparison constraints
- data quality
- interruption reason

**CrossoverEpisode**

一组能产生 paired effect 的 arm。

Initial：

~~~
A1 + B1 + A2
~~~

Revalidation：

~~~
A3 + B2
~~~

统一符号：

~~~
paired_effect_w < 0
=> candidate saves power
~~~

**EvidenceDecision**

对 compatible crossover evidence 做最终判定。

输出只有：

- WIN
- LOSE
- PRACTICALLY_EQUIVALENT
- INCONCLUSIVE

单独 arm 不叫 episode。

### 4.2 Initial 与独立 Revalidation

Initial：

~~~
A1 baseline
B1 candidate
A2 baseline
~~~

B1 需要形成 provisional improvement。

随后重新采：

~~~
A3 fresh baseline
B2 candidate
~~~

B2 必须独立再次达到当前 MUE。

禁止：

~~~
B1 大胜 + B2 无收益
=> 平均后 promotion
~~~

### 4.3 Evidence Decision

WIN 至少需要：

- paired effect 达到负向 MUE
- 足够独立 crossover
- 方向一致
- data quality 合格
- 无 UX/thermal/media hard veto

LOSE 包括：

- candidate 明显更耗电
- UX hard veto
- thermal hard veto
- media/stability hard veto

PRACTICALLY_EQUIVALENT：

- evidence budget 已足够
- effect 大部分落在 MUE 内

INCONCLUSIVE：

- data quality 不足
- evidence 数量不足
- effect 方向不稳定
- comparison 无法成立

Data-quality failure 不自动算 LOSE。

### 4.4 Evidence Budget

效果越接近 MUE，需要越多独立 evidence。

大且稳定的收益可以更快结束；中等效果需要更多 crossover。

必须有最大预算，不能无限追求统计确定性。

### 4.5 Comparability

外生条件改变可暂停/中止当前 crossover，例如：

- 用户主动换 workload
- brightness 明显改变
- suspend/resume
- power source 变化
- 数据缺口

离开可比窗口时：

- 停止 candidate
- 恢复 baseline
- 返回等待或 abort

不能让 candidate 长期留在机器上等未来“凑够数据”。

### 4.6 Washout / Carryover

Surface Pro 7 thermal carryover 明显。

Washout 使用：

> minimum time + experiment-local state convergence + maximum timeout

至少考虑：

- HWP read-back
- thermal slope
- short-window RAPL
- CPU/IO PSI
- backlog

arm 切换后重置 experiment-local rolling buffers。

300s 等长窗口可用于 heat-soak/trend，但不能直接决定短 arm washout。

### 4.7 用户体验

用户负面反馈是有效反证。

`sluggish / bad / unstable` 可以否决 candidate，即使 BAT 更低。

---

## 5. Evidence Epoch 与 Compatibility

不存在跨所有硬件/软件版本永久有效的“最佳配置”。

### 5.1 Hard Evidence Epoch

以下变化应创建新 hard epoch：

- battery replacement / battery epoch
- CPU power driver / HWP control semantics
- 重要 BIOS/firmware power behavior
- validated thermal safety provider 语义
- thermal sensor identity
- calibration semantics 不兼容变化
- Controller/Evidence 核心语义不兼容变化

旧 hard epoch evidence 不直接用于当前 promotion。

Active trial 必须固定其启动时的 hard evidence epoch。hard epoch 一旦在 trial
期间变化，整次 trial 必须 rollback/终止；旧 arm 不得暂停后在新 epoch 中继续，
也不得在 evaluation 时重新标记成新 epoch evidence。

已经完成但尚未 promotion 的 winner 同样受此约束：只有 trial 的固定 hard
evidence epoch 仍是 current epoch，且当前 Measurement Trust 仍与该 epoch 匹配，
才允许 promotion；否则必须重新验证。

### 5.2 Compatibility Tags

频繁软件变化只局部影响相关 evidence，例如：

- kernel/linux-surface
- browser major
- Mesa
- desktop
- media backend
- workload family

每类 evidence 可声明：

- required-compatible
- advisory
- irrelevant

Firefox major upgrade 不应让 ECO_IDLE 历史整体失效。

### 5.3 Historical Evidence

旧证据可以用于：

- prior
- regression investigation
- search direction
- historical comparison

不能简单当当前 epoch 票数累加。

---

## 6. Control Safety、Lifecycle 与 STABLE

三个状态正交。

### 6.1 Control Safety

状态：

- CONTROL_ALLOWED
- READ_ONLY
- DEGRADED
- EMERGENCY

回答：

> 当前允许什么控制动作？

validated thermal safety provider 失效时，可以只让 Control 进入 READ_ONLY，而不重置 Learning。

### 6.2 Learning Lifecycle

状态：

- CALIBRATING
- BASELINE_OBSERVATION
- COARSE_OPTIMIZATION
- VALIDATING
- STABLE
- REOPENED

回答：

> 当前应该积累什么 evidence，Scheduler 是否应该工作？

### 6.3 Investigation

状态：

- IDLE
- INVESTIGATING

例如：

~~~
Control = CONTROL_ALLOWED
Learning = STABLE
Investigation = INVESTIGATING
~~~

此时可以继续使用 verified envelope，但 Scheduler 不应因为一次异常直接搜索。

### 6.4 STABLE

STABLE 是正常目标状态。

STABLE 下：

- Controller 继续工作
- core telemetry 继续
- drift / UnexpectedPower detector 继续
- expensive attribution 降频
- Scheduler 默认睡眠
- 不主动 trial
- AI 最常见结论应是 NO_CHANGE

进入 STABLE 不要求每个理论 demand 都有独立最优 envelope。

核心标准是：

> 最近代表性真实使用时间的大多数已经有可信 verified policy coverage。

还需要：

- Measurement Trust READY
- reference/noise 可用
- 无 active trial
- 无 unresolved investigation/UnexpectedPower
- 无近期严重负面反馈
- Net Benefit 已评估

稀有 workload 可以 fallback 到安全 verified envelope。

### 6.5 Reopen

允许 reopen 的条件：

- 新 hard evidence epoch
- relevant compatibility change
- confirmed configuration regression
- verified envelope 被 BLOCKED
- 明确的新高价值控制假设
- 用户体验投诉且 evidence 指向当前 envelope

UnexpectedPower 本身不是 reopen 条件。

---

## 7. Candidate Scheduler

Scheduler 是有限搜索器，不是永久优化器。

### 7.1 默认搜索空间

第一版只允许可解释的离散邻域：

- max_perf_pct 小步
- named EPP
- Turbo on/off
- named envelope

没有真实 headroom 证据前，不开放：

- EPP 0–255 连续搜索
- arbitrary sysfs tuning
- Bayesian Optimization
- RL / neural policy

### 7.2 搜索方向

UX 正常：

优先 lower-energy neighbors。

只有以下情况优先提高性能：

- 用户报告 sluggish
- PSI/latency 显示 envelope 过紧
- evidence 支持 race-to-idle 可降低整机能耗

另外允许在 local compute pressure 明确为 MODERATE 时做有限、低风险的 race-to-idle probe，
用于产生这类 evidence；LOW/idle 场景不默认向更高性能方向探索。

### 7.3 Eligibility

至少检查：

- Automation Level
- Learning lifecycle
- ControlSafetyState
- active trial/calibration
- active investigation
- Measurement Trust
- verified baseline
- hard evidence epoch
- noise baseline
- CPU/headroom signal
- low battery
- thermal cooldown
- negative-feedback cooldown
- daily/weekly experiment budget
- minimum arm duration 是否超预算

### 7.4 Candidate priority

不使用伪精确复杂加权分数。

使用可审计顺序：

~~~
measurement confidence
  -> practical saving potential
  -> UX/thermal risk
  -> lifecycle/safety
  -> experiment cost
~~~

### 7.5 Stop Rules

停止探索，当：

- 没有 untried eligible neighbor
- effect 小于 MUE
- noise 使实验不经济
- minimum arm 超预算
- retry budget 用尽
- 用户负面反馈 cooldown
- thermal cooldown
- battery 太低
- STABLE
- active investigation
- full system Net Benefit 已不值得继续增加复杂度

---

## 8. UnexpectedPower、Drift 与 Attribution

### 8.1 UnexpectedPower

只表示：

> 实际整机 BAT 明显高于当前合理预期。

它不自动等于 waste。

检测到后：

- 建立 Investigation
- 触发 Diagnostic Burst
- 不直接降低 CPU
- 不直接唤醒 Scheduler

### 8.2 Drift

慢性 drift 同时看：

- FrozenReferenceBaseline
- recent windows
- robust slope

Frozen reference 不随 recent distribution 自动漂移。

### 8.3 Investigation 分类

允许：

- EXPECTED_WORKLOAD_CHANGE
- INSUFFICIENT_EVIDENCE
- SUSPECTED_REGRESSION
- ACTIONABLE_WASTE
- CONFIRMED_CONFIG_REGRESSION

只有 confirmed configuration regression 或明确的新控制假设才允许 reopen optimization。

### 8.4 Attribution

优先检查：

- top processes
- RAPL
- browser/media decode
- GPU
- network
- device/runtime PM
- wakeups
- compatibility changes
- thermal

本机 causal hypothesis 可以由 local evidence 直接提出，但必须有 verification plan。

外部 factual claim 应给 upstream 可核查来源，并在本机验证相关性。

---

## 9. Controller、Thermal 与 Telemetry

### 9.1 Deterministic Controller

正常控制只使用 verified envelope。

必须具备：

- startup/runtime actual-state reconcile
- minimum dwell
- manual override
- thermal preemption
- ControlSafetyState gate

数据库不能被当作 HWP 真实状态；重启/手工 sysfs 修改/系统事件后必须重新读实际状态。

### 9.2 HWP transaction

apply 必须是事务：

~~~
snapshot
 -> apply finite parameters
 -> read-back
 -> success
    or exact rollback
~~~

rollback integrity 不可信时，Control 应升级到只读/紧急状态，而不是继续写。

### 9.3 Thermal

validated thermal safety provider 当前实现可以是 thermald，但设计不永久绑定具体进程名。

PowerLab thermal observer 用于：

- pressure state
- trial outcome
- heat-soak/cooldown
- preemption

不能通过放宽热安全来让 candidate 通过。

### 9.4 Telemetry / Observer Effect

PowerLab 自己也耗电。

采样分层：

**Always-on core**

- BAT
- temperature
- HWP state
- aggregate CPU
- drift essentials

**Expensive attribution**

STABLE 下低频：

- process
- GPU
- device/runtime PM
- wakeups
- detailed network
- ActivityWatch enrichment

**Trial/Calibration**

提高信息密度。

**Diagnostic Burst**

异常时短时提高 attribution，随后恢复低频。

目标：

> PowerLab 越成熟，自己越安静。

---

## 10. Agent 与 Automation

### 10.1 Agent 角色

GPT-5.6 或其他高能力 Agent 可以拥有用户提供的 Bash/workspace/MCP。

适合：

- 读 agent-context
- 调查 UnexpectedPower
- 分析长期历史
- 查 upstream regression
- 提出/审查实验
- 修改用户态代码/配置
- 写测试
- 维护 Git
- 判断复杂度是否值得保留

Agent 不做实时 DVFS controller。

具体工作纪律见 `docs/LLM_BEHAVIOR.md`。

### 10.2 真正保留的硬边界

- root helper 不是 root shell
- HWP path/参数有限
- transaction/read-back/rollback
- validated thermal safety
- active trial deterministic Evidence contract

Automation Level 不是同 UID Bash Agent 的 capability sandbox。

### 10.3 Automation Levels

**Level 0**

只读。

**Level 1**

允许 deterministic controller 使用 verified envelope。

**Level 2**

Scheduler 可以提出 candidate，默认不自动 trial。

**Level 3**

全部 safety/evidence/budget gate 通过后，daemon 可自动开始低风险 trial。

**Level 4**

只有 `auto_promote=true` 且 trial 已是 VERIFIED_WINNER 才允许自动 promotion。

STABLE 下即使 Level 4，也默认不主动探索。

---

## 11. Net Battery Benefit 与复杂度删除

最终不能只看：

> candidate 比 baseline 少多少 W。

必须比较整个系统。

同一 MinimalMeter 下至少评估：

### A. fixed-good

~~~
MinimalMeter + fixed good verified configuration
~~~

### B. monitoring

~~~
MinimalMeter + PowerLab monitoring + fixed configuration
~~~

### C. dynamic controller

~~~
MinimalMeter + dynamic controller
~~~

### D. full PowerLab

~~~
MinimalMeter + full PowerLab
~~~

Net Benefit 直接来自 end-to-end comparison。

MinimalMeter capture 的 provenance 必须在**采集开始时**固定并持久化，至少包括 run/campaign、
capture mode、battery identity/epoch、hard evidence epoch/fingerprint、calibration version、evidence
semantics 和起始 verified envelope。比较时只能读取 capture-time provenance；禁止读取“当前 epoch”
后给旧采集文件事后贴标签。

Net Benefit 使用 gap-aware integrated BAT energy / valid discharge duration 计算 time-weighted mean
power。reference 或 candidate 的 BAT consistency / provenance / data-quality gate 失败时，不产生可供
STABLE 使用的 `candidate_minus_reference_w`。

用于 STABLE readiness 的 monitoring / dynamic / full 三种结果必须来自同一个 hard evidence
epoch 和同一个显式 validation campaign，不能把不同周或不同系统条件下各自最新的一次结果拼成
“完整比较”。

MonitoringOverhead 只用于解释，不从已经包含 monitoring 的结果中重复扣除。

允许结论：

- KEEP_FULL_POWERLAB
- KEEP_DYNAMIC_REDUCE_MONITORING
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

比较尽量采用 block/day-level crossover，避免单纯历史前后比较。

### Complexity Deletion

如果长期没有实际收益，应删除或降级：

- 多 envelope
- dynamic controller
- Candidate Scheduler
- high-frequency attribution
- Agent review cadence
- thermal model 的非安全部分
- full PowerLab

fixed-good 与复杂系统实际续航/UX 等价时，删掉复杂度是成功结果。

---

## 12. 版本、真机验证与验收

### 12.1 Schema / Versioning

设计必须保留这些概念：

- battery epoch
- hard evidence epoch
- compatibility tags
- reference baseline
- recent noise
- ArmMeasurement
- CrossoverEpisode
- EvidenceDecision
- candidate frontier
- trial / feedback
- control/learning history
- UnexpectedPower / investigation
- monitoring / MinimalMeter runs
- MinimalMeter capture provenance

详细字段属于实现，不复制进 PLAN2。

允许破坏式 schema 升级。

语义不兼容时：

- fail-fast
- reset/rebuild
- 不写隐藏兼容层

### 12.2 Git

Git 保存：

- code
- config
- docs
- durable knowledge

不保存高频 raw SQLite、临时 telemetry 和 runtime build 垃圾。

### 12.3 真机验证阶段

**Stage A — Measurement Trust / Calibration**

- hardware contract
- battery epoch
- gauge cadence/quantization
- BAT consistency
- Measurement Trust READY
- calibration
- HWP read-back/rollback

**Stage B — Reference / Noise**

- first real verified baseline
- FrozenReference
- RecentNoise
- practical threshold 初步验证
- monitoring overhead 初步量化

**Stage C — Coarse Search**

- 只测试有限邻域
- 验证 stop rules
- 只找 practical gains

**Stage D — Validation / STABLE**

- independent revalidation
- real usage coverage
- no unresolved regression
- STABLE 后系统真正安静

**Stage E — Net Benefit**

- fixed-good
- monitoring
- dynamic
- full PowerLab

然后决定保留哪些复杂度。

### 12.4 核心验收

**Measurement**

- Discharging-only reward
- gap-aware integration
- suspend 不进入 reward
- gauge resolution 已实测
- minimum arm 同时受 config/gauge/noise/budget 约束
- data-quality failure 不制造 winner

**Evidence**

- Arm/Crossover/Decision 分离
- initial/revalidation 独立
- B2 独立达到 MUE
- candidate-caused bad outcomes 保留
- old hard epoch 不直接 promotion
- equivalent/inconclusive 是正式结果
- negative feedback 可 veto

**Control/Safety**

- verified-envelope-only
- actual-state reconcile
- transaction/read-back/rollback
- thermal safety 可抢占
- ControlSafetyState 真正阻止正常写入

**Scheduler**

- Measurement/Control/Lifecycle/Investigation/noise/headroom/budget gates
- finite candidate space
- finite retry
- STABLE 不探索
- 无 practical headroom 时停止

**UnexpectedPower**

- detection 与 root cause 分离
- 高于历史不直接叫 waste
- investigation 可判 expected workload
- 不直接 reopen
- hypothesis 有本机 evidence/verification；外部事实有来源

**STABLE / Net Benefit**

- 真实 usage coverage 达标
- 稀有 workload 有安全 fallback
- 无 active trial/investigation
- reference/noise 可用
- monitoring/dynamic/full 都经过 end-to-end 评估
- full PowerLab 无 practical net gain 时允许退回 fixed-good

### 12.5 当前明确不做

没有真实 SP7 数据证明需要之前，不做：

- LLM per-sample controller
- semantic scene taxonomy 作为实时核心
- deep neural network 核心控制
- full reinforcement learning
- neural contextual bandit
- Bayesian Optimization
- continuous arbitrary EPP search
- 持续本地满载作为优化主目标
- 旧 schema 兼容迁移层

未来只有在粗粒度方案已到瓶颈、仍有明显 practical headroom，且新增复杂度可验证有净收益时才重新评估。

---

## 13. 最终成功定义

PowerLab 成功意味着：

1. 测量可信；
2. 控制安全且可恢复；
3. 用户体验良好；
4. thermal 可持续；
5. 异常功耗可调查；
6. candidate 收益有独立证据；
7. noise 内差异会被停止；
8. 大多数真实使用时间有可信策略覆盖；
9. 系统知道何时进入 STABLE；
10. 环境变化时按 evidence reopen；
11. PowerLab 自身开销明显小于实际收益；
12. 如果复杂系统不值得，能删除复杂度并退回 fixed-good。

最终原则：

> 先测准，再判断；异常先调查，不急着调参；先确认可行动的异常功耗，再考虑降性能；收益小于噪声就停止；真实使用覆盖够了就冻结；环境变了再按证据重新学习。
