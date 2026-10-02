# First Run — Surface Pro 7 PowerLab

本文件只描述真实 Surface Pro 7 第一次部署、新电池重新建基线，以及从空白 runtime 走到 STABLE 的**唯一操作顺序**。

AI / Agent 先读根目录 AGENTS.md。项目实现成熟度看 docs/PROJECT_STATUS.md。

任何时候都可以先运行：

```bash
sp7-powerlab agent-context
```

它会报告当前 runtime stage 和 blocker。不要从本文推断实时状态。

## 1. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

bash scripts/install-user-services.sh
bash scripts/install-root-helper.sh
```

root helper 只提供有限 HWP inspect/snapshot/apply/restore，不提供任意 root shell。

## 2. 先确认硬件契约

```bash
sp7-powerlab doctor
sp7-powerlab agent-context
```

自动控制至少依赖：

- Surface Pro 7
- i5-1035G4
- intel_pstate active
- HWP/EPP
- Turbo control
- BAT
- RAPL
- package thermal sensor
- thermald
- 无持续冲突 power writer

硬件契约不满足时，可以继续只读调查和软件维护，但不要开始 HWP trial。

## 3. 新电池先建立新的 battery epoch

如果刚更换电池，先确认 user service 已经写入至少一条新电池 telemetry：

```bash
sp7-powerlab observe power --hours 1
```

输出必须有 latest battery sample。若刚启动 service，先等正常采样出现，不要用旧电池最后一条 sample 创建新 epoch。

```bash
sp7-powerlab calibrate new-battery
```

新 battery epoch 会使旧 calibration / verified evidence 失效或需要重新验证。

坏旧电池和新电池的数据不要直接混在一起比较。

## 4. 先只读收集真实放电

默认 `automation.level = 0`。

保持只读，进行真实日常使用：

```bash
sp7-powerlab observe now
sp7-powerlab observe power --hours 6
sp7-powerlab agent-context
```

尽量覆盖：

- idle
- normal interactive
- browser
- media
- remote work

此阶段不要为了“加速学习”故意持续本地满载。

## 5. Stage A — Preliminary Measurement Trust

先看 gauge：

```bash
sp7-powerlab evidence gauge --hours 6
```

再评估：

```bash
sp7-powerlab evidence trust --hours 6
```

目标是 preliminary Measurement Trust = READY。

需要真实解决：

- energy_now quantum
- energy_now cadence
- power_now cadence/quantization
- BAT integration
- energy delta
- contiguous valid-discharge consistency
- minimum arm duration

长时间观察可以被 Charging/suspend/resume 打断，但 READY 必须来自足够长的连续 Discharging consistency windows；多个短片段累计够时长不能代替这个检查。

如果仍 BLOCKED：

- 继续收集更长的有效 Discharging 数据；
- 检查 suspend/resume gap；
- 检查 battery telemetry；
- 不开始 candidate search。

## 6. Calibration，然后重新建立 current-epoch Measurement Trust

preliminary trust READY 后，按顺序完成：

```bash
sp7-powerlab calibrate start cold_idle
sp7-powerlab calibrate finish

sp7-powerlab calibrate start normal_interactive
sp7-powerlab calibrate finish

sp7-powerlab calibrate start media
sp7-powerlab calibrate finish

sp7-powerlab calibrate start bounded_burst
sp7-powerlab calibrate finish

sp7-powerlab calibrate status
sp7-powerlab agent-context
```

bounded_burst 只用于热惯性和短时行为，不是 sustained benchmark。

Calibration 会改变 hard evidence context。之前的 preliminary trust 只证明“测量路径可用”，不能授权新的 current epoch。

重新运行：

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
```

只有这次 current-epoch Measurement Trust READY 后，才开始 Stage B。

## 7. Stage B — Verified Baseline + Natural Reference / Noise

### 7.1 收编第一条真实 VERIFIED baseline

不要把 `config/envelopes.toml` 里的 candidate 直接宣布 VERIFIED。

当 hardware contract、current-epoch Measurement Trust、calibration 和 helper 都正常后：

```bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT \
  --note "current real HWP baseline"

sp7-powerlab envelope list
sp7-powerlab agent-context
```

