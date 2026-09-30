# Surface Pro 7 PowerLab v2 — 热预算与性能需求驱动的破坏式重构计划

> 状态：v2 设计审查稿
>
> 目标硬件：**Microsoft Surface Pro 7 / Intel Core i5-1035G4**
>
> 设备角色：**低功耗个人交互终端**。浏览、文档、代码编辑、终端、远程开发、媒体为主要用途；持续高负载计算默认放在远程服务器，本机持续高 CPU/GPU 负载视为异常或短时 burst。
>
> 重构原则：**破坏式更新，不保留 v1 兼容层，不做旧数据库迁移，不保留旧 CLI alias，不同时维护 scene-based 与 demand-based 两套路由。**
>
> 核心目标：在保证前台交互和媒体连续性的前提下，减少整机电池功耗，主动避免 Surface Pro 7 无风扇机身的长期 heat soak 和 emergency throttle。

---

## 1. 为什么 v1 需要整体推倒重做

v1 的核心抽象是：

```text
foreground app / processes
        ↓
scene = reading / coding / web / compile / mixed ...
        ↓
scene -> verified profile
```

这个抽象存在三个根本问题。

### 1.1 “用户到底在做什么”并不是电源控制真正需要的问题

真实使用经常同时存在：

- 编辑器；
- 浏览器；
- 远程 SSH / Remote IDE；
- 音乐；
- 同步；
- language server；
- 后台短任务。

强制回答“这是 coding 还是 web 还是 mixed”并不能直接告诉 CPU 应该需要多少性能。

电源控制真正需要知道的是：

```text
用户当前是否活跃？
前台是否对延迟敏感？
本地计算强度有多高？
有没有持续媒体？
网络吞吐是否重要？
本机是否正在积热？
当前是否已经发生 thermal pressure？
```

因此 v2 不再把“语义场景名称”作为实时控制主键。

### 1.2 Surface Pro 7 i5 的热行为必须成为一等公民

Surface Pro 7 i5 为无风扇设计。Linux 社区已经长期报告：

```text
持续高负载
→ 机身 heat soak
→ 温度继续上升
→ 频率突然跌到极低水平
→ 冷却后恢复
→ 再次升温
```

这意味着仅用“当前 CPU load / 当前温度”做策略不够。

v2 必须显式建模：

- 当前温度；
- 温度变化速度 dT/dt；
- 10s / 60s / 300s RAPL package power；
- 整机电池功耗；
- CPU 频率是否异常塌陷；
- thermal throttle 计数（硬件暴露时）；
- 持续高功耗时间；
- thermal headroom。

### 1.3 本机不是高性能工作站

用户已明确：

> 长时间高负载任务会放到远程服务器。

因此 v2 不再围绕本地 compile/render/transcode 做大量特殊优化。

对于这台设备：

```text
持续本地高负载
```

优先解释为：

```text
LOCAL_COMPUTE_PRESSURE / 可能异常 / 需要热保护
```

而不是“进入性能模式”。

---

## 2. v2 的总原则

### 2.1 实时频率由 Linux 和 Intel HWP 决定

PowerLab 不实现自己的毫秒级 DVFS。

职责：

```text
Linux scheduler
+ intel_pstate
+ Intel HWP
```

负责实际 P-state / 频率选择。

PowerLab 只提供较慢的“性能意图”：

- EPP；
- max_perf_pct；
- turbo 是否允许；
- 已验证的设备节能设置。

### 2.2 热安全由 thermald 负责

thermald 作为独立安全层长期运行。

PowerLab：

- 不关闭 thermald；
- 不绕过 thermald；
- 不自动提高 thermald 的热限制；
- 不让 LLM 直接改 thermald trip point；
- 把 thermald / firmware 的限制视为高于 PowerLab 的安全覆盖。

### 2.3 PowerLab 只做秒级/分钟级策略

PowerLab 本地 daemon 负责：

```text
需求状态
+
热状态
+
电池状态
+
人工 override
        ↓
选择一个已验证 operating envelope
```

默认控制周期约 5–10 秒。

### 2.4 LLM 只做慢速研究

LLM 大约每小时运行一次。

允许：

- 分析回归；
- 查找异常进程；
- 判断哪个 envelope 值得实验；
- 生成受控 trial；
- 分析长期热行为；
- 建议人工修改 thermal calibration。

禁止：

- 实时调频；
- 实时分类工作内容；
- 直接 root shell；
- 自动提高安全温度；
- 自己宣布实验成功。

### 2.5 v2.0 不使用机器学习参与控制

v2.0 明确采用：

```text
deterministic signals
+ state machine
+ hysteresis
+ A/B experiment
```

