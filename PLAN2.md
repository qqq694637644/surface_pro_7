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
- GPT-5.6 作为受信任的个人用户态工程 Agent，可以获得类似 workspace/Bash 的通用工具，
  自主读取仓库、SQLite、日志和系统状态，也可以编写分析脚本、修改普通配置/代码并使用 Git；
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

### 2.4 Evidence Engine 在运行时判断事实，Agent 不临时改裁判

GPT-5.6 拥有用户态 Bash 后，技术上当然可以修改仓库代码和普通配置。

因此这里不是权限隔离，而是实验纪律：

- active experiment 的 reward 由确定性代码计算；
- active experiment 的 noise floor / data-quality gate / pass threshold 不能为了当前 candidate
  临时修改；
- WIN / LOSE / PRACTICALLY_EQUIVALENT / INCONCLUSIVE 由当前版本 Evidence Engine 生成；
- hypothesis 不能因为 Agent 主观确信就直接变成 confirmed root cause；
- promotion 应基于已有 evidence，而不是为了 promotion 反过来修改 evidence。

如果 Agent 发现 Evidence Engine 本身有缺陷，它可以像高级工程师一样：

```text
调查历史数据
→ 修改 Evidence Engine / config
→ 写测试
→ Git 记录变更
→ 切换 evidence epoch / invalidation
→ 用新规则重新积累证据
```

允许改系统。

不允许在同一实验里“移动球门柱”。

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
     WIN / LOSE / EQUIV       GPT-5.6 PowerLab Agent
       / INCONCLUSIVE          Bash / Research / Engineering
              │                    hypothesis / changes
              ▼
      Candidate Scheduler
      simple discrete search
              │
              ▼
          Trial Engine

              Control Safety State
 READ_ONLY / CONTROL_ALLOWED / DEGRADED / EMERGENCY

              Learning Lifecycle
 CALIBRATING / BASELINE_OBSERVATION / COARSE_OPTIMIZATION
              / VALIDATING / STABLE / REOPENED

             Investigation Status
                 IDLE / INVESTIGATING
