# Operations

日常运维的第一条命令：

~~~bash
sp7-powerlab agent-context
~~~

如果你是 AI，先读根目录 AGENTS.md。

## 1. 状态总览

~~~bash
sp7-powerlab agent-context
sp7-powerlab service status
~~~

按需继续：

~~~bash
sp7-powerlab lifecycle status
sp7-powerlab safety status
sp7-powerlab evidence status
sp7-powerlab scheduler status
~~~

不要从文档推断实时状态。

## 2. 日常 power / thermal

~~~bash
sp7-powerlab observe now
sp7-powerlab observe power --hours 24
sp7-powerlab observe thermal --hours 24
~~~

关注：

- battery_status
- BAT W
- battery %
- package temperature
- thermal state
- current envelope
- demand region
- resume grace

短时间高 W 不自动等于问题。

## 3. UnexpectedPower

检查：

~~~bash
sp7-powerlab unexpected-power list
~~~

看具体事件：

~~~bash
sp7-powerlab unexpected-power inspect <event-id>
~~~

如果有 investigation：

~~~bash
sp7-powerlab investigation list
sp7-powerlab investigation inspect <investigation-id>
sp7-powerlab investigation attribute <investigation-id>
~~~

正确顺序：

UnexpectedPower -> Investigation -> Attribution -> verification

不是：

UnexpectedPower -> 降 CPU

## 4. 关闭 investigation

只有有证据时分类。

示例：

~~~bash
sp7-powerlab investigation close <id> EXPECTED_WORKLOAD_CHANGE \
  --reason "user requested file transfer"

sp7-powerlab investigation close <id> CONFIRMED_CONFIG_REGRESSION \
  --reason "verified envelope regression reproduced"
~~~

CONFIRMED_CONFIG_REGRESSION 会让 learning reopen。

普通 EXPECTED_WORKLOAD_CHANGE 不应该唤醒 Scheduler。

## 5. Measurement Trust

~~~bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
~~~

如果 Measurement Trust BLOCKED：

- 不解释微小 W 差异；
- 不手工绕开 Scheduler gate；
- 先增加有效 Discharging 数据或修 telemetry。

Measurement Trust 绑定当前 battery/calibration/evidence epoch。换电池、重新 calibration 或 hard epoch
变化后，旧 READY 自动失效，必须重新评估。

## 6. Evidence / Noise

~~~bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
~~~

重点看：

- active hard evidence epoch
- frozen reference
- recent noise
- MUE
- recent decisions

自然 reference/noise 只接受当前 evidence epoch 且同一分钟内 brightness bucket、envelope、
media/active/remote 状态稳定的 rollup；混合分钟不会进入 frozen reference。

如果旧 epoch 与当前环境不兼容，不要把历史 winner 直接当当前 winner。

## 7. Scheduler

~~~bash
sp7-powerlab scheduler status
sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT
~~~

常见 blocker：

- measurement_trust_not_ready
- learning_lifecycle_does_not_allow_exploration
- control_safety_state_does_not_allow_trials
- investigation_active
- insufficient_noise_baseline
- weekly_trial_budget_exhausted
- daily_candidate_exposure_budget_exhausted
- thermal_event_cooldown_active
- negative_feedback_cooldown_active
- battery_below_exploration_threshold
- minimum_arm_duration_exceeds_daily_candidate_budget
- cpu_headroom_below_minimum_useful_effect

这些 blocker 是正常停止机制。

## 8. Trial

查看：

~~~bash
sp7-powerlab trial status
~~~

手动启动：

~~~bash
sp7-powerlab trial start proposals/example.json
~~~

必要时回滚：

~~~bash
sp7-powerlab trial rollback --trial-id trial-xxxx \
  --reason "manual rollback"
~~~

Promotion：

~~~bash
sp7-powerlab trial promote trial-xxxx
~~~

只有 VERIFIED_WINNER 能 promotion。

## 9. 用户体验反馈

