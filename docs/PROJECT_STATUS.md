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

- SQLite schema v10
- evidence semantics v9
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
- contiguous valid-discharge consistency windows
- interrupted Stage A observation 不会用累计 valid seconds 绕过 BAT consistency
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
- reference/noise rollup 要求 clean Discharging、无 resume/gap 且 valid fraction 达标
- natural Reference/Noise identity 绑定 frozen envelope content hash；同名新 revision 不继承旧基线
- NoiseTracker 独立要求当前 envelope 仍 VERIFIED 且 content hash 与 rollup 匹配
- kernel/linux-surface relevant change 进入 hard evidence epoch
- browser/Mesa/media backend 使用 media compatibility generation 隔离 media reference/noise
- hard strata + comparison constraints
- evidence_scope_key 绑定 hard epoch + compatibility + baseline + reference/workload strata + candidate
- CrossoverEpisode / EvidenceDecision / CandidateFrontier / retry budget 全部按 evidence scope 聚合
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
- 相同 HWP candidate content 不会跨 baseline/workload/compatibility 串 evidence

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
- runtime hardware refresh 会重新 discovery/bind root helper；helper 晚启动不再永久锁死为 READ_ONLY
- 已绑定可用 helper 时，单次明确 probe failure 立即撤销 hardware_writable；保留 reconnect client，active trial 冻结 backend identity
- STABLE readiness / usage coverage
- dirty/transitional rollup 保留在 usage 分母，但不计 trusted coverage
- STABLE readiness 有 minimum valid/trusted usage、distinct usage days、observation span floor

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
- evidence-scope frontier history/retry memory

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
- sp7-powerlab review-pack 按需历史摘要
- GPT-5.6 Sol + Bash 直接使用主 CLI / SQLite / journal / sysfs
- Bash/workspace Agent 信任模型
- task-mode-aware Agent 入口：仓库工程与真实 SP7 运维不再共用一套机械启动流程
- Agent 入口统一 Stage A→B→C→D→E→STABLE 生命周期与 evidence identity 模型
- agent-context 在 BASELINE_OBSERVATION + current reference 时报告 STAGE_C_READY，不再直接跳到 Stage D

AI 不进入 10 秒级实时控制链。

## Telemetry / Net Benefit

状态：

IMPLEMENTED + SOFTWARE-VALIDATED + BLOCKED-ON-HARDWARE

已实现：

- dynamic mode 下的 core telemetry；fixed-good 最终策略允许 main service 停止
- STABLE 下 expensive attribution 降频
- trial/calibration 高密度采样
- diagnostic burst
- MinimalMeter end-to-end comparison
- MinimalMeter capture-time provenance（epoch/battery/fingerprint/calibration/campaign/mode）
- natural Reference/Noise identity 包含 frozen envelope content hash；只接受 VERIFIED + matching hash
- STABLE coverage 有真实 usage/trusted time、distinct days、observation span 的 denominator floor
- qualified FIXED_GOOD 在 freeze 时保存 entry coverage；同 epoch/policy/mode 下 rolling coverage 自然过期不强迫 daemon 常驻
- STABLE negative feedback 只阻塞当前保留策略的未解决负面证据；已 reject/rollback 的 candidate 不继续冷却当前策略
- Net Benefit block 必须是单一 contiguous Discharging observation；Charging/resume/gap/epoch change 整块 fail-closed
- Net Benefit 使用 time-weighted integrated BAT power，并对 data quality fail-closed
- FIXED_GOOD 用 live systemd 状态 + one-shot runtime audit 证明 service inactive、thermald/ownership/hard identity/HWP 都正确；作为 STABLE selected policy 时还要求 main service disabled、fixed oneshot enabled 且持久选择与 baseline identity 一致
- MinimalMeter 用 wall-vs-monotonic 检测短 suspend；formal block 任一 suspend/resume 整块 fail-closed
- capture mode 验证真实 service state / Automation Level / ControlSafety；live Dynamic/Monitoring 还要求 daemon heartbeat 中的 loaded code/config identity 与当前磁盘/config 一致；FIXED 每点核对实际 HWP
- 正式 capture 会拒绝残留旧 hourly unit 若其处于 active；当前安装不再部署 scheduled review unit
- brightness/active/media/remote/network 作为低成本 comparability veto；package temperature 是 treatment outcome，不用于过滤 Dynamic 自己造成的热改善/恶化
- 正式 Net Benefit 使用 A1-B1-B2-A2 paired campaign，检查两个 candidate delta 的方向/spread
- Dynamic policy fingerprint 只固定 Level-1 runtime 相关 config / VERIFIED set / override / explicit code allowlist；Scheduler 保持在 Level-1 identity 之外，Level-1 实际执行的 evidence/evaluation/longterm/calibration 等路径包含在内
- Stage E 使用 measurement-contract identity：正式比较代码 + 相关阈值/config 子集共同决定；代码或合同参数变化后旧 Stage E 自动 stale
- media compatibility generation 进入 capture/campaign provenance，并在 compare/readiness 时用现场 browser/Mesa 状态重新验证
- formal capture 开始时拒绝 active investigation / unresolved UnexpectedPower / Diagnostic Burst；capture 期间新 investigation/event 或非 thermal 的 Control Safety interruption 会使 block INVALID，thermal intervention 作为 outcome 记录
- restricted root helper 暴露 protocol/implementation identity；主 runtime 与 root-owned wheel 不一致时直接 READ_ONLY，不兼容旧 helper
- `sp7-powerlab fixed apply <VERIFIED>` 提供不写 manual override 的 baseline 恢复原语；最终 fixed-good 用 `sp7-powerlab-fixed.service` 在 login/reboot one-shot 重应用选中的 envelope
- Net Benefit campaign 是 OPEN/COMPLETE/INVALID 实体；一个有效 Dynamic A1-B1-B2-A2 即可 COMPLETE
- 两个 Dynamic candidate block 都必须达到 practical saving；只有一个达到时 NEED_MORE_DATA
- MONITORING 仅为按需诊断，不进入 StableReadiness
- STABLE 同时验证 selected policy fingerprint 与实际 selected runtime mode
- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

仍需真机：

- optional monitoring overhead（只在需要解释 Dynamic 结果时）
- probe/DB write 成本
- dynamic controller 实际净收益
- 真机 A1-B1-B2-A2 block 时长和 comparability 阈值是否合适

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
11. Stage D independent validation + representative real-usage burn-in
12. Stage E fixed-good / dynamic A1-B1-B2-A2 Net Benefit campaign
13. deterministic readiness 全部通过后 freeze STABLE

## 当前不能声称

在真实 Stage A–E 数据完成前，不要声称：

- 已找到 SP7 最省电参数
- 当前 noise/MUE 已真机充分校准
- 当前 minimum arm duration 已真机确认
- Dynamic Controller 一定优于 fixed envelope
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

breaking schema mismatch 的 daemon exit status 为 78，systemd 不对该状态无限 restart；需要人工执行
`sp7-powerlab reset-runtime --yes`。

如果真实 SP7 数据以后证明值得，可以重新评估。

## 何时更新本文件

以下情况应更新：

- runtime schema / evidence semantics 改变
- 核心 Phase 实现状态改变
- 完成新的真实 SP7 验证阶段
- Net Benefit 得到真实结论
- 新增/删除核心架构组件

普通 commit、测试数变化、一次 trial 结果不需要更新本文件。
