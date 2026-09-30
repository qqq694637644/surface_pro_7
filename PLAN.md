# Surface Pro 7 PowerLab — 持续场景化续航优化完整计划

> 状态：已审核并完成 v1.0 代码实现；等待 Surface Pro 7 真机硬件验证
> 实现范围：v0.3 测量可信度 → v1.0 持续采集、场景策略、MCP/LLM、受控 trial、回滚与长期知识
> 历史基线：v0.2 commit `b4b0759`；v1.0 实现以本计划的审查结论为基准
> 使用模式：个人设备、允许保存完整使用日志；本计划**不设计日志脱敏流程**。
> 核心约束：LLM 可以持续分析并提出/执行受控实验，但实时电源管理必须由本地确定性组件完成，不能依赖 LLM 在线响应。

> 验证说明：代码级单元/集成测试与非 Linux 降级 smoke test 可以在开发 Workspace 完成；BAT/RAPL/i915、ActivityWatch/awatcher、Power Options/PPD、root helper、suspend/resume 和真实续航曲线必须在目标 SP7 Linux 上完成校准后，才建议开启无人值守 trial/promote。

---

## 1. 最终目标

把 Surface Pro 7 做成一个会随着真实使用习惯长期改进的 Linux 设备：

1. 持续记录整机电池功耗、硬件状态、活动应用、后台任务和场景。
2. 自动把连续使用分成“可比较的场景/会话”，而不是只依赖用户手工写 `web`、`video` 等标签。
3. 每约 1 小时通过本机 Bash/MCP 给 LLM 一份聚合上下文。
4. LLM 基于最近数据、长期历史、已经失败的实验和当前软件版本决定：
   - 不动作；
   - 请求更多数据；
   - 调整已验证场景策略；
   - 提出一个新的受控实验。
5. 已经验证过的策略可以由本地策略引擎自动选择。
6. 未验证的新设置必须先成为 trial，经过数据质量检查、性能约束和回退保护后，才能晋升为正式策略。
7. 优化目标不是“最低瞬时瓦数”，而是：
   - 在阅读/交互场景降低平均整机功耗；
   - 在编译/导出等固定任务中降低完成任务的总能耗；
   - 在视频/会议场景维持体验要求后降低功耗；
   - 在所有场景中保持稳定、可恢复、可解释。

最终系统应做到：

```text
真实使用
   │
   ├─ 连续轻量遥测
   ├─ 应用/窗口/空闲/进程事件
   └─ 用户偶尔反馈
          │
          ▼
    场景识别与会话分段
          │
          ├─────────────► 本地实时策略引擎
          │                  │
          │                  └─ 只切换“已验证策略”
          │
          ▼
     SQLite / 历史摘要
          │
     每约 1 小时
          ▼
      Bash MCP / LLM
          │
          ├─ no-op
          ├─ 分析回归
          ├─ 新假设
          └─ 受控 trial
                 │
                 ▼
          apply → observe
                 │
          ┌──────┴──────┐
          │             │
       保留/晋升       回滚/拒绝
          │             │
          └──────┬──────┘
                 ▼
              长期知识
```

---

## 2. 一个关键设计决定：1 小时调用 LLM，但不能 1 小时才采一次数据

用户计划提供一个类似 Workspace 的 Linux Bash MCP，大约每 1 小时运行一次。

这个频率适合作为 **LLM 决策频率**，不适合作为 **原始采样频率**。

原因：

- 一个 8 分钟编译任务可能在两个 MCP 周期之间完整发生；
- 一个 20 分钟视频会议可能结束后才轮到 LLM；
- 应用切换、CPU burst、掉帧和温度变化都可能被 1 小时快照完全遗漏；
- 只看每小时一个点无法建立功耗和使用场景之间的关系。

因此体系分成两层：

### 2.1 本机 Collector：持续运行

建议初始默认：

- 电池/CPU/温度/频率/网络等连续指标：5–10 秒一次；
- 前台应用切换：事件驱动，发生变化立即记录；
- AFK / 解锁 / suspend / resume：事件驱动；
- 进程摘要：10–30 秒一次；
- 1 分钟生成 rollup；
- 每小时生成 context summary。

这些间隔都必须配置化，并在 SP7 真机上测试 collector 自己的功耗开销后再定最终值。

### 2.2 LLM Orchestrator：约每 1 小时运行

MCP 每小时不读取整份原始日志，而是读取：

