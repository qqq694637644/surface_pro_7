# AGENTS.md — PowerLab AI Entry Point

本文件是任何 AI、coding agent、Bash agent 或自动化工具进入本仓库时的第一入口。

它的职责不是复制全部设计细节，也不保存实时机器状态。它负责让 Agent 在最少上下文下先建立**正确的全局模型**：项目目标、任务模式、事实优先级、系统主链、证据身份、生命周期、自治边界，以及各文档的唯一职责。

## 1. 项目目标与边界

PowerLab 只针对：

- Microsoft Surface Pro 7
- Intel Core i5-1035G4
- Linux + intel_pstate / Intel HWP

唯一总目标：

> 在用户体验良好、系统稳定、热状态可持续的前提下，尽可能降低真实整机 BAT 放电功率，提高实际电池续航。

核心使用假设：

- 持续本地高功耗会明显发热，并可能导致降频。
- 持续编译、训练、长计算通常放到远程服务器。
- 优化重点是日常交互、浏览、轻量本地工作、媒体、远程工作和后台异常功耗。
- 不以持续满载性能、benchmark 分数或“更复杂的算法”作为目标。

PowerLab 不是“LLM 每 10 秒调 CPU”。实时控制保持本地、确定性、可回滚；AI 位于慢速研究、调查、实验设计、代码维护和复杂度决策层。

## 2. 先判断自己正在做哪一类任务

不要机械地对所有任务执行同一套启动流程。

### 2.1 仓库工程 / 代码维护

适用于：实现、修 bug、重构、代码审查、测试、CI、文档、schema、架构维护。

先读：

1. AGENTS.md
2. docs/PROJECT_STATUS.md
3. docs/PROJECT_MAP.md
4. 需要修改设计语义时，再读 PLAN2.md 对应章节

只有当任务涉及真实 runtime 行为时才需要运行：

`sp7-powerlab agent-context`

在非 SP7 开发机上，unsupported hardware、旧本地 runtime DB、READ_ONLY/BLOCKED 都**不等于仓库维护被阻塞**。不要为了让开发机的 agent-context “变绿”而擅自 reset runtime、改硬件 gate 或伪造机器状态。

### 2.2 真实 SP7 日常运维 / 优化

先读：

1. AGENTS.md
2. docs/PROJECT_STATUS.md
3. 运行 `sp7-powerlab agent-context`
4. docs/LLM_BEHAVIOR.md
5. 按问题读 docs/OPERATIONS.md 或 PLAN2.md 相关章节

agent-context 是当前机器状态的首选摘要入口；看到 blocker 后再深挖 CLI、SQLite、journal、/proc、/sys 和源码。

### 2.3 首次部署 / 新电池 / 重建基线

读：

1. AGENTS.md
2. docs/PROJECT_STATUS.md
3. docs/FIRST_RUN.md
4. docs/DEPLOYMENT.md

按 Stage A → E 的顺序建立证据，不跳阶段。

### 2.4 架构 / Evidence / Lifecycle / Scheduler 变更

必须同时核对：

- PLAN2.md：设计合同
- docs/PROJECT_MAP.md：实现位置和数据流
- docs/PROJECT_STATUS.md：实现成熟度和真机验证状态
- tests：当前可执行语义
- config：当前参数合同

如果实现与 PLAN2 不一致，明确判断是“实现落后”还是“设计合同需要更新”；不要静默发明第三种行为。

### 2.5 Agent 集成

- docs/AI_LOOP.md：Agent 与 runtime 的交互模型
- docs/MCP.md：可选结构化接口

`sp7-powerlab-agent` 是便利入口，不是 capability sandbox。

## 3. 三类事实必须分开

### 3.1 当前机器事实

优先级：

1. 当前 OS / kernel / sysfs / systemd
2. 当前 PowerLab SQLite runtime state
3. 当前 sp7-powerlab CLI / agent-context
4. 当前机器配置
5. Markdown

不要从 README、PLAN2 或 PROJECT_STATUS 推断当前 battery epoch、calibration、Control Safety、active trial、Measurement Trust、当前 envelope 或 STABLE readiness。

### 3.2 当前代码事实

优先级：

1. 当前 checkout 源码
2. 当前测试与实际运行结果
3. Git / CI 真实状态
4. docs/PROJECT_STATUS.md
5. PLAN2.md

PROJECT_STATUS 是成熟度快照，不是 live Git 状态。

### 3.3 设计合同

PLAN2.md 回答“系统应该如何工作”。

如果源码、测试、配置和 PLAN2 不一致：

- 明确指出差异；
- 判断哪个需要改变；
- 同步修改受影响的设计、实现、测试和入口文档；
- 不用兼容兜底掩盖不一致。

本项目是个人使用项目。对于不兼容的 runtime/schema/evidence 语义，允许破坏式升级；默认策略是 **fail-fast + 显式 reset/rebuild**，不是维护隐藏 migration、旧接口 fallback 或长期双语义。

## 4. 全局系统模型

PowerLab 可以理解为四条相互连接但职责不同的链。

### 4.1 确定性实时控制链