不使用：

- 深度学习实时分类；
- contextual bandit 自动探索；
- 在线神经网络；
- LLM 实时控制。

以后如果数据证明规则不足，ML 只能先进入 **shadow mode**：

```text
预测
但不控制
```

经过离线验证后再单独审查是否允许参与决策。

---

## 3. 新总体架构

```text
                          Surface Pro 7
                                │
         ┌──────────────────────┴──────────────────────┐
         │                                             │
         ▼                                             ▼
   Demand Sensors                                Thermal Sensors
         │                                             │
   user active                                   package temp
   local CPU pressure                            dT/dt
   media playing                                 RAPL package power
   remote-session hint                           battery power
   network activity                              sustained power windows
   PSI / load                                    throttle indicators
         │                                             │
         ▼                                             ▼
   Demand Observer                               Thermal Observer
         │                                             │
         └──────────────────────┬──────────────────────┘
                                ▼
                         Local Controller
                                │
                     verified operating envelope
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
            intel_pstate/HWP               thermald
            performance intent           thermal safety
                  │                           │
                  └─────────────┬─────────────┘
                                ▼
                              CPU
                                │
                                ▼
                   battery / temperature result
                                │
                                ▼
                         SQLite history
                                │
                           ~1 hour
                                ▼
                              LLM
                                │
                         controlled trials
                                │
                        promote / reject
```

---

## 4. 删除 v1 的核心抽象

以下设计在 v2 中直接删除。

### 4.1 删除 scene 作为实时主键

删除：

```text
idle
reading
web_interactive
coding_interactive
office_interactive
remote_interactive
compile
background_compute
media_playback
video_call
file_transfer
mixed
unknown
```

实时控制不再依赖这些字符串。

可以保留少量“诊断标签”，但仅用于：

- UI；
- 报告；
- LLM 阅读；
- 用户理解。

它们不能直接决定 profile。

### 4.2 删除 ContextEngine v1

删除当前：

```text
src/sp7_powerlab/context.py
config/contexts.toml
schemas/context-v1.schema.json
```

不保留兼容 adapter。

### 4.3 删除 scene -> profile policy

删除：

```text
config/policy.toml
context_policies
UPDATE_CONTEXT_RULE
scene-specific profile promotion
```

不再存在：

```text
coding_interactive -> profile A
reading -> profile B
```

### 4.4 删除本地 heavy-job 优化主线

删除本地固定任务作为核心架构：

- compile 专用场景；
- job_min_repetitions；
- task-run 作为主要优化流程；
- compile/transcode 的自动 profile 学习；
- energy-per-local-heavy-task 的特殊状态机。

如果以后确实需要，可以重新作为插件设计；v2 核心不保留。

### 4.5 ActivityWatch 降级为可选诊断输入

ActivityWatch / awatcher 不再是实时控制必需依赖。

保留价值：

- 哪个程序导致异常；
- 用户活动统计；
- LLM 解释历史；
- 前台 app attribution。

实时控制必须在 ActivityWatch 不运行时仍能完成核心功能。

这是 v2 的设计要求，不是 v1 兼容 fallback。

---

## 5. 新硬件契约：只支持这一台 SP7

v2 不做通用 Linux laptop 框架。

启动写入功能前必须验证：

```text
DMI product == Surface Pro 7
CPU == Intel Core i5-1035G4
intel_pstate available
HWP available / active
battery sysfs available
thermal sensor available
RAPL package domain available
systemd available
```

如果硬件契约不满足：

```text
collector 可以输出诊断
controller 不允许写任何电源参数
```

不增加 AMD、其他 Surface、其他 Intel CPU 的兼容分支。

---

## 6. 两个实时状态：Demand State + Thermal State

v2 不再用一个 scene 描述整台机器。

实时状态拆成两个正交部分。

---

## 7. Demand State：机器当前需要什么

新的需求向量：

```json
{
  "user_active": true,
  "foreground_latency_need": 0.82,
  "local_compute_pressure": 0.18,
  "media_continuity": 1.0,
  "remote_interactive": 0.75,
  "network_intensity": 0.31,
  "io_pressure": 0.05
}
```

这些值不代表“用户在做什么职业活动”。

只代表：

> 当前机器需要什么。

### 7.1 user_active

优先来源：

- systemd-logind IdleHint；
- 桌面 idle API；
- 可选 ActivityWatch AFK。

输出：

```text
ACTIVE
IDLE
```

### 7.2 foreground_latency_need

初始确定性规则：

- 用户正在连续输入/切窗；
- CPU PSI；
- I/O PSI；
- runnable load；
- 短 burst 活跃度。

不需要知道前台是 Firefox 还是 VS Code 才能成立。