```

关键变化：

- `Evidence Engine` 和 `Candidate Scheduler` 分离；
- `Waste Detector` 改成 `Unexpected Power Detector`；
- 把控制安全状态与学习生命周期拆成两个正交状态机；
- 新增轻量 `Investigation Status`，让异常调查不必唤醒 Scheduler；
- 新增 empirical noise model；
- 新增 data-quality gate；
- 新增 monitoring overhead / net benefit；
- GPT-5.6 不进入高速 deterministic runtime，但作为受信任的个人 PowerLab Agent 负责研究、
  诊断、工程修改、数据分析、实验设计、维护和 Git 工作；机械性的长期搜索仍优先交给本地
  Candidate Scheduler。

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

上层合同使用：

`Validated Thermal Safety Provider`

当前 Surface Pro 7 实现仍然是：

`provider = thermald`

`thermald` 继续是当前正式独立安全层，但生命周期和 safety gate 不永久绑定具体进程名。

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

### 5.6 Trusted user-space Bash Agent

个人使用场景默认把 GPT-5.6 视为受信任的用户态工程代理。

可以提供类似 workspace/Bash 的通用工具，让它自行探索，而不是把所有能力预先封装成几十个
白名单 API。

允许 Agent 自由：

- 读取整个仓库和 Git 历史；
- 搜索、修改普通用户态文件；
- 读取 SQLite / JSON / TOML；
- 查看 journal 和普通系统状态；
- 查看 `/proc`、可读 `/sys`；
- 运行 `ps`、`top`、`powertop` 等诊断工具；
- 编写临时 Python / Bash 分析脚本；
- 运行测试和 benchmark；
- 使用主 `sp7-powerlab` CLI；
- 设计 trial proposal；
- 修改 PowerLab 自身代码；
- 使用 Git 保存、比较和回滚工程修改；
- 在行为指南允许时执行用户态 trial / lifecycle 操作。

`sp7-powerlab-agent` 可以保留作为便利的高层入口，但不再是 LLM 唯一允许调用的 capability。

真正保留的硬边界是：

- PowerLab 不主动提供任意 root shell；
- root helper 仍然只接受有限、校验后的 HWP 操作；
- thermald 仍然是独立 thermal safety；
- HWP actuator 继续做 transaction / read-back / rollback；
- runtime trial safety gate 继续检查 battery、thermal、suspend、telemetry、ownership 等条件；
- Evidence Engine 的当前运行版本仍然是 trial 判分事实源。

如果用户另外给 Bash Agent 配置了 sudo 或其他高权限，那属于用户自己的信任选择，不应在
PowerLab 文档里伪装成“模型技术上无法做到”。

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

该 ArmMeasurement / comparison window：

```text
DATA_QUALITY_FAILURE
```

不能进入 WIN / LOSE 判定。

具体容忍度不能先拍脑袋写死，应在 Stage A 真机 burn-in 后确定。

### 7.5 Battery Gauge Resolution

Stage A 必须实测：

- `energy_now` 最小变化量；
- `energy_now` 实际更新间隔；
- `power_now` 实际更新 cadence；
- EC / battery gauge 是否出现阶梯式或批量更新；
- suspend / resume 前后 gauge 行为。

实验最短 arm 时长不能独立拍脑袋决定。

必须满足类似：

```text
expected_arm_energy
>>
battery_gauge_quantum
```

具体倍率不在 PLAN2 预设，由真机 Stage A 决定。

如果 gauge 分辨率不足，`energy_now delta` 只能用于更长窗口 consistency check，而不能给
短 arm 提供虚假的精度。

---

## 8. Empirical Noise Floor

固定：

```text
min_power_saving_w = 0.10
```

不再作为最终科学依据。

### 8.1 Frozen Reference Baseline 与 Recent Noise Distribution 必须分离

不能用同一个自适应 baseline 同时负责：

- 长期 regression 检测；
- 当前自然噪声估计。

因此每个主要使用区域至少维护：

```text
FrozenReferenceBaseline
RecentAdaptiveDistribution
```

`FrozenReferenceBaseline` 在当前 hard evidence epoch 验证稳定后冻结。

它用于回答：

> 和这个环境刚稳定时相比，长期是否已经退化？

它不能因为最近功耗慢慢升高就自动跟着漂移。

`RecentAdaptiveDistribution` 维护例如：

```text
24h
7d
30d
```

它用于回答：

> 最近自然波动和当前 noise 有多大？

这样：

```text
reference = 4.82W
recent    = 5.13W
```

才能被识别成长期 drift，而不是把 5.13W 学成“新的正常”。

### 8.2 Noise Model 使用少量主分层 + comparison covariates

个人单机数据不能为所有条件建立笛卡尔积 bucket。

第一版 hard strata 只保留少量强因素，例如：

- hard battery / evidence epoch；
- effective envelope；
- active / idle；
- media / non-media；
- remote / local。

其他因素优先作为 comparison constraints / covariates：

- brightness delta <= X；
- thermal start 必须 acceptable；
- network / workload 在允许范围；
- data-quality 必须通过；
- compatibility tags 必须满足当前比较规则。

不为每一个 brightness bucket × thermal state × app version 建独立 noise model。

### 8.3 Noise 不是单一全局数字

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

### 8.4 第一版统计量

第一版不追求复杂概率模型。

至少保存：

- median；
- MAD；
- P25 / P75；
- P10 / P90；
- natural-window-to-window delta；
- valid duration；
- sample count；
- data-quality rejection rate。

优先使用 robust statistics。

### 8.5 Minimum Useful Effect

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

### 9.1 展示指标：Minutes Gained per Charge

Practical Value 可以额外换算成：

`预计每次充电增加多少分钟`

例如：

```text
usable battery = 40Wh
baseline       = 5.0W
candidate      = 4.9W
```

对应大约 2% 续航提升，可以进一步显示为每次充电增加的分钟数。

这个指标只用于人类理解和复杂度决策。

不能作为 Evidence reward。

---

## 10. Evidence Engine

Evidence Engine 是 PLAN2 的核心裁判。

它不负责选择下一个参数。

只负责回答：

> 当前证据是否足以说明 candidate 值得替换 baseline？

### 10.1 输入

- ArmMeasurements；
- CrossoverEpisodes；
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

### 10.3 第一版 Evidence Decision Contract

第一版不使用复杂 Bayesian inference，也不追求论文式显著性检验。

每个 `CrossoverEpisode` 先产生一个统一符号的：

```text
paired_effect_w

