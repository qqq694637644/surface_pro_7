# Surface Pro 7 PowerLab — PLAN2：收敛式长期学习与可信证据架构

> 状态：下一阶段设计合同，基于已合并到 `main` 的 PowerLab v2
>
> 目标硬件：Microsoft Surface Pro 7 / Intel Core i5-1035G4
>
> 核心目标：在用户体验良好、系统稳定、热状态可持续的前提下，提高真实电池续航；成熟后停止主动探索，只保留低开销监控、漂移检测与重新优化能力。
>
> 实施原则：下一阶段允许破坏式演进，不为旧运行时 SQLite 设计兜底兼容；任何新的自动学习能力都必须建立在可信测量和可审计证据之上。

---

## 1. PLAN2 为什么存在

PowerLab v2 已经解决了最危险的一批底层问题：

- 不让 LLM 进入实时控制回路；
- Intel HWP 负责毫秒级性能控制；
- PowerLab 只提供较慢的 operating envelope；
- BAT 整机功耗是主 reward；
- Demand / Thermal Observer 不再依赖模糊的语义场景分类；
- HWP 写入具备事务与 read-back；
- 真实 HWP 状态会与 SQLite 状态 reconcile；
- battery epoch、system fingerprint、calibration version 会约束证据有效性；
- Trial 已经能做 A1/B1/A2 + A3/B2 的独立 revalidation；
- candidate 造成的 PSI、thermal、media regression 不会被从结果里过滤；
- LLM 只接触窄 `sp7-powerlab-agent` capability；
- root helper 不提供任意 root shell。

这些基础继续保留。

PLAN2 不再讨论“如何让 AI 更聪明地自动调参数”。

PLAN2 要解决的是更基础的问题：

1. 我们是否真的能稳定测出 0.1W、0.2W 的差异？
2. 某个候选到底是真赢，还是恰好遇到一个幸运窗口？
3. 如果收益小于自然噪声，为什么还要继续实验？
4. 历史上曾经有效的配置，在 kernel、Firefox、Mesa、电池变化以后还可信吗？
5. PowerLab 自己的 telemetry、SQLite、process scan 和定时任务消耗多少电？
6. 找到足够好的配置以后，系统为什么还要继续折腾用户？
7. 如果一个固定良好配置已经达到完整 PowerLab 98% 的效果，是否应该承认复杂系统没有继续存在的必要？

PLAN2 的答案是：

> **PowerLab 不追求永远持续优化。它追求可信测量、有限探索、可靠收敛，以及在环境变化后重新优化的能力。**

---

## 2. 顶层设计原则

PLAN2 以以下七条原则作为设计合同。

### 2.1 先证明测量可信

任何优化器、搜索器、LLM proposal 都不能弥补不可信的数据。

如果系统不知道自己的自然波动是多少，就没有资格声称：

> “这个 candidate 节省了 0.08W。”

### 2.2 先找异常功耗，再考虑降低性能

优先级始终是：

1. 异常后台活动；
2. 硬件加速失效；
3. runtime PM / device idle 问题；
4. 软件或 kernel regression；
5. 不必要的 wakeup / interrupt；
6. 最后才是降低 CPU 性能意图。

一个真实的 +1W regression，通常比把 max_perf_pct 从 60 微调到 55 更有价值。

### 2.3 HWP 负责快控制，PowerLab 只给慢意图

PowerLab 不实现自定义 DVFS。

不做：

- 10ms 级频率调节；
- 用户态替代 Intel HWP；
- 实时 ML controller；
- 实时 contextual bandit；
- reinforcement learning 控制 CPU。

实时路径继续保持 deterministic。

### 2.4 Evidence Engine 判断事实，LLM 无权判输赢

LLM 不能：

- 计算 reward；
- 改实验通过门槛；
- 修改 noise floor；
- 修改 data-quality gate；
- 宣布 WIN；
- 宣布 promotion；
- 把 hypothesis 写成 confirmed root cause。

### 2.5 Candidate Scheduler 尽量简单

第一版只做有限、离散、邻域搜索。

不为了“机器学习”而引入 Bayesian Optimization。

### 2.6 进入 STABLE 后停止主动实验

大多数成熟运行时间应该是：

`NO_CHANGE`

只有发生漂移、异常或明显新机会，才重新开启优化。

### 2.7 最终只认 Net Battery Benefit

不是：

> PowerLab 算法多聪明。

而是：

> PowerLab 开启以后，真实续航净收益是否足以覆盖它自己的监控开销、误判风险和维护复杂度。

---

## 3. 项目目标重新定义

原来的“持续优化”改为：

> **持续保持重新优化的能力。**

PowerLab 生命周期不是无限 trial loop。

正确模式是：

```text
前期认真学习
    ↓
找到足够好的配置
    ↓
冻结
    ↓
长期低开销监控
    ↓
只有 drift / incident / new opportunity
    ↓
重新打开优化
```

成熟以后 99% 时间都可能没有主动实验。

这不是失败。

这是系统收敛成功。

---

## 4. PLAN2 总体架构

