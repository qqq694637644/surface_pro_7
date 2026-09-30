# Surface Pro 7 PowerLab v2 — 续航优先的长期自优化设计计划

> 状态：v2 破坏式重构设计稿
>
> 目标硬件：Microsoft Surface Pro 7，Intel Core i5-1035G4
>
> 设备定位：个人低功耗交互终端。主要用于浏览、文档、代码编辑、终端、远程开发、媒体播放和轻量本地任务；持续高负载计算默认放到远程服务器。
>
> 最高目标：在用户体验不出现可感知退化、系统保持稳定且不进入不可持续热状态的前提下，尽可能降低真实日常使用中的整机电池放电功率，从而延长实际续航。
>
> 重构原则：破坏式更新。v2 不保留 v1 的场景分类主架构、不迁移 v1 SQLite、不保留旧 CLI alias、不维护兼容适配层、不同时维护两套控制逻辑。

---

## 1. 项目到底要解决什么

PowerLab 不是性能管理器，也不是热管理器，更不是“识别用户正在做什么”的 AI。

它只有一个主目标：

**让这台 Surface Pro 7 在真实个人使用中尽量少从电池取电，同时保持用户愿意接受的体验。**

工程上写成：

- 主优化目标：最小化整机 BAT 放电功率和单位有效使用时间的能量消耗。
- 硬约束一：交互体验不能出现可感知退化。
- 硬约束二：媒体播放、网络和系统稳定性不能因为节能策略失效。
- 硬约束三：不能把机器推进不可持续的 heat soak、thermal throttling 或 emergency frequency collapse。
- 硬约束四：安全层 thermald、firmware 和 CPU thermal protection 永远高于 PowerLab。

因此：

**续航是 objective；体验、稳定和热状态是 constraints。**

PowerLab 不追求最低温度，也不追求最低 CPU 频率。

---

## 2. 设计哲学

### 2.1 先消除浪费，再考虑牺牲性能

节能动作分三层。

第一层：零成本或接近零成本的浪费消除。

包括：

- 异常后台进程；
- 浏览器 runaway tab；
- 硬件视频解码失效；
- GPU 无法进入 idle；
- 不必要的设备唤醒；
- 明显异常的 Wi-Fi / Bluetooth / USB 活动；
- kernel / driver 回归；
- idle residency 异常；
- 某次软件升级导致的额外功耗。

这类问题优先级最高，因为通常可以做到：

**更省电，同时不降低体验。**

第二层：性能/能效意图优化。

包括：

- EPP；
- intel_pstate max_perf_pct；
- Turbo 策略；
- HWP 可用的慢速性能意图；
- 已验证的设备节能选项。

目标是：

**只减少用户感觉不到的性能余量。**

第三层：明确的体验换续航。

例如非常激进的频率限制、显示策略、设备关闭等。

这类策略默认不自动启用，必须经过用户明确接受。

### 2.2 不自己做毫秒级 DVFS

PowerLab 不实现：

- 每几十毫秒决定频率；
- 自己计算 1.2 GHz / 1.8 GHz / 2.5 GHz；
- 用 LLM 或 Python 循环替代 CPU governor。

瞬时频率选择交给：

- Linux scheduler；
- intel_pstate；
- Intel HWP。

PowerLab 只提供较慢的“性能意图”。

### 2.3 不把 LLM 放进实时控制回路

LLM 大约每小时或按需运行。

它负责：

- 解释历史；
- 找异常；
- 研究社区和软件回归；
- 提出新的可验证假设；
- 生成受控 trial。

它不负责：

- 秒级控制；
- 实时调频；
- thermal safety；
- 决定实验是否成功；
- 任意 root 操作。

### 2.4 v2.0 不依赖机器学习控制

v2.0 的实时路径只使用：

- 可测物理量；
- 确定性状态机；
- hysteresis；
- rolling window；
- A/B 实验；
- 用户反馈。

以后即使研究 ML，也先进入 shadow mode，只预测、不控制。

---

## 3. 为什么不再以“工作场景分类”为核心

v1 试图把用户状态归类为 reading、coding、web、compile、mixed 等。

这有两个问题。

第一，真实个人使用天然是并发的。

用户可能同时：

- 编辑代码；
- 查浏览器文档；
- SSH 到服务器；
- 播放音乐；
- 后台同步；
- language server 工作。

第二，电源控制真正需要的是“机器需要什么”，而不是“这个行为叫什么”。

v2 只关心：

- 用户是否活跃；
- 前台是否需要低延迟；
- 本地计算压力；
- 是否需要连续媒体；
- 网络是否重要；
- 是否存在 I/O / CPU pressure；
- 当前热状态；
- 当前整机功耗。

因此 ActivityWatch、前台应用和窗口标题在 v2 中主要用于归因和解释，而不是实时策略主键。

---

## 4. 目标设备假设

v2 专门针对这一台机器设计，不做通用 Linux 笔记本框架。

硬件契约：

- DMI 必须是 Surface Pro 7；
- CPU 必须是 Intel Core i5-1035G4；
- intel_pstate 必须可用；
- HWP 必须确认可用或明确记录实际工作模式；
- BAT sysfs 必须可读取；
- 至少一个可信 CPU/package thermal sensor 必须可读取；
- Intel RAPL package energy 必须可读取，或系统进入只读诊断模式；
- systemd 可用；
- thermald 状态可查询。

不满足硬件契约时：

- 允许 doctor 和只读采集；
- 禁止自动写入；
- 禁止自动 trial；
- 禁止 envelope promotion。

不增加 AMD、其他 Surface、其他 Intel CPU 的兼容分支。

---

## 5. 设备使用模型

用户已明确：

