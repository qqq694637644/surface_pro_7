# PowerLab Project Map

这是 PowerLab 的**静态导航地图**：回答“代码、数据、命令和文档在哪里”。

它不保存当前机器状态，也不重新定义设计语义：

- 当前机器状态：`sp7-powerlab agent-context`
- 当前成熟度：`docs/PROJECT_STATUS.md`
- 正式设计合同：`PLAN2.md`
- 详细操作：`docs/OPERATIONS.md`

AI 首次进入仓库先读根目录 `AGENTS.md`。

## 1. 架构导航

主控制链：

```text
TelemetryCollector
  -> DemandObserver / ThermalObserver
  -> BatteryLifeController
  -> VERIFIED Envelope
  -> transactional HWP actuator
  -> intel_pstate / Intel HWP
```

学习链：

```text
BAT / telemetry
  -> Measurement Trust
  -> current evidence context
  -> Frozen Reference / Recent Noise
  -> Candidate Scheduler
  -> Trial
  -> deterministic Evidence
  -> VERIFIED / REJECTED / EQUIVALENT / INCONCLUSIVE
```

异常链：

```text
UnexpectedPower / Drift
  -> Investigation
  -> Attribution
  -> verification
  -> expected / insufficient / regression / actionable waste
```

收敛链：

```text
Stage D real-usage validation
  + Stage E Dynamic vs Fixed-good
  -> StableReadiness
  -> STABLE
```

详细语义见 `PLAN2.md`。

## 2. 三类 runtime state

这三个状态正交，不要压成一个“系统状态”。

| 状态轴 | 典型状态 | 主要实现 |
| --- | --- | --- |
| Control Safety | CONTROL_ALLOWED / READ_ONLY / DEGRADED / EMERGENCY | `lifecycle.py`, `controller.py`, `service.py` |
| Learning Lifecycle | CALIBRATING / BASELINE_OBSERVATION / COARSE_OPTIMIZATION / VALIDATING / STABLE / REOPENED | `lifecycle.py`, `longterm.py` |
| Investigation | open / resolved / classified | `unexpected_power.py`, `attribution.py`, `storage.py` |

当前值看 `agent-context`，不要从 Markdown 推断。

## 3. Evidence identity 导航

复用历史 evidence 时可能需要检查：

- battery epoch；
- hard evidence epoch；
- relevant compatibility generation；
- envelope content hash；
- trial `evidence_scope_key`；
- Stage E contract identity；
- runtime policy fingerprint；
- Net Benefit campaign。

这些字段“为什么存在”见 `PLAN2.md`；“当前是什么”看 `agent-context` / SQLite。

## 4. 源码地图

| 文件 | 主要职责 |
| --- | --- |
| `agent_context.py` | 聚合当前机器事实、Stage 和 next actions |
| `analytics.py` | 聚合/统计辅助 |
| `attribution.py` | investigation attribution 与分类 |
| `calibration.py` | calibration run、基线/热参数写入 |
| `cli.py` | 主 CLI、lifecycle/fixed/dynamic/Stage E orchestration |
| `config.py` | TOML 加载和 config access |
| `controller.py` | verified-envelope deterministic controller |
| `demand.py` | activity/latency/local compute/media/remote demand observation |
| `envelopes.py` | envelope registry、revision/status/content hash |
| `evaluation.py` | comparable duration / trial evaluation helpers |
| `evidence.py` | Measurement Trust integration、Reference/Noise evidence helpers |
| `experiments.py` | TrialManager、arm/crossover/revalidation/promotion |
| `hardware.py` | DMI/HWP/thermald/ownership/system fingerprint |
| `helper.py` | restricted root helper protocol/client/server |
| `knowledge.py` | on-demand review pack |
| `lifecycle.py` | Control Safety / Learning Lifecycle 状态 |
| `longterm.py` | coverage、drift、Net Benefit、StableReadiness、runtime/contract identity |
| `measurement.py` | BAT integration、energy consistency、MinimalMeter core |
| `minimal_meter_cli.py` | formal Stage E meter capture |
| `runtime_audit.py` | fixed-good live hard/BAT/HWP/systemd audit |
| `scheduler.py` | bounded candidate generation、budget、eligibility |
| `service.py` | 10 秒级 runtime orchestrator |
| `storage.py` | SQLite schema、durable state、queries |
| `telemetry.py` | battery/CPU/GPU/process/device/activity collectors |
| `thermal.py` | thermal state/guardrail |
| `unexpected_power.py` | unexpected-power / drift event detection |
| `actuators/hwp.py` | HWP inspect/snapshot/apply/restore |
| `actuators/base.py` | actuator shared errors/contracts |

修改模块行为前，先从调用方和 tests 确认真实数据流，不要只看文件名猜架构。

## 5. 配置地图

| 文件 | 用途 |
| --- | --- |
| `config/powerlab.toml` | runtime cadence、automation、evidence、trial、Net Benefit、STABLE 等主配置 |
| `config/machine.toml` | 当前机器 calibration / learned baselines |
| `config/envelopes.toml` | envelope candidate 定义；不等于 VERIFIED evidence |
| `config/thermal.toml` | thermal mapping / guardrail 配置 |