### 7.3 local_compute_pressure

使用滚动窗口：

```text
CPU usage EWMA
load
CPU PSI
RAPL package power
top process CPU
```

建议时间窗口：

- 10 秒；
- 60 秒；
- 300 秒。

输出：

```text
LOW
MEDIUM
SUSTAINED
```

由于目标设备不承担持续重计算：

```text
SUSTAINED local compute
```

默认触发异常记录和更保守的 thermal policy，而不是 performance mode。

### 7.4 media_continuity

来源：

- MPRIS；
- playerctl；
- 可选浏览器媒体 telemetry。

媒体播放是少数仍值得显式识别的需求，因为：

```text
不能为了省电导致播放中断
```

### 7.5 remote_interactive

只作为 hint。

来源可包括：

- SSH / mosh；
- Remote Desktop；
- VS Code remote helper；
- Remmina / xfreerdp；
- 网络流量 + 低本地 compute。

它不必 100% 准确。

远程工作的主要需求仍由：

```text
foreground latency + network continuity + low local compute
```

决定。

### 7.6 network_intensity

通过网卡计数器的差分计算。

只区分：

```text
LOW
INTERACTIVE
TRANSFER
```

不尝试猜具体应用协议。

---

## 8. Thermal State：Surface 的热预算

这是 v2 最重要的新模块。

### 8.1 输入

必须连续记录：

```text
package/core temperature
temperature slope dT/dt
RAPL package energy
RAPL rolling power:
    10s
    60s
    300s
whole-device battery power
CPU average frequency
intel_pstate max_perf_pct
EPP
turbo state
CPU utilization
thermal throttle count（可用时）
frequency collapse event
suspend/resume
ambient proxy（没有真实环境温度传感器时留空）
```

### 8.2 热状态

初始状态机：

```text
COOL
WARMING
HEAT_SOAKED
THERMAL_PRESSURE
THROTTLING
```

#### COOL

特征：

- 温度低；
- dT/dt 低；
- 60s/300s package power 低；
- 无 throttle。

允许正常短 burst。

#### WARMING

特征：

- 温度持续上升；
- package power 连续高于轻负载基线；
- 尚未进入高温。

开始收紧 sustained performance。

#### HEAT_SOAKED

特征：

- 机身已经积累明显热量；
- 温度不一定非常高，但即使负载下降也降温缓慢；
- 300s package energy 高。

此状态下不允许重新激进 boost。

#### THERMAL_PRESSURE

特征：

- 接近经真机校准的软温控阈值；
- dT/dt 仍为正；
- thermald 已开始限制；
- 或频率开始明显受热约束。

PowerLab 强制 thermal-safe envelope。

#### THROTTLING

特征：

- thermal throttle counter 增长；
- 频率异常塌陷；
- firmware / thermald 明显 clamp；
- 或达到危险阈值。

PowerLab：

- 立即停止 trial；
- 切 thermal-safe；
- 记录完整 incident；
- 在温度和 heat score 恢复前不退出。

### 8.3 不能硬编码 Reddit 的温度

社区给出的 65°C / 70°C 等经验只作为研究参考。

最终参数必须在本机校准后写入：

```text
config/machine.toml
```

系统在没有 machine calibration 时：

```text
允许只读采集
禁止自动 trial
禁止自动 envelope promotion
```

---

## 9. Thermal Pressure Score

除离散状态外，维护连续分值：

```text
thermal_pressure = 0.0 ... 1.0
```

第一版不用机器学习。

由以下归一化量确定：

```text
temperature level
temperature slope
60s package power
300s package energy
cool-down rate
throttle evidence
```

例如概念上：

```text
pressure =
    temp_component
  + slope_component
  + sustained_power_component
  + throttle_component
```

实际权重通过真机 replay 和校准确定。

用途：

- 避免状态阈值来回抖动；
- 作为 experiment 的约束；
- 识别“温度还没高，但热量正在快速累积”的情况。

---

## 10. Operating Envelope 取代 Scene Profile

v2 不再有几十个按 app/scene 命名的 profile。

只维护少量**性能/热包络**。

初始候选：

```text
ECO_IDLE
INTERACTIVE_EFFICIENT
REMOTE_EFFICIENT
MEDIA_EFFICIENT
THERMAL_SAFE
```

### 10.1 ECO_IDLE

目标：

- 人不操作时最低功耗；
- 尽量进入深 C-state；
- 不追求瞬时响应。

### 10.2 INTERACTIVE_EFFICIENT

默认主 envelope。

目标：

- 点击/输入/滚动有 burst 性能；
- burst 结束迅速回落；
- 不允许长时间高 package power。

