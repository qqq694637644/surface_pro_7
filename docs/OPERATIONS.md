# Operations

本文件描述**已经部署的真实 Surface Pro 7** 的日常运维、调查、实验、Stage D/E validation 和 STABLE 操作。

如果你是 AI，先读根目录 AGENTS.md。纯仓库工程/代码维护不要把本文件当作启动检查单；非 SP7 开发机的 runtime/hardware BLOCKED 也不是仓库维护 blocker。

真实 SP7 运维的第一条命令：

```bash
sp7-powerlab agent-context
```

不要从 Markdown 推断实时状态。

## 1. 状态总览与三类 runtime state

```bash
sp7-powerlab agent-context
sp7-powerlab service status
sp7-powerlab lifecycle status
sp7-powerlab safety status
```

必须分开理解：

- Control Safety：现在能不能写机器；
- Learning Lifecycle：现在应该积累什么 evidence / Scheduler 是否应工作；
- Investigation：是否正在调查异常。

它们是正交状态，不要压成一个“系统正常/异常”。

按问题继续：

```bash
sp7-powerlab evidence status
sp7-powerlab scheduler status
sp7-powerlab lifecycle readiness
```

## 2. 日常 power / thermal

```bash
sp7-powerlab observe now
sp7-powerlab observe power --hours 24
sp7-powerlab observe thermal --hours 24
```

关注：

- battery_status
- BAT W / battery energy
- battery %
- package temperature
- thermal state
- current envelope
- current envelope revision/content identity
- demand region
- resume grace

短时间高 W 不自动等于问题。先判断 workload、data quality 和持续时间。

## 3. UnexpectedPower / Drift 先调查

检查：

```bash
sp7-powerlab unexpected-power list
sp7-powerlab unexpected-power inspect <event-id>
```

如果有 investigation：

```bash
sp7-powerlab investigation list
sp7-powerlab investigation inspect <investigation-id>
sp7-powerlab investigation attribute <investigation-id>
```

正确顺序：

```
UnexpectedPower / Drift
  -> Investigation
  -> Attribution
  -> verification
```

不是：

```
UnexpectedPower -> 降 CPU -> 自动搜索
```

慢性 drift 看 FrozenReferenceBaseline 对 recent distribution；不要让 recent adaptive distribution 吞掉长期退化。

## 4. 关闭 investigation

只有有证据时分类。

例如：

```bash
sp7-powerlab investigation close <id> EXPECTED_WORKLOAD_CHANGE \
  --reason "user requested file transfer"

sp7-powerlab investigation close <id> CONFIRMED_CONFIG_REGRESSION \
  --reason "verified envelope regression reproduced"
```

`CONFIRMED_CONFIG_REGRESSION` 会让 learning reopen。

普通 `EXPECTED_WORKLOAD_CHANGE` 不应唤醒 Scheduler。

## 5. Measurement Trust

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
```

如果 Measurement Trust BLOCKED：

- 不解释微小 W 差异；
- 不手工绕开 Scheduler gate；
- 先增加有效 Discharging 数据或修 telemetry；
- 检查 contiguous consistency windows，而不是只看累计 valid seconds。

Measurement Trust 绑定当前 battery/calibration/evidence epoch。换电池、重新 calibration 或 hard epoch 变化后，旧 READY 不能继续授权当前学习。

## 6. Evidence identity / Reference / Noise

```bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab envelope list
```

复用历史 evidence 前先判断 identity：

- battery epoch
- hard evidence epoch
- relevant compatibility generation
- envelope content hash
- trial `evidence_scope_key`
- Stage E runtime policy fingerprint / campaign

同名不等于同一证据。

Natural Reference/Noise 只接受 clean Discharging rollup：

- 无 Charging/AC；
- 无 resume grace；
- 无超限 sample gap；
- battery/evidence epoch 单一；
- brightness/envelope/media/active/remote 等 required strata 稳定；
- valid-discharge fraction 达标。

sample 会冻结 `current_envelope_content_hash`。同名 envelope promotion 后，新 revision 进入新的 Reference/Noise scope；当前 envelope 还必须仍为 VERIFIED 且 DB content hash 与 rollup 冻结值匹配。

历史 evidence 可以做 prior / regression investigation，但不能在 identity 不兼容时直接给当前 promotion 或 trusted coverage 投票。

## 7. Envelope 与 manual override

```bash
sp7-powerlab envelope list
sp7-powerlab envelope inspect INTERACTIVE_EFFICIENT
```

Manual override：

```bash
sp7-powerlab envelope override INTERACTIVE_EFFICIENT
sp7-powerlab envelope clear-override
```

普通 Controller 只使用 VERIFIED envelope。

注意：manual override、VERIFIED envelope set/content hash 和 Level-1 Dynamic code/config identity 都属于
Dynamic runtime policy identity；Stage E 测量合同还有独立 code identity。相关 identity 改变后，旧 Net
Benefit 可能不再代表当前 runtime。

## 8. Scheduler

```bash
sp7-powerlab scheduler status
sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT
```

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

这些 blocker 是正常停止机制，不是需要“绕开”的错误。

Scheduler 是有限搜索器。没有 practical headroom 时应停止。

## 9. Trial / deterministic Evidence

查看：

```bash
sp7-powerlab trial status
```

手动启动：

```bash
sp7-powerlab trial start proposals/example.json
```

必要时回滚：

```bash
sp7-powerlab trial rollback --trial-id trial-xxxx \
  --reason "manual rollback"