```text
                    Linux / Intel HWP
                   毫秒级硬件性能控制
                           ▲
                           │
                 Deterministic Controller
                           │
                   VERIFIED ENVELOPE
                           │
          ┌────────────────┴────────────────┐
          │                                 │
  Demand / Thermal                  Unexpected Power
     Observers                         Detector
          │                                 │
          └────────────────┬────────────────┘
                           ▼
                  Trusted Measurements
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       Evidence Engine              Attribution
              │                         │
     WIN / LOSE / EQUIV                LLM
       / INCONCLUSIVE              Researcher
              │                    hypothesis
              ▼
      Candidate Scheduler
      simple discrete search
              │
              ▼
          Trial Engine

                 Lifecycle Manager
 READ_ONLY / CALIBRATING / OBSERVING / OPTIMIZING
              / VALIDATING / STABLE
```

关键变化：

- `Evidence Engine` 和 `Candidate Scheduler` 分离；
- `Waste Detector` 改成 `Unexpected Power Detector`；
- 新增 `Lifecycle Manager`；
- 新增 empirical noise model；
- 新增 data-quality gate；
- 新增 monitoring overhead / net benefit；
- LLM 从“optimizer”彻底降级为 researcher。

---

## 5. 现有 v2 中继续保留的部分

PLAN2 不推倒以下模块。

### 5.1 Telemetry

继续采集：

- BAT status；
- BAT power；
- energy_now；
- energy_full；
- battery epoch；
- CPU utilization；
- PSI；
- load；
- EPP；
- max_perf_pct；
- Turbo；
- package temperature；
- thermal slope；
- RAPL；
- brightness；
- network；
- GPU hints；
- media state；
- active/idle；
- process attribution；
- system/software fingerprint。

### 5.2 Deterministic Controller

Controller 继续保持“无聊”。

示意：

```text
idle
→ ECO_IDLE

remote + low local compute
→ REMOTE_EFFICIENT

media
→ MEDIA_EFFICIENT

normal interactive
→ INTERACTIVE_EFFICIENT

thermal pressure
→ THERMAL_SAFE
```

它不学习。

它只应用已经 VERIFIED 的 envelope。

### 5.3 Thermal safety

`thermald` 继续是独立安全层。

PowerLab ThermalObserver 继续用于：

- 观测；
- 实验约束；
- heat-soak 检测；
- trial preemption；
- cooldown / washout 判断。

### 5.4 HWP transaction

继续只允许受审查的有限 HWP 参数。

不开放任意 sysfs path。

### 5.5 Battery Epoch / Fingerprint

继续禁止把：

- 旧电池；
- 新电池；
- 大 kernel 变化；
- BIOS 变化；
- thermal sensor 变化；
- calibration 变化

混成一个长期“永久真理”。

### 5.6 Narrow LLM capability

继续禁止把：

- 任意 Bash；
- 任意 Python/code execution；
- 主 `sp7-powerlab` CLI；
- root helper socket

暴露给 LLM。

---

## 6. Trusted Measurements

PLAN2 的第一优先级不是 Scheduler，而是可信测量。

任何实验结论之前必须先经过：

`Measurement Quality Gate`

---

## 7. BAT reward 不再只是平均 power_now

实验层同时维护两个能量估计。

### 7.1 BAT power integration

对有效放电窗口：

```text
E_integrated
=
∫ P_battery(t) dt
```

必须：

- gap-aware；
- 只统计 Discharging；
- 不跨 suspend；
- 不跨 AC / Charging；
- 保留时间权重；
- 不把缺失样本插值成真实数据。

### 7.2 Battery energy delta

同时记录：

```text
E_delta
=
energy_now(start)
-
energy_now(end)
```

### 7.3 两者的定位

它们不是两台独立仪器。

很可能都来自同一 battery gauge / EC。

因此它们用于：

> consistency check

不是：

> 两个独立投票。

### 7.4 Data-quality gate

例如：

```text
integrated = 0.82 Wh
energy delta = 0.79 Wh
```

可接受。

如果：

```text
integrated = 0.82 Wh
energy delta = 0.41 Wh
```

该 episode：

```text
DATA_QUALITY_FAILURE
```

不能进入 WIN / LOSE 判定。

具体容忍度不能先拍脑袋写死，应在 Stage A 真机 burn-in 后确定。

---

## 8. Empirical Noise Floor

固定：

```text
min_power_saving_w = 0.10
```

不再作为最终科学依据。

### 8.1 Noise baseline 来源

在 VERIFIED envelope 自然运行时收集重复窗口。

匹配至少包括：

- battery epoch；
- system fingerprint；
- envelope revision；
- brightness bucket；
- demand region；
- active / idle；
- media state；
- remote / local；
- thermal start state；
- power source；
- data-quality status。

### 8.2 Noise 不是单一全局数字

不同 envelope / demand 应有不同 noise model。

例如：

```text
INTERACTIVE_EFFICIENT / active / local
noise = N1

REMOTE_EFFICIENT / active / remote
noise = N2

MEDIA_EFFICIENT / media
noise = N3
```

### 8.3 第一版统计量

第一版不追求复杂概率模型。

至少保存：

- median；
- MAD；
- P25 / P75；
- P10 / P90；
- episode-to-episode delta；
- valid duration；
- sample count；
- data-quality rejection rate。

优先使用 robust statistics。

### 8.4 Minimum Useful Effect

```text
minimum_useful_effect
=
max(
    empirical_noise_floor,
    practical_value_threshold
)
```

