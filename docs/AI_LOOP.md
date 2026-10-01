# AI loop v2

LLM 不做实时 controller。

## 每小时输入

knowledge pack 包含 BAT W / drain、demand、thermal、verified envelope、control actions、
UnexpectedPower/thermal events、investigations、EvidenceDecision、noise/reference evidence、
用户反馈、rejection memory、usage coverage、net-benefit assessment、system fingerprint
和 calibration version。

## 允许动作

- NO_CHANGE
- NEED_MORE_DATA
- INVESTIGATE_POWER_SPIKE
- INVESTIGATE_THERMAL_EVENT
- PROPOSE_POWER_FIX
- PROPOSE_ENVELOPE_TRIAL
- ROLLBACK_TRIAL
- PROMOTE_ENVELOPE
- PROPOSE_MANUAL_RECALIBRATION

## 优先级

LLM 必须优先：

1. 调查 UnexpectedPower，并区分 expected workload、regression 与 confirmed actionable waste。
2. 查软件/驱动/硬解回归。
3. 再考虑 HWP envelope tuning。
4. 最后才考虑会牺牲 UX 的方案。

LLM proposal 不是 reward。PowerLab 先用 A1/B1/A2 完成第一次 crossover；第一次达到
provisional win 后，必须重新采 A3，再用 A3/B2 做独立 revalidation。B2 必须独立超过
minimum useful effect，且跨 trial 累积证据只能发生在同一 hard evidence epoch。
PSI/体验、thermal、media continuity、data quality 与用户负面反馈仍可否决候选。

稳定运行时最常见动作应该是 NO_CHANGE。

## Governance boundary

GPT-5.6 可以拥有用户提供的 Bash/workspace/MCP 等通用用户态能力。
Automation Level 约束的是 PowerLab daemon / Scheduler 的默认自动行为，而不是把同 UID
Agent 伪装成技术上不可绕过的权限沙箱。

Level 2 默认仍采用“proposal → 用户审核 → trial/promotion”的治理方式；Level 3 才允许
本地 Scheduler 在全部 safety/evidence/budget gate 通过后自动开始低风险 trial；
Level 4 且 `auto_promote=true` 时才允许 daemon 自动 promotion。

真正不能由 Agent 临时覆盖的是确定性 runtime contract：validated thermal safety、
transactional HWP/read-back/rollback，以及 active trial 的 Evidence 判分标准。