negative = candidate saves power
positive = candidate is worse
```

然后对当前 compatible evidence 集合使用 robust aggregation。

#### WIN

至少满足：

```text
median(paired_effect_w) <= -minimum_useful_effect
AND
足够比例的 crossover 同方向
AND
达到当前 effect size 对应的最小 evidence budget
AND
没有 UX / thermal / media hard veto
AND
所有计入判定的 measurement quality 合格
```

#### LOSE

满足任一：

```text
median(paired_effect_w) 明显有害
OR
出现 UX / thermal / media hard veto
```

#### PRACTICALLY_EQUIVALENT

在已经达到足够 evidence budget 后，大部分可信 paired effect 落在：

```text
[-minimum_useful_effect, +minimum_useful_effect]
```

说明继续证明微小差异不值得。

#### INCONCLUSIVE

其他情况。

具体的：

- 最小 CrossoverEpisode 数；
- direction consistency 比例；
- harmful threshold；
- effect-size-dependent evidence budget；

都由 Stage A/B 真机数据确定。

但是上面的判定结构必须固定，不能让不同实现者自由发明完全不同的 Evidence Engine。

### 10.4 用户负面反馈

```text
sluggish
bad
unstable
```

继续保持 hard veto。

不与 0.5W 节省做数学加权。

用户明确觉得体验差：

> candidate 失败。

### 10.5 Evidence Engine 的版本边界

当前 active experiment 的判分参数来自：

- 本地代码；
- 受版本控制的配置；
- 真机 calibration；
- frozen/reference 与 recent noise model。

GPT-5.6 PowerLab Agent 可以通过正常工程流程修改 Evidence Engine。

但这种修改必须产生新的系统/evidence semantics 版本，并使相关旧证据重新评估兼容性。

不能为了让**当前 candidate** 通过而临时修改本轮 Evidence threshold。

---

## 11. ArmMeasurement / CrossoverEpisode / EvidenceDecision

Evidence Engine 不把单个 10 秒 sample 当独立证据。

三个数据概念必须严格分开。

### 11.1 ArmMeasurement

一个连续 arm 的实际测量结果。

例如：

```text
A1
B1
A2
A3
B2
```

记录：

- arm identity；
- baseline / candidate role；
- start / end；
- valid duration；
- BAT integrated Wh；
- battery energy delta；
- gauge consistency；
- PSI；
- thermal；
- media continuity；
- workload / brightness constraints；
- data-quality；
- interruption reason。

### 11.2 CrossoverEpisode

一组可以产生 paired effect 的实验结构。

例如 initial：

```text
A1 + B1 + A2
```

revalidation：

```text
A3 + B2
```

一个 CrossoverEpisode 产生：

```text
paired_effect_w
paired_effect_wh
constraint outcomes
```

### 11.3 EvidenceDecision

EvidenceDecision 作用于一个或多个 compatible CrossoverEpisode。

输出：

```text
WIN
LOSE
INCONCLUSIVE
PRACTICALLY_EQUIVALENT
```

数据库、代码、CLI 和文档中不得再把单独 arm 叫作 episode。

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

- 至少 2 个独立 CrossoverEpisode；
- 全部 data-quality 通过；
- 无 UX veto；
- effect 大于 noise floor 与 practical threshold。

中等效果：

- 需要更多独立 CrossoverEpisode；
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
experiment-local short-window RAPL state stable
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
- 或当前 CrossoverEpisode abort。

不能把 candidate 长期留在机器上等待“以后总会稳定”。

### 15.1 Washout 不使用污染了上一 arm 的长 rolling window

arm 切换时应重置 experiment-local rolling buffers。

washout 判定优先使用：

- instantaneous / short interval；
- 10s / 30s / 60s experiment-local window；
- thermal slope；
- PSI / backlog；
- HWP read-back。

`RAPL 300s`、长期 thermal rolling 等指标继续用于：

- heat-soak；
- long-term trend；
- thermal context。

但不能直接决定：

> candidate 是否已经完成 washout。

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

这些应暂停 / abort 当前 CrossoverEpisode。

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

## 18. Hard Evidence Epoch、Compatibility Tags 与历史衰减

不存在“全生命周期永久最优配置”。

只有：

> 当前 hard evidence epoch + compatible workload/software 条件下的最佳 verified 配置。

### 18.1 Hard Evidence Epoch

Hard Epoch 表示：

> 硬件/控制环境的大时代。

真正应该整体创建新 epoch 的变化包括：

- battery replacement / battery epoch；
- CPU power driver / HWP control semantics 变化；
- 重要 BIOS / firmware power behavior 变化；
- validated thermal safety provider 的安全模型发生实质变化；
- thermal sensor identity 变化；
- calibration semantics / calibration version 发生不兼容变化；
- PowerLab Controller / Evidence Engine 的核心语义发生不兼容变化。

Hard Epoch 变化后，旧 evidence 不直接用于新 promotion。

### 18.2 Compatibility Tags

频繁的软件变化不直接炸掉整个 hard epoch。

保存局部 tags，例如：

- kernel release / linux-surface version；
- Firefox / Chromium major；
- Mesa；
- desktop environment；
- app major；
- media backend；
- workload family。

这些 tags 只影响相关 evidence 的兼容性。

例如：

```text
Firefox major upgrade
→ MEDIA + Firefox evidence revalidation
```

不应该导致：

```text
ECO_IDLE evidence 全部失效
```

### 18.3 三层证据

```text
current_epoch_evidence
recent_compatible_evidence
historical_evidence
```

### 18.4 Promotion

主要依赖：

`current_epoch_evidence`

recent evidence 可以辅助。

historical evidence 只作为：

> 以前它曾经好用。

不能当：

> 过去赢了 20 次，所以今天有 20 票。

Compatibility Tags 不要求绝对完全相同。

由每个 evidence family 明确声明哪些 tag 是：

- required-compatible；
- advisory；
- irrelevant。

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
- FrozenReferenceBaseline；
- RecentAdaptiveDistribution；
- rolling median；
- robust slope。

Frozen reference 不自动跟随 recent distribution 漂移。

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
CONFIRMED_CONFIG_REGRESSION
```

只有证据真正支持以后才能称：

`ACTIONABLE_WASTE`

`UNEXPECTED_POWER` 本身只触发 investigation。

它**不能直接唤醒 Candidate Scheduler**。

只有 Attribution / verification 最终得到：

`CONFIRMED_CONFIG_REGRESSION`

或其他明确说明 verified envelope 已不适合当前环境的证据时，才允许：

`REOPEN_OPTIMIZATION`

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

## 23. GPT-5.6 PowerLab Agent

GPT-5.6 在个人使用场景中不是一个只能调用几个预定义 API 的 researcher。