适合：

- 网页；
- 文档；
-代码编辑；
- 终端；
- 普通桌面操作。

不需要识别这些具体 app。

### 10.3 REMOTE_EFFICIENT

目标：

- 本地 CPU 保持较低；
- 前台延迟优先；
- 网络连续；
- 不因本地后台任务抢走热预算。

### 10.4 MEDIA_EFFICIENT

目标：

- 媒体连续；
- 硬件解码正常；
- CPU 尽可能 idle；
- 避免无意义 turbo。

### 10.5 THERMAL_SAFE

最高优先级软策略。

目标：

- 降低持续 package power；
- 禁止 aggressive boost；
- 等待 thermal pressure 回落。

它不是“省电 profile”，而是：

> heat soak 恢复 envelope。

---

## 11. SP7 专用执行器，不再依赖通用 Power Options 主控制

v2 删除“自动探测 Power Options / PPD / sysfs 三选一”的通用执行器模型。

原因：

- 目标硬件固定；
- 需要明确控制 ownership；
- 通用 daemon 很容易与 thermald / HWP 形成不可见竞态；
- 用户明确接受破坏式、单机专用设计。

新的动态 CPU actuator 直接针对：

```text
intel_pstate + HWP
```

初始允许的控制面只包括经过审查的少量参数：

```text
energy_performance_preference
intel_pstate/max_perf_pct
turbo / no_turbo（是否启用需要真机确认与 ownership 审查）
hwp_dynamic_boost（若本机暴露且验证）
```

不直接做：

- 任意 sysfs 写入；
- 通用 PCI 树扫描写入；
- 任意 USB autosuspend；
- 任意 ASPM；
- 任意 kernel cmdline；
- 任意 RAPL PL1/PL2 自动写入。

RAPL 限制优先交给 thermald。

---

## 12. 控制 ownership

必须显式定义谁拥有哪个旋钮。

| 层 | Owner | 职责 |
|---|---|---|
| 实际 P-state | Intel HWP | 微秒/毫秒级硬件频率选择 |
| scheduler utilization | Linux kernel | 任务调度与利用率 |
| performance intent | PowerLab | EPP / max_perf_pct 等慢速意图 |
| thermal safety | thermald | trip / RAPL / cooling action |
| firmware emergency | Surface firmware / CPU | 最后安全保护 |
| long-term experiment | PowerLab + LLM | envelope 参数研究 |

优先级：

```text
firmware emergency
    >
thermald safety
    >
PowerLab envelope request
    >
HWP normal selection
```

如果 thermald 覆盖了 PowerLab 请求：

```text
这是安全 override
不是“写入冲突”
```

PowerLab 必须识别并记录：

```text
thermal_override_active = true
```

---

## 13. 本地控制状态机

每 5–10 秒运行一次。

概念逻辑：

```text
if THROTTLING:
    THERMAL_SAFE

elif THERMAL_PRESSURE:
    THERMAL_SAFE

elif HEAT_SOAKED:
    conservative envelope
    no new burst promotion

elif user_idle:
    ECO_IDLE

elif media_continuity high:
    MEDIA_EFFICIENT

elif remote_interactive high and local_compute low:
    REMOTE_EFFICIENT

else:
    INTERACTIVE_EFFICIENT
```

再叠加：

- minimum dwell time；
- hysteresis；
- manual override；
- battery low guard；
- trial lock。

禁止：

```text
每个 5 秒采样都切一次 envelope
```

---

## 14. sustained local compute 是异常状态

由于用户把重负载放到远程服务器，本机出现：

```text
high CPU
+
high RAPL package power
+
持续超过一定窗口
```

时，v2 进入：

```text
LOCAL_COMPUTE_PRESSURE
```

行为：

1. 记录 top processes；
2. 记录 process tree；
3. 不切 performance envelope；
4. 如果 thermal pressure 上升，提前进入 THERMAL_SAFE；
5. 下一次 LLM 分析时解释：
   - 浏览器 runaway tab；
   - language server；
   - update/indexer；
   - 本地意外编译；
   - 其他持续 CPU 消耗。

这比优化本地大型编译更符合该设备定位。

---

## 15. 数据库 v2：全新 schema，不迁移 v1

版本直接升级：

```text
2.0.0
```

v2 **不读取 v1 SQLite schema**。

安装/首次运行时如果检测到旧：

```text
runtime/powerlab.sqlite3
```

行为：

```text
FAIL FAST
提示用户删除 runtime/
```

不自动 migration。

原因：

- v1 scene/context 表的语义已经失效；
- 自动迁移会制造假兼容；
- 用户尚未在正式 SP7 上产生必须保存的长期 v1 数据。