```

Promotion：

```bash
sp7-powerlab trial promote trial-xxxx
```

只有 VERIFIED_WINNER 能 promotion。

Trial evidence 必须按当前 `evidence_scope_key` 聚合；相同 candidate 参数在不同 baseline/workload/compatibility 下不是同一个实验问题。

Initial crossover 和 independent revalidation 必须独立。Candidate 造成的 thermal / PSI / media / UX 坏结果不能过滤。

## 10. 用户体验反馈

```bash
sp7-powerlab feedback good --envelope INTERACTIVE_EFFICIENT

sp7-powerlab feedback sluggish \
  --trial-id trial-xxxx \
  --notes "remote interaction feels slower"
```

负面反馈是重要 outcome，不要因为 BAT 更低而忽略卡顿、不稳定或 remote latency。

## 11. Thermal

THERMAL_PRESSURE / THROTTLING 时：

- active trial 应回滚；
- Controller 优先 THERMAL_SAFE；
- validated thermal safety provider 保持独立；
- 先检查 workload 是否异常；
- 持续高本地负载优先考虑远程执行，而不是放宽热安全。

thermal safety 可以抢占任何 trial。

## 12. Rollback integrity recovery

如果 HWP apply 后无法精确恢复并验证 baseline，PowerLab 会把 `rollback_integrity_fault` 持久锁存为 EMERGENCY。helper/sysfs 后续重新可写不会自动清除它。

先人工确认机器状态，再执行：

```bash
sp7-powerlab safety recover-rollback \
  --reason "verified actual HWP state after manual inspection"