其中：

- empirical noise：机器真实自然波动；
- practical threshold：即使真实存在，也必须“大到值得折腾”。

不能只靠统计显著。

还要考虑工程价值。

---

## 9. Practical Value Threshold

一个 candidate 即使真实省：

`0.04W`

也未必值得继续证明。

需要把收益换算到真实使用。

例如：

```text
0.06W × 8h = 0.48Wh/day
```

如果为了证明这 0.06W：

- 要几周实验；
- 会不断切配置；
- 有 UX 风险；
- 增加监控成本；

那么正确结论应是：

`PRACTICALLY_EQUIVALENT`

而不是无限：

`NEED_MORE_DATA`

---

## 10. Evidence Engine

Evidence Engine 是 PLAN2 的核心裁判。

它不负责选择下一个参数。

只负责回答：

> 当前证据是否足以说明 candidate 值得替换 baseline？

### 10.1 输入

- Trial episodes；
- BAT integrated energy；
- energy_now delta；
- noise model；
- PSI；
- thermal；
- media continuity；
- brightness；
- workload matching；
- 用户反馈；
- data-quality；
- battery epoch；
- system/software fingerprint；
- trial interruption / carryover 信息。

### 10.2 输出

正式状态：

```text
WIN
LOSE
INCONCLUSIVE
PRACTICALLY_EQUIVALENT
```

另有前置失败状态：

```text
DATA_QUALITY_FAILURE
INVALID_COMPARISON
```

### 10.3 用户负面反馈

```text
sluggish
bad
unstable
```

继续保持 hard veto。

不与 0.5W 节省做数学加权。

用户明确觉得体验差：

> candidate 失败。

### 10.4 Evidence Engine 不可被 LLM 修改

以下参数只能来自：

- 本地代码；
- 受版本控制的配置；
- 真机 calibration；
- noise model。

LLM proposal 无权覆盖。

---

## 11. Evidence Episode

Evidence Engine 不把单个 10 秒 sample 当独立证据。

基本单位是：

`episode`

每个 episode 包含：

- baseline arm；
- candidate arm；
- washout / settling；
- matching context；
- aggregate BAT energy；
- outcome constraints；
- data-quality；
- interruption reason。

---

## 12. Initial crossover 与独立 revalidation

现有正确结构继续保留，并正式写入合同。

### Initial

```text
A1 baseline
   ↓
B1 candidate
   ↓
A2 baseline
   ↓
initial evaluation
```

### Revalidation

只有 initial WIN 后：

```text
等待新的 comparable window
   ↓
A3 fresh baseline
   ↓
B2 candidate
   ↓
revalidation evaluation
```

要求：

- B1 不进入 B2 判分；
- A2 不作为 B2 的旧 baseline；
- revalidation 必须使用新的 A3；
- B2 只与同一轮 A3 比。

---

## 13. Evidence 数量不能“结果越漂亮越立即宣布胜利”

禁止实现：

```text
第一次 -0.9W
→ 看起来很强
→ 立即 promotion
```

这会产生提前停止偏差。

### 13.1 第一版简单规则

不使用复杂 sequential statistics。

大效果：

- 至少 2 个独立 crossover / revalidation episode；
- 全部 data-quality 通过；
- 无 UX veto；
- effect 大于 noise floor 与 practical threshold。

中等效果：

- 需要更多独立 episode；
- 初步目标 3–4 个；
- 真机 burn-in 后再确定。

很小效果：

```text
PRACTICALLY_EQUIVALENT
```

停止。

### 13.2 证据预算

每个 candidate 必须有最大实验预算。

达到预算仍不能判：

```text
INCONCLUSIVE
```

然后停止主动测试。

不能无限消耗用户体验。

---

## 14. Carryover 与状态型 Washout

SP7 是被动散热设备。

相邻 A/B arm 不独立。

主要 carryover：

- package temperature；
- chassis heat soak；
- RAPL rolling average；
- CPU backlog；
- cache / browser work；
- network transfer backlog。

因此不能只写：

```text
sleep 60s
→ MEASURING
```

---

## 15. Washout 采用“最短时间 + 状态收敛 + 最大超时”

正确逻辑：

```text
SETTLING
   ↓
minimum settle time satisfied
AND
HWP read-back correct
AND
thermal state acceptable
AND
thermal slope stable
AND
RAPL rolling state stable
AND
CPU/IO PSI/backlog acceptable
   ↓
MEASURING
```

同时必须有：

`maximum washout timeout`

如果长期无法稳定：

- 恢复 baseline；
- 返回 WAITING；
- 或 episode abort。

不能把 candidate 长期留在机器上等待“以后总会稳定”。

---

## 16. Candidate 造成的结果必须保留

重要区分：

### 外生 workload change

例如：

- 用户开始视频；
- 亮度明显改变；
- suspend；
- AC 接入；
- remote session 新开始；
- battery epoch 变化。

这些应暂停 / abort episode。

### Candidate-caused outcome

例如：

- PSI 上升；
- thermal pressure 增加；
- CPU busy 时间拉长；
- media continuity 下降；
- latency proxy 恶化；
- demand 数值因性能限制而改变。

这些不能过滤。

它们就是 candidate 的结果。

---

