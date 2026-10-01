# AGENTS.md — PowerLab AI Entry Point

本文件是 AI / coding agent / Bash agent 的第一入口，只保留长期稳定约束；不要把它扩成第二份 PLAN2。
实时状态看 `sp7-powerlab agent-context`，正式设计看 `PLAN2.md`，操作步骤看对应 docs。

## 1. 目标

PowerLab 只针对 Microsoft Surface Pro 7 / Intel Core i5-1035G4 / Linux + intel_pstate / Intel HWP。

> 在用户体验良好、系统稳定、热状态可持续的前提下，降低真实整机 BAT 放电功率，提高实际续航。

不以 benchmark 分数、机制数量或 Agent 活跃度作为成功标准。
持续重计算通常应远程执行；实时控制必须本地、确定性、可回滚。AI 只做慢速研究、调查、实验设计、代码维护和复杂度决策。

## 2. 任务路由

### 仓库工程 / 代码维护
依次读 `AGENTS.md`、`docs/PROJECT_STATUS.md`、`docs/PROJECT_MAP.md`；设计语义变化时再读 `PLAN2.md` 对应章节。
非 SP7 开发机上的 unsupported hardware、READ_ONLY、旧本地 runtime DB 不等于代码维护被阻塞。
不要为让开发机 `agent-context` 变绿而 reset runtime、伪造机器状态或放宽硬件 gate。

### 真实 SP7 运维 / 优化
依次读 `AGENTS.md`、`docs/PROJECT_STATUS.md`，运行 `sp7-powerlab agent-context`，再读 `docs/LLM_BEHAVIOR.md` 和对应 `docs/OPERATIONS.md` / `PLAN2.md`。

### 首次部署 / 新电池 / 重建证据
跟随 `docs/FIRST_RUN.md`。

### Agent 集成
`docs/AI_LOOP.md` 定义长期闭环；`docs/MCP.md` 定义可选结构化接口。

## 3. Truth hierarchy

### 当前机器事实
1. 当前 OS / kernel / sysfs / systemd
2. 当前 PowerLab SQLite runtime state
3. 当前 CLI / `agent-context`
4. 当前机器 config
5. Markdown

不要从文档推断当前 battery epoch、Measurement Trust、Control Safety、active trial、current envelope 或 STABLE readiness。

### 当前代码事实
1. 当前 checkout
2. tests / 实际执行结果
3. Git / CI
4. `docs/PROJECT_STATUS.md`
5. `PLAN2.md`

`PROJECT_STATUS.md` 是成熟度快照，不是 live Git 状态。

### 设计合同
`PLAN2.md` 定义系统应该如何工作。实现、测试、配置和 PLAN2 冲突时，明确判断哪一方需要改变并同步真正受影响内容；不要用 fallback 掩盖语义冲突。

这是个人项目。不兼容 runtime/schema/evidence 语义优先采用：

> fail-fast + 显式 reset/rebuild

不维护隐藏 migration、旧接口 fallback 或长期双语义。

## 4. 不可绕过的 contracts

无论 Agent 能力多强，都不能绕过：
- validated thermal safety
- verified-envelope-only normal control
- transactional HWP apply / read-back / rollback
- rollback integrity latch
- current Measurement Trust
- current battery / hard evidence epoch
- deterministic active-trial Evidence contract
- candidate-caused thermal / PSI / media / UX bad outcomes
- independent revalidation
- destructive runtime reset 的明确授权

BAT 整机放电是主能量证据。Charging、suspend/resume、超限 gap、resume grace 或 epoch change 不能过滤后重新拼成连续实验。
正式 Stage E 每个 block 必须是一段连续 Discharging observation；任一上述中断都让整个 block fail closed。

root helper 明确 probe 失败后立即撤销正常写权限；可以保留 reconnect-capable client object，但不能继续声称 hardware writable。

## 5. 生命周期与复杂度边界

唯一顺序：

```
Stage A  Measurement Trust + Calibration
Stage B  Verified Baseline + Natural Reference / Noise
Stage C  Bounded Coarse Search
Stage D  Independent Validation + Real-Usage Burn-in
Stage E  End-to-End Net Benefit / Complexity Selection
         -> StableReadiness -> STABLE
```

`BASELINE_OBSERVATION` 有当前 reference 后只是 Stage C ready；必须显式进入 optimize，不能直接跳 Stage D。

正式 Stage E 只测：
- FIXED_GOOD
- MONITORING
- DYNAMIC_CONTROLLER

MONITORING 只解释 observer overhead。最终只保留 `KEEP_DYNAMIC_CONTROLLER`、`FIXED_GOOD_ENVELOPE` 或 `NEED_MORE_DATA`。
Level 2+ Scheduler/Agent/自动实验是按需学习能力，不是 formal Stage E 的长期 treatment。
正式 Stage E 期间 hourly timer/service 必须停止；默认安装也不自动启用 hourly timer。

StableReadiness 必须同时验证 selected policy fingerprint 和 actual selected runtime mode。
runtime policy fingerprint 包含核心 control/evidence code identity；核心代码变化后旧 Stage E 不继续给新 runtime 背书。

同名不等于同一份证据。按问题检查 battery epoch、hard evidence epoch、compatibility generation、envelope content hash、trial `evidence_scope_key`、Net Benefit campaign 和 runtime policy fingerprint。

Control Safety、Learning Lifecycle、Investigation 三类 runtime state 正交，不要压成一个“系统状态”。

## 6. Agent 默认工作流

1. 先确定任务类型和事实源。
2. 搜索并读取最小必要代码、测试、配置和文档。
3. 审查意见先复现；区分 correctness 问题与设计偏好。
4. 优先删除无收益复杂度，不为“框架完整”新增状态机或 Agent 中间层。
5. UnexpectedPower / Drift 先调查 attribution，不直接触发 CPU tuning。
6. 负面反馈是反证；已 reject/rollback 的坏 candidate 不继续阻塞当前保留策略。
7. 语义变化同步实现、测试、配置和真正受影响的文档。
8. 软件任务完成前运行 `bash scripts/quality-gate.sh`。
9. 报告区分 SOFTWARE-VALIDATED 与 REAL-HARDWARE-VALIDATED。
10. 涉及远端时，以重新查询的 Git / PR / CI 状态为准。

## 7. 自治边界

- 回答/审查/诊断：调查并给结论，不做范围外修改。
- 明确要求修改/修复/重构：完成范围内修改并验证。
- root/firmware 操作、不可逆数据破坏、外部发布、明显扩大任务范围：除非已明确授权，否则停在可审查结果。

Automation Level 约束 daemon/Scheduler 默认行为，不是同 UID Agent 的权限边界。

## 8. 文档路由

- `README.md`：人类入口
- `AGENTS.md`：AI 入口与不变约束
- `docs/PROJECT_MAP.md`：源码 / 数据 / CLI 导航
- `docs/PROJECT_STATUS.md`：实现成熟度与真机验证缺口
- `PLAN2.md`：正式设计合同
- `docs/LLM_BEHAVIOR.md`：调查/实验行为纪律
- `docs/FIRST_RUN.md`：首次部署 / 新电池 Stage A→STABLE
- `docs/OPERATIONS.md`：已部署系统日常运维
- `docs/DEPLOYMENT.md`：systemd / root helper / breaking runtime
- `docs/AI_LOOP.md`：长期 Agent/runtime 闭环
- `docs/MCP.md`：可选结构化接口

不要用 Markdown 中的旧测试数、旧 commit 或旧 runtime 数字冒充当前事实。