它读取机器当前真实 HWP snapshot。

### 7.2 自然积累 Reference / Noise

继续真实日常使用：

```bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab lifecycle coverage
```

自然 Reference/Noise 只接受 clean Discharging rollup，并按 frozen envelope content hash 隔离 policy revision。

这意味着：

- Charging/resume/gap transitional minute 可以保留为 usage telemetry，但不能进入 Reference/Noise；
- 同名 envelope promotion 后的新 content hash 不继承旧 Frozen Reference/Recent Noise；
- 当前 DB envelope 必须仍是 VERIFIED 且 content hash 匹配，才能继续作为 trusted policy evidence。

如果 noise/reference 不足，Scheduler 应保持 blocked。

## 8. Stage C — Bounded Coarse Search

建议先使用 Level 2：

```toml
[automation]
level = 2
auto_promote = false
```

然后：

```bash
sp7-powerlab lifecycle optimize --reason "begin coarse search"
sp7-powerlab scheduler status
sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT
```

Level 2 默认只提出 candidate。

实验前仍检查：

- ControlSafetyState
- current-epoch Measurement Trust
- current Reference/Noise
- minimum arm duration
- battery level
- thermal cooldown
- experiment budget
- active investigation
- current evidence scope

### Trial

常规 proposal：

```json
{
  "kind": "envelope",
  "baseline_envelope": "INTERACTIVE_EFFICIENT",
  "changes": {
    "max_perf_pct": 55
  }
}
```

启动：

```bash
sp7-powerlab trial start proposals/example.json
sp7-powerlab trial status
```

实验必须经历 initial crossover 和 independent revalidation。

只有 VERIFIED_WINNER 才能 promotion；PRACTICALLY_EQUIVALENT 和 INCONCLUSIVE 都是正常终点。

candidate 造成的 PSI、thermal、media 或 UX 坏结果不能被过滤掉。

## 9. 及时记录用户体验

出现卡顿、滚动不顺、remote latency 或不稳定时：

```bash
sp7-powerlab feedback sluggish \
  --trial-id trial-xxxx \
  --notes "browser scroll/input latency"
```

用户负面体验是重要 outcome，不要为了 BAT 更低而忽略。

## 10. Stage D — Independent Validation + Real-Usage Burn-in

当 coarse search 已经找到值得保留的 verified policy，进入 validation：

```bash
sp7-powerlab lifecycle validate --reason "begin independent validation and usage burn-in"
sp7-powerlab lifecycle coverage
sp7-powerlab lifecycle readiness
```

Stage D 的目标不是“先 freeze STABLE”，而是证明当前系统在代表性真实使用中稳定成立。

必须积累：

- independent revalidation；
- representative real usage；
- trusted usage fraction；
- minimum total valid usage seconds；
- minimum total trusted usage seconds；
- minimum distinct usage days；
- minimum observation span；
- 没有 unresolved UnexpectedPower/investigation；
- 没有针对当前保留策略的未解决严重负面反馈；已 reject/rollback 的坏 candidate 不继续阻塞当前策略。

`stable.coverage_days` 是 lookback window，不代表已经自动观察了这么久。

此时 readiness 仍可能因为 `net_benefit_validation_incomplete` 被 BLOCKED，这是正常的：还需要 Stage E。

## 11. Stage E — End-to-End Net Benefit / Complexity Selection

PowerLab 自己也必须证明值得。

正式 Stage E 只强制比较两个有清晰物理意义的状态：

- FIXED_GOOD：main service off，actual HWP 固定在同一个 VERIFIED envelope；
- DYNAMIC_CONTROLLER：service live，Automation Level 1，CONTROL_ALLOWED。

MONITORING（Level 0 + fixed HWP）仍可作为 observer-overhead 诊断，但不参与 StableReadiness，也不要求每次 Stage E 都跑。

Level 2+ 的 Scheduler/Agent/自动实验不是必须长期常开的第四种 treatment。成熟系统需要学习时再按需启用。

正式 Net Benefit 不接受任意旧 JSONL 后补 provenance。每个 capture 在开始时固定：