## 17. Workload 并不真正完全相同

真实交互设备中：

```text
max_perf 60
网页完成 300ms

max_perf 45
网页完成 450ms
```

用户后续行为自然不同。

因此 PowerLab 不能假装这是实验室 benchmark。

Evidence Engine 只能做：

> 尽可能可比的 N-of-1 工程证据。

不能声称：

> 完全相同 workload 的严格因果实验。

这也是保留人工 UX feedback 的原因。

---

## 18. Evidence Epoch 与历史衰减

不存在“全生命周期永久最优配置”。

只有：

> 当前 evidence epoch 下的最佳 verified 配置。

### 18.1 Evidence Epoch 至少受这些因素影响

- battery epoch；
- kernel；
- BIOS；
- linux-surface；
- thermald；
- thermal config；
- Firefox / Chromium major version；
- Mesa；
- firmware；
- calibration version；
- thermal sensor path。

### 18.2 三层证据

```text
current_epoch_evidence
recent_compatible_evidence
historical_evidence
```

### 18.3 Promotion

主要依赖：

`current_epoch_evidence`

recent evidence 可以辅助。

historical evidence 只作为：

> 以前它曾经好用。

不能当：

> 过去赢了 20 次，所以今天有 20 票。

---

## 19. Drift Detection

系统必须同时支持：

### 19.1 突发异常

例如：

`+1.5W overnight`

### 19.2 慢性退化

例如：

```text
4.80
4.90
5.00
5.05
5.10
5.20
```

没有一天构成巨大 incident。

但 30–60 天已经有明显变化。

因此 drift detector 至少维护：

- 24h；
- 7d；
- 30d；
- current epoch baseline；
- rolling median；
- robust slope。

---

## 20. Waste Detector 改成 Unexpected Power Detector

“功耗高于历史”不等于 waste。

所以 PLAN2 不再使用：

`WasteDetector`

作为事实名称。

改为：

`UnexpectedPowerDetector`

它只负责输出：

> 这个窗口比历史相似窗口高，值得调查。

---

## 21. Unexpected Power Event 分类

第一层事件：

`UNEXPECTED_POWER`

经过 Attribution 后才可能变成：

```text
EXPECTED_WORKLOAD_CHANGE
INSUFFICIENT_EVIDENCE
SUSPECTED_REGRESSION
ACTIONABLE_WASTE
```

只有证据真正支持以后才能称：

`ACTIONABLE_WASTE`

---

## 22. Attribution

Attribution 的职责是解释：

> 多出来的 W 大概率来自哪里？

输入可以包括：

- BAT delta；
- RAPL delta；
- GPU activity；
- media decode hint；
- process CPU；
- process IO；
- network；
- interrupts / wakeups；
- runtime PM；
- software fingerprint；
- recent package upgrades。

第一版优先做 deterministic attribution。

例如：

```text
BAT +1.3W
RAPL +1.1W
Firefox content CPU high
GPU decode low
MPRIS playing
```

可以产生：

`SUSPECTED_MEDIA_DECODE_REGRESSION`

不能直接产生：

`CONFIRMED_FIREFOX_BUG`

---

## 23. LLM Researcher

LLM 的最终职责是：

- 解释异常；
- 汇总长期趋势；
- 阅读外部资料；
- 关联软件版本；
- 提出验证假设；
- 建议新增搜索维度；
- 总结用户长期历史。

LLM 不负责：

- 实时控制；
- Evidence Engine；
- noise threshold；
- trial pass/fail；
- promotion；
- root cause confirmation。

---

## 24. LLM 外部事实必须带来源

如果 Researcher 提出：

> “Firefox 某版本存在 VA-API regression。”

actionable proposal 必须带：

- release note；
- bug tracker；
- GitHub / GitLab issue；
- upstream discussion；
- 其他可核查来源。

没有来源时只能保存为：

`UNVERIFIED_HYPOTHESIS`

---

## 25. LLM cadence 改为双轨

### 25.1 每小时本地运行

不需要 LLM。

运行：

- summarizer；
- noise estimator；
- drift detector；
- UnexpectedPowerDetector；
- Evidence Engine；
- lifecycle update。

### 25.2 Event-triggered LLM

发生以下事件可以立即调用 Researcher：

- significant power regression；
- thermal anomaly；
- trial result；
- fingerprint change；
- unexpected process；
- media regression；
- repeated data-quality failure；
- Scheduler 找不到合理 candidate。

### 25.3 Scheduled slow review

每日：

`24h summary`

每周：

`7d / 30d drift review`

避免完全 event-only 导致慢性退化被忽略。

---

## 26. Lifecycle Manager

PLAN2 新增顶层生命周期。

```text
READ_ONLY
   ↓
CALIBRATING
   ↓
BASELINE_OBSERVATION
   ↓
COARSE_OPTIMIZATION
   ↓
VALIDATION
   ↓
STABLE
```

异常时：

```text
STABLE
   ↓
DRIFT / INCIDENT / NEW_BATTERY /
SYSTEM_UPDATE / USER_COMPLAINT /
NEW_HIGH_VALUE_HYPOTHESIS
   ↓
REOPEN_OPTIMIZATION
```

---

## 27. READ_ONLY

条件：