```

只有实际 HWP snapshot 能唯一匹配一个 VERIFIED envelope 时 recovery 才成功。命令成功后先进入 READ_ONLY，下一轮正常 safety synchronization 再决定是否恢复 CONTROL_ALLOWED。

## 13. Suspend / Resume

Resume 后：

- 不信任 suspend gap 内 BAT integration；
- experiment-local rolling state 重建；
- resume grace 内不自动控制；
- HWP actual state 重新 reconcile。

如果 resume 后当前 envelope 与真实 HWP 不一致，以真实 snapshot/reconcile 为准。

## 14. Service / root helper restart

service restart 不继续跨重启 active trial。

未完成 trial：

- 尝试恢复 baseline snapshot；
- 标记 rolled back/failed；
- rollback integrity 不可信时 Control 进入 EMERGENCY/READ_ONLY。

root helper 晚启动时，runtime hardware refresh 会重新 discovery/bind actuator，不需要为了“让 helper 被发现”手工重启 PowerLab。

已经绑定可用 backend 后：

- 单次明确 probe failure 立即撤销 `hardware_writable` / 正常写权限；
- reconnect-capable client object 可以保留，下一次 probe 成功后重新通过 Control Safety gate；
- active trial 期间 actuator backend identity 冻结，不能静默切到另一个 backend。

恢复写入仍必须重新通过 Control Safety gate。

## 15. Breaking runtime schema

项目不维护旧 SQLite runtime migration/fallback。

schema mismatch 是人工处置状态：

- service 使用 exit status 78；
- systemd 不应 crash-loop；
- Agent 不得仅因为 agent-context 建议 reset 就自行删除 runtime。

确认用户允许丢弃旧 runtime 后：

```bash
sp7-powerlab reset-runtime --yes
systemctl --user restart sp7-powerlab.service
```

这是破坏式 reset。

## 16. Stage D — Validation / Real-Usage Burn-in

Stage C 找到值得保留的 verified policy 后，进入 validation：

```bash
sp7-powerlab lifecycle validate --reason "begin validation and real-usage burn-in"
sp7-powerlab lifecycle coverage
sp7-powerlab lifecycle readiness
```

Stage D 要证明当前 policy 在代表性真实使用中成立，而不是立刻 freeze STABLE。

检查：

- independent revalidation；
- trusted usage fraction；
- minimum total valid usage seconds；
- minimum total trusted usage seconds；
- minimum distinct usage days；
- minimum observation span；
- current Reference/Noise；
- no active trial；
- no unresolved investigation/UnexpectedPower；
- no unresolved severe negative feedback against the current retained policy；已 reject/rollback 的 candidate
  反馈不继续阻塞当前策略。

`stable.coverage_days` 是 lookback window，不等于已经观察够时长。

如果此时 readiness 的主要剩余 blocker 是 `net_benefit_validation_incomplete`，说明 Stage D 已接近完成，进入 Stage E；**不要先 freeze STABLE**。

## 17. Stage E — Net Benefit / Complexity Selection

正式 Net Benefit capture 不接受任意旧 JSONL 后补 epoch/campaign 标签。capture-time provenance 包括：

- current evidence epoch
- battery identity/epoch
- hard fingerprint
- calibration
- evidence semantics
- campaign
- fixed baseline name/content hash
- capture mode
- runtime policy fingerprint
- Stage E contract identity
- live media compatibility generation

Dynamic runtime policy fingerprint 覆盖 Level-1 runtime 真正使用的 config、Automation Level、VERIFIED
envelope set/content hashes、manual override 和 explicit code allowlist。Scheduler search code 不进入 Level-1
identity；Level-1 实际执行的 evidence/evaluation/longterm/calibration 等路径进入。Stage E 测量合同另有
独立 contract identity（正式比较代码 + 相关 config/threshold）；media compatibility generation 也属于
campaign provenance，并在 compare/readiness 时现场刷新。

live service 的 heartbeat 还带 daemon 启动时冻结的 runtime code/config identity。正式 Dynamic/Monitoring
capture 会把它与当前磁盘源码/config 重算值比较；若代码或 `powerlab.toml` 已更新但 service 没 restart，
capture 直接拒绝。部署代码/config 后，正式 Stage E 前先：

```bash
systemctl --user restart sp7-powerlab.service
```

每个 A1-B1-B2-A2 comparison 内必须先冻结该次要验证的 policy/config；四个 block 期间 Automation
Level、VERIFIED envelope set 和 manual override 不得变化。FIXED_GOOD 不使用 manual override；A1/A2 都用
`sp7-powerlab fixed apply <VERIFIED envelope>` 显式恢复同一个物理 baseline。

正式 Stage E 只强制比较：

- FIXED_GOOD：main service off + fixed VERIFIED HWP；
- DYNAMIC_CONTROLLER：live Level 1 + CONTROL_ALLOWED。

MONITORING 是可选 observer-overhead 诊断，不参与 StableReadiness。Level 2+ 的 Scheduler/Agent/自动实验
也不是必须长期常开的 treatment。

当前版本不部署 scheduled review unit。MinimalMeter 会 fail-closed 检查遗留 hourly unit；旧 unit 若仍 active，
先清理再采集。

### 17.1 正式 Dynamic vs Fixed 使用 A1-B1-B2-A2

```bash
# A1: stop service, then explicitly restore the VERIFIED fixed baseline
systemctl --user stop sp7-powerlab.service
sp7-powerlab fixed apply INTERACTIVE_EFFICIENT
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60

# B1/B2: Automation Level 1, current daemon implementation, two independent Dynamic blocks
systemctl --user restart sp7-powerlab.service
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode DYNAMIC_CONTROLLER --count 60
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode DYNAMIC_CONTROLLER --count 60