**持续重负载任务放在远程服务器。**

所以正常本机负载应该是：

- 长时间低本地计算；
- 大量 idle / shallow-interactive；
- 偶尔短 burst；
- 远程工作；
- 媒体；
- 短时文件传输；
- 少量后台服务。

本机持续高 CPU / RAPL package power 不默认解释为“需要性能”。

它优先进入：

**LOCAL_COMPUTE_PRESSURE**

系统会：

1. 记录 top processes 和 process tree；
2. 记录持续时间和热变化；
3. 不自动切高性能策略；
4. 如果 thermal pressure 上升，提前收紧；
5. 在下一次 LLM 分析中解释异常来源。

---

## 6. 总体架构

整体分六层。

第一层：Hardware / Telemetry

持续观察：

- 电池；
- CPU；
- RAPL；
- 温度；
- PSI；
- 网络；
- 媒体；
- 用户活动；
- 进程归因；
- brightness；
- 系统版本。

第二层：Observers

两个独立观察器：

- Demand Observer：当前需要多少性能；
- Thermal Observer：当前还有多少可持续热余量。

第三层：Battery-Life Controller

根据：

- Demand；
- Thermal；
- Battery；
- 用户 override；
- Trial lock；

选择一个已经验证的 operating envelope。

第四层：Actuators / Safety

- PowerLab HWP actuator：表达性能意图；
- thermald：热安全；
- Intel HWP：实际瞬时频率；
- Surface firmware / CPU：最后硬保护。

第五层：Storage / Experiment

SQLite 保存：

- 原始采样；
- rollup；
- controller action；
- thermal incident；
- trial；
- feedback；
- 系统漂移。

第六层：LLM

每小时读取压缩后的 knowledge pack：

- 分析浪费；
- 分析热事件；
- 提实验；
- 不参与实时控制。

---

## 7. 时间尺度与控制职责

必须明确每一层工作的速度。

### 微秒/毫秒级

Owner：

- CPU；
- Intel HWP；
- Linux scheduler；
- intel_pstate。

职责：

- P-state；
- burst；
- 任务调度；
- 瞬时频率。

PowerLab 不参与。

### 5–10 秒级

Owner：

- PowerLab local controller。

职责：

- 更新 demand；
- 更新 thermal state；
- 检查异常；
- 选择 verified envelope；
- thermal preemption；
- hysteresis。

### 1–5 分钟级

Owner：

- PowerLab rolling analysis。

职责：

- 持续功耗；
- heat soak；
- battery drain；
- 异常后台负载；
- 浪费事件。

### 小时级

Owner：

- LLM / MCP。

职责：

- 长期趋势；
- regression；
- 实验建议；
- 异常解释。

### 天/周级

Owner：

- Experiment engine + user review。

职责：

- 验证 envelope；
- battery-life 对比；
- drift；
- calibration 更新。

---

## 8. 真正的优化指标

### 8.1 整机电池功耗是主指标

主数据源：

- BAT power_now；
- energy_now / energy_full；
- 必要时用 current_now × voltage_now fallback。

CPU RAPL 不是续航 reward。

RAPL 用来解释：

- CPU 在整机功耗中占多少；
- heat soak 是否来自 CPU；
- 性能意图变化是否真的影响 package power。

### 8.2 不追求固定 5W / 6W 目标

不同：

- 亮度；
- Wi-Fi；
- 媒体；
- 电池健康；
- 软件版本；
- 环境温度；

都会改变整机 W。

因此不设一个拍脑袋的“优秀功耗”。

我们只做相对比较：

- stock baseline；
- current verified envelope；
- candidate；
- 历史相似窗口。

### 8.3 续航评价

长期报告至少给出：

- 有效放电时间；
- 平均 BAT W；
- 中位数 BAT W；
- P90 / P95；
- 单位活动时间 Wh；
- 基于当前 usable energy 的 projected runtime；
- battery drain slope。

Projected runtime 只是展示指标，不直接作为实验 reward。

---

## 9. 亮度的特殊处理

屏幕是整机功耗的重要组成部分，但自动降亮度极易损伤体验。

v2 默认：

**不自动改变用户亮度。**

所有实验必须：

- 记录 brightness；
- 按 brightness bucket 匹配；
- 候选和 baseline 亮度差异过大时判不可比。

以后可以增加可选 Display Saver，但必须由用户显式启用，并配置：

- 最小亮度；
- 允许自动调整的范围；
- 是否在媒体/远程状态关闭。

Display Saver 不属于 v2 初始自动控制面。

---

## 10. Telemetry v2

建议基础采样周期 5–10 秒；重型诊断采用更低频率。

每条 sample 至少包含：

### Battery

- status；
- percent；
- power_w；
- energy_wh；
- voltage；
- current；
- energy_full；
- energy_full_design；
- cycle_count（可用时）；
- battery epoch。

### CPU / HWP

- cpu utilization；
- load；
- average frequency；
- policy max/min；
- EPP；
- max_perf_pct；
- Turbo / no_turbo；
- HWP dynamic boost（可用时）。

### Pressure

- CPU PSI；
- I/O PSI；
- memory PSI。

### Thermal

- package/core temperature；
- 温度斜率；
- throttle counters；
- frequency collapse evidence；
- thermald status。

### RAPL

- package energy；
- derived power 10s；
- derived power 60s；
- derived power 300s。

### Display / Devices

- brightness；
- backlight level；
- Wi-Fi traffic；
- Bluetooth state；
- 可用时的 GPU frequency / RC6；
- 设备 wakeup 诊断只低频采样。

### User / Media

- user active / idle；
- MPRIS playing；
- 可选 ActivityWatch attribution。

### Process Attribution