- calibration 无效；
- hardware contract 不完整；
- thermald 不健康；
- HWP ownership 冲突；
- battery telemetry 不可信；
- thermal sensor 未确认；
- controller rollback integrity failure。

行为：

- 只观测；
- 不自动写 envelope；
- 不运行 trial；
- 不 Scheduler。

---

## 28. CALIBRATING

目标：

建立：

- BAT baseline；
- thermal baseline；
- sensor identity；
- initial battery epoch；
- telemetry data quality；
- initial monitoring overhead。

不能在 calibration 未完成时开始长期搜索。

---

## 29. BASELINE_OBSERVATION

这是 PLAN2 最重要的新阶段之一。

持续自然使用。

主要回答：

1. 我的整机 BAT W 分布是什么？
2. 不同 demand / envelope 的自然波动是多少？
3. PowerLab 自身开销是多少？
4. CPU package 在整机功耗里占多大比例？
5. 哪些维度真正有优化 headroom？
6. 有没有明显 regression 比 CPU tuning 更重要？

这个阶段不急于自动搜索。

---

## 30. COARSE_OPTIMIZATION

只有当满足：

- measurement trust；
- noise model 足够；
- headroom 足够；
- lifecycle 允许；
- 用户体验风险可接受；

才开始。

只做 coarse discrete local search。

---

## 31. VALIDATION

Scheduler 找到 candidate 后：

- 独立 episode；
- fresh baseline；
- noise-aware Evidence Engine；
- practical threshold；
- user feedback；
- data-quality gate；
- current epoch evidence。

只有明确 WIN 才 promotion。

---

## 32. STABLE

这是成熟 PowerLab 的默认状态。

STABLE 中：

- Controller 正常运行；
- Telemetry 低开销运行；
- drift detector 运行；
- UnexpectedPowerDetector 运行；
- noise model 缓慢更新；
- active Scheduler 睡眠；
- 无主动 trial；
- LLM 不按小时机械调用；
- 用户没有被不断实验。

---

## 33. STABLE 的进入条件

至少满足：

- 每个主要 demand class 有 verified envelope；
- 最近 Scheduler 邻域没有高价值未测试 candidate；
- 剩余可见差异进入 noise / practical equivalence；
- 最近没有 unresolved regression；
- monitoring overhead 已测；
- current system epoch 数据足够；
- 用户没有负面反馈；
- dynamic controller 自身有净收益或至少没有显著负收益。

---

## 34. 重新开启 Optimization 的条件

包括：

- new battery epoch；
- kernel / BIOS / Mesa / browser significant change；
- system fingerprint change；
- current verified envelope regression；
- sustained drift；
- UnexpectedPower event；
- user complaint；
- 新的高价值 deterministic hypothesis；
- 新 actuator / control dimension；
- verified envelope 被 BLOCKED；
- monitoring overhead 大幅改变。

---

## 35. Candidate Scheduler

Scheduler 只负责：

> 如果值得继续探索，下一个试谁？

不负责判 winner。

---

## 36. Candidate Space

第一阶段继续使用 named EPP：

- performance；
- balance_performance；
- balance_power；
- power。

max_perf_pct 使用离散邻域。

示例：

```text
baseline:
balance_power / 60 / turbo on

neighbors:
balance_power / 55 / on
balance_power / 65 / on
power         / 60 / on
balance_performance / 60 / on
balance_power / 60 / off
```

如果 55 真赢：

下一步才允许 50。

这是：

`discrete local search`

---

## 37. 暂不开放 EPP 0–255 搜索

即使硬件技术上支持 numeric EPP，也不代表现在值得探索。

正确顺序：

```text
named EPP
   ↓
证明 EPP 是高价值维度
   ↓
证明 coarse search 已不够
   ↓
才考虑 numeric refinement
```

---

## 38. 暂不上 Bayesian Optimization

原因：

- candidate space 很小；
- 数据昂贵；
- reward 非平稳；
- workload 不是实验室 benchmark；
- model 容易给出虚假的平滑结构；
- local search 更透明；
- 个人项目更需要可审计性。

只有将来控制维度明显增加，并且 Evidence Engine 已稳定以后才重新评估 BO。

---

## 39. Scheduler Stop Rules

Scheduler 最重要的能力之一：

> 知道什么时候什么都不做。

停止条件：

- 所有邻居都 LOSE；
- 所有邻居 PRACTICALLY_EQUIVALENT；
- remaining headroom 小于 minimum useful effect；
- experiment budget exhausted；
- 用户进入高价值工作时段；
- monitoring overhead 接近潜在收益；
- data-quality 长期不足。

然后 lifecycle：

`STABLE`

---

## 40. Search Budget

每个 envelope / epoch 都有预算。

例如控制：

- 每周最多多少 active trial；
- 每个 candidate 最多多少 episode；
- 每天最多多少分钟候选配置；
- 负面反馈后 cooldown 多久；
- 最近有 thermal event 时停止多久；
- 电量低于阈值时不探索。

默认应极其保守。

---

## 41. Power Attribution 与 Headroom

优化前先回答：

> 我的 5W 到底花在哪里？

至少估计：

- whole-device BAT；
- CPU package RAPL；
- display-related contribution proxy；
- GPU；
- network；
- platform remainder。

不是要求精确拆成物理账单。