# A2: stop service and physically restore the exact same fixed baseline again
systemctl --user stop sp7-powerlab.service
sp7-powerlab fixed apply INTERACTIVE_EFFICIENT
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60
```

MinimalMeter 默认 60 秒采样；上面的 `--count 60` 约为一小时。短暂 Control Safety 跳变不依赖提高
MinimalMeter 轮询频率捕获，而是在 block 结束后查询 durable `control_safety_history`。实际最低 block duration 仍取当前
Measurement Trust recommendation 与配置下限的较大值。

比较：

```bash
sp7-powerlab net-benefit compare meter-a1 meter-b1 meter-b2 meter-a2
```

runtime mode 不是标签：

- DYNAMIC_CONTROLLER：live Level 1 + CONTROL_ALLOWED
- FIXED_GOOD：live systemd state 必须 inactive + actual HWP 每点匹配同一个 VERIFIED fixed envelope

A1/B1/B2/A2 四块必须具有同一 runtime policy fingerprint、Stage E contract identity 和 media generation。
B1/B2 中间发生 promotion/config/override/policy 修改时，该 comparison 作废。完成 comparison 后，summary/
readiness 仍会用**当前** contract identity 与现场 browser/Mesa generation 复核，旧 COMPLETE campaign 不会
永久背书 STABLE。

PowerLab 还检查：

- BAT consistency/data quality
- 每个 block 恰好一段连续 Discharging observation；Charging/resume/gap/epoch change 整块失败
- provenance
- inter-block gap
- fixed baseline identity
- brightness / active / media / remote
- network
- reference drift
- B1/B2 effect direction/spread

package temperature 单独作为 treatment outcome。Dynamic 更凉不会被 veto；明显更热或出现 thermal safety
intervention 会阻止直接 KEEP_DYNAMIC_CONTROLLER，转为 NEED_MORE_DATA。

formal capture 还要求没有 active investigation、unresolved UnexpectedPower 或 Diagnostic Burst。capture
期间若新 investigation/event 出现，run INVALID；结束时会查询 `control_safety_history`，helper/ownership/
sensor 等导致的 READ_ONLY/DEGRADED/EMERGENCY interruption 会使 Dynamic block INVALID，thermal emergency
作为 outcome 保留。

### 17.2 campaign 是 bounded DB entity

`--campaign` 对应 OPEN/COMPLETE/INVALID validation campaign：

- 新 campaign 从 FIXED_GOOD A1 开始；
- 固定 hard/battery/calibration/semantics context 与 fixed baseline；
- 受 `net_benefit.max_campaign_span_seconds` 限制；
- context/baseline 改变或超时会 INVALID；
- 一个有效 Dynamic comparison 完成后自动 COMPLETE/CLOSED；
- CLOSED campaign 不能继续复用名字写入新结果。

查看：

```bash
sp7-powerlab net-benefit history
sp7-powerlab net-benefit summary
sp7-powerlab lifecycle readiness
```

可能结果：

- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

只有 B1/B2 两个 Dynamic paired delta 都达到 practical threshold 才保留 Dynamic；只有一个达到时
NEED_MORE_DATA；两个都达不到时回到 fixed-good。MONITORING 只在需要解释 observer overhead 时按需运行。

Stage E 结束后，把当前 runtime 落成 recommendation 对应的**已验证 selected policy**：

```bash
# 如果 summary 选择 FIXED_GOOD_ENVELOPE：
sp7-powerlab envelope activate-fixed-good

# 如果 summary 选择 KEEP_DYNAMIC_CONTROLLER（且 automation.level=1）：
sp7-powerlab envelope activate-dynamic
```

`activate-fixed-good` 会 disable main daemon、应用并回读 fixed baseline、保存 epoch/hash selection，并 enable
`sp7-powerlab-fixed.service`。该 oneshot 在 login/reboot 重应用 fixed envelope 后退出；不会把 10 秒 collector/
controller 重新常驻。`activate-dynamic` 做相反切换。之后 StableReadiness 同时校验 selected policy
fingerprint、actual runtime mode/daemon identity 或 fixed persistent audit。

## 18. 进入和运行 STABLE

**只有 Stage A–E 和 deterministic readiness 全部通过后**才正常 freeze：

```bash
sp7-powerlab lifecycle readiness
sp7-powerlab lifecycle freeze --reason "Stage A-E and current policy net benefit validated"
```

STABLE readiness 对 Dynamic 校验 selected runtime fingerprint/mode 和 daemon loaded code/config identity；对
Fixed-good 执行 one-shot live audit，直接验证 service inactive+disabled、fixed oneshot enabled、thermald、
ownership、hard identity、现场 media compatibility、baseline hash、persistent selection 和 actual HWP。

STABLE 下：

- selected=Dynamic：main service 保留 core telemetry / drift / UnexpectedPower；
- selected=Fixed-good：main service 可保持停止，状态/调查按需执行；
- qualified fixed-good 使用 freeze 时的 entry coverage；同 epoch/policy/mode 下 rolling coverage 自然过期不单独判 stale；
- 不部署 scheduled review timer；
- Scheduler 睡眠；
- 无主动 trial；
- 正常 Agent 结论经常是 NO_CHANGE。

如果真实数据不再支持当前配置，才 reopen：

```bash
sp7-powerlab lifecycle reopen --reason "confirmed regression"
```

reopen 后按当前证据缺口回到合适 Stage，不是假设必须从零重跑所有历史。

## 19. Git / Knowledge

高频 SQLite / runtime telemetry 不提交 Git。

代码、配置、文档和 durable knowledge 可以提交。

需要按需 review/知识快照：

```bash
sp7-powerlab review-pack --output history/continuous/knowledge.json
```

默认不自动 push。

仓库代码/文档修改涉及架构、schema、Evidence、Lifecycle、Agent 入口、实验或部署链时，本地运行：

```bash
bash scripts/quality-gate.sh
```

## 20. 常见入口

代码/文件不知道在哪：

- docs/PROJECT_MAP.md

不知道项目做到哪：

- docs/PROJECT_STATUS.md

不知道设计为什么这样：

- PLAN2.md

AI 调教/调查纪律：

- docs/LLM_BEHAVIOR.md

首次新电池/从零建证据：

- docs/FIRST_RUN.md

systemd/root helper/schema reset：

- docs/DEPLOYMENT.md