### 15.1 新表

建议：

```text
samples
thermal_windows
demand_windows
controller_state
control_actions
thermal_incidents
envelopes
envelope_validations
trials
trial_results
user_feedback
process_attribution
system_fingerprints
battery_health
llm_runs
llm_decisions
rejections
```

删除：

```text
contexts
context_policies
scene-based sessions
task_runs
scene profile applications
```

### 15.2 raw sample

核心字段：

```text
ts
battery_status
battery_pct
battery_power_w
battery_energy_wh

package_temp_c
temp_slope_c_per_min

rapl_package_energy_uj
rapl_power_10s_w
rapl_power_60s_w
rapl_power_300s_w

cpu_usage
cpu_psi
io_psi
load
avg_freq
max_freq
epp
max_perf_pct
turbo

user_active
media_playing
network_rx_rate
network_tx_rate

thermal_pressure
thermal_state
demand_state
current_envelope
thermal_override_active
```

---

## 16. Activity / app 信息只用于 attribution

仍可收集：

- foreground app；
- executable；
- top processes；
- process tree；
- window title；
- ActivityWatch timeline。

但是这些字段不再参与：

```text
app name -> profile
```

它们主要回答：

> 为什么今天这一段功耗异常？

例如：

```text
13:22–13:40
local_compute_pressure=SUSTAINED
top process=firefox content process
battery=10.2W
thermal state=WARMING
```

这比“scene=web_interactive”有用得多。

---

## 17. thermald 设计

v2 将 thermald 作为正式依赖，不再只是“可选建议”。

### 17.1 Stage 0 先验证 stock thermald

真机先检查：

```bash
systemctl status thermald
thermald --version
journalctl -u thermald
```

验证：

- Surface thermal zones；
- RAPL powercap；
- intel_pstate；
- thermald adaptive engine 是否正常工作。

### 17.2 自定义 thermal 配置必须人工审核

如果 stock thermald 无法避免 SP7 的 emergency throttle：

可以设计 SP7 专用 thermald 配置。

但：

```text
LLM 不能自动修改 thermal-conf.xml
```

thermal limit 属于 Class D 安全配置：

- 人工审核；
- 真机短时间验证；
- Git 记录；
- 可恢复。

### 17.3 不追求靠近 100°C

CPU Tjunction 并不是目标工作温度。

v2 的目标是：

> 在 heat soak 导致 firmware emergency throttle 之前，平滑降低持续 package power。

---

## 18. 真机 thermal calibration

v2 自动控制启用前必须完成 calibration。

输出：

```text
config/machine.toml
```

包含：

- DMI fingerprint；
- CPU model；
- kernel；
- BIOS；
- thermald version；
- RAPL domains；
- idle thermal baseline；
- normal-interactive thermal baseline；
- cool-down rate；
- passive threshold；
- thermal pressure normalization；
- emergency observations。

### 18.1 Calibration A — cold idle

机器充分冷却后：

```text
15–20 min idle
```

记录：

- idle temperature；
- package power；
- battery power；
- cool-down floor。

### 18.2 Calibration B — normal interactive

真实使用：

```text
30–60 min
```

包括：

- 浏览；
- 编辑；
- 终端；
- 远程操作。

确定：

- 正常 package power；
- 正常 dT/dt；
- 不应触发 heat-soak 的范围。

### 18.3 Calibration C — bounded burst

不是长时间 stress test。

可选：

```text
30–90 s 受控 CPU burst
```

设置明确停止条件：

- 温度达到保守阈值；
- dT/dt 过高；
- thermald 开始明显 clamp；
- 用户中止。

目标只是测：

```text
热惯性
+
升温斜率
+
冷却速度
```

不测试“极限性能”。

---

## 19. 实验系统 v2

v1 trial 框架的“可回滚实验”思想保留，但实验对象改变。

### 19.1 允许实验

主要探索：

```text
EPP
max_perf_pct
是否允许 turbo
envelope dwell / hysteresis
thermal pressure soft threshold
非危险的设备节能设置
```

### 19.2 不允许自动实验

禁止无人值守修改：

- thermald hard trip；
- RAPL hard safety limit；
- kernel cmdline；
- suspend mode；
- ACPI / firmware；
- arbitrary sysfs；
- emergency threshold；
- battery charging firmware setting。

### 19.3 实验比较不再按 scene

baseline/candidate 需要匹配：

```text
demand bucket
thermal starting state
brightness bucket
battery state
kernel / system fingerprint
media state
remote state
```

比较的不是：

```text
coding vs coding
```

而是：

```text
类似性能需求 + 类似热起点
```

### 19.4 主要 objective

轻交互/远程：