- 最近 1 小时场景分布；
- 最近 1 小时有效会话；
- 当前系统/应用状态；
- 最近 trial 状态；
- 最近 N 次同场景实验；
- 长期最佳策略；
- 被拒绝/回滚的参数；
- 当前版本变化和异常；
- 必要时再按需查询原始片段。

**每小时运行不代表每小时必须改配置。**

大多数周期的正确输出应该允许是：

```text
NO_CHANGE
原因：没有足够的新证据 / 当前 trial 尚未结束 / 当前场景不适合实验
```

---

## 3. 当前 v0.2 的定位

### 3.1 保留

当前设计中以下思想继续保留：

- 整机电池功耗是主要目标；
- 配置/实验/结果都要可追溯；
- proposal 与实际 trial 建立明确关联；
- 人工体验反馈可以覆盖“纯瓦数结果”；
- 新实验尽量一次只改变一个主要变量；
- Git 保存紧凑的长期历史；
- 高风险设置不应无约束地自动修改；
- `ai-pack` 的“给 AI 一份结构化历史”思路继续保留。

### 3.2 必须重做或扩展

v0.2 不能直接作为持续优化系统，以下部分要重构：

1. `workload` 不能继续主要依赖用户手工字符串。
2. CSV 单次实验模型要升级为连续事件/时序数据库模型。
3. `evaluate_proposal()` 的证据完整性规则必须重写。
4. “上一条相同 workload”不能作为默认基线选择逻辑。
5. 需要真实应用/进程/任务识别。
6. 需要 systemd 常驻 collector。
7. 需要约 1 小时的 MCP/LLM 周期。
8. 需要 actuator adapter 和真实 read-back。
9. 需要 trial 自动回滚和 crash recovery。
10. 需要已验证策略库，而不只是 proposal 历史。
11. 需要软件/内核/电池健康度发生变化后的自动再验证。
12. 需要区分：
    - 平均功耗优化；
    - 固定工作量总能耗优化。
13. 需要处理未知场景、混合场景和场景置信度。
14. 需要长期拒绝记忆，不让旧失败因为历史截断而被 AI 反复尝试。

---

## 4. 社区项目复用策略

不重新造这些已有轮子。

### 4.1 ActivityWatch + awatcher：应用活动与空闲识别

用途：

- 当前活动应用；
- 活动窗口；
- AFK / active；
- 应用切换时间线。

Linux Wayland 上优先评估 awatcher。GNOME Wayland 需要对应的 GNOME active-window/Focused Window D-Bus 扩展路径。

PowerLab 不复制 ActivityWatch 的完整 UI；只消费它的事件/API，或者直接实现一个更薄的 adapter。

### 4.2 Power Options：优先执行器候选

用途：

- 多 profile；
- CPU/EPP；
- 屏幕；
- Wi-Fi/Bluetooth；
- ASPM/PCI/USB/GPU/RAPL 等高级选项。

原则：

- PowerLab 通过 adapter 控制它；
- 不把业务逻辑绑定到 Power Options 的文件格式；
- 同一时间只允许一个工具拥有同一组底层设置；
- 不同时运行 TLP、auto-cpufreq、dynamic-power-daemon 等去争抢同一参数。

### 4.3 dynamic-power-daemon：参考实时策略结构

不计划与 Power Options 同时作为主执行器运行。

重点借鉴：

- load-based profile；
- process rule；
- AC/battery 事件；
- D-Bus 控制；
- 用户 override；
- sysfs 能力动态发现。

### 4.4 PowerJoular：辅助解释，不作为整机目标

用途：

- CPU/RAPL；
- 特定进程/应用能耗；
- 辅助解释“为什么这一小时功耗高”。

主目标仍以：

```text
/sys/class/power_supply/BAT*/energy_now / power_now
```

以及真实电池能量差为准。

### 4.5 optim-agent：后期作为可插拔提案优化器

它适合“有明确参数空间 + 有 objective + 有历史 trial”的问题。

计划：

- v0.3–v0.5 不强依赖；
- 等测量和 trial 闭环稳定后再接入；
- LLM/optim-agent 只能在声明过的 bounded search space 内提参数；
- objective 由 PowerLab 自己计算，不能让 LLM 自评“我觉得变好了”。

---

## 5. 总体架构

计划拆成 7 个逻辑层。

### Layer A — Sensors

负责只读采集。

来源：