仅保存少量 top processes：

- PID；
- start time；
- executable；
- CPU；
- RSS；
- I/O；
- parent PID。

不对全进程树做高频永久采样。

---

## 11. Battery Epoch

用户计划更换电池，因此 v2 必须避免把旧坏电池和新电池数据混在一起。

定义 battery_epoch。

当检测到以下明显变化时启动新 epoch：

- battery identity 变化；
- energy_full 大幅跃迁；
- energy_full_design / serial / model 变化；
- 用户手工声明已换电池。

不同 battery epoch：

- 续航数据不直接横向比较；
- envelope 可以复用，但需要 revalidation；
- projected runtime 使用当前 epoch 数据。

---

## 12. Demand Observer

Demand Observer 不输出“用户正在做 coding”。

它输出当前性能需求向量。

建议字段：

- user_active；
- latency_need；
- local_compute_pressure；
- media_continuity；
- remote_hint；
- network_intensity；
- io_pressure。

### user_active

来源优先级：

1. systemd-logind IdleHint；
2. 桌面 idle API；
3. 可选 ActivityWatch AFK。

输出：

- ACTIVE；
- IDLE。

### latency_need

根据：

- 最近输入/活动；
- 短 burst；
- CPU PSI；
- I/O PSI；
- runnable pressure；
- 最近 envelope 下的反馈。

输出：

- LOW；
- MEDIUM；
- HIGH。

不要求知道前台软件名。

### local_compute_pressure

基于：

- CPU EWMA；
- load；
- CPU PSI；
- RAPL 10/60/300s；
- top-process CPU。

输出：

- LOW；
- MODERATE；
- SUSTAINED。

SUSTAINED 不自动触发 performance envelope。

### media_continuity

根据：

- MPRIS；
- playerctl；
- 以后可选 dropped-frame telemetry。

### remote_hint

只做辅助。

可能来源：

- SSH / mosh；
- VS Code remote；
- Remmina / xfreerdp；
- 持续交互网络 + 低本地 compute。

它不需要做到语义上 100% 准确。

### network_intensity

输出：

- LOW；
- INTERACTIVE；
- TRANSFER。

只看吞吐和变化，不猜协议。

---

## 13. Thermal Observer

热管理不是主目标，但必须是强约束。

Surface Pro 7 i5 为被动散热设备，持续 package power 会产生明显 heat soak。

因此不能只看当前温度。

### 输入

- package/core temp；
- dT/dt；
- RAPL 10s / 60s / 300s；
- BAT power；
- CPU frequency；
- CPU utilization；
- throttle counter；
- thermald action；
- cooldown rate。

### 状态

- COOL；
- WARMING；
- HEAT_SOAKED；
- THERMAL_PRESSURE；
- THROTTLING。

### COOL

- 温度稳定；
- 持续 package power 低；
- 无 throttle。

允许正常短 burst。

### WARMING

- dT/dt 持续为正；
- 持续功耗高于个人轻负载基线；
- 尚未热到需要强制限制。

不立即压性能，但避免进一步增加 sustained envelope。

### HEAT_SOAKED

- 一段时间内累计热负荷高；
- 降温速度慢；
- 即使瞬时温度不高，也不立即恢复激进 burst。

### THERMAL_PRESSURE

- 接近真机校准的软限制；
- 或 thermald 已主动 clamp；
- 或开始出现受热限制迹象。

Controller 强制 THERMAL_SAFE。

### THROTTLING

检测到：

- throttle counter 增长；
- frequency collapse；
- 明显 firmware / thermald clamp；
- 校准定义的危险状态。

立即：

- 终止 trial；
- 进入 THERMAL_SAFE；
- 记录 incident；
- 禁止新的自动实验。

---

## 14. Thermal Pressure 不用 ML

维护连续 thermal_pressure 0–1。

由以下量的归一化组合得到：

- 温度水平；
- 温度斜率；
- 60s package power；
- 300s package energy；
- cooldown rate；
- throttle evidence。

作用：

- 给状态机提供 hysteresis；
- 提前识别 heat soak；
- 匹配实验起始条件；
- 判断候选是否让热行为恶化。

具体阈值必须来自真机 calibration，不从社区帖子硬抄。

---

## 15. Waste Detector：续航优化的第一优先级

v2 新增 Waste Detector，优先级高于 Envelope Tuner。

它寻找：

**当前性能需求很低，但整机功耗明显高于个人历史基线。**

典型事件：

- low demand + high BAT W；
- low demand + high RAPL；
- idle + CPU wakeups 异常；
- 媒体 + CPU 异常高；
- 网络低但 radio/device 功耗异常；
- GPU 不进入 idle；
- 某个进程长期吃 CPU；
- 软件更新后功耗跃升。

### Personal Baseline

Waste Detector 使用个人历史而不是固定阈值。

例如：

- 相同 brightness bucket；
- 相似 battery epoch；
- 相同 media state；
- 相近 demand；
- COOL thermal start；

历史正常窗口 P50 / P90。

当前窗口若长期显著偏离，生成：

WASTE_INCIDENT

同时保存：

- top processes；
- foreground app（如果可用）；
- RAPL；
- GPU；
- network；
- brightness；
- system fingerprint。

### Waste Incident 的处理顺序

1. 解释；
2. 找软件/设备原因；
3. 优先修浪费；
4. 只有确认不是异常以后，才考虑进一步限制 CPU。

---

## 16. Operating Envelopes

v2 不维护几十个 app/scene profile。

初始只有少量 envelope。

### ECO_IDLE

目的：

- 人不操作时最低功耗；
- 提高深 idle residency；
- 避免不必要 burst。

不自动改变屏幕亮度。

