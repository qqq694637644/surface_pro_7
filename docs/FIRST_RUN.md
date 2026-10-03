# First Run — Surface Pro 7 PowerLab

本文件只负责**第一次部署 / 新电池从空白 evidence 走到 STABLE 的顺序**。

它不是详细命令手册：

- 当前状态：`sp7-powerlab agent-context`
- 详细命令与 recovery：`docs/OPERATIONS.md`
- systemd / root helper：`docs/DEPLOYMENT.md`
- 正式设计语义：`PLAN2.md`

不要从本文推断当前机器已经完成哪个 Stage。

## 1. 安装并确认硬件契约

安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

bash scripts/install-user-services.sh
bash scripts/install-root-helper.sh
```

然后：

```bash
sp7-powerlab doctor
sp7-powerlab agent-context
```

进入真实学习前至少确认：

- Surface Pro 7 / i5-1035G4；
- intel_pstate active；
- HWP/EPP 与 Turbo control；
- BAT、RAPL、package thermal sensor；
- thermald active；
- 没有持续冲突 power writer；
- root helper protocol / implementation identity 匹配。

不满足硬件合同可以继续只读调查和软件维护，但不要开始 HWP trial。

## 2. 新电池先建立新的 battery epoch

更换电池后，先让 main service 产生至少一条属于新电池的 telemetry，再运行：

```bash
sp7-powerlab calibrate new-battery
sp7-powerlab agent-context
```

不要拿旧电池最后一条 sample 创建新 epoch，也不要把旧电池 calibration/evidence 直接复用于新电池。

battery epoch 的检查和 recovery 细节见 `docs/OPERATIONS.md`。

## 3. Stage A — Measurement Trust + Calibration

### 3.1 先只读收集真实 Discharging

保持默认：

```toml
[automation]
level = 0
```

覆盖真实日常使用：

- idle；
- normal interactive；
- browser；
- media；
- remote work。

不要为了“加速学习”故意持续本地满载。

### 3.2 Preliminary Measurement Trust

至少检查：

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
```

preliminary READY 要来自真实连续 Discharging consistency，而不是把 Charging/suspend/gap 前后的短片段拼起来。

如果仍 BLOCKED，继续收集真实数据并按 `OPERATIONS.md` 的 Measurement Trust 章节排查；不要进入 candidate search。

### 3.3 Calibration

preliminary trust READY 后完成：

- cold_idle；
- normal_interactive；
- media；
- bounded_burst。

具体 start/finish 命令见 `docs/OPERATIONS.md`。

bounded_burst 只用于观察短时行为和热惯性，不是 sustained benchmark。

### 3.4 重新建立 current-epoch Measurement Trust

Calibration 会改变 hard evidence context，因此 preliminary trust 不能继续授权新的 current epoch。

