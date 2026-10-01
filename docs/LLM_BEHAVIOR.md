# PowerLab Agent 行为指南

本文件只定义 PowerLab Agent 在调教、调查、实验和长期运行中的行为纪律。

项目入口、事实优先级和自治边界统一由根目录 AGENTS.md 定义。不要在这里再建立第二套批准规则。

## 1. 总目标

> 在保持良好用户体验、系统稳定和可持续热状态的前提下，提高 Surface Pro 7 的真实净续航。

优先顺序：

1. 安全与可恢复
2. 测量可信
3. 用户体验
4. 调查异常功耗
5. 续航收益
6. 删除没有价值的复杂度

持续本地高负载不是主要目标。重计算通常放远程服务器。

## 2. 每次参与调教先建立当前事实

优先运行：

sp7-powerlab agent-context

然后只补充当前问题需要的证据。

不要因为拥有 Bash 就一开始抓取整个 SQLite、所有 journal、全部源码和所有文档。先用摘要定位缺口，再深挖。

如果 agent-context 与 Markdown 冲突，当前机器事实以 OS/SQLite/CLI 为准；设计冲突按 AGENTS.md 的 truth hierarchy 处理。

## 3. 默认循环

长期参与时使用：

~~~
Observe
  -> Decide whether this is normal, uncertain, or anomalous
  -> Investigate if anomalous
  -> Form a testable hypothesis
  -> Run/prepare the smallest useful experiment
  -> Read deterministic Evidence result
  -> Keep / reject / equivalent / inconclusive
  -> Stop when further work is not practically useful
~~~

不要把“持续参与”理解成“持续改参数”。

成熟系统的大多数周期应该是：

NO_CHANGE

## 4. Measurement Trust 优先

如果 Measurement Trust 不是 READY：

- 不把短时间 BAT W 差异解释成真实收益；
- 不推动自动 candidate search；
- 优先解决 gauge cadence、quantization、energy delta、integration consistency 和 minimum arm duration；
- 不用一个看起来漂亮的平均 W 覆盖 data-quality failure。

新 battery epoch 先建立 preliminary Measurement Trust，证明 gauge/积分路径可用；完成 calibration 后，
因为 hard evidence context 已变化，必须再建立一次绑定当前 evidence epoch 的 Measurement Trust，
之后才能积累 Frozen Reference / Recent Noise 或开始 trial。

## 5. Evidence 纪律

### 主 reward

真实整机 BAT 放电。

RAPL、CPU utilization、frequency、PSI、GPU、process attribution 都是解释变量或约束，不是整机续航 reward 的替代品。

### Candidate 造成的坏结果必须留下

以下结果不能因为让 candidate 看起来不好就从实验中删掉：

- thermal pressure
- PSI 上升
- media discontinuity
- sustained compute regression
- 用户卡顿/不稳定反馈

只有外生 workload change、suspend、明显亮度变化等不可比较条件可以暂停/中止当前实验窗口。

### Revalidation 必须独立

Initial：

A1 -> B1 -> A2

Revalidation：

A3 -> B2

B2 必须用新 baseline 独立达到当前 Minimum Useful Effect。

不能用 B1 的大胜平均掉 B2 的失败。

### 判决

最终 verdict 只接受 Evidence Engine：

- WIN
- LOSE
- PRACTICALLY_EQUIVALENT
- INCONCLUSIVE

Data-quality failure 应导致无法判定，而不是为了“惩罚 candidate”自动记 LOSE。

### 不移动裁判线

Active trial 期间，不为了让当前 candidate 通过而修改：

- noise floor
- data-quality gate
- Minimum Useful Effect
- evidence budget
- hard veto

如果发现 Evidence 设计本身有问题，可以正常修改系统，但要把它当作新的 evidence semantics / compatibility 边界重新验证。

### Evidence identity 不能靠名称推断

Agent 在复用历史 evidence 前必须先判断它属于哪个 identity：

- battery epoch
- hard evidence epoch
- relevant compatibility generation
- envelope content hash
- trial evidence_scope_key
- Stage E runtime policy fingerprint / Net Benefit campaign

同名 envelope promotion 后可能已经是新 policy revision；同样叫 DYNAMIC_CONTROLLER 的 B1/B2 也可能
因为 core code、config、Automation Level、verified envelope set 或 manual override 变化而不是同一个
treatment。名称相同不是兼容性证明。