- battery sysfs；
- CPU cpufreq / intel_pstate；
- cpuidle；
- thermal；
- RAPL；
- GPU i915 信息（可用时）；
- backlight；
- Wi-Fi；
- network counters；
- `/proc` / psutil；
- ActivityWatch/awatcher；
- MPRIS；
- systemd/logind suspend/resume；
- 软件版本、kernel、firmware；
- Power Options 当前 profile；
- 可选 PowerJoular。

### Layer B — Event Store

SQLite 为主。

原始数据不再围绕“一个实验一个 CSV”，而是连续存储：

```text
samples
app_events
process_samples
power_events
system_events
sessions
contexts
profiles
profile_applications
trials
trial_results
user_feedback
llm_runs
decisions
system_versions
battery_health
```

CSV 作为导出格式，不作为核心数据库。

### Layer C — Context Engine

把时间线转换成“场景”。

示例：

```json
{
  "context": "coding_interactive",
  "foreground_app": "code",
  "background_classes": ["language_server"],
  "media": false,
  "user_active": true,
  "cpu_class": "bursty",
  "network_class": "light",
  "confidence": 0.86
}
```

必须允许：

- `unknown`
- `mixed`
- 低置信度

禁止为了“看起来智能”而强行分类。

### Layer D — Policy Engine

实时选择**已验证 profile**。

它不调用 LLM。

输入：

- context；
- battery level；
- AC/battery；
- thermal state；
- user override；
- 当前 trial；
- verified profile registry。

输出：

- 保持当前 profile；
- 切换另一个已验证 profile；
- 紧急回到 safe baseline。

### Layer E — Experiment Engine

负责 trial 生命周期：

```text
proposal
  ↓
preflight
  ↓
snapshot old state
  ↓
apply one primary change
  ↓
read-back
  ↓
settle/warm-up
  ↓
observe
  ↓
quality check
  ↓
evaluate
  ↓
promote / reject / rollback
```

### Layer F — LLM / Optimizer

约每小时通过 MCP 调用。

职责：

- 阅读 context pack；
- 找异常；
- 识别缺数据；
- 解释最近功耗变化；
- 选择“继续观察 / 新 trial / 回滚”；
- 提议 bounded parameter；
- 更新实验假设。

不负责：

- 毫秒/秒级调频；
- 任意 root shell 修改；
- 自己宣称 trial 成功。

### Layer G — Git Knowledge

Git 保存：

- 代码；
- schema；
- profile 定义；
- proposal；
- trial summary；
- accepted/rejected decision；
- 长期 context summary；
- 版本变更记录。

高频 raw samples 默认不进 Git，原因是体积，不是隐私。

---

## 6. 数据采集设计

### 6.1 必须持续采集的基础字段

电池：

- status；
- capacity；
- energy_now；
- power_now；
- voltage_now；
- energy_full；
- energy_full_design；
- cycle_count。

CPU：

- driver；
- governor；
- EPP；
- min/max/current freq；
- turbo；
- load；
- CPU usage；
- per-process CPU；
- PSI；
- cpuidle/C-state。

系统：

- RAM；
- swap；
- disk I/O；
- network RX/TX；
- thermal zones；
- brightness；
- AC/battery；
- suspend/resume；
- battery health。

版本：

- kernel；
- distro；
- Power Options version；
- browser/app versions（对重点应用）；
- linux-surface kernel version（若使用）；
- firmware/BIOS。

### 6.2 应用与场景字段

允许直接保存：

- app/process name；
- executable；
- window title；
- active time；
- AFK state；
- process tree；
- CPU；
- memory；
- I/O；
- network（能合理获得时）；
- MPRIS playback state；
- 用户自定义标签。

本项目是个人使用，不实现日志脱敏管线。

但仍要做到采集字段可配置，因为“没有必要记录”与“隐私”是两个不同问题。

### 6.3 采样策略

初始建议：

| 数据 | 初始频率 |
|---|---:|
| 电池/CPU/温度 | 5–10 s |
| 进程摘要 | 10–30 s |
| 前台窗口切换 | event |
| AFK | event |
| suspend/resume | event |
| 1 分钟 rollup | 60 s |
| LLM context | ~1 h |

实际频率要通过 collector-overhead benchmark 调整。

目标：collector 自身不能明显吞掉要节省的电。

---

## 7. 场景识别模型

不要只做：

```text
firefox => web
code => coding
```

需要至少两级。

### 7.1 Level 1：确定性特征

先不用 LLM：

- foreground app；
- AFK；
- 活动时间；
- CPU load；
- top processes；
- disk I/O；
- network rate；
- media playing；
- AC/battery；
- process tree。

由规则得到粗分类：

