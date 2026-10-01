# PowerLab Project Status

本文件只记录软件实现成熟度、真机验证缺口和下一里程碑。

实时机器状态不要从这里推断。运行：

sp7-powerlab agent-context

## 当前软件基线

目标平台：

- Surface Pro 7
- Intel Core i5-1035G4
- Linux + intel_pstate / Intel HWP

当前 runtime：

- SQLite schema v5
- evidence semantics v2
- 旧 runtime schema fail-fast，不维护兼容迁移
- AI 入口：AGENTS.md + sp7-powerlab agent-context
- 设计合同：PLAN2.md

状态标签：

- IMPLEMENTED：主 runtime 已接入
- SOFTWARE-VALIDATED：有自动化/静态验证
- REAL-HARDWARE-VALIDATED：已在目标 SP7 + 当前有效 battery epoch 上验证
- BLOCKED-ON-HARDWARE：软件可继续开发，但真实结论依赖目标机器数据

除非明确写 REAL-HARDWARE-VALIDATED，否则不要把软件测试当成真机结论。

## Measurement Trust

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- MinimalMeter
- gap-aware BAT integration
- energy_now delta
- power/energy cadence 与 quantization
- consistency gate
- Measurement Trust READY/BLOCKED
- gauge + empirical noise 约束 minimum arm duration
- Scheduler 在 Measurement Trust 未准备好时禁止探索

仍需真机：

- 新电池 gauge quantum/cadence
- 实际 consistency tolerance
- 实际 minimum arm duration
- MinimalMeter 自身开销

## Calibration

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

阶段：

- cold_idle
- normal_interactive
- media
- bounded_burst

正确顺序：

battery epoch -> preliminary Measurement Trust -> calibration -> current-epoch Measurement Trust

bounded_burst 只看热惯性和短时行为，不用于证明持续满载性能。

## Evidence Engine / Trial

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- FrozenReferenceBaseline
- RecentNoiseDistribution
- hard strata + comparison constraints
- Minimum Useful Effect
- ArmMeasurement
- CrossoverEpisode
- EvidenceDecision
- WIN / LOSE / PRACTICALLY_EQUIVALENT / INCONCLUSIVE
- effect-size-dependent evidence budget
- data-quality failure -> INCONCLUSIVE
- UX / thermal / media veto
- candidate-caused bad outcomes 保留
- A1/B1/A2 initial crossover
- A3/B2 independent revalidation
- B2 必须独立达到 MUE
- state-based washout
- service-restart rollback

仍需真机：

- 真实 noise floor
- washout/settle 参数
- thermal carryover
- workload comparability
- 当前 practical threshold 是否合适

## Control / Lifecycle / HWP

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

Control Safety：

- CONTROL_ALLOWED
- READ_ONLY
- DEGRADED
- EMERGENCY

Learning Lifecycle：

- CALIBRATING
- BASELINE_OBSERVATION
- COARSE_OPTIMIZATION
- VALIDATING
- STABLE
- REOPENED

Investigation：

- IDLE
- INVESTIGATING

已实现：

- Controller 真正读取 ControlSafetyState
- verified-envelope-only normal control
- startup/runtime HWP reconcile
- transactional apply/read-back/rollback
- thermal preemption
- manual override
- single-writer ownership
- limited root helper
- STABLE readiness / usage coverage

仍需真机：

- HWP path 与 reboot/resume 行为
- package thermal sensor 固定路径
- thermald 长时间协作
- STABLE coverage/burn-in 是否自然

## UnexpectedPower / Drift / Attribution

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- UnexpectedPowerDetector
- FrozenReference vs recent drift
- Investigation
- Diagnostic Burst
- deterministic local attribution
- verification plan
- CONFIRMED_CONFIG_REGRESSION 才自动 reopen

不会直接执行：

UnexpectedPower -> 降 CPU -> 自动搜索

仍需真机：

- false-positive rate
- browser/media regression attribution
- device/runtime-PM/wakeup 线索质量
- 慢性 drift 灵敏度