而是为了判断：

> 某一维最多可能省多少。

---

## 42. Headroom Score

每个优化方向先估计：

```text
potential_saving_w
measurement_confidence
ux_risk
experiment_cost
```

Scheduler 优先：

> 高潜力、低风险、可测量。

例如：

### CPU

```text
BAT = 5.0W
RAPL = 1.0W
```

CPU tuning 理论 headroom 有限。

### Media regression

```text
historical = 5.0W
current = 6.4W
RAPL/GPU attribution abnormal
```

优先调查 regression。

---

## 43. Observer Effect

PowerLab 自己也耗电。

可能来源：

- 10s telemetry；
- process scan；
- SQLite 写入；
- ActivityWatch；
- GPU inspection；
- hourly summarizer；
- LLM pack generation；
- systemd timers。

PLAN2 必须测它。

---

## 44. Monitoring Overhead Experiment

真机 Stage A 必须包含：

### A

```text
PowerLab OFF
固定良好 envelope
```

### B

```text
PowerLab ON
同一个固定 envelope
Scheduler OFF
Trial OFF
```

比较：

- BAT W；
- Wh；
- RAPL；
- CPU wakeups；
- residency；
- service CPU；
- disk writes。

---

## 45. Net Battery Benefit

最终指标：

```text
NetSaving
=
GrossSaving
-
MonitoringOverhead
```

如果：

```text
PowerLab overhead = +0.12W
optimization gain  = -0.15W
```

净收益只有：

`0.03W`

这不能称为成功。

---

## 46. Telemetry 采样应随生命周期降频

STABLE 状态不应一直按实验密度采所有数据。

建议未来支持：

### Trial / Calibration

高信息密度。

### STABLE

降低：

- process scan 频率；
- GPU probe 频率；
- attribution probe；
- DB flush；
- LLM pack cadence。

保留核心：

- BAT；
- thermal；
- HWP state；
- drift essentials。

目标：

> PowerLab 越成熟，自己越安静。

---

## 47. Controller 本身也必须接受 A/B

最终要比较：

### Baseline A

```text
thermald
+
一个固定良好 envelope
```

### PowerLab B

```text
deterministic controller
+
dynamic verified envelopes
```

如果长期：

```text
A = 5.05W
B = 5.00W
```

但 B：

- 更多 wakeups；
- 更多错误切换；
- UX 更不稳定；
- 维护成本更高；

正确工程结论可以是：

> 删除 dynamic controller。

---

## 48. 多 Envelope 也必须允许被合并

如果长期发现：

```text
INTERACTIVE_EFFICIENT
REMOTE_EFFICIENT
MEDIA_EFFICIENT
```

最终参数几乎一样：

```text
balance_power
60%
turbo on
```

应该合并为：

`NORMAL_EFFICIENT`

不要为了架构漂亮保留无价值概念。

---

## 49. Thermal 研究也要有停止规则

如果一个月实际使用：

```text
THERMAL_PRESSURE = 0
THROTTLING = 0
```

那么 ThermalObserver 继续作为安全检测器即可。

不要继续把主要开发资源投到热模型微调。

本项目主目标仍然是：

> 续航。

---

## 50. 数据模型新增概念

下一阶段 SQLite 至少需要概念上支持：

### `evidence_epochs`

记录：

- epoch id；
- battery epoch；
- system fingerprint；
- software fingerprint；
- calibration version；
- start / end；
- invalidation reason。

### `noise_baselines`

记录：

- envelope；
- demand region；
- brightness bucket；
- thermal start；
- median / MAD / IQR；
- episode count；
- data quality。

### `evidence_episodes`

记录：

- trial；
- arm；
- baseline/candidate；
- BAT integrated Wh；
- battery energy delta；
- consistency error；
- PSI；
- thermal；
- media continuity；
- UX feedback；
- validity。

### `evidence_decisions`

记录：

- WIN；
- LOSE；
- INCONCLUSIVE；
- PRACTICALLY_EQUIVALENT；
- reason；
- thresholds；
- current noise model version。

### `candidate_frontier`

记录：

- current baseline；
- tested neighbors；
- rejected candidates；
- equivalent candidates；
- candidate budget。

### `unexpected_power_events`

替代含义过强的 waste incident。

### `attribution_hypotheses`

记录：

- deterministic evidence；
- LLM hypothesis；
- external sources；
- verification state。

### `lifecycle_history`

记录：

- state；
- transition reason；
- timestamp。

### `monitoring_overhead_runs`

记录 PowerLab ON/OFF 对照。

---

## 51. Schema / Migration 策略

PLAN2 实施按破坏式演进设计。

原则：

- 不为 v2 runtime SQLite 写复杂迁移；
- 新 schema version fail-fast；
- 必要时显式 reset runtime DB；
- Git 中的 durable config 独立保留；
- machine-specific VERIFIED 身份不能因为 Git 文件存在就自动继承；
- 新 Evidence Engine 上线后，旧 trial result 不自动视为新 evidence vote。

如果未来已经积累大量真机数据，再单独决定是否写一次性 import 工具。

---

## 52. CLI 规划

人类 CLI 可以新增：

