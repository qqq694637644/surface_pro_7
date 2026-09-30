# AI loop v2

LLM 不做实时 controller。

## 每小时输入

knowledge pack 包含 BAT W / drain、demand、thermal、verified envelope、control actions、waste/thermal incidents、trial blocks/results、用户反馈、rejection memory、system fingerprint 和 calibration version。

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

LLM proposal 不是 reward。PowerLab 只接受真实 BAT 数据的 A/B/A + revalidation 结果。

candidate 只有同时满足 BAT W 下降、PSI/体验不恶化、thermal 不恶化且无用户负反馈，才能成为 VERIFIED_WINNER。

稳定运行时最常见动作应该是 NO_CHANGE。

Level 2 的“人工批准”不属于 LLM decision JSON。用户通过
`sp7-powerlab llm-apply decision.json --approve` 独立授权；模型自己输出任何 approval 字段都会被 schema/validator 拒绝。