~~~bash
sp7-powerlab feedback good --envelope INTERACTIVE_EFFICIENT

sp7-powerlab feedback sluggish \
  --trial-id trial-xxxx \
  --notes "remote interaction feels slower"
~~~

负面反馈是重要 outcome。

不要因为 BAT 更低而忽略卡顿。

## 10. Envelope

~~~bash
sp7-powerlab envelope list
sp7-powerlab envelope inspect INTERACTIVE_EFFICIENT
~~~

Manual override：

~~~bash
sp7-powerlab envelope override INTERACTIVE_EFFICIENT
sp7-powerlab envelope clear-override
~~~

普通控制只接受 verified envelope。

## 11. Thermal

THERMAL_PRESSURE / THROTTLING 时：

- active trial 应回滚；
- Controller 优先 THERMAL_SAFE；
- validated thermal safety provider 保持独立；
- 先检查 workload 是否异常；
- 持续高本地负载优先考虑远程执行，而不是放宽热安全。

## 12. Rollback integrity recovery

如果 HWP apply 后无法精确恢复并验证 baseline，PowerLab 会把
`rollback_integrity_fault` 持久锁存为 EMERGENCY。helper/sysfs 后续重新可写不会自动清除它。

先人工确认机器状态，再执行：

~~~bash
sp7-powerlab safety recover-rollback --reason "verified actual HWP state after manual inspection"
~~~

只有实际 HWP snapshot 能唯一匹配一个 VERIFIED envelope 时 recovery 才成功。命令成功后先进入
READ_ONLY，下一轮正常 runtime safety synchronization 再决定是否恢复 CONTROL_ALLOWED。

## 13. Suspend / Resume

Resume 后：

- 不信任 suspend gap 内的 BAT integration；
- experiment-local rolling state 重建；
- resume grace 内不自动控制；
- HWP actual state 重新 reconcile。

如果 resume 后当前 envelope 和真实 HWP 不一致，应以真实 HWP snapshot/reconcile 为准。

## 14. Service restart

重启时不继续跨重启 active trial。

未完成 trial：

- 尝试恢复 baseline snapshot；
- 标记 rolled back/failed；
- rollback integrity 不可信时 Control 进入 EMERGENCY/READ_ONLY。

## 15. Drift

慢性 drift 看 FrozenReferenceBaseline 对 recent distribution。

不要让 adaptive recent baseline 吞掉长期退化。

出现 sustained drift：

- 打开 investigation；
- diagnostic burst；
- 不直接开始参数搜索。

## 16. STABLE

检查：

~~~bash
sp7-powerlab lifecycle readiness
sp7-powerlab lifecycle coverage
~~~

STABLE 下：

- core telemetry 保留；
- expensive attribution 降频；
- Scheduler 睡眠；
- 无主动 trial；
- 异常时 diagnostic burst。

如果真实数据不再支持当前配置，才 reopen：

~~~bash
sp7-powerlab lifecycle reopen --reason "confirmed regression"
~~~

## 17. Net Benefit

历史：

~~~bash
sp7-powerlab overhead history
~~~

结论：

~~~bash
sp7-powerlab overhead summary
~~~

可能结果：

- KEEP_FULL_POWERLAB
- KEEP_DYNAMIC_REDUCE_MONITORING
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

Full PowerLab 没有 practical net gain 时，应简化。

## 18. Git / Knowledge

高频 SQLite 不提交 Git。

代码、配置、文档和 durable knowledge 可以提交。

如果需要长期知识快照：

~~~bash
sp7-powerlab knowledge-export
bash scripts/commit-knowledge.sh
~~~

默认不自动 push。

## 19. 常见排查入口

代码/文件不知道在哪：

docs/PROJECT_MAP.md

不知道项目做到哪：

docs/PROJECT_STATUS.md

不知道设计为什么这样：

PLAN2.md

AI 调教纪律：

docs/LLM_BEHAVIOR.md
