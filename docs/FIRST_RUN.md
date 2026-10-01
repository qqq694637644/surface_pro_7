# First Run — Surface Pro 7 PowerLab

本文件只描述真实 Surface Pro 7 第一次部署和新电池重新建基线的顺序。

AI / Agent 先读根目录 AGENTS.md。

任何时候都可以先运行：

sp7-powerlab agent-context

它会告诉你当前 runtime stage 和 blocker。

## 1. 安装

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
~~~

安装用户服务：

~~~bash
bash scripts/install-user-services.sh
~~~

安装 root helper：

~~~bash
bash scripts/install-root-helper.sh
~~~

root helper 只提供有限 HWP 操作，不提供任意 root shell。

## 2. 先确认硬件契约

~~~bash
sp7-powerlab doctor
sp7-powerlab agent-context
~~~

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

如果刚更换电池：

先确认 user service 已经写入至少一条新电池 telemetry：

~~~bash
sp7-powerlab observe power --hours 1
~~~

输出必须有 latest battery sample。若刚启动 service，先等正常采样出现，不要用旧电池最后一条
sample 创建新 epoch。

~~~bash
sp7-powerlab calibrate new-battery
~~~

新 battery epoch 会使旧 calibration / verified evidence 失效或需要重新验证。

坏旧电池和新电池的数据不要直接混在一起比较。

## 4. 先只读收集真实放电

默认 automation.level = 0。

保持只读，进行真实日常使用。

检查：

~~~bash
sp7-powerlab observe now
sp7-powerlab observe power --hours 6
sp7-powerlab agent-context
~~~

尽量覆盖：

- idle
- normal interactive
- browser
- media
- remote work

此阶段不要为了“加速学习”故意持续本地满载。

## 5. Stage A — Measurement Trust

先看 gauge：

~~~bash
sp7-powerlab evidence gauge --hours 6
~~~

再评估：

~~~bash
sp7-powerlab evidence trust --hours 6
~~~

目标是：

Measurement Trust = READY

需要真实解决：

- energy_now quantum
- energy_now cadence
- power_now cadence/quantization
- BAT integration
- energy delta
- consistency
- minimum arm duration

如果仍 BLOCKED：

- 继续收集更长的 Discharging 数据；
- 检查 suspend/resume gap；
- 检查 battery telemetry 是否稳定；
- 不开始自动 candidate search。

## 6. preliminary Measurement Trust READY 后再 Calibration

按顺序：

~~~bash
sp7-powerlab calibrate start cold_idle
sp7-powerlab calibrate finish

sp7-powerlab calibrate start normal_interactive
sp7-powerlab calibrate finish

sp7-powerlab calibrate start media
sp7-powerlab calibrate finish

sp7-powerlab calibrate start bounded_burst
sp7-powerlab calibrate finish
~~~

检查：

~~~bash
sp7-powerlab calibrate status
sp7-powerlab agent-context
~~~

bounded_burst 只用于热惯性和短时行为。

不要把它当 sustained benchmark。

Calibration 完成会改变 hard evidence context。此时之前的 preliminary trust 只证明“测量路径可用”，
不能直接授权当前 epoch 的长期学习。重新运行：

~~~bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
~~~

只有新的 current-epoch Measurement Trust READY 后，才建立 Frozen Reference / Recent Noise 或开始 trial。

## 7. 收编第一个真实 verified baseline

不要把 config/envelopes.toml 里的 candidate 直接宣布 VERIFIED。

当 hardware contract、Measurement Trust、calibration 和 helper 都正常后：

~~~bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT \
  --note "current real HWP baseline"
~~~

它读取机器当前真实 HWP snapshot。

检查：

~~~bash
sp7-powerlab envelope list
sp7-powerlab agent-context
~~~

## 8. Stage B — Natural Baseline / Noise

先让机器在真实日常工作里自然运行。

目标：

- FrozenReferenceBaseline
- RecentNoiseDistribution
- representative usage coverage