```text
minimize whole-device battery W
```

约束：

- CPU PSI 不恶化；
- I/O PSI 不恶化；
- thermal_pressure 不恶化；
- 无 throttle；
- 无明显用户负反馈。

媒体：

```text
minimize whole-device battery W
```

约束：

- MPRIS playing continuity；
- 无明显播放中断；
- 后续可增加 dropped-frame telemetry。

### 19.5 热表现作为一等约束

候选即使省电，如果：

```text
温度更高
或
dT/dt 更陡
或
heat soak 更严重
```

也不能自动晋升。

---

## 20. 新 Envelope 生命周期

状态：

```text
CANDIDATE
VALIDATING
REVALIDATING
VERIFIED
BLOCKED
RETIRED
```

不再有 scene-scoped promotion。

验证记录绑定：

```text
machine fingerprint
kernel
thermald config hash
thermal calibration version
demand region
thermal starting region
battery health
```

只要以下任意变化：

- kernel；
- BIOS；
- thermald；
- thermal config；
- calibration；
- CPU power driver；
- battery health 明显变化；

相关 envelope 自动变：

```text
NEEDS_REVALIDATION
```

---

## 21. LLM v2 的输入

每小时 knowledge pack 不再以 scene summary 为主。

核心：

### 21.1 Energy

- 最近 1h / 24h battery W；
- envelope 分布；
- idle vs active；
- remote/media 时间；
-异常高功耗窗口。

### 21.2 Thermal

- thermal state 分布；
- heat-soak episode；
- max temp；
- dT/dt；
- RAPL 60s/300s；
- throttle event；
- cool-down episode；
- thermal-safe 触发次数。

### 21.3 Demand

- active / idle；
- local compute pressure；
- remote hint；
- media；
- network intensity；
- PSI。

### 21.4 Attribution

仅在异常时提供：

- top processes；
- foreground app；
- process tree；
- app timeline。

### 21.5 Learning

- verified envelopes；
- candidate trials；
- rejections；
- 用户反馈；
- version drift。

---

## 22. LLM v2 动作集合

删除：

```text
UPDATE_CONTEXT_RULE
```

新动作：

```text
NO_CHANGE
NEED_MORE_DATA
INVESTIGATE_POWER_SPIKE
INVESTIGATE_THERMAL_EVENT
PROPOSE_ENVELOPE_TRIAL
ROLLBACK_TRIAL
PROMOTE_ENVELOPE
PROPOSE_MANUAL_THERMAL_RECALIBRATION
```

其中：

```text
PROPOSE_MANUAL_THERMAL_RECALIBRATION
```

只能生成建议，不能自动执行 thermal safety 修改。

---

## 23. 机器学习路线：明确延后

v2.0 不引入 ML 控制。

### 23.1 收集至少 30 天以后才评估

只有积累：

- 足够 demand windows；
- 多个 thermal episode；
- 多个 envelope trial；
- 用户真实反馈；

以后才考虑研究模型。

### 23.2 ML 第一阶段只 shadow

候选：

- Gradient Boosting；
- HDBSCAN；
-简单 anomaly detection。

只能输出：

```text
prediction / cluster / anomaly
```

不允许改变 envelope。

### 23.3 不允许“因为 ML 比较高级就上线”

必须通过 replay：

- 历史 trace；
- train/test 按时间切分；
- false positive；
- thermal safety；
- 控制抖动；
- collector overhead。

如果不能明显优于 deterministic v2：

```text
不启用
```

---

## 24. CLI v2：全部破坏式重命名

旧 CLI 全部删除，不留 alias。

删除：

```text
contexts
profile
task-run
knowledge-pack(v1 semantics)
trial with scene context
override old profile semantics
```

新 CLI 建议：

```bash
sp7-powerlab doctor
sp7-powerlab calibrate status
sp7-powerlab calibrate start
sp7-powerlab calibrate finish

sp7-powerlab observe now
sp7-powerlab observe thermal --hours 6
sp7-powerlab observe demand --hours 6
sp7-powerlab observe power --hours 24
sp7-powerlab incidents

sp7-powerlab envelope list
sp7-powerlab envelope inspect NAME
sp7-powerlab envelope set NAME
sp7-powerlab envelope clear-override

sp7-powerlab trial start proposal.json
sp7-powerlab trial status
sp7-powerlab trial rollback
sp7-powerlab trial evaluate

sp7-powerlab hourly --output runtime/hourly-pack.json
sp7-powerlab llm-apply runtime/llm-decision.json

sp7-powerlab service status
```

---

## 25. 配置文件 v2

删除：

```text
config/contexts.toml
config/policy.toml
config/profiles/
```

新增：

