# PowerLab Project Map

这是 PowerLab 的**静态导航地图**：回答“代码、数据、命令和文档在哪里”。

它不保存当前机器状态，也不重新定义设计语义：

- 当前机器状态：`sp7-powerlab agent-context`
- 当前成熟度：`docs/PROJECT_STATUS.md`
- 正式设计合同：`PLAN2.md`
- Agent 决策纪律：`docs/AI_LOOP.md`
- 详细操作：`docs/OPERATIONS.md`

AI 首次进入仓库先读 `AGENTS.md`。

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
```

异常链：

```text
UnexpectedPower / Drift
  -> Investigation
  -> Attribution
  -> verification
```

收敛链：

```text
Stage D validation
  + Stage E Dynamic vs Fixed-good
  -> StableReadiness
  -> STABLE
```

详细语义见 `PLAN2.md`。

## 2. Runtime state 导航

PowerLab 有三类正交状态：

| 状态轴 | 典型状态 | 主要实现 |
| --- | --- | --- |
| Control Safety | CONTROL_ALLOWED / READ_ONLY / DEGRADED / EMERGENCY | `lifecycle.py`, `controller.py`, `service.py` |
| Learning Lifecycle | CALIBRATING / BASELINE_OBSERVATION / COARSE_OPTIMIZATION / VALIDATING / STABLE / REOPENED | `lifecycle.py`, `longterm.py` |
| Investigation | open / resolved / classified | `unexpected_power.py`, `attribution.py`, `storage.py` |

当前值看 `agent-context`。

Evidence identity 的设计由 `PLAN2.md` 定义；当前 battery/hard/compatibility/envelope/trial/Stage E identity 看 `agent-context` 或 SQLite。

## 3. 源码地图

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

修改模块行为前，从调用方和 tests 确认真实数据流，不要只看文件名猜架构。

## 4. 配置地图

| 文件 | 用途 |
| --- | --- |
| `config/powerlab.toml` | runtime cadence、automation、evidence、trial、Net Benefit、STABLE |
| `config/machine.toml` | 当前机器 calibration / learned baselines |
| `config/envelopes.toml` | envelope candidate 定义；不等于 VERIFIED evidence |
| `config/thermal.toml` | thermal mapping / guardrail |

配置语义属于 `PLAN2.md`；真实运行值同时受 SQLite durable state 和 live OS 状态影响。

## 5. SQLite 事实模型

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

### Reference / investigation / Stage E

- `reference_baselines`
- `recent_noise_distributions`
- `investigations`
- `unexpected_power_events`
- `net_benefit_campaigns`
- `net_benefit_results`
- `minimal_meter_runs`
- `minimal_meter_samples`

其他 durable metadata 存于 `metadata`。

SQLite 是 runtime durable truth；高频 runtime DB 不提交 Git。

## 6. CLI 导航

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

常见问题：

| 想知道 / 做什么 | 入口 |
| --- | --- |
| 整体当前状态 | `sp7-powerlab agent-context` |
| 硬件合同 | `sp7-powerlab doctor` |
| Control Safety | `sp7-powerlab safety status` |
| lifecycle / STABLE blocker | `sp7-powerlab lifecycle ...` |
| Measurement Trust / noise | `sp7-powerlab evidence ...` |
| Scheduler eligibility / candidate | `sp7-powerlab scheduler ...` |
| Trial | `sp7-powerlab trial ...` |
| Envelope / final runtime | `sp7-powerlab envelope ...` |
| UnexpectedPower | `sp7-powerlab unexpected-power ...` |
| Investigation | `sp7-powerlab investigation ...` |
| Stage E | `sp7-powerlab net-benefit ...` |
| fixed-good audit | `sp7-powerlab fixed audit` / `lifecycle readiness` |
| 历史摘要 | `sp7-powerlab review-pack` |

完整参数和操作顺序以 `--help` 与 `docs/OPERATIONS.md` 为准。

## 7. 调查导航

| 问题 | 先看哪里 | 再看哪里 |
| --- | --- | --- |
| 电池异常掉电 | `agent-context`、UnexpectedPower | `AI_LOOP.md` 调查纪律、`OPERATIONS.md` §3 |
| 用户卡顿 | active trial / feedback / PSI | `OPERATIONS.md` §9–10 |
| 浏览器 / 视频功耗高 | media compatibility、GPU/process attribution | `AI_LOOP.md` 外部事实纪律 |
| Scheduler blocked | Measurement Trust、Reference/Noise、lifecycle | `OPERATIONS.md` §5–8 |
| Stage E / STABLE | coverage、campaign、selected runtime、readiness | `OPERATIONS.md` §16–18 |
| systemd / root helper | `DEPLOYMENT.md` | `doctor` / journal |

本文件不复制调查 procedure。

## 8. 文档地图

- `../AGENTS.md`：AI 第一入口、truth hierarchy、硬 contracts
- `../README.md`：人类 landing page
- `../PLAN2.md`：正式设计合同
- `PROJECT_STATUS.md`：软件成熟度 / 真机缺口
- `AI_LOOP.md`：Agent 每轮决策纪律
- `FIRST_RUN.md`：首次部署 / 新电池 Stage 顺序
- `OPERATIONS.md`：唯一详细 runbook
- `DEPLOYMENT.md`：systemd / root helper / breaking runtime

按任务读，不要机械加载所有文档。

## 9. 什么时候读 PLAN2

涉及以下设计语义时读对应章节：

- Measurement Trust / evidence semantics；
- trial protocol / Evidence Engine；
- lifecycle / StableReadiness；
- Scheduler search / budget；
- hard epoch / compatibility；
- thermal / control safety；
- Stage E / Net Benefit；
- actuator / automation level；
- 新控制维度。

普通 CLI 使用、日志调查或小 bug 优先从本地图和 `OPERATIONS.md` 定位。