```text
sp7-powerlab lifecycle status
sp7-powerlab lifecycle freeze
sp7-powerlab lifecycle reopen

sp7-powerlab evidence status
sp7-powerlab evidence inspect <candidate>
sp7-powerlab evidence noise

sp7-powerlab scheduler status
sp7-powerlab scheduler candidates
sp7-powerlab scheduler pause
sp7-powerlab scheduler resume

sp7-powerlab unexpected-power list
sp7-powerlab unexpected-power inspect <id>

sp7-powerlab overhead status
sp7-powerlab overhead compare
```

名称是设计占位，不要求完全照抄实现。

---

## 53. Agent capability 规划

LLM agent 继续保持窄接口。

未来最多新增只读/提案型动作：

```text
observe
hourly-summary
drift-summary
unexpected-power-context
submit-hypothesis
submit-research
```

不能新增：

- lifecycle force transition；
- trial start；
- trial promote；
- threshold edit；
- Scheduler direct execute；
- helper call；
- arbitrary file / shell。

---

## 54. Researcher 外部来源结构

LLM hypothesis 若引用外部事实，必须保存结构化 source：

```text
title
url
source_type
retrieved_at
claim
relevance
```

actionable hypothesis 至少需要一个可核查来源。

没有来源：

`UNVERIFIED_HYPOTHESIS`

---

## 55. PLAN2 自动化等级建议

### Level 0

只读。

### Level 1

自动 Controller 只切 VERIFIED envelope。

### Level 2

Scheduler 可以提出 candidate。

Trial 必须人工批准。

Promotion 必须人工批准。

推荐个人长期使用级别。

### Level 3

允许低风险 candidate 自动开始 trial。

Evidence Engine 仍完全本地确定。

### Level 4

允许满足严格条件的自动 promotion。

只有真机长期验证以后才考虑。

STABLE 状态下即使 Level 4，也默认不主动探索。

---

## 56. 实施顺序

不能先写 Scheduler。

正确顺序如下。

---

## 57. Phase 0 — Measurement Trust

先实现：

- power integration；
- energy_now delta；
- consistency gate；
- data-quality status；
- noise baseline；
- monitoring overhead experiment；
- robust baseline statistics。

验收问题：

> 我们到底能不能可信地测出 candidate 的量级？

如果答案是否定的：

停止。

不要实现 optimizer。

---

## 58. Phase 1 — Evidence Engine

实现：

- episode aggregation；
- current epoch evidence；
- noise-aware thresholds；
- WIN；
- LOSE；
- INCONCLUSIVE；
- PRACTICALLY_EQUIVALENT；
- user-feedback veto；
- data-quality rejection。

替代当前 Trial 内部“固定阈值直接 winner”的职责。

---

## 59. Phase 2 — Lifecycle Manager / STABLE

实现：

- lifecycle state；
- transition log；
- STABLE；
- freeze；
- reopen conditions；
- drift trigger；
- scheduler sleeping。

这是 PLAN2 最重要的功能之一。

---

## 60. Phase 3 — State-based Washout

把固定 settle time 升级为：

```text
minimum time
+
state convergence
+
maximum timeout
```

重点真机验证 SP7 heat soak。

---

## 61. Phase 4 — Unexpected Power Detector

重命名并重构现有 WasteDetector。

先检测：

`UNEXPECTED_POWER`

再 attribution。

不能直接把异常叫 waste。

---

## 62. Phase 5 — Candidate Scheduler

在 Evidence Engine 稳定后才实现。

第一版：

- discrete local search；
- neighbor expansion；
- blacklist；
- experiment budget；
- headroom priority；
- stop rules。

不实现 BO。

---

## 63. Phase 6 — Researcher Cadence

把当前“每小时 LLM”改成：

- 每小时 local；
- event-driven researcher；
- daily review；
- weekly drift review。

并要求外部事实带来源。

---

## 64. Phase 7 — Net Benefit Validation

正式比较：

1. fixed good envelope；
2. PowerLab monitoring only；
3. PowerLab dynamic controller；
4. full PowerLab。

最终决定：

- 哪些模块保留；
- 哪些模块合并；
- 哪些模块删除。

---

## 65. Stage A 真机验证重新定义

Stage A 不先调 EPP。

先回答：

### Measurement

- BAT power 是否稳定可读？
- energy_now delta 是否可信？
- 两者 consistency 如何？
- noise floor 多大？
- suspend/resume 是否破坏数据？

### Attribution

- 正常 5W 花在哪里？
- CPU RAPL 占多少？
- GPU / display / network 大概是什么量级？
- 有没有明显常驻浪费？

### Overhead

- PowerLab OFF vs ON 增加多少 W？
- process scan 的成本是多少？
- ActivityWatch 的成本是多少？
- SQLite / timer 的成本是多少？

### Thermal

- 实际是否经常进入 HEAT_SOAKED？
- 是否发生 THERMAL_PRESSURE？
- remote/light workload 下 thermal 是否基本无关？

Stage A 结束后才能决定：

> CPU envelope optimization 的 ROI 到底有多大。

---

## 66. Stage B — Baseline 与 Noise

目标：

- 每个主要 demand class 得到自然分布；
- noise model 达到最低 episode 数；
- practical threshold 定义完成；
- current evidence epoch 建立。

不做大量 candidate exploration。

---

## 67. Stage C — Coarse Search

只探索：