- idle
- reading
- web_interactive
- coding_interactive
- background_compute
- compile
- media_playback
- video_call
- file_transfer
- mixed
- unknown

### 7.2 Level 2：LLM 重新解释长期模式

LLM 每小时可以发现：

> 用户经常同时开 Code + 浏览器，但 CPU 高负载来自 rustc；应该将这类时间段归为 compile，而不是 coding_interactive。

它可以建议更新 context rules。

规则更新也要进入 Git/历史，而不是在 prompt 中临时存在。

### 7.3 Context Key

实验比较不再只用 `workload=web`。

建议 context key 包含：

```text
scene
foreground app class
background task class
brightness bucket
AC/battery
kernel generation
major app version bucket
battery health bucket
profile family
```

不要求所有维度完全相等，但比较器必须给出 comparability score。

---

## 8. 不同场景必须使用不同 objective

### 8.1 交互式任务

例：

- 浏览器；
- PDF；
- 编辑代码；
- Office；
- 终端。

主要 objective：

```text
平均整机 W
```

约束：

- 响应性；
- CPU/IO pressure；
- 温度；
- 用户 override；
- 稳定性。

### 8.2 固定工作量任务

例：

- 编译；
- 压缩；
- 导出；
- 转码；
- 批处理。

主要 objective：

```text
任务总能耗 Wh/J
```

同时记录：

```text
任务完成时间
```

不能因为 6 W 比 10 W 小，就认为 6 W 策略更省电。

### 8.3 视频播放

主要 objective：

- Wh/hour；
- 平均 W。

约束：

- dropped frames；
- 硬解是否生效；
- 播放分辨率/FPS；
- 音画稳定。

### 8.4 视频会议 / 远程桌面

默认采取保守策略。

优先约束：

- 网络稳定；
- 音频；
- 延迟；
- 视频稳定。

除非有足够历史，不主动做激进自动实验。

### 8.5 Unknown

使用 safe baseline。

LLM 可以要求更多数据，但不能因为没有分类就猜一个激进 profile。

---

## 9. “持续优化”算法

不计划一开始使用强化学习。

阶段策略：

### 阶段 1：规则 + A/B

最可靠。

- 一个 primary variable；
- 同场景；
- 多次 session；
- baseline/candidate；
- 明确 rollback。

### 阶段 2：按场景维护候选 profile

例如：

```text
reading:
  baseline
  low-power-a
  low-power-b

coding_interactive:
  balanced-a
  balanced-b

compile:
  performance-efficient-a
  capped-a
```

在已有安全 profile 中做少量探索。

### 阶段 3：Contextual bandit / bounded optimizer

当每个场景有足够历史后：

- 本地 policy engine 在已验证 profile 中选择；
- LLM 负责提出新搜索方向；
- optim-agent/Optuna 等可负责 bounded 参数探索；
- objective 仍由 PowerLab 计算。

LLM 不是 reward function。

---

## 10. 每小时 MCP/LLM 循环

建议 Bash MCP 暴露 PowerLab 的高层命令，而不是鼓励 LLM 每次手写任意 sysfs 命令。

未来接口示例：

```bash
sp7-powerlab service status
sp7-powerlab observe --since 1h
sp7-powerlab contexts --since 24h
sp7-powerlab current
sp7-powerlab trial status
sp7-powerlab knowledge pack --hours 24 --history 30
sp7-powerlab proposal validate proposal.json
sp7-powerlab trial start proposal.json
sp7-powerlab trial rollback
sp7-powerlab profile list
sp7-powerlab profile inspect NAME
sp7-powerlab actuator inspect
```

每小时逻辑：

### Step 1 — Health

检查：

- collector 是否存活；
- 数据是否连续；
- 当前是否 discharging；
- actuator 状态是否与记录一致；
- 有没有未结束 trial；
- 最近是否 suspend/reboot；
- 电池读数是否可用。

异常先修健康，不优化。

### Step 2 — Summarize

汇总：

- 最近 1h；
- 最近 6h；
- 最近 24h；
- 当前场景；
- 场景占比；
- 耗电最高场景；
- 相对历史回归；
- trial 状态。

### Step 3 — Decide

LLM 必须在有限动作中选：

```text
NO_CHANGE
NEED_MORE_DATA
PROPOSE_TRIAL
ROLLBACK_TRIAL
PROMOTE_PROFILE
INVESTIGATE_REGRESSION
UPDATE_CONTEXT_RULE
```

### Step 4 — Validate

所有输出进入 schema validator。

