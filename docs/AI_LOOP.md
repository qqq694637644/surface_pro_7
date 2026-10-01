# AI Loop

PowerLab Agent 是慢速研究/调查/工程层，不是实时 DVFS controller。

AI 启动入口：

1. 读 AGENTS.md。
2. 读 docs/PROJECT_STATUS.md。
3. 运行 sp7-powerlab agent-context。
4. 只加载当前问题需要的额外材料。

## 1. 两种上下文

### agent-context

用于快速建立当前事实。

内容包括：

- Git/schema
- hardware contract
- Control Safety
- Learning Lifecycle
- battery/calibration
- Measurement Trust
- Evidence/reference/noise
- active trial/investigation
- Scheduler gate
- coverage
- Net Benefit
- next stage

它应该是大多数 Agent turn 的第一入口。

### knowledge pack

用于需要更长历史时。

~~~bash
sp7-powerlab-agent hourly
~~~

包含：

- recent BAT/rollups
- thermal/demand
- control actions
- evidence decisions
- investigations
- UnexpectedPower
- trials
- feedback
- rejection memory
- coverage
- net benefit
- versions

不要每次都把整个历史 pack 塞给模型。

## 2. Agent cadence

推荐：

### Event-driven

当出现：

- UnexpectedPower
- sustained drift
- thermal incident
- user complaint
- failed trial
- system/software compatibility change

触发调查。

### Slow periodic review

用于：

- 检查长期 drift
- 总结 evidence
- 判断是否该进入 STABLE
- 判断复杂度是否值得保留

### User-driven

用户主动要求：

- 研究
- 解释
- 试验
- 改代码/配置
- 做系统 review

固定 hourly 可以作为一种 slow-review transport，但不是设计要求，也不意味着每小时都必须修改系统。

## 3. 决策优先级

Agent 应优先：

1. 判断测量是否可信。
2. 调查 UnexpectedPower。
3. 查软件/驱动/硬解/device regression。
4. 使用已有 verified envelope。
5. 最后才做 HWP candidate tuning。

不要把“可以调参数”当成“应该调参数”。

## 4. Structured actions

可选结构化 decision contract：

- NO_CHANGE
- NEED_MORE_DATA
- INVESTIGATE_POWER_SPIKE
- INVESTIGATE_THERMAL_EVENT
- PROPOSE_POWER_FIX
- PROPOSE_ENVELOPE_TRIAL
- ROLLBACK_TRIAL
- PROMOTE_ENVELOPE
- PROPOSE_MANUAL_RECALIBRATION

这些 action 是便利协议。

拥有 Bash 的 Agent 不被限制只能使用它们。

但 active trial 的 deterministic Evidence contract 仍不能被临时绕过。

## 5. 典型长期闭环

~~~
agent-context
  |
  +--> normal/STABLE
  |      |
  |      +--> NO_CHANGE
  |
  +--> Measurement Trust blocked
  |      |
  |      +--> gather/repair measurement
  |
  +--> UnexpectedPower
  |      |
  |      +--> investigate -> verify hypothesis
  |
  +--> optimization open
         |
         +--> inspect Scheduler candidate
                |
                +--> trial
                       |
                       +--> deterministic Evidence
                              |
                              +--> keep/reject/equivalent
~~~

## 6. Agent 不应该制造永久忙碌

长期目标是收敛。

如果：

- measurement noise 吃掉 candidate effect；
- 搜索邻域已经耗尽；
- dynamic controller 没净收益；
- monitoring overhead 太高；
- fixed-good 已经足够；

Agent 应建议停止、冻结或删除复杂度。

正常成熟状态是：

NO_CHANGE
