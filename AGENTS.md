# AGENTS.md — PowerLab AI Entry Point

本文件是任何 AI、coding agent 或 Bash agent 进入本仓库时的第一入口。

它不复制设计合同，也不保存实时机器状态。它只定义项目目标、启动顺序、事实优先级、自治边界和必须遵守的项目约束。

## 1. 项目与目标

PowerLab 只针对：

- Microsoft Surface Pro 7
- Intel Core i5-1035G4
- Linux + intel_pstate / Intel HWP

唯一总目标：

> 在用户体验良好、系统稳定、热状态可持续的前提下，尽可能降低真实整机 BAT 放电功率，提高实际电池续航。

重要使用假设：

- Surface Pro 7 的持续高本地功耗会造成明显发热，并可能导致降频。
- 本项目不以持续本地高负载为主要 workload。
- 编译、训练、长时间计算等重任务通常放到远程服务器。
- 优化重点是日常交互、浏览、轻量本地工作、媒体、远程工作和后台异常功耗，而不是榨取持续满载性能。

## 2. 每次进入仓库先做什么

开始实质工作前按顺序：

1. 读本文件。
2. 读 docs/PROJECT_STATUS.md，确认软件实现成熟度和真机验证缺口。
3. 运行 sp7-powerlab agent-context。
4. 需要定位源码时读 docs/PROJECT_MAP.md。
5. 需要理解设计理由或修改架构时读 PLAN2.md 的相关章节。
6. 参与调教、调查或实验时读 docs/LLM_BEHAVIOR.md。
7. 只在需要时读 FIRST_RUN / OPERATIONS / DEPLOYMENT / AI_LOOP / MCP，不要一次把所有文档重复塞进上下文。

agent-context 是当前机器状态的首选摘要入口。它不替代深挖；当摘要显示异常、阻塞或不确定时，再查询对应 CLI、SQLite、日志、/proc、/sys 和源码。

## 3. 三类事实不要混淆

### 3.1 当前机器事实

优先级：

1. 当前 OS / kernel / sysfs / systemd 的真实状态
2. 当前 PowerLab SQLite runtime state
3. 当前 sp7-powerlab CLI 输出
4. 当前 config
5. Markdown 文档

不要从 README、PLAN2 或 PROJECT_STATUS 推断当前 battery epoch、calibration、control state、active trial、measurement trust 或当前 envelope。

### 3.2 当前代码事实

优先级：

1. 当前 checkout 源码
2. 当前测试与实际运行结果
3. Git 状态与历史
4. docs/PROJECT_STATUS.md
5. PLAN2.md

PROJECT_STATUS.md 是实现成熟度快照，不是 live Git 状态。真正的 branch / commit / dirty state 以 Git 和 agent-context 为准。

### 3.3 设计合同

PLAN2.md 表示“系统应该如何工作”。

如果源码与 PLAN2 不一致：

- 明确指出差异；
- 判断是实现未完成，还是设计合同需要更新；
- 不要静默发明第三种行为。

## 4. Runtime BLOCKED 不等于不能维护仓库

在非 SP7 开发机、坏电池、未 calibration 或 Measurement Trust 尚未建立时：

- 真机控制和实验可能必须 BLOCKED；
- 但代码调查、文档重构、测试、静态分析、Git 工作仍可正常进行。

不要因为 agent-context 显示 runtime BLOCKED 就停止授权范围内的软件维护。

## 5. 自治与批准边界

在适用的平台与安全约束之下，以用户当前明确任务定义工作范围。

- 用户要求回答、审查、诊断或规划：先调查并给出结论，不擅自做范围外修改。
- 用户要求修改、实现、修复或重构：直接完成范围内可逆的本地修改并运行必要验证，不为每个普通步骤重复询问。
- 外部发布、不可逆数据破坏、真实 root/firmware 操作、明显扩大任务范围：除非当前任务已经明确授权，否则应先给出具体可审查结果，再请求确认。
- PowerLab 的 Automation Level 约束 daemon / Scheduler 默认自动行为，不是同 UID Bash Agent 的权限沙箱。