### INTERACTIVE_EFFICIENT

默认主 envelope。

目的：

- 输入、点击、滚动、网页 burst 正常；
- 任务完成后迅速回落；
- 不保留无意义持续性能余量。

预计这是用户最常用的 envelope，也是最重要的续航优化对象。

### REMOTE_EFFICIENT

目的：

- 本地计算尽量低；
- UI 和网络响应正常；
- 远程服务器承担重计算；
- 本地后台进程不得抢占大量功耗和热预算。

### MEDIA_EFFICIENT

目的：

- 硬件解码正常；
- 播放连续；
- CPU 尽可能 idle；
- 避免无意义 Turbo。

### THERMAL_SAFE

目的：

- 只在 heat soak / thermal pressure 时使用；
- 降低持续 package power；
- 等待热压力恢复。

它是恢复 envelope，不是正常日常工作模式。

---

## 17. Envelope 参数面

v2 初始动态 CPU 控制面尽量小。

允许研究：

- energy_performance_preference；
- intel_pstate max_perf_pct；
- Turbo / no_turbo；
- HWP dynamic boost（仅在硬件与 kernel 实测确认后）。

不自动控制：

- 任意 sysfs；
- 任意 PCI；
- 任意 ASPM；
- 任意 USB autosuspend；
- kernel cmdline；
- ACPI；
- thermald hard trip；
- RAPL hard safety limit。

设备级节能选项可以以后作为 Waste Elimination proposal 单独加入，不能混入 CPU envelope。

---

## 18. 控制 Ownership

每个旋钮只能有一个 owner。

| 层 | Owner | 责任 |
| --- | --- | --- |
| 实际 P-state | Intel HWP | 瞬时频率 |
| 调度/利用率 | Linux scheduler | task scheduling |
| CPU performance intent | PowerLab | EPP / max_perf_pct |
| 热安全 | thermald | thermal/RAPL safety |
| 最终保护 | CPU / Surface firmware | emergency protection |
| 长期优化 | PowerLab experiments + LLM | 提案、验证、回滚 |

PowerLab 不允许同时运行会持续写同一 EPP/max_perf_pct 的 TLP、auto-cpufreq、Power Options、PPD 等竞争控制器。

安装阶段必须显式检查并报告 ownership conflict。

---

## 19. thermald 的定位

thermald 是正式依赖，不是 PowerLab 的优化器。

职责：

- 热安全；
- 必要时通过 Intel power/thermal mechanism 限制。

PowerLab：

- 不关闭 thermald；
- 不自动提高热限制；
- 不让 LLM 自动改 thermald 配置；
- 将 thermald 的 clamp 视为安全 override。

如果 thermald 不健康：

- 停止自动实验；
- controller 进入只读或 safe 模式；
- 记录 incident；
- 提示人工检查。

如果 stock thermald 无法避免 SP7 emergency throttle，可以设计专门配置，但必须：

- 人工审核；
- 真机短时间验证；
- Git 记录；
- 明确回滚。

---

## 20. Controller

本地 controller 每约 5–10 秒运行一次。

基本优先级：

1. Sensor invalid → 不做新的写入。
2. Trial safety violation → 立即 rollback。
3. THROTTLING → THERMAL_SAFE。
4. THERMAL_PRESSURE → THERMAL_SAFE。
5. HEAT_SOAKED → 禁止激进恢复。
6. IDLE → ECO_IDLE。
7. MEDIA continuity → MEDIA_EFFICIENT。
8. remote hint 高 + local compute 低 → REMOTE_EFFICIENT。
9. 其他活跃状态 → INTERACTIVE_EFFICIENT。

### 防抖

必须有：

- minimum dwell time；
- enter threshold；
- exit threshold；
- cooldown；
- suspend/resume grace period。

不能每个采样周期切 envelope。

### Trial lock

Trial 运行时：

- 普通 controller 不得覆盖 candidate；
- thermal safety 仍可抢占；
- 用户 manual override 可以中止 trial。

---

## 21. Calibration

v2 第一次安装后默认只读。

没有 calibration：

- 采集正常；
- controller 不自动调参数；
- 不启动自动 trial；
- 不自动 promote。

Calibration 生成 config/machine.toml。

### A. Hardware calibration

确认：

- DMI；
- CPU；
- HWP；
- intel_pstate；
- RAPL；
- thermal sensor；
- BAT；
- thermald。

### B. Cold idle baseline

机器充分冷却后观察一段时间。

测：

- 最低稳定 BAT W；
- RAPL；
- idle temperature；
- cooldown floor；
- C-state / wakeup diagnostics。

### C. Normal interactive baseline

用户按真实方式使用：

- 浏览；
- 编辑；
- 终端；
- 远程。

记录：

- 正常 BAT W 分布；
- RAPL；
- PSI；
- 短 burst；
- 温度；
- dT/dt。

### D. Media baseline

确认：

- 硬件解码；
- 媒体 continuity；
- CPU/RAPL；
- GPU；
- BAT W。

### E. Bounded thermal burst

只做短时受控 burst，不做长期烤机。

目标：

- 测升温速度；
- 热惯性；
- cooldown；
- thermald 介入迹象。

遇到停止条件立即结束。

---

## 22. Baseline 不是一个数字

v2 不维护“这台机器正常是 5W”。

Baseline 是条件化分布。

至少按以下维度分桶：

- battery epoch；
- brightness bucket；
- user active / idle；
- media on/off；
- remote hint；
- network intensity；
- demand region；
- thermal starting state；
- kernel / system fingerprint。

因此实验比较的是：

**相似需求 + 相似亮度 + 相似热起点下，candidate 是否更省电。**

---

## 23. Experiment Engine v2