### Step 5 — Execute

只有允许的动作进入 Experiment/Policy Engine。

### Step 6 — Record

记录：

- 输入 pack hash；
- LLM 输出；
- 实际执行；
- read-back；
- 结果；
- 下次检查时间。

---

## 11. 自动修改的权限等级

个人项目可以比通用产品更自动，但仍不能把 root shell 当优化器。

### Class A — 已验证策略切换

允许自动：

- 在已验证 PowerLab profile 之间切换；
- 用户已经接受过的 context → profile 规则。

### Class B — 低风险 trial

成熟后可自动试验，例如：

- CPU EPP；
- CPU max freq；
- turbo policy；
- 有边界的 brightness policy；
- 已验证不会断设备的 radio 策略；
- browser power policy。

要求：

- snapshot；
- read-back；
- timeout；
- rollback；
- 一次一个 primary variable。

### Class C — 影响设备/连接的高级设置

默认需要更强 precondition，初期不自动：

- Wi-Fi driver reload；
- Bluetooth disable（正在使用设备时）；
- PCI runtime PM；
- USB autosuspend；
- audio powersave；
- ASPM；
- GPU 高级参数。

### Class D — 启动/休眠/内核风险

不进入无人值守自动 trial：

- kernel cmdline；
- suspend internals；
- I2C；
- firmware；
- linux-surface kernel 切换；
- 可能导致无法启动/无法唤醒的参数。

LLM 可以建议，但必须单独人工批准。

---

## 12. Trial 的完整状态机

```text
PROPOSED
   ↓
VALIDATED
   ↓
WAITING_FOR_CONTEXT
   ↓
SNAPSHOTTED
   ↓
APPLIED
   ↓
READBACK_OK
   ↓
SETTLING
   ↓
MEASURING
   ↓
EVALUATING
   ├─ INSUFFICIENT_DATA
   ├─ REJECTED → ROLLED_BACK
   ├─ FAILED → ROLLED_BACK
   └─ CANDIDATE_WINNER
             ↓
       REVALIDATION
             ↓
       PROMOTED / REJECTED
```

重要原则：

- `criteria-met` 不能因为缺少某些检查就成立；
- 核心指标缺失 → `INSUFFICIENT_DATA`；
- 采样过少 → `INSUFFICIENT_DATA`；
- 场景不匹配 → `INSUFFICIENT_DATA`；
- 充电/放电混杂 → 切段，不直接混平均；
- profile 改动后的 warm-up/settling 期不纳入正式统计；
- suspend gap 不算普通持续运行时间。

---

## 13. 数据质量规则

必须在 v0.3 优先实现。

### 13.1 有效放电样本

整机续航 objective 默认只接受：

```text
battery_status == Discharging
```

充电数据可以记录，但不能混进同一 battery-life objective。

### 13.2 时间加权

不要简单平均：

```python
mean(power_samples)
```

应按样本时间间隔计算：

```text
energy = Σ(power_i × Δt_i)
average_power = energy / valid_duration
```

### 13.3 数据间断

如果：

- suspend；
- collector crash；
- 长时间没有样本；

应切 session，而不是把两个点之间整段当成连续工作。

### 13.4 最低证据要求

初始默认建议：

交互场景：

- 至少 3 个有效 session；
- 或累计 >= 60–90 分钟有效数据；
- 候选与 baseline 都满足。

固定任务：

- 至少 3 次可识别完整任务；
- 比较总能耗 + duration。

这些阈值都配置化。

### 13.5 Comparability Score

比较两个样本组时评分：

- scene 一致；
- brightness；
- battery health；
- kernel；
- app major version；
- network intensity；
- background load；
- thermal state；
- profile 之外的配置。

低可比性 → LLM 只能形成 hypothesis，不能宣布优化成功。

---

## 14. 回滚设计

自动优化能否放心运行，主要取决于回滚。

每次 trial 前必须保存：

```text
profile
EPP
freq limits
turbo
brightness policy
radio state
relevant sysfs values
actuator config hash
```

### 自动回滚条件

例：

- actuator read-back 不一致；
- collector 丢失；
- 温度超过约束；
- 场景意外变化；
- 系统负载严重异常；
- 用户手动 override；
- 应用明显卡住的可检测信号；
- trial 超时；
- reboot 后发现未清理 trial；
- LLM 下一轮发现 trial 不安全。

### Crash recovery

systemd 启动时：

```text
发现 trial_lock
    ↓
读取 pre-trial snapshot
    ↓
恢复 safe baseline
    ↓
记录 crash_recovery
```