重新运行：

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
sp7-powerlab agent-context
```

只有 current-epoch Measurement Trust READY 后才进入 Stage B。

## 4. Stage B — Verified Baseline + Natural Reference / Noise

### 4.1 收编第一条真实 baseline

不要把 `config/envelopes.toml` 的候选参数直接宣布 VERIFIED。

在 current hardware/calibration/trust/helper 都正常时，从机器真实 HWP snapshot 收编 baseline。常用入口：

```bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT   --note "current real HWP baseline"
sp7-powerlab envelope list
```

### 4.2 自然积累 Reference / Noise

继续真实日常使用，观察：

```bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab lifecycle coverage
sp7-powerlab agent-context
```

此阶段目标不是“快点出 winner”，而是建立可信的自然 baseline/noise。

Reference/Noise 的 clean-discharge、envelope revision 和 compatibility 规则见 `PLAN2.md`；实际排障命令见 `OPERATIONS.md`。

## 5. Stage C — Bounded Coarse Search

只有 Stage B 已建立 current reference/noise 且 Scheduler eligible 时才开始。

建议先使用 Level 2：

```toml
[automation]
level = 2
auto_promote = false
```

进入 coarse search：

```bash
sp7-powerlab lifecycle optimize --reason "begin coarse search"
sp7-powerlab scheduler status
sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT
```

Level 2 默认只提出 candidate。proposal、trial start/status、promotion、feedback 和 rollback 的唯一详细流程见 `docs/OPERATIONS.md`。

Stage C 接受这些正常终点：

- VERIFIED winner；
- REJECTED / LOSE；
- PRACTICALLY_EQUIVALENT；
- INCONCLUSIVE；
- 没有 practical headroom 后停止搜索。

candidate 自己造成的 thermal / PSI / media / UX 坏结果必须保留。

## 6. Stage D — Independent Validation + Real-Usage Burn-in

有值得保留的 verified policy 后：

```bash
sp7-powerlab lifecycle validate --reason "begin independent validation and usage burn-in"
sp7-powerlab lifecycle coverage
sp7-powerlab lifecycle readiness
```

Stage D 要证明的是“真实使用中可持续”，不是先 freeze STABLE。

关注：

- independent revalidation；
- representative usage；
- total valid / trusted usage；
- trusted fraction；
- distinct usage days；
- observation span；
- 当前策略 UX；
- unresolved UnexpectedPower / investigation；
- drift。

此时 readiness 因 `net_benefit_validation_incomplete` 保持 BLOCKED 是正常的，因为 Stage E 尚未完成。

## 7. Stage E — Dynamic vs Fixed-good

正式 Stage E 只比较：

- A = FIXED_GOOD；
- B = DYNAMIC_CONTROLLER（Automation Level 1）。

MONITORING 只在需要解释 observer overhead 时做可选诊断，不参与 formal StableReadiness。

正式实验使用一个 bounded A1-B1-B2-A2 campaign：

```text
A1  fixed-good
B1  Dynamic
B2  Dynamic
A2  same fixed-good
```

**不要在 FIRST_RUN 复制完整 meter/runbook。** 按 `docs/OPERATIONS.md` 的 “Stage E — Net Benefit / Complexity Selection” 章节执行，那里是唯一正式命令来源。

Stage E 会验证当前：

- hard/battery/calibration/evidence context；
- fixed baseline identity；
- formal cadence / gap / sample floor；
- runtime policy fingerprint；
- daemon loaded code/config identity；
- Stage E contract identity；
- media compatibility generation；
- investigation / UnexpectedPower / control-safety contamination；
- brightness/activity/media/remote/network comparability；
- reference drift 和 B1/B2 repeatability。

可能结论：

- `KEEP_DYNAMIC_CONTROLLER`
- `FIXED_GOOD_ENVELOPE`
- `NEED_MORE_DATA`

B1/B2 都达到 practical saving 才保留 Dynamic；只有一个达到时不强行选择。

## 8. 恢复最终 selected runtime

在检查 STABLE readiness 前，先把机器恢复到 Stage E recommendation 对应的真实 runtime。

如果选择 Dynamic：

```bash
sp7-powerlab envelope activate-dynamic
```

如果选择 fixed-good：

```bash
sp7-powerlab envelope activate-fixed-good
```

这些命令负责相应的 live preflight、daemon/oneshot 切换和 identity/readiness 验证；不要用手工 stop/start 替代正式最终切换。

然后：

```bash
sp7-powerlab agent-context
sp7-powerlab lifecycle readiness
```

## 9. A–E 全部完成后才进入 STABLE

只有 deterministic readiness 为 ready 才：

```bash
sp7-powerlab lifecycle freeze --reason "Stage A-E and current policy net benefit validated"
```

进入 STABLE 后：

- Dynamic：main service 保留 core telemetry / drift / UnexpectedPower；
- Fixed-good：main daemon 保持 disabled，login/reboot 只运行最小 fixed oneshot；
- 不部署 scheduled Agent review timer；
- Scheduler 默认睡眠；
- 不主动制造新 trial；
- 正常 Agent 结论应经常是 `NO_CHANGE`。

fixed-good 下按需用 `agent-context` / `lifecycle readiness` 做现场 audit；不要把 daemon 停止前的 `latest_sample` 当成实时 telemetry。

## 10. Level 3/4 暂不作为首次运行目标

Level 3/4 已实现，但尚未经过真实 SP7 长期验证。

首次新电池流程优先证明：

- Measurement Trust；
- transactional HWP / rollback；
- thermal preemption；
- Reference/Noise；
- independent revalidation；
- Scheduler stop rules；
- Stage D usage；
- Stage E Net Benefit。

真实数据证明 Level 0–2 已可靠、且更高自动化确实有价值后，再决定是否保留/启用 Level 3/4。

## 11. 真机完成前不要声称

在实际新电池 Stage A–E 完成前，不要声称：

- 已找到最优省电参数；
- 当前 noise/MUE 已充分真机校准；
- Dynamic Controller 一定省电；
- Level 3/4 已适合长期无人监督。

当前软件成熟度见 `docs/PROJECT_STATUS.md`。