它是一个拥有用户态 Bash / workspace 能力的受信任工程代理。

职责可以包括：

- 解释异常；
- 汇总长期趋势；
- 阅读外部资料；
- 关联软件版本；
- 提出并验证假设；
- 建议新增搜索维度；
- 读取 SQLite / 日志 / Git 历史；
- 编写临时分析脚本；
- 修改 PowerLab 普通用户态代码和配置；
- 写测试；
- 运行测试与 benchmark；
- 设计实验；
- 维护仓库和 Git 历史；
- 根据用户行为指南执行普通用户态 PowerLab 操作；
- 总结长期历史并提出删减复杂度的建议。

但 runtime 中以下职责仍由确定性组件承担最终事实权：

- Intel HWP：毫秒级性能控制；
- thermald：独立 thermal safety；
- transactional HWP actuator：有限参数写入、read-back、rollback；
- Evidence Engine：当前版本的 reward / data-quality / WIN / LOSE 判定；
- Trial safety gate：battery / thermal / suspend / ownership / telemetry 等运行时条件。

Agent 可以通过正常工程流程修改这些组件的代码或受版本控制配置。

但这种修改必须被视为“系统版本变化”，而不是当前实验中的临时裁判动作。

---

## 24. Agent 事实来源规则

必须区分两类 hypothesis。

### 24.1 External Factual Claim

例如：

> “Firefox 某版本存在 VA-API regression。”

这类外部事实必须带可核查来源：

- release note；
- bug tracker；
- GitHub / GitLab issue；
- upstream discussion；
- 其他可靠来源。

没有来源时只能保存为：

`UNVERIFIED_EXTERNAL_CLAIM`

### 24.2 Local Causal Hypothesis

如果 hypothesis 完全来自本机证据，例如：

```text
process X CPU 持续 40%
停止 X 后 BAT -1.1W
```

Agent 可以直接提出：

> 验证关闭 X 的后台扫描是否消除异常功耗。

这不要求互联网 source。

但必须保存：

```text
local_evidence
verification_plan
```

本机观察不能被伪装成已经确认的外部软件事实。

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

### 25.2 Event-triggered Agent

发生以下事件可以立即调用 PowerLab Agent：

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

## 26. Control Safety、Learning Lifecycle 与 Investigation 正交

PLAN2 不再用一个状态机同时表达：

- 现在能不能写机器；
- 现在应该学习什么；
- 现在是否正在调查异常。

三个状态正交存在。

### 26.1 Control Safety State

```text
CONTROL_ALLOWED
READ_ONLY
DEGRADED
EMERGENCY
```

回答：

> 现在允许执行什么控制动作？

例如：

```text
Learning = STABLE
Control  = READ_ONLY
```

validated thermal safety provider 临时失效时，可以保持学习生命周期为 STABLE，只停止机器写入。

provider 恢复以后：

```text
Learning = STABLE
Control  = CONTROL_ALLOWED
```

不需要因此重新 calibration。

### 26.2 Learning Lifecycle

```text
CALIBRATING
BASELINE_OBSERVATION
COARSE_OPTIMIZATION
VALIDATING
STABLE
REOPENED
```

回答：

> 当前应该积累哪种证据，Scheduler 是否应该醒着？

### 26.3 Investigation Status

```text
IDLE
INVESTIGATING
```

一个 UnexpectedPower 事件可以得到：

```text
Learning      = STABLE
Control       = CONTROL_ALLOWED
Investigation = INVESTIGATING
```

而 Scheduler 继续睡眠。

---

## 27. Control Safety State

进入 `READ_ONLY / DEGRADED / EMERGENCY` 的条件可以包括：

- calibration 无效；
- hardware contract 不完整；
- validated thermal safety provider 不健康；
- HWP ownership 冲突；
- battery telemetry 不可信；
- thermal sensor 未确认；
- controller rollback integrity failure。

READ_ONLY 行为：

- 只观测；
- 不自动写 envelope；
- 不运行 trial；
- 不 Scheduler。

Control Safety 恢复不会自动改变 Learning Lifecycle。

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

- 独立 CrossoverEpisode；
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
- FrozenReferenceBaseline 保持冻结；
- RecentAdaptiveDistribution 缓慢更新；
- active Scheduler 睡眠；
- 无主动 trial；
- LLM 不按小时机械调用；
- 用户没有被不断实验。

---

## 33. STABLE 的进入条件

至少满足：

- 过去代表性窗口内，绝大多数真实有效使用时间已有可信 verified policy coverage；
- 稀有 demand 可以安全 fallback 到已验证的通用 envelope；
- 最近 Scheduler 邻域没有高价值未测试 candidate；
- 剩余可见差异进入 noise / practical equivalence；
- 最近没有 unresolved regression；
- monitoring overhead 已测；
- current hard evidence epoch 数据足够；
- 最近代表性窗口的 verified policy 使用覆盖率达到真机定义阈值；
- 用户没有负面反馈；
- dynamic controller 自身有净收益或至少没有显著负收益。

STABLE 不要求每个理论 demand class 都收集到独立最优 envelope。