除非明确证明 trial profile 可以跨 reboot。

---

## 15. 用户反馈

虽然目标是尽量自动，不要求用户每轮手工评分。

反馈分两类：

### 显式

用户偶尔提供：

- accepted / rejected；
- responsiveness 1–5；
- stability；
- suspend；
- notes。

### 隐式

以后可以记录：

- 用户立刻手动切回别的 profile；
- 用户频繁 override；
- trial 后应用被强杀；
- trial 后机器 reboot；
- trial 期间明显出现 pressure/throttle。

隐式信号只能作为证据，不能过度解释。

---

## 16. 长期知识模型

不要只把最近 12 个实验给 AI。

长期知识拆成：

### 16.1 Immutable Trial History

每次 trial 永久保存紧凑结果。

### 16.2 Rejection Memory

按：

```text
context + parameter + value/range + reason
```

保存。

避免半年后再次提同一个坏参数。

### 16.3 Verified Policy Registry

例如：

```yaml
coding_interactive:
  preferred: coding-balanced-v4
  alternatives:
    - coding-balanced-v3
  evidence:
    sessions: 18
    valid_hours: 12.4
  last_validated_kernel: 6.x
```

### 16.4 Regression Baselines

每个主要场景保存长期基线。

软件/内核升级后可自动判断：

> 当前 reading 比过去同场景高 0.8 W。

### 16.5 Hardware Drift

记录：

- battery health；
- cycles；
- full capacity；
- firmware。

电池老化后不要把“续航小时减少”误判为 profile 变差。

---

## 17. 软件升级和概念漂移

长期优化必须承认最佳策略会过期。

以下事件触发 policy 降低置信度：

- kernel update；
- linux-surface update；
- major browser update；
- desktop/Wayland update；
- Power Options update；
- firmware update；
- 电池更换；
- 电池 health 大幅变化；
- ActivityWatch/awatcher 版本变化。

处理：

```text
verified → needs_revalidation
```

而不是永久认为旧结果正确。

---

## 18. 本地 MCP 权限设计

用户会提供一个 Linux Bash MCP。

建议 MCP 本身可以执行 Bash，但给 LLM 的“正常路径”仍是 PowerLab 高层命令。

理由不是多用户安全，而是防止优化系统自己破坏实验可重复性。

原则：

1. 查询命令自由；
2. 普通 trial 通过 PowerLab actuator API；
3. root helper 只接受声明过的参数；
4. 任意 shell 作为人工调试 escape hatch；
5. LLM 正常循环不直接：
   - `echo ... > /sys/...`
   - 改 grub；
   - 安装内核；
   - 重载 Wi-Fi driver；
   - 删除历史数据库。

这样每个动作才有：

- before；
- after；
- reason；
- rollback；
- trial ID。

---

## 19. 目录规划

建议逐步迁移为：

```text
surface_pro_7/
├── PLAN.md
├── README.md
├── pyproject.toml
├── config/
│   ├── powerlab.toml
│   ├── contexts.yaml
│   └── profiles/
├── schemas/
│   ├── proposal-v2.schema.json
│   ├── context-v1.schema.json
│   └── trial-v1.schema.json
├── src/sp7_powerlab/
│   ├── cli.py
│   ├── metrics.py
│   ├── storage.py
│   ├── collector.py
│   ├── activity.py
│   ├── processes.py
│   ├── context.py
│   ├── quality.py
│   ├── sessions.py
│   ├── policy.py
│   ├── experiments.py
│   ├── objectives.py
│   ├── knowledge.py
│   ├── llm_pack.py
│   ├── optimizer.py
│   ├── safety.py
│   └── actuators/
│       ├── base.py
│       ├── power_options.py
│       ├── power_profiles_daemon.py
│       └── mock.py
├── systemd/
│   ├── sp7-powerlab-collector.service
│   ├── sp7-powerlab-hourly.service
│   └── sp7-powerlab-hourly.timer
├── tests/
├── history/
├── proposals/
└── runtime/     # gitignored
    ├── powerlab.sqlite3
    ├── trial.lock
    └── exports/
```

---

## 20. SQLite 初步 schema

### samples

高频系统样本。

```text
ts
battery_status
power_w
energy_wh
battery_pct
cpu_usage
load
freq
epp
temp
brightness
wifi_rx
wifi_tx
...```

### app_events

```text
start_ts
end_ts
app
executable
window_title
active
afk
```