```text
config/powerlab.toml
config/machine.toml
config/envelopes.toml
config/thermal.toml
```

### 25.1 powerlab.toml

只保存：

-采样周期；
- retention；
- LLM 周期；
- automation 开关；
- service 参数。

### 25.2 machine.toml

SP7 本机专属：

- hardware fingerprint；
- calibrated thermal parameters；
- sensor paths；
- capability assertions。

### 25.3 envelopes.toml

只包含：

```text
ECO_IDLE
INTERACTIVE_EFFICIENT
REMOTE_EFFICIENT
MEDIA_EFFICIENT
THERMAL_SAFE
```

及候选 envelope。

### 25.4 thermal.toml

包含：

- thermal state threshold；
- hysteresis；
- slope limits；
- rolling window；
- hard guard。

修改该文件必须进入 Class D 人工审核路径。

---

## 26. 模块结构 v2

建议直接删除 v1 模块后重建：

```text
src/sp7_powerlab/
├── cli.py
├── config.py
├── hardware.py
├── telemetry.py
├── demand.py
├── thermal.py
├── controller.py
├── envelopes.py
├── experiments.py
├── evaluation.py
├── storage.py
├── attribution.py
├── llm.py
└── actuators/
    ├── hwp.py
    └── base.py
```

不保留：

```text
context.py
profiles.py
policy.py
jobs.py
generic Power Options adapter
generic PPD adapter
v1 proposal compatibility
```

root helper 也重写成只允许 v2 HWP actuator 声明的固定操作。

---

## 27. systemd v2

常驻：

```text
sp7-powerlab.service
thermald.service
```

定时：

```text
sp7-powerlab-hourly.timer
```

删除 v1 名称和多 service 拆分兼容逻辑。

PowerLab service 启动顺序：

```text
systemd
→ thermald active
→ hardware contract
→ machine calibration valid
→ collector
→ controller
```

如果 thermald 不健康：

```text
controller 进入 read-only
```

---

## 28. fail-safe 原则

### 28.1 Sensor failure

以下任意核心传感器失效：

- battery；
- CPU temperature；
- RAPL；
- intel_pstate state；

行为：

```text
停止实验
不做新的自动写入
记录 incident
保持 thermald
```

### 28.2 Controller crash

重启后：

- 重新读取真实 sysfs；
- 不相信旧内存状态；
- trial 未完成则恢复 baseline envelope；
- thermald 永远独立继续运行。

### 28.3 Suspend/resume

resume 后：

- 前 N 秒只观察；
- thermal rolling window 重建；
- 不把 suspend 时间计入 power/thermal 积分；
- 不立即触发 envelope trial。

---

## 29. v1 数据与兼容策略

**没有兼容策略。**

实施 v2 时：

### 删除

- v1 schemas；
- v1 ContextEngine；
- v1 scene policy；
- v1 profile registry；
- v1 scene tests；
- v1 CLI；
- v1 SQLite migration；
- v1 proposal schema；
- v1 docs。

### 不迁移

```text
runtime/powerlab.sqlite3
```

v2 第一次启动要求：

```bash
rm -rf runtime/
```

### Git 历史

Git commit 历史自然保留 v1。

不在 v2 源码里放：

- legacy reader；
- compatibility parser；
- legacy command alias；
- schema migration code。

---

## 30. 实现阶段

### Phase 0 — Delete v1 control plane

一次破坏性 commit：

- 删除 ContextEngine；
- 删除 scene policy；
- 删除 profile abstraction；
- 删除 task-run heavy job；
- 删除旧 schemas/config/tests；
- CLI 只保留暂时的 doctor/reset；
- version -> 2.0.0-dev。

验收：

```text
代码里不存在双架构
```

### Phase 1 — SP7 Hardware Contract

实现：

- DMI；
- CPU model；
- HWP；
- intel_pstate；
- battery；
- thermal；
- RAPL；
- thermald health。

验收：

错误硬件必须 fail-fast。

### Phase 2 — Telemetry v2

实现：

- 5–10s sample；
- rolling power；
- thermal slope；
- PSI；
- throttling evidence；
- attribution。

验收：

- suspend gap 正确；
- battery charging 不进入放电 objective；
- collector overhead 真机测量。

### Phase 3 — Thermal Observer

实现：

- COOL；
- WARMING；
- HEAT_SOAKED；
- THERMAL_PRESSURE；
- THROTTLING；
- hysteresis；
- thermal_pressure。

验收：

用 synthetic trace + 真机 replay 测试无状态抖动。

### Phase 4 — Demand Observer

实现：

- active；
- latency need；
- local compute pressure；
- remote hint；
- media；
- network；
- PSI。