建议以最近 30 天的有效使用时间覆盖率作为主要指标。

具体阈值，例如 90% / 95%，由真机数据和实际使用分布决定。

---

## 34. 重新开启 Optimization 的条件

包括：

- new battery epoch；
- hard evidence epoch change；
- compatibility tag 变化后相关 verified evidence 需要 revalidation；
- current verified envelope regression；
- sustained drift；
- user complaint；
- 新的高价值 deterministic hypothesis；
- 新 actuator / control dimension；
- verified envelope 被 BLOCKED；
- monitoring overhead 大幅改变。

`UnexpectedPower` 本身不在列表中。

它先进入：

`INVESTIGATING`

只有调查确认：

- `CONFIRMED_CONFIG_REGRESSION`；
- verified envelope 已不满足 UX/energy 约束；
- 或存在明确的新高价值控制假设；

才进入 `REOPENED / COARSE_OPTIMIZATION`。

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

### 36.1 默认搜索方向不是对称的

如果当前 UX 全部通过：

优先测试：

`lower-energy neighbors`

例如更低 max_perf_pct、更偏节能的 named EPP。

只有在以下情况才优先测试更高性能邻居：

- 用户报告 sluggish；
- PSI / latency proxy 显示当前 envelope 明显过紧；
- 已有 evidence 表明 race-to-idle 可能使更高性能反而降低整机能耗。

因此 Scheduler 的默认策略是：

> 能耗优先向低能方向搜索，高能方向主要用于 UX rescue 或有证据支持的 race-to-idle 假设。

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
- 每个 candidate 最多多少 CrossoverEpisode；
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

## 42. Candidate Eligibility / Priority Filter

每个优化方向先估计：

```text
potential_saving_w
measurement_confidence
ux_risk
experiment_cost
```

这些变量不合成为一个伪精确的加权总分。

第一版采用 lexicographic filter：

```text
measurement confidence 足够？
    ↓
potential saving >= minimum useful effect？
    ↓
UX / thermal risk 是否允许？
    ↓
lifecycle / safety 是否允许？
    ↓
实验成本最低的候选优先
```

Scheduler 优先：

> 可测量、值得测、风险允许，并且实验成本较低。

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

先定义一个独立：

`MinimalMeter`

它只负责用极低开销记录：

- timestamp；
- battery status；
- energy_now；
- 必要时 power_now；
- 最少量的质量信息。

建议 30–60s 采样，不做 process scan、GPU probe、ActivityWatch enrichment、Scheduler、
Trial 或高频复杂 SQLite 写入。

### A

```text
MinimalMeter
+
固定良好 envelope
```

### B

```text
MinimalMeter
+
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

MinimalMeter 两边都存在，使测量方法保持一致。

“PowerLab OFF”不再意味着“完全没有测量器”。

---

## 45. Net Battery Benefit

最终 Net Benefit 应通过 end-to-end 对照直接测量，而不是主要靠两个模型相减。

```text
A:
MinimalMeter + fixed good configuration