PowerLab 的学习来自实验，不来自 LLM 的主观判断。

### 23.1 实验对象

优先顺序：

第一类：Waste elimination。

例如：

- 修硬件解码；
- 处理异常服务；
- 验证某设备 runtime PM；
- 浏览器设置；
- 后台程序配置。

第二类：Envelope tuning。

例如：

- EPP；
- max_perf_pct；
- Turbo policy。

第三类：体验换续航。

默认不自动。

### 23.2 一次一个主要变量

默认实验：

- 一个 primary change；
- 其他条件保持不变。

Envelope 级实验如果包含多项变化，必须把完整 diff 当成一个不可拆的 candidate，并且需要更严格验证。

### 23.3 不使用单纯“历史前后对比”

优先采用个人 N-of-1 crossover。

推荐结构：

- A：当前 verified；
- B：candidate；
- A：回到 verified；
- 后续新的 B：revalidation。

尽量在：

- 同一天；
- 相似 brightness；
- 相似 demand；
- 相似 thermal start；

进行。

历史 baseline 只作为辅助证据，不作为单次自动 promotion 的唯一依据。

### 23.4 自然使用，不要求人工 benchmark

Interactive trial 可以进入 WAITING_FOR_COMPARABLE_WINDOW。

只有出现：

- 电池 Discharging；
- 温度稳定；
- 没有 suspend/resume；
- 没有异常后台负载；
- demand 合适；
- brightness 可比；

才开始记录 candidate。

用户不需要为了实验刻意模拟工作。

---

## 24. Trial 状态机

建议 v2 使用：

- PROPOSED；
- WAITING_FOR_COMPARABLE_WINDOW；
- SNAPSHOTTED；
- APPLIED；
- SETTLING；
- MEASURING；
- EVALUATING；
- REVALIDATING；
- VERIFIED_WINNER；
- REJECTED；
- ROLLED_BACK；
- FAILED。

任何阶段：

- sensor failure；
- thermal violation；
- user negative feedback；
- thermald anomaly；
- manual override；

都可以中止并 rollback。

---

## 25. 实验评价

### Interactive / Remote

主目标：

- 降低 BAT W。

约束：

- CPU PSI 不显著恶化；
- I/O PSI 不显著恶化；
- demand 不出现明显 backlog；
- thermal pressure 不恶化；
- 无 throttle；
- 无用户负反馈。

### Media

主目标：

- 降低 BAT W。

约束：

- MPRIS playing continuity；
- 无明显播放中断；
- 硬件解码状态正常；
- CPU/RAPL 不出现异常；
- thermal 不恶化。

### Idle

主目标：

- 降低稳定 idle BAT W。

约束：

- 系统能够正常唤醒；
- 后台关键服务不被破坏；
- suspend/resume 不退化。

### 统计

至少使用：

- time-weighted average；
- median；
- P90/P95；
- valid duration；
- gap filtering；
- paired delta；
- 独立 revalidation。

后续可增加 block bootstrap / confidence interval，但 promotion 逻辑不能依赖只有两个点的平均值。

---

## 26. 用户体验是硬约束

PowerLab 不假装完全自动量化“好不好用”。

客观信号：

- CPU PSI；
- I/O PSI；
- 响应 backlog proxy；
- media continuity；
- 系统错误；
- network stalls（能可靠获取时）。

主观信号：

用户可以快速反馈：

- good；
- sluggish；
- bad；
- unstable。

负反馈必须：

- 绑定 envelope / trial；
- 进入 rejection memory；
- 自动阻止同一设置被短期重复推荐。

即使 candidate 节省很多电，只要用户认为体验不可接受：

**直接 reject。**

---

## 27. Waste Investigation

当检测到异常功耗时，LLM 的首选动作不是调低 CPU。

先生成 incident pack：

- 当前 BAT W vs personal baseline；
- RAPL；
- thermal；
- brightness；
- GPU；
- network；
- top processes；
- foreground app；
- 最近软件版本变化；
- kernel / BIOS / driver fingerprint。

LLM 动作：

- NO_CHANGE；
- NEED_MORE_DATA；
- INVESTIGATE_POWER_SPIKE；
- INVESTIGATE_THERMAL_EVENT；
- PROPOSE_WASTE_FIX；
- PROPOSE_ENVELOPE_TRIAL；
- ROLLBACK_TRIAL；
- PROMOTE_ENVELOPE；
- PROPOSE_MANUAL_RECALIBRATION。

---

## 28. LLM 的边界

LLM 可以：

- 解释异常；
- 寻找社区已知回归；
- 提出单变量实验；
- 总结长期趋势；
- 发现软件升级前后差异；
- 建议用户检查某进程/浏览器/驱动。

LLM 不可以：

- 直接写 sysfs；
- 直接调用 sudo shell；
- 改 thermald hard limit；
- 自己宣布 trial 成功；
- 绕过 data quality；
- 绕过 revalidation；
- 因为“应该省电”就永久应用参数。

最常见的合法动作应该是：

**NO_CHANGE。**

持续优化不等于持续改配置。

---

## 29. Root Helper v2

v1 generic helper 删除。

v2 root helper 重新实现，只暴露固定操作：

- read HWP state；
- set EPP；
- set max_perf_pct；
- set turbo policy（如果最终允许）；
- restore exact pre-trial snapshot。

要求：

- 不接受任意 sysfs path；
- 不接受任意 shell；
- 每个参数都有 enum/range；
- 真实 read-back；
- 操作写审计日志；
- root-owned 独立安装；
- systemd hardening；
- 客户端 socket 只允许目标用户。

---

## 30. Storage v2

SQLite schema 重新设计。

不迁移 v1。

