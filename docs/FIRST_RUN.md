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
- 没有近期严重负面反馈。

`stable.coverage_days` 是 lookback window，不代表已经自动观察了这么久。

此时 readiness 仍可能因为 `net_benefit_validation_incomplete` 被 BLOCKED，这是正常的：还需要 Stage E。

## 11. Stage E — End-to-End Net Benefit / Complexity Selection

PowerLab 自己也必须证明值得。

正式 Net Benefit 不接受任意旧 JSONL 在比较时补贴 epoch/campaign 标签。每个 capture 在开始时固定：

- battery identity / epoch
- current hard evidence epoch/fingerprint
- calibration version
- evidence semantics
- capture mode
- fixed baseline name/content hash
- runtime policy fingerprint
- validation campaign

runtime policy fingerprint 代表 relevant runtime/config、Automation Level、VERIFIED envelope set/content hashes 和 manual override。B1/B2 如果 policy fingerprint 不同，即使 mode 名相同也不是同一 treatment。

每一个 A1-B1-B2-A2 comparison 开始前，先把 Automation Level、verified envelope set 和 manual override
设成该 comparison 要验证的 policy，并在四个 block 期间保持不变。FIXED_GOOD A block 只是停止 service，
不是临时改 config；否则 A/B fingerprint 会不同。

### 11.1 一个 comparison 使用 A1-B1-B2-A2

例如 monitoring：

```bash
# A1：实际 HWP 回到选定的 fixed-good VERIFIED envelope，然后停止 service。
systemctl --user stop sp7-powerlab.service
# FIXED_GOOD 会拒绝仍然 fresh 的 service heartbeat；默认配置下等待 >30s。
sleep 35
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60

# B1/B2：Automation Level 0，启动 service，连续采两个 monitoring block。
systemctl --user start sp7-powerlab.service
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode MONITORING --count 60
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode MONITORING --count 60

# A2：恢复同一个 fixed-good envelope，停止 service。
systemctl --user stop sp7-powerlab.service
sleep 35
sp7-powerlab-meter --campaign sp7-net-benefit-01 --mode FIXED_GOOD --count 60
```

记录四个 `meter-...` run id：

```bash
sp7-powerlab overhead compare meter-a1 meter-b1 meter-b2 meter-a2
```

再在**同一个 OPEN campaign** 中分别完成：

- MONITORING
- DYNAMIC_CONTROLLER
- FULL_POWERLAB

对应 runtime mode：

- `MONITORING`：live service + Automation Level 0 + fixed HWP
- `DYNAMIC_CONTROLLER`：live CONTROL_ALLOWED service + Automation Level 1
- `FULL_POWERLAB`：live CONTROL_ALLOWED service + Automation Level >= 2
- `FIXED_GOOD`：service stopped + actual HWP 每点匹配同一个 VERIFIED fixed envelope

PowerLab 会检查：

- BAT consistency / data quality
- provenance
- A1/B1/B2/A2 时间顺序和 inter-block gap
- fixed baseline identity
- runtime policy fingerprint
- brightness
- active/media/remote fraction
- network
- package temperature
- reference drift
- 两个 candidate delta 的方向和 spread

明显不可比时结果不会进入 STABLE evidence。

### 11.2 campaign 不是字符串标签

`--campaign` 对应数据库中的 validation campaign entity：

- 新 campaign 必须从 FIXED_GOOD A1 开始；
- 固定 hard/battery/calibration/semantics context 与 fixed baseline；
- 受 `net_benefit.max_campaign_span_seconds` 限制；
- context/baseline 变化或超时会 INVALID；
- Monitoring/Dynamic/Full 三类有效 comparison 各完成一次后自动 COMPLETE/CLOSED；
- CLOSED campaign 不能继续塞结果。

查看：

```bash
sp7-powerlab overhead history
sp7-powerlab overhead summary
sp7-powerlab lifecycle readiness
```

可能结论：

- KEEP_FULL_POWERLAB
- KEEP_DYNAMIC_REDUCE_MONITORING
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

如果结果是 FIXED_GOOD_ENVELOPE，不要因为系统已经复杂就强行保留动态层。

在进入 STABLE 前，把 runtime 恢复到 recommendation 对应、已经验证过的 policy identity：

- KEEP_FULL_POWERLAB：恢复 Full comparison 的 policy/config；
- KEEP_DYNAMIC_REDUCE_MONITORING：恢复 Dynamic comparison 的 policy/config；
- FIXED_GOOD_ENVELOPE：恢复 Level-0/fixed-good policy，并确保 actual HWP 是固定 VERIFIED baseline。

随后再次运行 readiness；不要用“最后采集的是 Full”代替“最终选择的是哪个 policy”。

## 12. A–E 全部完成后才进入 STABLE

再次检查：

```bash
sp7-powerlab lifecycle readiness
```

只有 deterministic readiness 为 ready 才正常 freeze：

```bash
sp7-powerlab lifecycle freeze --reason "Stage A-E and current policy net benefit validated"
```

STABLE 还会确认 recommendation 对应的 selected policy fingerprint 仍代表**当前** runtime。Stage E 后如果
config、verified envelope set 或 override 改变，旧 Net Benefit 只能当历史记录，需要重新验证。

进入 STABLE 后：

- core telemetry 继续；
- drift / UnexpectedPower 继续；
- expensive attribution 降频；
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
- Full PowerLab 一定有净收益；
- Level 3/4 已适合长期无人监督。

当前实现/验证进度看 docs/PROJECT_STATUS.md。