- named EPP；
- max_perf_pct 粗粒度邻居；
- Turbo on/off。

最多改变一个 primary dimension。

每个 candidate 必须受：

- experiment budget；
- noise floor；
- UX veto；
- washout；
- independent revalidation

约束。

---

## 68. Stage D — STABLE Burn-in

进入 STABLE 后至少观察数周。

重点不是继续优化。

而是验证：

- drift false positive；
- UnexpectedPower false positive；
- monitoring overhead；
- envelope switching stability；
- 用户是否真正无感。

---

## 69. Stage E — 是否值得保留复杂度

最后必须回答：

### Dynamic controller 是否值得？

### 多 envelope 是否值得？

### Candidate Scheduler 是否值得？

### LLM researcher 是否值得？

### PowerLab 整体净收益是否值得？

允许答案是：

> 不值得。

---

## 70. Acceptance Criteria — Measurement

必须满足：

- BAT energy integration gap-aware；
- energy delta 可用；
- consistency gate 有真机阈值；
- Charging / AC 不进入放电 reward；
- suspend gap 不进入 reward；
- noise floor 有真实数据；
- monitoring overhead 已量化。

---

## 71. Acceptance Criteria — Evidence Engine

必须满足：

- B1 不污染 B2；
- B2 使用 fresh A3；
- candidate-caused regression 保留；
- external workload change abort/pause；
- UX negative feedback veto；
- small effect 可输出 PRACTICALLY_EQUIVALENT；
- data-quality failure 不判 winner；
- old epoch evidence 不直接累计票数。

---

## 72. Acceptance Criteria — Scheduler

必须满足：

- 只选有限邻居；
- 不修改 Evidence threshold；
- 不越过 automation level；
- 有 experiment budget；
- 有 stop rules；
- STABLE 默认不探索；
- headroom 太低时不探索。

---

## 73. Acceptance Criteria — Unexpected Power

必须满足：

- detection 与 root-cause 分离；
- 不能把“高于历史”直接叫 waste；
- attribution 可返回 EXPECTED；
- LLM 只能提供 hypothesis；
- external factual claim 有来源。

---

## 74. Acceptance Criteria — STABLE

进入 STABLE 后：

- 7 天内无主动 trial，除非明确 trigger；
- Scheduler 不机械寻找更细参数；
- controller 稳定；
- telemetry 降到合适低开销；
- drift detection 正常；
- 用户无显著负面反馈。

---

## 75. Acceptance Criteria — Net Benefit

最终必须完成：

```text
fixed good profile
vs
PowerLab monitoring only
vs
dynamic controller
vs
full PowerLab
```

如果完整 PowerLab 的净收益小于 practical threshold：

应删除、冻结或简化相关模块。

---

## 76. 明确不做

PLAN2 默认不做：

- deep learning；
- neural network；
- reinforcement learning；
- online ML real-time controller；
- realtime contextual bandit；
- arbitrary EPP 0–255 search；
- 自动调屏幕亮度以“刷低功耗”；
- undervolting；
- 任意 MSR 写入；
- 任意 sysfs 写入；
- 自定义 thermald hard trip；
- LLM root access；
- LLM shell access；
- LLM 修改 Evidence Engine。

---

## 77. 复杂度删除原则

任何模块都必须允许被证据淘汰。

如果：

### 多 envelope 没收益

合并。

### dynamic controller 没收益

删除。

### Scheduler 没收益

永久 STABLE。

### thermal model 没实际使用价值

只保留安全检测。

### LLM researcher 没带来 actionable finding

降低 cadence 或关闭。

### PowerLab 总体净收益不足

退回固定良好配置。

项目没有义务证明自己复杂才有价值。

---

## 78. 成功标准

PowerLab 成功不等于：

- trial 数量多；
- LLM proposal 多；
- 参数越来越精细；
- 数据库越来越大；
- 算法越来越复杂。

成功意味着：

1. 正常使用体验好；
2. 实际电池续航更长；
3. 热状态可持续；
4. 遇到 regression 能发现；
5. 系统变化后能重新学习；
6. 成熟以后基本不打扰用户；
7. 自身监控开销远小于真实收益；
8. 如果没有进一步收益，它知道停止。

---

## 79. PLAN2 最终系统定义

PowerLab 不再定义为“持续自动优化器”。

最终定义：

> **一个专门针对 Surface Pro 7 i5-1035G4 的个人续航证据系统。它以低开销方式长期记录整机电池、性能压力、热状态和系统版本；先建立真实噪声与功耗基线，再用保守的 Evidence Engine 判断候选配置是否具有可重复且有实际价值的收益。Candidate Scheduler 只在存在足够优化 headroom 时进行有限、离散、可回滚的探索；一旦找到足够好的配置，系统进入 STABLE 并停止主动实验。只有发生漂移、异常、系统/电池变化、用户投诉或新的高价值假设时才重新打开优化。LLM 只负责研究、解释与外部资料关联，不参与实时控制，也无权修改实验裁判标准。最终评价只看真实净续航收益，而不是 PowerLab 自身的算法复杂度。**

---

## 80. 一句话原则

> **先测准，再判断；先找浪费，再降性能；收益小于噪声就停止；找到足够好的答案就冻结；环境变了再重新学习。**