核心表：

- samples；
- power_rollups；
- demand_windows；
- thermal_windows；
- controller_states；
- control_actions；
- waste_incidents；
- thermal_incidents；
- envelopes；
- envelope_validations；
- trials；
- trial_blocks；
- trial_results；
- user_feedback；
- process_attribution；
- system_fingerprints；
- battery_epochs；
- llm_runs；
- llm_decisions；
- rejections。

删除 v1 的：

- contexts；
- context_policies；
- scene sessions；
- task_runs；
- profile applications。

---

## 31. 数据保留

高频 raw：

- 默认有限天数；
- 用于近期故障和 trial。

长期保留：

- 分钟 rollup；
- hourly summary；
- incidents；
- trials；
- envelope validation；
- feedback；
- system drift；
- battery epoch。

Git 不提交 raw SQLite。

Git 只保存：

- 配置；
- calibration；
- envelope 定义；
- 审核后的 proposal；
- 紧凑知识导出；
- 重要 incident summary。

---

## 32. 系统漂移

以下变化会让历史 envelope 进入 NEEDS_REVALIDATION：

- kernel；
- BIOS/UEFI；
- intel_pstate/HWP 行为；
- thermald 版本；
- thermald 配置；
- PowerLab calibration；
- battery epoch；
- 重大浏览器/媒体栈变化；
- 关键 driver 变化。

不是所有软件更新都立即废弃 envelope。

只有与该 envelope 相关的 fingerprint 变化才触发 revalidation。

---

## 33. ActivityWatch 的新位置

ActivityWatch / awatcher 变成可选 Attribution Provider。

用途：

- 解释哪一个 app 占据异常窗口；
- 长期使用时间统计；
- 给 LLM 更容易理解的上下文。

不用于：

- app name → envelope；
- 实时 scene classification；
- controller hard dependency。

ActivityWatch 挂掉时：

- 续航控制继续正常；
- 只是 attribution 信息变少。

---

## 34. PowerTOP、turbostat、powerstat 的定位

这些工具不做常驻控制器。

### turbostat

用于：

- 真机 calibration；
- C-state；
- package power；
- frequency；
- throttle diagnostics。

### PowerTOP

用于：

- wakeup；
- device runtime PM；
- idle residency；
- 候选 waste fix 发现。

不自动执行 powertop --auto-tune。

### powerstat

用于：

- 人工基准；
- 验证整机 battery discharge；
- 与 PowerLab 结果交叉检查。

这些工具用于诊断和验证，不与 PowerLab 抢控制权。

---

## 35. 媒体与浏览器

媒体是续航的重要特殊路径。

v2 必须关注：

- VA-API / hardware decode 是否实际工作；
- CPU package power；
- GPU/i915 activity；
- MPRIS continuity；
- BAT W。

如果硬解回归导致视频从正常低 CPU 变成持续高 CPU：

优先修回归，不通过更激进 CPU 限频“掩盖”问题。

---

## 36. 网络与远程工作

由于重计算主要在服务器上，REMOTE_EFFICIENT 是核心 envelope 之一。

目标：

- 本地 CPU 尽量低；
- 短交互 burst 不迟钝；
- 网络不因为激进 powersave 明显增加抖动；
- remote UI 正常。

Wi-Fi powersave 不默认无限激进。

任何无线策略都必须验证：

- 响应；
- 断流；
- 吞吐；
- BAT W。

---

## 37. 设备级节能

CPU 不是整机唯一耗电来源。

v2 会记录并逐步研究：

- display；
- Wi-Fi；
- Bluetooth；
- USB；
- GPU；
- SSD/runtime PM；
- Type Cover/backlight（能可靠读取时）。

但这些不全部进入 v2.0 自动控制面。

顺序：

1. 只读测量；
2. 识别浪费；
3. 提出单独 proposal；
4. 人工验证；
5. 有足够证据后才允许自动使用。

---

## 38. Service 模型

v2 只保留清晰的服务：

- sp7-powerlab.service：collector + observers + controller；
- sp7-powerlab-hourly.timer：生成 knowledge pack；
- sp7-powerlab-root-helper.service：受限写入；
- thermald.service：独立热安全。

启动顺序：

1. thermald health；
2. hardware contract；
3. database schema；
4. calibration；
5. telemetry；
6. observers；
7. controller。

没有 calibration 时：

- controller read-only；
- 其他只读功能正常。

---

## 39. Fail-safe

### Sensor failure

核心传感器缺失：

- 停止 trial；
- 不做新自动写入；
- 保留最后 verified baseline 或安全恢复；
- 记录 incident。

### thermald down

- 禁止 trial；
- controller 不做激进 envelope；
- 提示人工处理。

### Controller crash

重启后：

- 重新读取真实 sysfs；
- 不信任旧内存状态；
- 未完成 trial 回滚；
- thermald 独立继续。

### Suspend / resume

resume 后：

- rolling window 清空或标记 discontinuity；
- 一段 grace period 只观察；
- 不把 suspend 算作有效能耗时间；
- 不立即进入 trial。

### Low battery

低于校准/配置阈值：

- 禁止新实验；
- 允许 verified envelope；
- 不为了实验消耗剩余电量。

---

## 40. v1 破坏式删除清单

实现 v2 时直接删除：

- src/sp7_powerlab/context.py；
- src/sp7_powerlab/profiles.py；
- src/sp7_powerlab/policy.py；
- src/sp7_powerlab/jobs.py；
- v1 generic actuator manager；
- Power Options / PPD 自动探测主控制；
- config/contexts.toml；
- config/policy.toml；
- config/profiles/；
- v1 context schemas；
- v1 proposal/decision semantics；
- scene-based tests；
- task-run heavy-job 主流程；
- v1 compatibility CLI；
- v1 SQLite migration。