配置语义属于 `PLAN2.md`；真实运行值同时受 SQLite durable state 和 live OS 状态影响。

## 6. SQLite 事实模型

主要表按领域分组。

### Telemetry / rollup

- `samples`
- `power_rollups`
- `demand_windows`
- `thermal_windows`
- `process_attribution`

### Control / lifecycle

- `controller_states`
- `control_actions`
- `control_safety_history`
- `learning_lifecycle_history`
- `thermal_incidents`

### Envelope / trial / evidence

- `envelopes`
- `envelope_validations`
- `trials`
- `trial_results`
- `arm_measurements`
- `crossover_episodes`
- `evidence_decisions`
- `candidate_frontier`
- `rejections`
- `user_feedback`

### Epoch / compatibility / calibration

- `battery_epochs`
- `calibration_runs`
- `evidence_epochs`
- `compatibility_tags`
- `system_fingerprints`

### Reference / noise

- `reference_baselines`
- `recent_noise_distributions`

### Investigation

- `investigations`
- `unexpected_power_events`

### Stage E

- `net_benefit_campaigns`
- `net_benefit_results`
- `minimal_meter_runs`
- `minimal_meter_samples`

### Misc

- `metadata`

SQLite 是当前 runtime durable truth，不提交高频 runtime DB 到 Git。

## 7. CLI 导航

顶层命令：

```text
doctor
agent-context
reset-runtime
calibrate
observe
incidents
lifecycle
safety
investigation
unexpected-power
evidence
net-benefit
fixed
scheduler
envelope
trial
feedback
review-pack
service
```

常见问题对应入口：

| 问题 | 首选命令 |
| --- | --- |
| 现在整体处于什么状态？ | `sp7-powerlab agent-context` |
| 硬件是否满足合同？ | `sp7-powerlab doctor` |
| Control Safety？ | `sp7-powerlab safety status` |
| 生命周期 / STABLE blocker？ | `sp7-powerlab lifecycle status/readiness/coverage` |
| Measurement Trust / noise？ | `sp7-powerlab evidence ...` |
| Scheduler 为什么不能搜索？ | `sp7-powerlab scheduler status` |
| 有哪些 candidate？ | `sp7-powerlab scheduler candidates <baseline>` |
| Trial 状态？ | `sp7-powerlab trial status` |
| 当前 envelope？ | `sp7-powerlab envelope list` |
| UnexpectedPower？ | `sp7-powerlab unexpected-power list` |
| Investigation？ | `sp7-powerlab investigation list` |
| Stage E？ | `sp7-powerlab net-benefit ...` |
| fixed-good 现场审计？ | `sp7-powerlab fixed audit` / `lifecycle readiness` |
| 历史摘要？ | `sp7-powerlab review-pack` |

完整参数和操作顺序以 `--help` 与 `docs/OPERATIONS.md` 为准。

## 8. 常见调查路线

### 电池掉得快

1. `agent-context`
2. Measurement Trust / current evidence context
3. UnexpectedPower / investigation
4. review pack 或最小必要 telemetry
5. attribution：process / browser / GPU / device / wakeup / network / thermal
6. 只有形成高价值控制假设才进入 Scheduler/Trial

### 用户说卡

1. feedback / active trial
2. PSI、latency、thermal、media continuity
3. candidate-caused bad outcome 必须保留
4. 必要时 rollback / reject，不用更低 BAT W 覆盖 UX 失败

### 浏览器 / 视频功耗高

1. media compatibility generation
2. hardware decode / GPU activity / process attribution
3. Firefox/Mesa/kernel 外部事实需要可核实来源
4. 本机是否受影响仍需本机验证

### Stage E / STABLE

1. Stage D representative usage 是否达标
2. formal campaign 是否 COMPLETE/current
3. selected runtime 是否已恢复
4. `lifecycle readiness`
5. 只有 deterministic readiness ready 才 freeze

详细命令见 `docs/OPERATIONS.md`。

## 9. 文档地图

- `../AGENTS.md`：AI 第一入口、truth hierarchy、不变量
- `../README.md`：人类 landing page
- `../PLAN2.md`：正式设计合同
- `PROJECT_STATUS.md`：软件成熟度 / 真机缺口
- `AI_LOOP.md`：Agent 调查、实验和长期工作纪律
- `FIRST_RUN.md`：首次部署 / 新电池唯一 Stage 顺序
- `OPERATIONS.md`：唯一详细 runbook
- `DEPLOYMENT.md`：systemd / root helper / breaking runtime

按任务读，不要机械加载所有文档。

## 10. 什么时候读 PLAN2

涉及以下设计语义时读对应章节：

- Measurement Trust / evidence semantics；
- trial protocol / Evidence Engine；
- lifecycle / StableReadiness；
- Scheduler 搜索空间或 budget；
- hard epoch / compatibility；
- thermal / control safety；
- Stage E / Net Benefit；
- actuator / automation level；
- 新控制维度。

普通 CLI 使用、日志调查或小 bug 优先从本地图和 `OPERATIONS.md` 定位，不需要把整个 PLAN2 塞进上下文。