```
Telemetry
  -> Demand / Thermal
  -> BatteryLifeController
  -> VERIFIED Envelope
  -> transactional actuator
  -> Intel HWP
```

这一链不调用 LLM。它受 Control Safety、thermal preemption、read-back 和 rollback 约束。

### 4.2 测量与学习链

```
BAT telemetry
  -> Measurement Trust
  -> current hard evidence epoch
  -> Frozen Reference + Recent Noise
  -> finite Candidate Scheduler
  -> bounded Trial
  -> deterministic EvidenceDecision
  -> VERIFIED / BLOCKED / equivalent / inconclusive
```

Scheduler 是有限搜索器，不是永久优化器。

### 4.3 调查链

```
UnexpectedPower / Drift
  -> Investigation
  -> Attribution
  -> verification
  -> expected workload / insufficient evidence / confirmed regression
```

UnexpectedPower 不是“浪费”的同义词，也不直接触发调参。

### 4.4 收敛与复杂度决策链

```
real-usage coverage
  + current Reference / Noise
  + validated controller behavior
  + Net Benefit A1-B1-B2-A2 campaigns
  -> StableReadiness
  -> STABLE
  -> monitor / drift / NO_CHANGE
```

如果 dynamic controller、monitoring 或 full PowerLab 没有 practical net gain，删除复杂度是成功结果。

## 5. Stage A–E 与 STABLE 的唯一顺序

不要把 STABLE 当成 Stage E 之前的中间状态。

### Stage A — Measurement Trust + Calibration

```
battery epoch
  -> preliminary Measurement Trust
  -> calibration
  -> new/current hard evidence epoch
  -> current-epoch Measurement Trust
```

preliminary trust 只证明测量路径可用；calibration 改变 hard evidence context 后，必须重新建立 current-epoch trust。

### Stage B — Verified Baseline + Natural Reference / Noise

- 收编真实 HWP state 为首个 VERIFIED baseline。
- 只用 clean Discharging rollup 建 FrozenReference / RecentNoise。
- 收集经验 noise 和 minimum useful effect 所需数据。

### Stage C — Bounded Coarse Search

- 只探索有限、可解释、低风险邻域。
- 受 Measurement / Control / Lifecycle / Investigation / noise / headroom / budget gate 约束。
- 没有 practical headroom 就停止。

### Stage D — Independent Validation + Real-Usage Burn-in

- 完成 independent revalidation。
- 积累 representative real-usage coverage。
- 满足 minimum valid/trusted usage、distinct usage days 和 observation span。
- 处理负面反馈、UnexpectedPower、drift 和稳定性问题。

### Stage E — End-to-End Net Benefit / Complexity Selection

在同一正式 validation campaign 中比较：

- fixed-good
- monitoring
- dynamic controller
- full PowerLab

只有 Stage E 也完成、推荐/选定 policy 的 fingerprint 仍代表当前 runtime、且其他 readiness gate 全部满足后，才允许进入 STABLE。

### STABLE — 收敛状态，不是继续搜索的阶段

STABLE 下：

- Controller 继续工作；
- core telemetry / drift / UnexpectedPower 继续；
- Scheduler 默认睡眠；
- 不主动 trial；
- AI 最常见结论应是 NO_CHANGE。

## 6. 证据身份：同名不等于同一份证据

这是 Agent 最容易犯错的地方。

### 6.1 battery epoch

电池 identity/capacity context 变化后，旧坏电池或旧 battery epoch 数据不能直接和当前 evidence 合并。

### 6.2 hard evidence epoch

kernel/power-driver、battery、calibration semantics、thermal identity、Controller/Evidence 核心语义等重大变化会切 hard epoch。

旧 hard epoch 可以做历史参考，但不能直接给当前 promotion 投票。

### 6.3 compatibility generation

浏览器/Mesa/media backend 等变化不一定切整个 hard epoch；相关 evidence 必须按 compatibility generation 隔离。

### 6.4 envelope identity

envelope name 不够。

Natural Reference / Noise 使用冻结的 envelope content hash；同名 envelope promotion 成新 revision 后，不得继承旧 revision 的 Frozen Reference / Recent Noise。

### 6.5 trial evidence scope

相同 candidate 参数也不一定是同一个实验问题。

`evidence_scope_key` 至少绑定：

- hard evidence epoch
- relevant compatibility generation
- baseline content hash
- reference/workload strata
- candidate content hash

WIN/LOSE/frontier/retry 不能跨 scope 累加。

### 6.6 Net Benefit treatment identity

MONITORING / DYNAMIC_CONTROLLER / FULL_POWERLAB 只是 mode 名，不足以定义 treatment。

正式 capture 还冻结 runtime policy fingerprint；它代表 relevant runtime/config、Automation Level、VERIFIED envelope set/content hashes 和 manual override。

A1/B1/B2/A2 四个 block 必须处于同一个 policy identity。

### 6.7 Net Benefit campaign

campaign 是 OPEN / COMPLETE / INVALID 的 DB entity，不是可跨周复用的字符串。

它固定 hard/battery/calibration/semantics context 与 fixed baseline，并受最大 span 限制。三种正式 comparison 完成后 campaign 关闭。