D:
MinimalMeter + full PowerLab
```

最终关注：

```text
NetBenefit
=
A 的真实 BAT / usable runtime
-
D 的真实 BAT / usable runtime
```

`MonitoringOverhead` 继续单独测量，但主要用于解释：

> 为什么完整系统没有达到 envelope 实验显示的理论收益？

不能在一个已经包含 PowerLab overhead 的 A/B envelope effect 上再次机械减去同一个 overhead。

---

## 46. Telemetry 使用分层采样与 Diagnostic Burst

STABLE 状态不应一直按实验密度采所有数据。

### 46.1 Always-on Core

长期保持低开销核心采样，例如 10–15s：

- BAT；
- temperature；
- HWP state；
- CPU aggregate；
- 少量 drift essentials。

### 46.2 Expensive Attribution

正常 STABLE 下低频运行，例如 60–300s：

- process scan；
- GPU probe；
- device runtime PM；
- wakeups / interrupts；
- detailed network attribution；
- ActivityWatch enrichment；
- heavier DB rollup。

### 46.3 Trial / Calibration

高信息密度。

### 46.4 Diagnostic Burst Mode

如果 always-on core 发现：

```text
unexpected BAT
CPU spike
thermal slope anomaly
repeated data-quality issue
```

进入短时：

`Diagnostic Burst Mode`

例如持续 2–5 分钟，提高：

- process；
- GPU；
- device；
- network；
- wakeup attribution

采样频率。

事件结束后恢复低频。

目标仍然是：

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

Controller A/B 不能只做：

```text
这个星期 fixed
下个星期 dynamic
```

至少采用 day/block-level crossover，例如：

```text
A
B
B
A
```

或在自然日/代表性 block 间做受控随机分配。

目的不是追求临床试验式复杂度，而是避免单纯历史前后比较被 workload、Wi-Fi、室温和使用习惯
变化污染。

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
- hard battery epoch；
- CPU power driver / control semantics；
- BIOS / firmware power identity；
- thermal safety provider identity；
- thermal sensor identity；
- calibration version；
- PowerLab control/evidence semantics version；
- start / end；
- invalidation reason。

### `compatibility_tags`

记录局部兼容条件：

- kernel / linux-surface；
- Firefox / Chromium；
- Mesa；
- desktop；
- app major；
- media backend；
- workload family；
- tag relevance scope。

### `reference_baselines`

记录当前 hard epoch 稳定后冻结的 reference：

- effective envelope；
- primary hard strata；
- median / robust spread；
- established_at；
- supporting evidence；
- freeze reason。

默认不可自动随 recent data 漂移。

### `recent_noise_distributions`

记录：

- envelope；
- 少量 hard strata；
- 24h / 7d / 30d window；
- median / MAD / IQR；
- crossover count；
- data quality。

brightness、thermal start 等优先保存在 comparison constraint / covariate，不默认展开成独立 bucket。

### `arm_measurements`

记录：

- trial；
- arm；
- baseline/candidate；
- start / end / valid duration；
- BAT integrated Wh；
- battery energy delta；
- consistency error；
- PSI；
- thermal；
- media continuity；
- UX feedback；
- validity。

### `crossover_episodes`

记录：

- episode id；
- initial / revalidation；
- constituent arm ids；
- paired effect W / Wh；
- comparison constraints；
- compatibility tags；
- evidence epoch；
- outcome vetoes；
- validity。

### `evidence_decisions`

记录：

- WIN；
- LOSE；
- INCONCLUSIVE；
- PRACTICALLY_EQUIVALENT；
- reason；
- thresholds；
- reference baseline version；
- recent noise model version；
- evidence semantics version；
- supporting crossover ids。

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
- Agent hypothesis；
- external sources；
- verification state。

### `control_safety_history`

记录：

- CONTROL_ALLOWED / READ_ONLY / DEGRADED / EMERGENCY；
- transition reason；
- timestamp。

### `learning_lifecycle_history`

记录：

- state；
- transition reason；
- timestamp。

### `investigations`

记录：

- unexpected power event；
- status；
- deterministic attribution；
- local evidence；
- Agent hypothesis；
- external source（若涉及外部事实）；
- verification plan；
- final classification。

### `monitoring_overhead_runs`

记录 MinimalMeter 下的 PowerLab monitoring ON/OFF 对照。

### `minimal_meter_runs`

记录最终 end-to-end A/B/C/D 对照所需的极轻测量数据。

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

sp7-powerlab safety status

sp7-powerlab investigation list
sp7-powerlab investigation inspect <id>

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

## 53. Agent capability 与信任模型

PLAN2 的默认个人使用信任模型：

> GPT-5.6 可以获得类似 workspace/Bash 的通用用户态工具，并自行探索完成任务。

不要求把所有行为提前封装成白名单 MCP API。

典型能力包括：

```text
repo / git
SQLite
logs / journal
/proc / readable /sys
PowerLab CLI
temporary Python/Bash
tests / benchmarks
config editing
code editing
data analysis
system inspection
```

`sp7-powerlab-agent` 可以保留，作为常用高层 workflow 的便利入口。

但它不是安全沙箱，也不是 GPT-5.6 唯一允许接触的接口。

### 53.1 为什么不做过度 capability isolation

这是个人设备，不是多租户平台。

过度限制会让 Agent 在遇到真实问题时无法：

- 顺着日志继续调查；
- 临时写分析程序；
- 对比 Git commit；
- 检查新出现的 sysfs / process / package 状态；
- 自己修复 PowerLab；
- 为从未预定义过的问题构造诊断方法。

GPT-5.6 的主要价值之一就是开放式工程探索。

### 53.2 真正值得保留的硬边界

硬边界应集中在故障后果大的底层：

- PowerLab 自己不提供任意 root shell；
- root helper 只接受有限且经过校验的 HWP 参数；
- 不提供 arbitrary privileged sysfs / MSR writer；
- validated thermal safety provider 保持独立（当前实现为 thermald）；
- HWP actuator transaction / read-back / rollback 保持强制；
- runtime trial safety gate 不因为 Agent proposal 而关闭；
- active experiment 使用当时已经确定的 Evidence Engine 版本判分。

### 53.3 行为边界与权限边界必须区分

如果 GPT-5.6 与用户运行在同一个 UID，并拥有通用 Bash：

它技术上可能运行：

```text
sp7-powerlab trial start ...
sp7-powerlab trial promote ...
编辑 config
修改源码
```

因此不能再声称：

> “模型技术上无法启动 trial / promotion。”

对于个人使用，默认采用：

> 行为指南 + Git 可审计历史 + deterministic runtime safety

而不是额外构造复杂的权限隔离系统。

如果未来真的要求不可绕过的人类 approval，需要另外设计 Unix user / ACL / sudo / polkit /
approval daemon；这不属于 PLAN2 默认目标。

### 53.4 推荐 Agent 行为指南

行为指南保持短而明确：

1. 目标是提高真实净续航，同时保持良好用户体验和系统稳定。
2. 优先调查异常功耗，再考虑通过降低性能节能。
3. 使用真实 BAT 和 Evidence Engine 结果，不选择性忽略坏结果。
4. active trial 期间不要为了让当前 candidate 通过而修改判分标准。
5. root、thermal safety、不可逆系统状态相关操作特别保守。
6. 对不确定问题主动探索仓库、数据库、系统状态和外部资料，不局限于预定义 workflow。
7. 如果复杂系统没有明显净收益，应主动建议简化甚至删除。
8. 重要工程修改使用 Git 保留可审计历史。

---

## 54. Agent 证据与外部来源结构

Agent hypothesis 如果引用外部事实，必须保存结构化 source：

```text
title
url
source_type
retrieved_at
claim
relevance
```

但本机 causal hypothesis 不强制互联网来源。

本机 hypothesis 必须保存：

```text
local_evidence
verification_plan
```

只有涉及外部 factual claim 时才要求 external source。

---

## 55. PLAN2 自动化等级建议

这些 Level 主要约束 PowerLab 内建 daemon / Scheduler 的默认自动行为。

它们不是针对同 UID Bash Agent 的强制安全沙箱。

Agent 是否执行某个操作，还受用户当前指令和行为指南约束。

### Level 0

只读。

### Level 1

自动 Controller 只切 VERIFIED envelope。

### Level 2

Scheduler 可以提出 candidate。

默认工作流中，Trial 和 Promotion 先向用户报告再执行。

这是推荐治理策略，不宣称是不可绕过的权限隔离。

推荐个人长期使用级别。

### Level 3

允许低风险 candidate 自动开始 trial。

Evidence Engine 仍完全本地确定。

用户也可以明确授权 PowerLab Agent 在这个等级主动处理低风险实验。

### Level 4

允许满足严格条件的自动 promotion。

只有真机长期验证以后才考虑。

STABLE 状态下即使 Level 4，也默认不主动探索。

Level 4 仍不允许任何组件为了 promotion 临时修改当前实验的 Evidence 判分标准。

---

## 56. 实施顺序

不能先写 Scheduler。

正确顺序如下。

---

## 57. Phase 0 — Measurement Trust

先实现：

- MinimalMeter；
- power integration；
- energy_now delta；
- battery gauge quantum / update cadence measurement；
- measurement-derived minimum arm duration；
- consistency gate；
- data-quality status；
- FrozenReferenceBaseline；
- RecentAdaptiveDistribution；
- hard strata + comparison covariates；
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

- ArmMeasurement；
- CrossoverEpisode；
- EvidenceDecision；
- paired effect；
- hard evidence epoch；
- compatibility tags；
- noise-aware thresholds；
- explicit first-version decision contract；
- WIN；
- LOSE；
- INCONCLUSIVE；
- PRACTICALLY_EQUIVALENT；
- user-feedback veto；
- data-quality rejection。

替代当前 Trial 内部“固定阈值直接 winner”的职责。

---

## 59. Phase 2 — Control Safety / Learning Lifecycle / STABLE

实现：

- ControlSafetyState；
- LearningLifecycle；
- InvestigationStatus；
- separate transition logs；
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

washout 只使用 experiment-local short-window state；300s 等长 rolling 只用于 heat-soak / trend。

---

## 61. Phase 4 — Unexpected Power Detector

重命名并重构现有 WasteDetector。

先检测：

`UNEXPECTED_POWER`

再 attribution。

不能直接把异常叫 waste。

UnexpectedPower 只打开 investigation。

不直接 reopen optimization。

---

## 62. Phase 5 — Candidate Scheduler

在 Evidence Engine 稳定后才实现。

第一版：

- discrete local search；
- neighbor expansion；
- blacklist；
- experiment budget；
- lexicographic eligibility / priority filter；
- energy-first directional search；
- stop rules。

不实现 BO。

---

## 63. Phase 6 — Agent Cadence

把当前“每小时 LLM”改成：

- 每小时 local；
- event-driven Agent review / investigation；
- daily review；
- weekly drift review。

并要求**外部 factual claim** 带来源；纯本机 hypothesis 使用 local evidence + verification plan。

---

## 64. Phase 7 — Net Benefit Validation

通过 MinimalMeter 下的 crossover 正式比较：

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
- energy_now quantum 是多少？
- energy_now / power_now 多久更新一次？
- quantization pattern 是什么？
- gauge 分辨率允许的 minimum arm duration 是多少？
- 两者 consistency 如何？
- noise floor 多大？
- suspend/resume 是否破坏数据？

### Attribution

- 正常 5W 花在哪里？
- CPU RAPL 占多少？
- GPU / display / network 大概是什么量级？
- 有没有明显常驻浪费？

### Overhead

- MinimalMeter + fixed envelope vs MinimalMeter + PowerLab monitoring 增加多少 W？
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

- FrozenReferenceBaseline 建立；
- RecentAdaptiveDistribution 建立；
- 主要 hard strata 得到自然分布；
- comparison covariates / tolerances 定义完成；
- recent noise model 达到最低 natural window 数；
- practical threshold 定义完成；
- current hard evidence epoch / compatibility tags 建立。

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

### PowerLab Agent 是否带来额外价值？

### PowerLab 整体净收益是否值得？

允许答案是：

> 不值得。

---

## 70. Acceptance Criteria — Measurement

必须满足：

- MinimalMeter 可独立运行且显著轻于完整 PowerLab；
- BAT energy integration gap-aware；
- energy delta 可用；
- energy_now quantum / update cadence 已实测；
- power_now cadence / quantization 已实测；
- minimum arm duration 由 gauge resolution 和真机噪声约束；
- consistency gate 有真机阈值；
- Charging / AC 不进入放电 reward；
- suspend gap 不进入 reward；
- FrozenReferenceBaseline 与 RecentAdaptiveDistribution 分离；
- noise model 使用少量 hard strata + comparison covariates，不能 bucket explosion；
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
- old hard epoch evidence 不直接累计票数；
- compatibility tags 只局部影响相关 evidence；
- ArmMeasurement / CrossoverEpisode / EvidenceDecision 语义分离；
- 第一版 Evidence Decision Contract 有明确 WIN / LOSE / EQUIVALENT / INCONCLUSIVE 结构。

---

## 72. Acceptance Criteria — Scheduler

必须满足：

- 只选有限邻居；
- 不修改 Evidence threshold；
- 不越过 automation level；
- 有 experiment budget；
- 有 stop rules；
- STABLE 默认不探索；
- potential headroom 太低时不探索；
- 使用 lexicographic filter，不依赖伪精确加权总分；
- UX 正常时默认优先 lower-energy neighbor，高性能方向只用于 UX rescue 或有证据支持的 race-to-idle。

---

## 73. Acceptance Criteria — Unexpected Power

必须满足：

- detection 与 root-cause 分离；
- 不能把“高于历史”直接叫 waste；
- attribution 可返回 EXPECTED；
- UnexpectedPower 只触发 investigation，不直接 reopen optimization；
- 只有 CONFIRMED_CONFIG_REGRESSION 等明确证据才唤醒 Scheduler；
- Agent 的 root-cause 输出在证据确认前保持 hypothesis；
- external factual claim 有来源；
- local causal hypothesis 可以使用 local evidence + verification plan。

---

## 74. Acceptance Criteria — STABLE

进入 STABLE 后：

- 7 天内无主动 trial，除非明确 trigger；
- Scheduler 不机械寻找更细参数；
- 最近代表性窗口的 verified policy 覆盖率达到真机定义阈值；
- 稀有 demand 有安全 fallback，不阻塞 STABLE；
- controller 稳定；
- FrozenReferenceBaseline 不随 recent data 漂移；
- telemetry 使用 always-on core + low-frequency attribution + diagnostic burst；
- drift detection 正常；
- 用户无显著负面反馈。

---

## 75. Acceptance Criteria — Net Benefit

最终必须完成：

```text
MinimalMeter + fixed good profile
vs
MinimalMeter + PowerLab monitoring only
vs
MinimalMeter + dynamic controller
vs
MinimalMeter + full PowerLab
```

比较采用 block/day-level crossover 或其他能降低纯历史前后偏差的设计。

最终 Net Benefit 直接来自 end-to-end 条件对照。

MonitoringOverhead 是解释变量，不重复从已经包含 PowerLab 开销的 envelope effect 中机械扣除。

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
- PowerLab 主动提供 unrestricted root shell；
- 把通用用户态 Bash 误写成“技术上不可绕过的人工批准”；
- active experiment 中为了让当前 candidate 通过而临时修改 Evidence Engine / threshold。

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

### PowerLab Agent 长期没带来 actionable finding

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
7. end-to-end Full PowerLab 相比 MinimalMeter + fixed good configuration 有足够实际价值；
8. 如果没有进一步收益，它知道停止。

---

## 79. PLAN2 最终系统定义

PowerLab 不再定义为“持续自动优化器”。

最终定义：

> **一个专门针对 Surface Pro 7 i5-1035G4 的个人续航证据系统。它把“能不能安全控制机器”“当前要不要继续学习”“是否正在调查异常”作为正交状态；先用 MinimalMeter、battery gauge 分辨率、FrozenReferenceBaseline 与 RecentAdaptiveDistribution 建立可信测量，再用明确的 Evidence Decision Contract 判断候选配置是否具有可重复且有实际价值的收益。Candidate Scheduler 只在存在足够优化 headroom 且 investigation 已证明值得重新优化时，进行有限、离散、可回滚的探索；一旦大多数真实使用时间已有可信配置覆盖，系统进入 STABLE 并停止主动实验。UnexpectedPower 只触发 investigation，不直接触发参数搜索。GPT-5.6 作为受信任的用户态 PowerLab Agent，可以使用 Bash 自由研究、诊断、分析数据、修改代码和配置、运行实验工具并维护 Git；但实时性能控制、validated thermal safety、HWP transaction 和当前实验的 Evidence 判分继续由确定性 runtime 组件承担。最终评价使用 MinimalMeter 下的 end-to-end crossover，只看真实净续航与用户体验，而不是 PowerLab 自身的算法复杂度。**

---

## 80. 一句话原则

> **先测准，再判断；异常先调查，不急着调参；先找浪费，再降性能；收益小于噪声就停止；真实使用覆盖够了就冻结；环境变了再按证据重新学习。**
