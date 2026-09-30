# AI loop v2

LLM 不做实时 controller。

## 每小时输入

knowledge pack 包含 BAT W / drain、demand、thermal、verified envelope、control
actions、waste/thermal incidents、trial blocks/results、用户反馈、rejection memory、
system fingerprint 和 calibration version。

## 允许动作

- NO_CHANGE
- NEED_MORE_DATA
- INVESTIGATE_POWER_SPIKE
- INVESTIGATE_THERMAL_EVENT
- PROPOSE_WASTE_FIX
- PROPOSE_ENVELOPE_TRIAL
- ROLLBACK_TRIAL
- PROMOTE_ENVELOPE
- PROPOSE_MANUAL_RECALIBRATION

## 优先级

LLM 必须优先：

1. 查明显 waste。
2. 查软件/驱动/硬解回归。
3. 再考虑 HWP envelope tuning。
4. 最后才考虑会牺牲 UX 的方案。

LLM proposal 不是 reward。PowerLab 先用 A1/B1/A2 完成第一次 crossover；第一次胜出后，
必须重新采 A3，再用 A3/B2 做独立 revalidation。B2 不复用 B1 的好结果，也不使用等待很久
以前的旧 baseline。candidate 只有两轮都单独满足 BAT W 下降、PSI/体验不恶化、thermal
不恶化且无用户负反馈，才能成为 VERIFIED_WINNER。

稳定运行时最常见动作应该是 NO_CHANGE。

## Approval boundary

LLM 只接触 `sp7-powerlab-agent` 的窄 capability，不接触 Bash/Python、主
`sp7-powerlab` CLI 或 root-helper socket。Level 2 的 proposal 只能落盘等待审核；
用户在独立的人类 capability 中运行 `sp7-powerlab trial start ...` 或
`sp7-powerlab trial promote ...`。

如果把任意 shell 暴露给同 UID 的 LLM，人工批准边界即失效，这种部署不属于受支持的
Level 2 模式。