把自治规则集中理解为这一节，不要在其他文档中自行叠加一套相互冲突的批准流程。

## 6. 不能临时破坏的 Runtime Contract

即使 Agent 有 Bash，也不要为了当前候选通过实验而临时绕开这些确定性合同：

- validated thermal safety provider；
- transactional HWP apply / read-back / rollback；
- active trial 的 data-quality gate；
- active trial 的 Evidence 判分语义；
- battery / suspend / thermal / ownership 等 trial safety gate。

如果这些规则本身设计有问题，可以修改代码和配置，但应：

1. 作为正常工程变更审查；
2. 更新 tests；
3. 必要时提升 evidence semantics / hard epoch；
4. 用新规则重新积累或重新判断证据兼容性。

不要边跑一个 candidate，边移动裁判线让它过关。

## 7. 核心心智模型

PowerLab 不是“LLM 实时调 CPU”。

主链：

Telemetry -> Measurement Trust -> Evidence -> bounded Trial -> Verified Envelope -> Convergence

旁路：

UnexpectedPower / Drift -> Investigation -> Attribution -> verification

只有调查确认配置本身 regression，或形成明确且有价值的新控制假设，才重新打开优化。

实时 10 秒级控制保持本地确定性；AI 用于解释、调查、读长期历史、提出和验证假设、修改代码/配置、设计实验，以及判断复杂度是否值得保留。

成熟系统的正常答案经常是 NO_CHANGE。

## 8. 证据纪律

必须记住：

- BAT 整机放电是主能量证据。
- power_now 不能单独冒充精确真值；要结合积分、energy_now、gauge resolution 和 data quality。
- Candidate 造成的 thermal / PSI / media / UX 变差是 outcome，不能从实验数据中过滤掉。
- Initial crossover 和 revalidation 必须独立。
- 旧 hard evidence epoch 不能直接给当前 promotion 投票。
- 相同 HWP candidate content 不代表同一个实验问题；baseline、workload/reference stratum 或 relevant
  compatibility generation 不同，就必须是不同 `evidence_scope_key`，不能累加 WIN/LOSE/frontier。
- 收益小于 noise / Minimum Useful Effect 时，应允许 PRACTICALLY_EQUIVALENT 并停止折腾。
- 用户负面体验可以否决节能候选。

## 9. 当前硬件验证警告

除非真实 SP7 新电池数据已经完成相应 Stage：

> 不要声称 PowerLab 已在真实 Surface Pro 7 上完成 Measurement Trust、calibration、noise、thermal、monitoring overhead 或 Net Benefit 验证。

自动化测试通过只证明软件逻辑，不等于真机结论。

真机验证状态看：

- docs/PROJECT_STATUS.md
- sp7-powerlab agent-context

## 10. 文档职责

- README.md：人类项目入口与快速开始
- AGENTS.md：AI 入口与工作约束
- docs/PROJECT_MAP.md：源码、数据流、CLI 导航
- docs/PROJECT_STATUS.md：当前实现成熟度与未验证事项
- PLAN2.md：设计合同
- docs/LLM_BEHAVIOR.md：PowerLab Agent 调教/调查纪律
- docs/FIRST_RUN.md：真实设备首次部署
- docs/OPERATIONS.md：日常运行
- docs/DEPLOYMENT.md：systemd / helper 部署
- docs/AI_LOOP.md：Agent 与 runtime 的交互模型
- docs/MCP.md：可选结构化 Agent 接口

避免把同一规则复制到多份文档。需要详细解释时，用链接指向唯一负责该主题的文件。

## 11. 完成任务时

软件修改完成前至少：

- 检查实际 diff；
- 跑与改动相称的测试；
- 不因为“小改动”机械重复全套测试；
- 架构、schema、控制、Evidence、实验、部署入口发生变化时应跑完整质量门；
- 区分“软件验证通过”和“真机验证通过”。

向用户报告：

- 做了什么；
- 验证了什么；
- 哪些仍依赖真实 SP7 / 新电池数据；
- 当前 Git/PR/CI 状态（如果任务涉及远端）。

不要用文档中的旧数字冒充当前测试数、commit 或机器状态。