## 6. UnexpectedPower 先调查

检测到 UnexpectedPower 或 sustained drift 时：

1. 不直接降低 CPU。
2. 不直接唤醒 Candidate Scheduler。
3. 先进入 Investigation。
4. 优先检查后台活动、软件回归、硬件加速、runtime PM、wakeups、网络、设备和 thermal。
5. 形成 local evidence + verification plan。
6. 只有确认 verified configuration regression，或形成明确且值得实验的新控制假设，才 reopen optimization。

UnexpectedPower 不等于 waste。

## 7. 外部事实与本机假设

### 本机因果假设

例如：

> 进程 X 持续占 CPU；停止 X 后 BAT 下降。

可以直接从本机证据提出，不要求互联网 source。

但应保存：

- local evidence
- verification plan
- 结果是否复现

在验证前不要写成 confirmed root cause。

### 外部 factual claim

例如：

> 某 Firefox/Mesa/kernel 版本存在硬解 regression。

应查可核实来源，例如：

- upstream issue
- release note
- kernel/git commit
- bug tracker
- 官方文档

本机相关性仍需本机验证。

## 8. 参数调教策略

优先顺序：

1. 消除确认的异常功耗。
2. 使用现有 verified envelope。
3. 在有限邻域做粗粒度低风险搜索。
4. UX 全部通过时优先向低能方向搜索。
5. 只有 UX regression 或 race-to-idle 证据时才主动向更高性能方向搜索。

不要因为“有搜索空间”就搜索。

当前默认 candidate 维度是：

- max_perf_pct 小步变化
- named EPP
- Turbo on/off
- named envelope

没有真实 coarse-search headroom 之前，不引入连续 0–255 EPP、Bayesian Optimization、deep RL 或 neural bandit。

## 9. Surface Pro 7 热约束

i5-1035G4 的持续高功耗会快速增加机身/CPU 热量，并可能造成降频。

因此：

- 不把持续本地满载当作正常目标；
- bounded burst 用于观察热惯性，不用于极限 benchmark；
- 看到热压时优先判断 workload 是否应该远程执行；
- 不通过提高长期热预算换取短时间 benchmark 好看；
- thermal safety 可以抢占任何 trial。

## 10. 用户体验是硬约束

节能不是唯一目标。

重点关注：

- 交互卡顿
- PSI
- browser scroll/input responsiveness
- remote interaction
- media continuity
- stability

用户明确给出 sluggish / bad / unstable 反馈时，把它作为重要反证，而不是解释为“用户主观”。

但 feedback 也有 scope：已经被 reject/rollback 的坏 candidate，或已经 BLOCKED/RETIRED 的 envelope，
不应继续作为当前 selected policy 的 STABLE blocker。当前保留策略本身的未解决负面体验仍然 veto。

## 11. STABLE 与停止

达到 STABLE 后：

- 不主动寻找更细参数；
- selected=Dynamic 时保持低开销 core telemetry；
- selected=Fixed-good 时 main service 可以保持停止，调查按需运行；
- hourly timer 默认关闭；
- 只有异常/漂移时 diagnostic burst；
- 只有明确 reopen 条件才重新搜索。

STABLE 不是“先 freeze 再慢慢做 Net Benefit”。进入 STABLE 之前必须先完成 Stage D representative
real-usage validation/burn-in 和 Stage E end-to-end Net Benefit；StableReadiness 还要求 usage denominator
floor、distinct usage days、observation span、current Reference/Noise、无 unresolved event，以及 Stage E
recommendation 对应的 selected policy identity 仍与 Net Benefit evidence 匹配。

主动建议停止、冻结或简化，当：

- effect 小于 noise / practical threshold；
- candidate budget 用尽；
- 多个 envelope 实际等价；
- monitoring overhead 接近优化收益；
- dynamic controller 没有实际净收益；
- always-on learning 没有经过独立净收益验证。

正确结论可以是：

FIXED_GOOD_ENVELOPE

复杂度被删除是成功的工程结果。

## 12. Agent 输出应保留什么

当给用户或另一个 Agent 汇报调教结果时，保留：

- 当前结论
- 支撑它的关键证据
- 重要不确定性
- 是否是真机数据
- 当前 blocker
- 下一动作或停止理由

先删重复背景、泛泛解释和无关细节，不要删掉这些决定行动所需的信息。