## Candidate Scheduler

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- finite discrete local search
- max_perf_pct 小步
- named EPP
- Turbo on/off
- energy-first direction
- UX rescue direction
- Measurement Trust / Control / lifecycle / investigation gates
- noise/headroom gate
- low-battery gate
- daily/weekly experiment budgets
- finite retry
- feedback/thermal cooldown
- minimum-arm-duration stop rule
- frontier memory

当前没有：

- Bayesian Optimization
- deep RL
- neural bandit
- continuous raw EPP search

只有真实 coarse search 证明仍有 practical headroom，才考虑增加算法复杂度。

## Automation Levels

状态：

IMPLEMENTED + SOFTWARE-VALIDATED

- Level 0：只读
- Level 1：只切 verified envelope
- Level 2：可以提出 candidate，默认不自动 trial
- Level 3：全部 gate 通过后 daemon 可自动开始低风险 trial
- Level 4：还要求 auto_promote=true 且 VERIFIED_WINNER 才自动 promotion

Automation Level 是 daemon/Scheduler 治理，不是同 UID Bash Agent 的权限沙箱。

## Agent Integration

状态：

IMPLEMENTED + SOFTWARE-VALIDATED

已有：

- AGENTS.md
- PROJECT_MAP
- PROJECT_STATUS
- LLM_BEHAVIOR
- sp7-powerlab agent-context
- sp7-powerlab-agent convenience interface
- knowledge pack / structured decision contract
- Bash/workspace Agent 信任模型

AI 不进入 10 秒级实时控制链。

## Telemetry / Net Benefit

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- always-on core telemetry
- STABLE 下 expensive attribution 降频
- trial/calibration 高密度采样
- diagnostic burst
- MinimalMeter end-to-end comparison
- KEEP_FULL_POWERLAB
- KEEP_DYNAMIC_REDUCE_MONITORING
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

仍需真机：

- monitoring overhead
- probe/DB write 成本
- dynamic controller 实际净收益
- full PowerLab 实际净收益

## 当前最重要的未完成里程碑

下一外部里程碑：

> 在正常可用的 Surface Pro 7 电池上完成 Stage A Measurement Trust 和 calibration。

建议顺序：

1. 正常电池 + 新 battery epoch
2. hardware/thermald/ownership 只读检查
3. 记录真实 gauge cadence / quantization
4. preliminary Measurement Trust READY，证明 gauge/积分路径可用
5. 完成四阶段 calibration
6. calibration 产生新的 hard evidence epoch 后重新运行 Measurement Trust
7. current-epoch Measurement Trust READY
8. adopt 当前真实 HWP state 为首个 verified baseline
9. 收集只属于当前 evidence epoch、稳定 brightness/envelope 的自然 baseline/noise
10. coarse optimization
11. STABLE burn-in
12. fixed-good / monitoring / dynamic / full Net Benefit comparison

## 当前不能声称

在真实 Stage A–E 数据完成前，不要声称：

- 已找到 SP7 最省电参数
- 当前 noise/MUE 已真机充分校准
- 当前 minimum arm duration 已真机确认
- Dynamic Controller 一定优于 fixed envelope
- Full PowerLab 一定有净收益
- Level 3/4 已适合长期无人监督

## 刻意不做

当前不做：

- LLM per-sample controller
- semantic scene taxonomy 作为实时控制核心
- deep neural network 核心控制
- full RL
- Bayesian Optimization
- continuous arbitrary EPP search
- 持续本地满载作为优化主目标
- 旧 runtime schema 兼容层

如果真实 SP7 数据以后证明值得，可以重新评估。

## 何时更新本文件

以下情况应更新：

- runtime schema / evidence semantics 改变
- 核心 Phase 实现状态改变
- 完成新的真实 SP7 验证阶段
- Net Benefit 得到真实结论
- 新增/删除核心架构组件

普通 commit、测试数变化、一次 trial 结果不需要更新本文件。