不实现 semantic scene classifier。

### Phase 5 — HWP Envelope Controller

实现：

- v2 root helper；
- EPP；
- max_perf_pct；
-可审查 turbo control；
- dwell；
- hysteresis；
- manual override；
- thermal preemption。

### Phase 6 — Calibration

实现：

- calibration command；
- machine.toml；
- thermal baseline；
- bounded burst；
- validation hash。

没有 calibration：

```text
controller read-only
```

### Phase 7 — Experiment Engine v2

实现：

- envelope trial；
- thermal starting-state matching；
- demand-region matching；
- revalidation；
- rollback；
- user feedback。

### Phase 8 — Hourly LLM v2

重写：

- knowledge pack；
- decision schema；
- thermal incident analysis；
- power spike analysis；
- envelope proposal。

### Phase 9 — 真机 burn-in

至少：

```text
7 天只读
+
7 天 verified envelope
+
再决定是否开启自动 trial
```

---

## 31. 测试要求

### 31.1 Unit

必须覆盖：

- thermal state transition；
- hysteresis；
- dT/dt；
- rolling power；
- demand vector；
- thermal override；
- sensor failure；
- suspend gap；
- trial rollback；
- no-calibration read-only。

### 31.2 Trace replay

构造：

```text
cold interactive
short burst
slow heat soak
rapid heating
thermald clamp
frequency collapse
cool-down
remote session
media playback
runaway background process
```

重放必须得到预期 envelope。

### 31.3 Safety invariants

测试直接断言：

```text
THROTTLING => THERMAL_SAFE
sensor invalid => no new write
thermald down => no automatic experiment
trial active => controller cannot overwrite trial
manual override => trial stops
no calibration => no automatic control
```

---

## 32. 真机验收标准

v2 不是“代码测试过了”就完成。

### 32.1 Collector

- 平均 CPU overhead < 1%；
- 不产生可明显测出的额外续航损失；
- 5–10s 采样稳定；
- suspend/resume 正常。

### 32.2 Thermal

在用户真实轻负载模式下：

- 不应出现 200–400 MHz emergency collapse；
- 不应频繁进入 THROTTLING；
- 短 burst 后能恢复；
- heat soak 能被提前识别；
- thermald 与 PowerLab 不发生持续互相覆盖。

### 32.3 Interaction

- 网页滚动；
- 文字输入；
- 终端；
- Remote IDE；

不能因为节能产生明显主观延迟。

### 32.4 Battery

最终才比较：

```text
v2 verified envelope
vs
stock baseline
```

按：

- idle；
- normal interactive；
- remote；
- media；

分别统计整机 battery W。

不设“必须省 X%”的虚假目标。

---

## 33. 对 AI/ML 的最终定位

v2 的原则是：

> 能用可靠物理量解决的问题，不交给 AI 猜。

实时路径：

```text
temperature
power
pressure
activity
network
media
→ deterministic control
```

LLM：

```text
历史分析
异常解释
实验假设
社区研究
```

ML：

```text
以后
shadow mode
证明有效以后再讨论
```

---

## 34. 本次重构后的项目定位

v1：

> “识别用户场景，然后为不同场景学习省电 profile。”

v2：

> **“针对 Surface Pro 7 i5-1035G4 的热预算感知个人电源控制实验室：Linux/HWP 负责瞬时性能，thermald 负责热安全，PowerLab 根据实时性能需求和 thermal headroom 选择经过验证的 operating envelope，并用长期实验逐渐降低整机功耗。”**

这是 v2 唯一主架构。

不保留 v1 并行路径。

---

## 35. 技术依据与社区参考

本计划的核心取向来自以下现有实现/文档，而不是凭空设计：

- Linux Kernel `intel_pstate` / HWP：
  https://www.kernel.org/doc/html/latest/admin-guide/pm/intel_pstate.html
- Intel thermald：
  https://github.com/intel/thermal_daemon
- linux-surface SP7 thermal throttle issue #221：
  https://github.com/linux-surface/linux-surface/issues/221
- linux-surface SP7 thermal performance issue #1098：
  https://github.com/linux-surface/linux-surface/issues/1098
- auto-cpufreq：
  https://github.com/AdnanHodzic/auto-cpufreq
- dynamic-power-daemon：
  https://github.com/evertvorster/dynamic-power-daemon

它们共同支持一个更可靠的工程结论：

```text
低层实时控制
→ kernel / HWP / traditional feedback

热保护
→ thermald / RAPL / hardware

个人长期优化
→ PowerLab experiments

解释与研究
→ LLM
```

而不是：

```text
LLM / neural network
→ 直接实时控制 Surface CPU
```