### process_samples

```text
ts
pid
start_time
exe
cpu
rss
read_bytes
write_bytes
```

不能只用 PID 作为永久身份，必须结合进程 start time，避免 PID reuse。

### sessions

```text
session_id
start
end
context_id
valid_duration
energy_wh
avg_power_w
quality
```

### contexts

```text
context_id
scene
features_json
confidence
rule_version
```

### profiles

```text
profile_id
backend
content_hash
parameters_json
status
```

状态：

- experimental
- verified
- deprecated
- blocked

### trials

```text
trial_id
proposal_id
context
baseline_profile
candidate_profile
state
start/end
```

### decisions

保存：

- LLM；
- rule engine；
- user；
- actual action。

---

## 21. 配置文件初稿

未来 `config/powerlab.toml` 大致：

```toml
[collector]
system_interval_seconds = 10
process_interval_seconds = 20
rollup_seconds = 60

[llm]
analysis_interval_minutes = 60
history_days = 30
recent_hours = 24

[experiment]
settle_seconds = 120
interactive_min_sessions = 3
interactive_min_valid_minutes = 60
job_min_repetitions = 3
max_one_primary_change = true

[automation]
auto_switch_verified_profiles = true
auto_run_low_risk_trials = false
auto_promote_profiles = false

[storage]
raw_retention_days = 30
keep_rollups_forever = true

[activity]
store_window_title = true
store_executable = true
store_process_tree = true
```

在成熟后再逐步开启：

```toml
auto_run_low_risk_trials = true
```

---

## 22. 计划中的版本阶段

# v0.3 — 测量可信度修复

**优先级最高。**

实现：

- 时间加权能耗；
- 只使用有效 discharging segment；
- suspend/gap 分段；
- 数据质量评分；
- insufficient-data；
- proposal hash 校验；
- baseline comparability；
- battery health/version 进入实验上下文；
- 修复当前可能在证据不足时 `criteria-met` 的逻辑。

验收：

- 缺少功耗不能通过；
- 2 个样本不能被当成强证据；
- 充电/放电混杂不会直接平均；
- suspend gap 不会扩大有效时长；
- candidate 与不可比 baseline 不会被声明为 winner。

# v0.4 — 常驻 Collector + SQLite

实现：

- systemd service；
- SQLite；
- continuous telemetry；
- rollup；
- crash-safe write；
- retention；
- collector overhead benchmark。

验收：

- 连续运行 24h；
- suspend/resume 后恢复；
- service crash 自动恢复；
- 数据无明显断层；
- collector 开销在可接受范围。

# v0.5 — 全应用场景识别

实现：

- ActivityWatch/awatcher adapter；
- foreground app；
- AFK；
- process telemetry；
- MPRIS；
- scene rules；
- unknown/mixed；
- sessions。

验收：

至少能正确区分：

- idle；
- browser reading；
- interactive coding；
- compile/background compute；
- video playback；
- mixed。

# v0.6 — Profile/Actuator 层

实现：

- actuator interface；
- Power Options adapter；
- current-state read-back；
- verified profile registry；
- safe baseline；
- manual override；
- rollback；
- trial lock。

验收：

- profile 切换可以确认真实生效；
- apply 失败自动恢复；
- crash/reboot 残留 trial 能恢复 safe baseline。

# v0.7 — Hourly MCP/LLM Orchestrator

实现：

- `knowledge pack`；
- hourly systemd timer / MCP entrypoint；
- structured action schema；
- LLM run log；
- no-op / need-data / propose-trial；
- long-term rejection memory。

验收：

- LLM 断网不会影响已验证实时策略；
- 每小时运行不强制修改；
- invalid response 不执行；
- 旧拒绝不会因为 context window 截断而丢失。

# v0.8 — 自动低风险实验

初始只开放：

- EPP；
- max frequency；
- turbo；
- 少量 profile 级参数。

实现：

- waiting-for-context；
- settle；
- measure；
- automatic rollback；
- repeated validation；
- promote candidate。

默认仍可配置为：

```text
auto_run = false
```

先在真机观测一段时间后再打开。

# v0.9 — 多场景优化

实现：

- interactive objective；
- job energy objective；
- video objective；
- contextual candidate profiles；
- version drift；
- regression detection。

# v1.0 — 长期自治

目标：

- collector 7×24；
- 本地 verified-policy 自动切换；
- 每小时 LLM 慢速分析；
- bounded 自动实验；
- 自动回滚；
- profile 晋升/降级；
- 软件升级重新验证；
- 长期趋势报告；
- 用户只在高风险参数或明显体验问题时介入。