检查：

~~~bash
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab lifecycle coverage
~~~

不要急着搜索。

如果 noise baseline 不够，Scheduler 应保持 blocked。

## 9. 再进入 coarse optimization

建议先 Level 2。

修改 config/powerlab.toml：

~~~toml
[automation]
level = 2
auto_promote = false
~~~

然后：

~~~bash
sp7-powerlab lifecycle optimize --reason "begin coarse search"
sp7-powerlab scheduler status
sp7-powerlab scheduler candidates INTERACTIVE_EFFICIENT
~~~

Level 2 默认只提出 candidate。

实验前仍应检查：

- current ControlSafetyState
- Measurement Trust
- noise
- arm duration
- battery level
- thermal cooldown
- experiment budget

## 10. Trial

常规 proposal：

~~~json
{
  "kind": "envelope",
  "baseline_envelope": "INTERACTIVE_EFFICIENT",
  "changes": {
    "max_perf_pct": 55
  }
}
~~~

启动：

~~~bash
sp7-powerlab trial start proposals/example.json
~~~

查看：

~~~bash
sp7-powerlab trial status
~~~

实验必须经历 initial crossover 和 independent revalidation。

最终只有 VERIFIED_WINNER 才能 promotion。

## 11. 用户反馈

出现卡顿、滚动不顺、remote latency 或不稳定时及时记录：

~~~bash
sp7-powerlab feedback sluggish \
  --trial-id trial-xxxx \
  --notes "browser scroll/input latency"
~~~

不要为了节能数字忽略真实体验退化。

## 12. Stage C/D 后才考虑 Level 3

只有以下链路在真机反复可靠后再考虑 Level 3：

- Measurement Trust
- transaction/read-back/rollback
- thermal preemption
- independent revalidation
- Scheduler stop rules
- experiment budget
- negative feedback

Level 3 允许 daemon 自动开始通过全部 gate 的低风险 trial。

Level 4 还允许满足条件的自动 promotion。

个人设备没有必要为了“自动化程度高”升级 level。

## 13. STABLE

检查：

~~~bash
sp7-powerlab lifecycle readiness
~~~

STABLE 需要真实 coverage、reference/noise、无 unresolved investigation 和 Net Benefit evidence。

满足后：

~~~bash
sp7-powerlab lifecycle freeze --reason "real usage coverage and net benefit validated"
~~~

进入 STABLE 后正常行为应该更安静，而不是继续找新参数。

## 14. PowerLab 本身的 Net Benefit

用 sp7-powerlab-meter 分别记录可比较条件。

至少比较：

- fixed-good
- monitoring
- dynamic controller
- full PowerLab

然后：

~~~bash
sp7-powerlab overhead compare reference.jsonl monitoring.jsonl \
  --mode MONITORING_OVERHEAD --campaign sp7-net-benefit-01

sp7-powerlab overhead compare reference.jsonl dynamic.jsonl \
  --mode DYNAMIC_CONTROLLER --campaign sp7-net-benefit-01

sp7-powerlab overhead compare reference.jsonl full.jsonl \
  --mode FULL_POWERLAB --campaign sp7-net-benefit-01

sp7-powerlab overhead summary
~~~

三个 mode 必须来自同一个 current evidence epoch，并使用同一个 campaign 名称；否则不会作为
一组完整 Net Benefit evidence 让 STABLE readiness 通过。

如果结果建议 FIXED_GOOD_ENVELOPE，就不要因为项目已经复杂而强行保留动态系统。

## 15. 真机完成前不要声称什么

在实际新电池 Stage A–E 完成前，不要声称：

- 已找到最优省电参数；
- 当前 noise/MUE 已被 SP7 真机充分校准；
- Dynamic Controller 一定省电；
- Full PowerLab 一定有净收益；
- Level 3/4 已适合长期无人监督。

当前实现/验证进度看 docs/PROJECT_STATUS.md。