## 7. 三个正交 runtime state

不要把它们压成一个“系统状态”。

### Control Safety

回答“当前能不能写机器”：

- CONTROL_ALLOWED
- READ_ONLY
- DEGRADED
- EMERGENCY

### Learning Lifecycle

回答“当前应该积累什么 evidence / Scheduler 是否应工作”：

- CALIBRATING
- BASELINE_OBSERVATION
- COARSE_OPTIMIZATION
- VALIDATING
- STABLE
- REOPENED

### Investigation

回答“是否正在调查异常”：

- IDLE
- INVESTIGATING

例如 Control=CONTROL_ALLOWED、Learning=STABLE、Investigation=INVESTIGATING 可以同时成立。

## 8. Evidence 与安全纪律

必须遵守：

- BAT 整机放电是主能量证据。
- power_now 不能单独冒充精确真值；必须结合积分、energy_now、gauge resolution、cadence 和 data quality。
- Charging、suspend gap、resume grace、超限 gap 不进入 reward。
- Measurement Trust 的 consistency 必须来自足够长的 contiguous valid-discharge windows。
- Candidate 造成的 thermal / PSI / media / UX 变差是 outcome，不能过滤。
- Initial crossover 和 revalidation 必须独立；B2 不能靠 B1 的大胜平均过关。
- 收益小于 noise / MUE 时，PRACTICALLY_EQUIVALENT 是正常终点。
- 用户负面体验可以 veto 节能候选。
- Active trial 期间不能移动 noise/MUE/data-quality/evidence-budget/hard-veto 裁判线。
- validated thermal safety、transaction/read-back/rollback、trial safety gate 不能为了“先跑起来”临时绕过。

## 9. 自治与批准边界

以用户当前明确任务定义工作范围。

- 回答/审查/诊断/规划：先调查，不擅自做范围外修改。
- 修改/实现/修复/重构：直接完成范围内可逆修改并验证。
- 真实 root/firmware 操作、不可逆数据破坏、外部发布、明显扩大任务范围：除非已明确授权，否则先给出可审查结果。
- Automation Level 约束 daemon/Scheduler 默认行为，不是同 UID Agent 的权限边界。

特别注意：

- agent-context 提示 runtime DB schema mismatch，不代表 Agent 已获授权删除 runtime。
- 非 SP7 开发机的 hardware BLOCKED 不应诱导 Agent 修改硬件安全合同。
- 不要为了当前 candidate 或测试方便添加隐藏兼容 fallback。

## 10. 文档职责与变更传播

每个主题只保留一个主要事实源。

- README.md：人类入口、快速开始、项目概览
- AGENTS.md：AI 第一入口、任务模式、全局心智模型、事实优先级、工作约束
- docs/PROJECT_MAP.md：源码/数据流/CLI/事实模型导航
- docs/PROJECT_STATUS.md：当前实现成熟度、真机验证缺口、下一里程碑
- PLAN2.md：正式设计合同与不变量
- docs/LLM_BEHAVIOR.md：调教、调查、实验时的 Agent 行为纪律
- docs/FIRST_RUN.md：首次真机部署 / 新电池从 Stage A 到 STABLE 的操作顺序
- docs/OPERATIONS.md：已部署系统的日常运维、调查、实验、validation、Net Benefit、STABLE
- docs/DEPLOYMENT.md：systemd、root helper、安装和 breaking runtime 处置
- docs/AI_LOOP.md：Agent 与 runtime 的慢速闭环、事件/周期/用户触发模型
- docs/MCP.md：可选结构化 Agent 接口和 decision contract

发生以下变更时，不要只改代码：

- schema / evidence semantics：更新 PROJECT_STATUS，必要时 PLAN2 / DEPLOYMENT / FIRST_RUN
- Stage / lifecycle / readiness：更新 PLAN2、AGENTS、PROJECT_MAP、FIRST_RUN、OPERATIONS、AI_LOOP
- Evidence identity：更新 PLAN2、AGENTS、PROJECT_MAP、LLM_BEHAVIOR
- CLI / 操作流程：更新 PROJECT_MAP 和对应 FIRST_RUN / OPERATIONS / DEPLOYMENT
- Agent context 输出：更新 AGENTS / AI_LOOP / MCP，并更新 tests
- 真机验证结果：只更新 PROJECT_STATUS 和必要的 durable knowledge；不要把一次结果改写成设计合同

## 11. 完成软件任务时

至少：

- 检查实际 diff；
- 跑与改动相称的测试；
- 架构、schema、Evidence、Lifecycle、Agent 入口、实验或部署链变化时运行完整质量门：

```bash
bash scripts/quality-gate.sh
```

- 区分 SOFTWARE-VALIDATED 与 REAL-HARDWARE-VALIDATED；
- 如果任务涉及远端，报告真实 Git/PR/CI 状态。

向用户报告：

- 改了什么；
- 为什么这样改；
- 验证了什么；
- 哪些仍依赖真实 SP7 / 当前有效 battery epoch；
- 当前 Git/PR/CI 状态。

不要用 Markdown 中的旧测试数、旧 commit、旧 runtime 数字冒充当前事实。