不写：

- legacy reader；
- compatibility adapter；
- old command alias；
- scene → demand bridge。

Git 历史就是 v1 的档案。

---

## 41. v2 配置文件

重构后只保留：

### config/powerlab.toml

- 采样周期；
- rollup；
- retention；
- LLM cadence；
- automation 开关；
- service 参数。

### config/machine.toml

只属于这台 SP7：

- DMI / CPU fingerprint；
- sensor mapping；
- battery epoch；
- calibration version；
- thermal baseline；
- cooldown；
- pressure normalization。

### config/envelopes.toml

定义：

- ECO_IDLE；
- INTERACTIVE_EFFICIENT；
- REMOTE_EFFICIENT；
- MEDIA_EFFICIENT；
- THERMAL_SAFE；
- 候选 envelope。

### config/thermal.toml

- 状态阈值；
- hysteresis；
- hard guard；
- grace period。

修改 thermal.toml 属于高风险人工审核。

---

## 42. CLI v2

旧 CLI 全部删除。

新 CLI 建议：

- sp7-powerlab doctor
- sp7-powerlab reset-runtime
- sp7-powerlab calibrate status
- sp7-powerlab calibrate start
- sp7-powerlab calibrate finish
- sp7-powerlab observe now
- sp7-powerlab observe power
- sp7-powerlab observe demand
- sp7-powerlab observe thermal
- sp7-powerlab incidents
- sp7-powerlab envelope list
- sp7-powerlab envelope inspect
- sp7-powerlab envelope adopt-current
- sp7-powerlab envelope override
- sp7-powerlab envelope clear-override
- sp7-powerlab trial status
- sp7-powerlab trial start
- sp7-powerlab trial evaluate
- sp7-powerlab trial rollback
- sp7-powerlab feedback
- sp7-powerlab hourly
- sp7-powerlab llm-apply
- sp7-powerlab service status

不保留 v1 alias。

---

## 43. LLM Knowledge Pack v2

每小时只给 LLM 聚合数据，不给大量 raw samples。

### Battery

- 最近 1h / 24h BAT W；
- battery drain；
- brightness distribution；
- envelope distribution；
- battery epoch。

### Demand

- active/idle 时间；
- latency need；
- local compute pressure；
- media；
- remote；
- network。

### Thermal

- 状态分布；
- max temp；
- dT/dt；
- heat-soak episode；
- throttle；
- THERMAL_SAFE 时间。

### Waste

- 异常功耗窗口；
- top processes；
- GPU/network attribution；
- 软件版本漂移。

### Experiments

- active trial；
- 最近候选；
- paired blocks；
- revalidation；
- rejection memory；
- user feedback。

---

## 44. 自动化等级

### Level 0 — Read only

默认首次安装。

- 只采集；
- 只报告。

### Level 1 — Verified control

允许 controller 自动切换已经人工验证的 envelope。

### Level 2 — Assisted trials

LLM 提 proposal。

用户批准后：

- PowerLab 自动等待合适窗口；
- 自动 A/B；
- 自动回滚；
- 自动评价。

### Level 3 — Low-risk autonomous trials

只允许白名单低风险参数。

要求：

- calibration 成熟；
- 至少一段稳定 burn-in；
- 回滚已经真机验证；
- thermald 正常；
- 用户显式开启。

### Level 4 — Auto promotion

不是 v2 初始默认。

只有多次独立验证、无 UX 负反馈、无 thermal regression 后才允许。

---

## 45. 实现阶段

### Phase 0 — 破坏式清场

- version 改为 2.0.0-dev；
- 删除 v1 control plane；
- 删除旧 config/schema/tests/CLI；
- 旧 runtime 检测后 fail-fast；
- 要求清空 runtime。

验收：

**源码中只剩一套 v2 架构。**

### Phase 1 — SP7 Hardware Contract

实现：

- hardware.py；
- DMI；
- CPU；
- HWP；
- intel_pstate；
- BAT；
- RAPL；
- thermal；
- thermald health。

### Phase 2 — Telemetry v2

实现：

- battery；
- RAPL rolling power；
- PSI；
- thermal；
- network；
- brightness；
- GPU；
- process attribution；
- suspend gap。

### Phase 3 — Calibration + Battery Epoch

实现：

- machine.toml；
- cold idle；
- normal interactive；
- media；
- bounded burst；
- battery epoch。

### Phase 4 — Demand Observer

实现：

- active；
- latency need；
- local compute pressure；
- media；
- remote hint；
- network；
- I/O pressure。

### Phase 5 — Thermal Observer

实现：

- rolling thermal model；
- COOL；
- WARMING；
- HEAT_SOAKED；
- THERMAL_PRESSURE；
- THROTTLING；
- hysteresis。

### Phase 6 — HWP Actuator + Root Helper

实现：

- EPP；
- max_perf_pct；
- 经过审查的 Turbo；
- read-back；
- snapshot；
- rollback；
- systemd hardening。

### Phase 7 — Battery-Life Controller

实现：

- envelope selection；
- dwell；
- hysteresis；
- manual override；
- thermal preemption；
- read-only degradation。

### Phase 8 — Waste Detector

实现：

- personal baseline；
- power spike；
- low-demand/high-power anomaly；
- process attribution；
- incident pack。

### Phase 9 — Experiment Engine

实现：

- N-of-1 crossover；
- WAITING_FOR_COMPARABLE_WINDOW；
- A/B/A；
- revalidation；
- UX feedback；
- thermal constraints。

### Phase 10 — LLM v2

重写：