---

## 23. 开发顺序禁止事项

在以下完成前，不做“看起来很智能”的自动化：

### 不先做

- 全自动 kernel 参数实验；
- 自动 linux-surface kernel 切换；
- 自动 PCI/USB 大规模 powertop tune；
- 强化学习；
- 每小时强制改一个参数；
- 同时运行多个 power manager；
- 根据单次平均 W 自动晋升 profile；
- 直接让 LLM 获取无审计 root sysfs 修改路径。

### 先做

1. 测量正确；
2. 场景正确；
3. 回滚正确；
4. 才开始自动实验。

---

## 24. 验证策略

测试分四级。

### Unit

- energy integration；
- gap detection；
- context rules；
- proposal validation；
- rollback state machine；
- comparability。

### Replay

把过去日志重放，确认：

- 同样输入得到相同 session；
- LLM/optimizer 不参与实时 deterministic 路径。

### Simulation

mock battery/sysfs/ActivityWatch/actuator。

模拟：

- 充电器突然插入；
- suspend；
- collector crash；
- profile apply 失败；
- 温度过高；
- 数据缺失；
- app 快速切换。

### SP7 实机

每个新自动权限必须经过真实 Surface Pro 7 验证。

---

## 25. 成功指标

系统本身：

- 无效数据不会被判定为 winner；
- 所有 profile change 有 before/after/read-back；
- 所有 trial 有 rollback；
- collector 异常不改变 profile；
- LLM 异常不改变 profile；
- 未知场景回 safe baseline。

优化效果：

按场景看，不只看总平均。

例：

```text
reading
  baseline 5.8 W
  verified profile 5.1 W

coding_interactive
  baseline 6.4 W
  verified 5.9 W
  responsiveness 无可感知退化

compile
  baseline 0.72 Wh/task, 4m20s
  verified 0.65 Wh/task, 4m28s
```

长期目标：

- 同类使用场景能耗下降；
- 更新导致的回归能自动发现；
- 用户实际续航改善；
- AI 不重复提出已经被证明失败的策略；
- 配置随着使用习惯变化继续演进。

---

## 26. 对“每小时持续优化”的最终定义

项目中“持续优化”定义为：

> **每小时持续观察和重新判断，但只在证据充分且当前环境适合时改变策略。**

不是：

> 每小时必须修改一次机器。

理想长期行为可能是：

```text
09:00 NO_CHANGE
10:00 NO_CHANGE
11:00 发现 coding_interactive 比长期 baseline 高 0.5W
12:00 INVESTIGATE_REGRESSION
13:00 发现 Code 更新 + language server 活动变化
14:00 NEED_MORE_DATA
15:00 PROPOSE_TRIAL cpu.epp balance_power → power
16:00 WAITING_FOR_CONTEXT
17:00 自动遇到合适 coding session，启动 trial
18:00 收集不足，继续
19:00 CANDIDATE_WINNER
20:00 再验证
第二天 PROMOTE_PROFILE
```

这种“慢速、长期、证据驱动”的优化，才适合个人日常电脑。

---

## 27. 审查时需要你重点决定的几个问题

### A. LLM 调用频率

当前计划默认：

```text
约 60 分钟一次
```

Collector 独立持续运行。

### B. 自动实验权限

建议第一阶段：

```text
自动切换 verified profile：开
自动启动新的低风险 trial：关
自动 promote：关
```

积累一段真实数据后再逐步打开。

### C. ActivityWatch

建议：

```text
ActivityWatch server + awatcher
```

特别针对 Wayland/GNOME 验证实际可用性。

### D. Actuator

当前优先候选：

```text
Power Options
```

但保留 adapter 抽象，不锁死。

### E. LLM optimizer

先使用当前 AI proposal 逻辑。

后期对比：

```text
普通 LLM proposal
vs
optim-agent
vs
非 LLM bounded optimizer
```

谁在真实 SP7 数据上更有效就用谁，而不是预设 LLM 一定最好。

---

## 28. 下一步

本计划通过审查后，不直接进入“自动 AI 调参”。

下一开发任务应严格按顺序：

1. **v0.3 测量可信度修复**
2. **v0.4 Continuous Collector**
3. **v0.5 App/Context Engine**
4. 真机运行至少一段只读观察
5. 再实现 actuator 和自动 trial

这是当前认为风险最低、最不容易做出“看起来智能但实际学错东西”的路线。
