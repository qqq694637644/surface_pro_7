# First Run — Surface Pro 7 PowerLab

本文件只负责**第一次部署 / 新电池从空白 evidence 走到 STABLE 的顺序和 checkpoint**。

它不是详细命令手册：

- 当前机器事实：`sp7-powerlab agent-context`
- 详细命令与 recovery：`docs/OPERATIONS.md`
- systemd / root helper：`docs/DEPLOYMENT.md`
- 设计语义：`PLAN2.md`

不要从本文推断当前机器已经完成哪个 Stage。

## 1. 安装并确认硬件合同

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

进入真实学习前确认目标 SP7、intel_pstate/HWP、BAT、thermal、thermald、ownership 和 root helper 都满足当前合同。

不满足硬件合同可以继续只读调查和软件维护，但不要开始 HWP trial。

## 2. 新电池先建立新的 battery epoch

更换电池后，先让 main service 产生属于新电池的 telemetry，再按 `OPERATIONS.md` 建立新 battery epoch。

不要拿旧电池最后一条 sample 创建新 epoch，也不要把旧电池 calibration/evidence 直接复用于新电池。

完成后重新运行：

```bash
sp7-powerlab agent-context
```

## 3. Stage A — Measurement Trust + Calibration

默认保持：

```toml
[automation]
level = 0
```

### 3.1 收集真实 Discharging

覆盖自然日常使用：

- idle；
- normal interactive；
- browser；
- media；
- remote work。

不要为了“加速学习”持续本地满载。

### 3.2 Preliminary Measurement Trust

检查 BAT gauge 与 Measurement Trust：

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
```

如果仍 BLOCKED，继续收集真实数据并按 `OPERATIONS.md` §5 排查；不要进入 candidate search。

### 3.3 Calibration

preliminary trust READY 后完成：

- cold_idle；
- normal_interactive；
- media；
- bounded_burst。

具体 start/finish 命令只看 `OPERATIONS.md`。

### 3.4 重新建立 current-epoch Measurement Trust

Calibration 改变 hard evidence context 后，重新建立 current-epoch trust：

```bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
sp7-powerlab agent-context
```

只有 current-epoch Measurement Trust READY 后进入 Stage B。

## 4. Stage B — Verified Baseline + Natural Reference / Noise

第一条 baseline 应来自机器真实 HWP snapshot，不要把候选配置文件里的参数直接宣布 VERIFIED。

常用入口：

```bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT   --note "current real HWP baseline"
```

继续真实日常使用，观察：

```bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab lifecycle coverage
sp7-powerlab agent-context
```

Stage B 的退出条件不是“等够时间”，而是 current reference/noise 足以支持后续 bounded search。具体 evidence 语义见 `PLAN2.md`。

## 5. Stage C — Bounded Coarse Search

只有 Stage B 已有 current reference/noise，且 Scheduler eligible 时才开始。

建议首次真实学习只使用 Level 2：

```toml
[automation]
level = 2
auto_promote = false
```

进入 coarse search：

```bash
sp7-powerlab lifecycle optimize --reason "begin coarse search"
sp7-powerlab scheduler status
```

candidate、trial、feedback、promotion、rollback 的详细命令只看 `OPERATIONS.md` §8–12。

Stage C 可以正常结束为：

- verified winner；
- rejection；
- practical equivalence；
- inconclusive；
- 没有 practical headroom。

不要把“必须找到 winner”当作阶段成功条件。

## 6. Stage D — Independent Validation + Real-Usage Burn-in

有值得保留的 verified policy 后：

```bash
sp7-powerlab lifecycle validate --reason "begin independent validation and usage burn-in"
sp7-powerlab lifecycle coverage
sp7-powerlab lifecycle readiness
```

Stage D 验证的是：

- independent revalidation；
- representative real usage；
- trusted/valid usage；
- current-policy UX；
- unresolved UnexpectedPower / investigation；
- drift。

此时 readiness 因 Stage E 尚未完成而 BLOCKED 是正常的。不要提前 freeze STABLE。

详细 coverage/readiness 操作见 `OPERATIONS.md` §16。

## 7. Stage E — Dynamic vs Fixed-good

Formal Stage E 只回答：

> Level-1 Dynamic 长期运行是否比 verified fixed-good 有可重复的实际净收益？

正式实验、A1-B1-B2-A2 capture、campaign、comparison 和 summary **全部按 `OPERATIONS.md` §17 执行**；本文不复制第二套 procedure 或 contract 字段列表。

可能结论：

- `KEEP_DYNAMIC_CONTROLLER`
- `FIXED_GOOD_ENVELOPE`
- `NEED_MORE_DATA`

MONITORING 只在需要解释 observer overhead 时做可选诊断，不参与 formal StableReadiness。

## 8. 恢复最终 selected runtime

Stage E 得到可执行 recommendation 后，使用正式 runtime 切换命令。

Dynamic：

```bash
sp7-powerlab envelope activate-dynamic
```

Fixed-good：

```bash
sp7-powerlab envelope activate-fixed-good
```

不要用手工 stop/start 代替最终切换。

然后：

```bash
sp7-powerlab agent-context
sp7-powerlab lifecycle readiness
```

如果 selected runtime 与 evidence/current physical reality 不一致，先解决 blocker，不要 freeze。

## 9. A–E 完成后才进入 STABLE

只有 deterministic readiness 为 ready 才：

```bash
sp7-powerlab lifecycle freeze --reason "Stage A-E and current policy net benefit validated"
```

进入 STABLE 后：

- Dynamic：保留 Level-1 core runtime；
- Fixed-good：main daemon disabled，login/reboot 使用最小 fixed one-shot；
- Scheduler 默认睡眠；
- 不部署 scheduled Agent review；
- 正常 Agent 结论应经常是 `NO_CHANGE`。

fixed-good 下按需用 `agent-context` / `lifecycle readiness` 做现场 audit；不要把 daemon 停止前的 `latest_sample` 当成实时 telemetry。

## 10. Level 3/4 暂不作为首次运行目标

Level 3/4 已实现，但尚未经过真实 SP7 长期验证。

首次新电池流程优先证明 Level 0–2、Measurement Trust、rollback、thermal preemption、Reference/Noise、independent validation 和 Stage E。

是否保留/启用 Level 3/4 留给真机数据决定。

## 11. 真机完成前不要声称

在实际新电池 Stage A–E 完成前，不要声称：

- 已找到最省电参数；
- noise/MUE 已充分真机校准；
- minimum arm duration 已真机确认；
- Dynamic Controller 一定优于 fixed-good；
- Level 3/4 已适合长期无人监督。

当前软件成熟度见 `docs/PROJECT_STATUS.md`。