- knowledge pack；
- decision schema；
- waste investigation；
- thermal investigation；
- envelope trial proposal。

### Phase 11 — 真机 burn-in

至少分阶段：

1. 只读；
2. verified envelope；
3. assisted trial；
4. 再决定 autonomous trial。

---

## 46. 测试体系

### Unit

必须覆盖：

- BAT 单位；
- RAPL rolling power；
- dT/dt；
- PSI；
- demand；
- thermal transitions；
- hysteresis；
- battery epoch；
- brightness comparability；
- suspend gap；
- controller priorities；
- root helper ranges；
- trial rollback；
- user rejection。

### Trace Replay

构造：

- cold idle；
- normal interactive；
- remote interactive；
- media；
- short CPU burst；
- runaway browser；
- slow heat soak；
- rapid heat；
- thermald clamp；
- frequency collapse；
- cooldown；
- suspend/resume；
- sensor failure。

Replay 必须得到预期 action。

### Safety Invariants

直接断言：

- THROTTLING => THERMAL_SAFE；
- core sensor invalid => no new automatic write；
- thermald unhealthy => no trial；
- trial active => normal controller cannot overwrite candidate；
- thermal safety may preempt trial；
- negative feedback => rollback/reject；
- no calibration => read-only；
- low battery => no new trial；
- wrong hardware => no write。

---

## 47. 真机验证流程

v2 不以 CI 绿灯作为完成。

### Stage A — 只读 7 天左右

目标：

- 确认 sensor；
- collector overhead；
- normal BAT W；
- thermal；
- brightness；
- waste incident；
- suspend/resume。

### Stage B — 单个 verified envelope

只启用 INTERACTIVE_EFFICIENT。

观察：

- 体验；
- BAT W；
- thermal；
- stability。

### Stage C — 其他 envelope

依次验证：

- ECO_IDLE；
- REMOTE_EFFICIENT；
- MEDIA_EFFICIENT；
- THERMAL_SAFE。

### Stage D — Assisted Trials

用户批准 proposal。

验证：

- snapshot；
- apply；
- read-back；
- rollback；
- revalidation。

### Stage E — 可选自动实验

只有前面稳定以后才讨论。

---

## 48. 真机验收标准

### Collector

- 平均 CPU overhead 目标 < 1%；
- 不会明显增加整机 BAT W；
- 长时间稳定；
- suspend/resume 正常。

### 续航

必须能给出：

- stock baseline；
- verified v2；
- 相同 brightness/demand 条件下的 paired comparison；
- 日级平均 drain。

项目成功不要求预先承诺“省 X%”。

只要求：

**真实数据证明它比 baseline 更省，同时用户愿意继续使用。**

### 用户体验

至少检查：

- 网页滚动；
- 文字输入；
- 终端；
- VS Code / 编辑器；
- Remote IDE / SSH；
- 媒体。

任何明显退化都优先于功耗收益。

### Thermal

正常日常轻负载：

- 不应频繁进入 THERMAL_PRESSURE；
- 不应发生 200–400 MHz 类 emergency collapse；
- 短 burst 后应恢复；
- heat soak 能够提前识别或由 thermald 平滑处理。

---

## 49. ML 的未来路线

只有 deterministic v2 收集足够真实数据后才重新评估。

候选用途：

- 异常检测；
- demand clustering；
- 预测 heat soak；
- 预测某 envelope 的节能收益。

第一阶段只能 shadow：

- 不写参数；
- 不影响 controller；
- 只做离线 replay。

比较对象必须是现有规则系统。

如果不能在：

- 准确性；
- 稳定性；
- 能耗；
- 维护复杂度；

上明显胜出，就不采用。

不因为“深度学习更高级”而上线。

---

## 50. 明确的 Non-goals

v2 不做：

- 通用 Linux 电源管理框架；
- 全自动 AI agent 控制 root；
- 实时工作内容理解；
- 语义场景分类器；
- 深度学习实时控制；
- 本地重负载性能优化；
- 极限温度挑战；
- 一键启用所有 powertop tunables；
- 同时兼容多套 power manager；
- 自动修改 kernel cmdline；
- 自动修改 thermald hard safety limit。

---

## 51. 项目最终定位

PowerLab v2 的最终定义：

**一个专门针对 Surface Pro 7 i5-1035G4 的个人续航优化实验系统。它持续测量整机电池功耗，优先发现并消除无意义的能耗；在没有明显用户体验损失、系统保持稳定且热状态可持续的前提下，通过少量经过验证的 operating envelope 调整 Intel HWP 性能意图，并用可回滚、可复测的个人 A/B 实验长期寻找更省电的配置。LLM 只负责慢速分析、解释和提出实验，不参与实时控制。**

最终判断一个改动是否值得保留，只问三件事：

1. **真实整机电池功耗是否下降？**
2. **用户是否仍然觉得好用？**
3. **这种状态是否能够长期稳定持续，而不是靠积热或隐藏问题换来的？**

三者同时成立，才是 PowerLab 的“优化”。

---

## 52. 技术参考方向

实现阶段主要参考：

- Linux kernel intel_pstate / HWP 文档；
- Intel thermald；
- linux-surface Surface Pro 7 thermal issue；
- auto-cpufreq 的负载/温度监测思路；
- dynamic-power-daemon 的简单本地策略；
- PowerTOP / turbostat / powerstat 的诊断方法。

参考这些项目的职责划分和测量方法，但不把它们全部同时作为控制器。

v2 的核心原则始终是：

**能测量，就不要猜；能消除浪费，就不要先降性能；能交给 kernel/HWP/thermald，就不要在 Python 或 LLM 中重新实现；所有长期优化最终都必须回到真实 BAT 功耗和用户体验。**