- battery identity / epoch
- current hard evidence epoch/fingerprint
- calibration version
- evidence semantics
- capture mode
- fixed baseline name/content hash
- runtime policy fingerprint
- Stage E measurement-contract identity（正式比较代码 + 相关 config/threshold）
- media compatibility generation
- validation campaign

Dynamic runtime policy fingerprint 只包含 Level-1 长期 runtime 真正使用的 config / VERIFIED set / override
和明确 code allowlist；Scheduler/Agent search code 不在其中，Level-1 实际执行的 evidence/evaluation/
longterm/calibration 等路径进入。Stage E 的测量/比较代码和相关合同参数组成独立 contract identity；任一
identity 改变都不会继续复用旧 Stage E。Dynamic/Monitoring 还会把 live daemon heartbeat 中的 loaded
code/config identity 与当前磁盘/config 比较；更新代码/config 后正式 Stage E 前必须 restart main service。

### 11.1 正式 capture 前清理遗留 scheduled review

当前版本不再安装 scheduled review timer/service。安装脚本会删除旧 unit；MinimalMeter 仍会检查遗留
`sp7-powerlab-hourly.timer/service`，若它们意外 active，整个 run INVALID。

### 11.2 一个 comparison 使用 A1-B1-B2-A2

正式 comparison 前把 Automation Level 设为 1，并在 A1-B1-B2-A2 四个 block 内保持 config、verified
envelope set 和 manual override 不变。A block 不使用 manual override；停止 main service 后用
`sp7-powerlab fixed apply <VERIFIED envelope>` 显式恢复同一个真实 HWP baseline。

```bash
# A1
systemctl --user stop sp7-powerlab.service
sp7-powerlab fixed apply INTERACTIVE_EFFICIENT
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60

# B1 / B2
systemctl --user restart sp7-powerlab.service
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode DYNAMIC_CONTROLLER --count 60
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode DYNAMIC_CONTROLLER --count 60

# A2
systemctl --user stop sp7-powerlab.service
sp7-powerlab fixed apply INTERACTIVE_EFFICIENT
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60
```

MinimalMeter 默认每 60 秒采样；`--count 60` 约为一小时。短暂 Control Safety transition 通过 durable
history 事后检查，不依赖更高频 polling。实际 formal minimum 仍以当前 Measurement Trust recommendation
和实验配置下限为准。

Formal cadence 由 `[net_benefit] sample_seconds/max_sample_gap_seconds/minimum_samples_per_block` 固定并进入
Stage-E contract identity。production meter/compare 不暴露 interval、gap 或 fake sys/proc root 的放宽参数；
四个 block cadence 必须一致且每块至少满足 sample floor。

记录四个 `meter-...` run id：

```bash
sp7-powerlab net-benefit compare meter-a1 meter-b1 meter-b2 meter-a2
```

PowerLab 会检查：

- BAT consistency / data quality
- 整个 block 恰好是一段连续 Discharging observation
- Charging/AC、resume、超限 gap、battery/evidence epoch change 会整块 fail closed
- provenance
- A1/B1/B2/A2 时间顺序和 inter-block gap
- fixed baseline identity
- runtime policy fingerprint
- Stage E contract identity / live media compatibility generation
- daemon loaded runtime code/config identity
- 遗留 scheduled-review units 不 active
- 无 active investigation / unresolved UnexpectedPower / Diagnostic Burst
- capture 期间无新 investigation/event；非 thermal Control Safety interruption 使 Dynamic block INVALID
- brightness
- active/media/remote fraction
- network
- reference drift
- 两个 candidate delta 的方向和 spread

package temperature 是 treatment outcome，不是 comparability veto；更凉不会被过滤，明显更热或 thermal
intervention 会阻止直接 KEEP_DYNAMIC_CONTROLLER。明显不可比时结果不会进入 STABLE evidence。

### 11.3 campaign 不是字符串标签

`--campaign` 对应数据库中的 validation campaign entity：

- 新 campaign 必须从 FIXED_GOOD A1 开始；
- 固定 hard/battery/calibration/semantics context 与 fixed baseline；
- 受 `net_benefit.max_campaign_span_seconds` 限制；
- context/baseline 变化或超时会 INVALID；
- Dynamic comparison 有效写入后自动 COMPLETE/CLOSED；
- CLOSED campaign 不能继续塞结果。

查看：

```bash
sp7-powerlab net-benefit history
sp7-powerlab net-benefit summary
sp7-powerlab lifecycle readiness
```

可能结论：

- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

最终选择只看 Dynamic Controller 相比 fixed-good。**B1 和 B2 两个独立 paired delta 都必须达到 practical
threshold** 才 KEEP_DYNAMIC_CONTROLLER；只有一个达到时是 NEED_MORE_DATA；两个都达不到则
FIXED_GOOD_ENVELOPE。

如果结果接近、需要知道 observer 本身是否太贵，可以在正式 Dynamic comparison 之前额外做一组
MONITORING A1-B1-B2-A2。它只进入诊断 history，不决定 campaign 是否 COMPLETE。

在进入 STABLE 前，把 runtime 恢复到 recommendation 对应、已经验证过的 policy identity：

- KEEP_DYNAMIC_CONTROLLER：Automation Level 1，执行 `sp7-powerlab envelope activate-dynamic`，等待 main service
  进入 fresh Level-1 / CONTROL_ALLOWED 且 loaded code/config identity 匹配；
- FIXED_GOOD_ENVELOPE：执行 `sp7-powerlab envelope activate-fixed-good`，由命令完成 live hard/battery/helper
  preflight、应用并回读固定 VERIFIED baseline、disable main daemon，并启用本 session 已成功执行的
  persistent fixed oneshot。

随后再次运行 readiness。它会同时检查 selected policy fingerprint 和 actual runtime mode。如果只是尚未恢复
selected mode/config，先 reconcile，不需要自动重做 Stage E。

## 12. A–E 全部完成后才进入 STABLE

再次检查：

```bash
sp7-powerlab lifecycle readiness
```

只有 deterministic readiness 为 ready 才正常 freeze：

```bash
sp7-powerlab lifecycle freeze --reason "Stage A-E and current policy net benefit validated"
```

STABLE 对 Dynamic 校验 selected runtime fingerprint；对 Fixed-good 每次 readiness / agent-context / freeze
都执行 one-shot live audit：main service inactive、thermald active、无 ownership conflict、live hard identity
和 live BAT identity/energy_full 仍匹配 evidence/battery epoch、现场 media compatibility 仍匹配、baseline
仍 VERIFIED/hash 一致、actual HWP 仍匹配 fixed envelope。若 fixed-good 是最终 selected runtime，还要求
main service disabled、fixed oneshot enabled 且当前为 active/exited，证明本 session 已成功应用；enabled
本身不算成功证据。

进入 STABLE 后：

- KEEP_DYNAMIC_CONTROLLER：main service 继续 core telemetry / drift / UnexpectedPower；
- FIXED_GOOD_ENVELOPE：main service 可以保持停止；按需使用 `agent-context` / `lifecycle readiness`
  检查现场 fixed runtime，用 `review-pack` 读取历史摘要。不要把 daemon 停止前的 `latest_sample` 当成
  当前实时 telemetry；
- qualified fixed-good 会保留 freeze 时的 entry coverage，只要 epoch/policy/mode 不变，不因 rolling window 自然过期要求常驻采样；
- 不部署 scheduled review timer；
- Scheduler 默认睡眠；
- 不主动 trial；
- 正常 Agent 结论应经常是 NO_CHANGE。

## 13. Level 3/4 只在真机链路成熟后考虑

只有以下链路在真机反复可靠后再考虑 Level 3：

- Measurement Trust
- transaction/read-back/rollback
- thermal preemption
- independent revalidation
- Scheduler stop rules
- experiment budget
- negative feedback
- Stage D burn-in
- Stage E Net Benefit

Level 3 允许 daemon 自动开始通过全部 gate 的低风险 trial。

Level 4 还允许满足条件的自动 promotion。

个人设备没有必要为了“自动化程度高”升级 level。

## 14. 真机完成前不要声称什么

在实际新电池 Stage A–E 完成前，不要声称：

- 已找到最优省电参数；
- 当前 noise/MUE 已被 SP7 真机充分校准；
- Dynamic Controller 一定省电；
- Level 3/4 已适合长期无人监督。

当前实现/验证进度看 docs/PROJECT_STATUS.md。
