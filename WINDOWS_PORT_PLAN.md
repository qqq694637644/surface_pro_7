# Windows 移植计划与项目缺口（Surface Pro 7 PowerLab）

> 状态：**PROPOSAL / NOT IMPLEMENTED** · 日期：2026-10-11
> 目标：Microsoft Surface Pro 7 / Intel Core i5-1035G4 / Windows 10 Pro 22H2（当前实机）；未来 Windows 11 须独立验证。
> 本文只提出 Windows 移植路线和可验收的缺口，**不修改** `PLAN2.md` 的 Linux 正式设计合同、不改变现有 Linux 实现或其成熟度。合并本计划不代表 Windows 版可运行或已经硬件验证。

## 0. 决策摘要

1. **保留 Python 核心**：`measurement`、`evidence`、`experiments`、`scheduler`、`controller`、`storage`、`longterm` 等算法、持久化和 A/B/A 证据合同优先复用；不要另造 Windows 版算法栈。
2. **先测量，后控制**：Windows Stage A 首先交付 `READ_ONLY` 的 BAT 能量 / 功率 / 温度 / 活动采集；完成 Measurement Trust 和传感器可信度验证前，不启用任何自动 CPU 写入。
3. **驱动不是先天不可行**：CPU Package/Core 温度和 Intel RAPL 先研究 LibreHardwareMonitor + PawnIO / Intel PCM 等现有实现；GPU 独立温度/功耗以 Ice Lake 驱动的真实能力为准，不制造数值。
4. **Windows CPU 控制不等于 Linux sysfs 写入**：`PERFEPP`、`PROCTHROTTLEMAX`、`PERFBOOSTMODE` 具有可用 Windows 策略接口，但 Linux EPP / `max_perf_pct` / Turbo 的语义不可假定逐项等价；必须实机建立映射并验证 read-back、所有权和 rollback。
5. **RDP 阻止睡眠是预期行为，不是待修复缺陷**：活动远程桌面会话使机器保持可用是合理的工作负载要求。Windows 后端应识别 `remote_active` 并独立统计显示关闭和 S0 Sleep；**不得**为了降低待机功耗强制断开 RDP、强制 Sleep 或误报为异常回归。
6. **安全先于自动化**：签名、驱动来源、热保护、写入权仲裁、证据隔离和故障回滚均为启用 `CONTROL_ALLOWED` 的硬门槛。Linux 已验证的 envelope 不自动成为 Windows `VERIFIED`。
7. **有些指标不可能无损移植**：Linux PSI 没有 Windows 下同语义的原生实现。ETW / 性能计数器需要定义、校准 Windows 专用 proxy 和 evidence contract，不得用 0、假数据或原 Linux 阈值替代。

### 非目标

- 本计划不追求禁用 Windows 原生热管理、Secure Boot、Defender 或驱动阻止列表。
- 不通过 PowerLab 维持持续唤醒以采集睡眠数据。
- 不将原项目既有 Linux Stage A–E / STABLE 结论转移到 Windows。
- 不假设驱动可用就拥有 GPU 独立传感器或风扇/EC 控制。
- 不默认移植 Level 2+ 自动搜索、Agent 自动调参，也不增加实时 LLM 控制回路。

## 1. 已核实的 Surface Pro 7 Windows 事实

以下来自 2026-10-11 对实机的 **PowerShell 只读探测**，不代表正式测量校准：

| 项目 | 实机观察 | 结论 / 限制 |
| --- | --- | --- |
| 平台 | Surface Pro 7 / i5-1035G4 / 4C8T / Windows 10 Pro 22H2（19045） | 符合目标硬件；Windows 是新的平台身份 |
| GPU | Intel Iris Plus Graphics，驱动 `31.0.101.2130` | GPU Engine 性能计数器存在；独立温度/功率未验证 |
| BAT 数据 | `root/wmi:BatteryStatus` 返回 `RemainingCapacity`、`DischargeRate`、`Charging`、`Discharging`、`PowerOnline` | mWh / mW 字段可读取；插电时 `DischargeRate=0` 不能验证电池放电精度 |
| 电池健康 | `BatteryFullChargedCapacity` 约 44,900 mWh；`BatteryCycleCount` 为 3 | 仍需真实 Discharging gauge cadence / quantum / 一致性检验 |
| 静态电池身份 | `BatteryStaticData` 类可发现，但一次读实例发生 WMI 错误 | 身份必须有明确降级策略；拿不到可靠 identity 时不伪造 battery epoch |
| Windows CPU 策略 | 电池档 `PERFEPP=80`、`PERFEPP1=50`、`PROCTHROTTLEMAX=100`、`PERFBOOSTMODE=2` | 已确认“方案值可读”；尚未证实硬件 MSR 真实状态与策略一致 |
| 温度 | `MSAcpi_ThermalZoneTemperature` 有 9 个区域 | **不能**未经标定就当 CPU Package/Core/GPU 温度 |
| Intel GPU 接口 | 显卡驱动目录存在 `ControlLib.dll` / `IntelControlLib.dll` | IGCL 值得探测；是否导出目标传感器仍未知 |
| 监控驱动 | 未发现已装 PawnIO / LibreHardwareMonitor 驱动 | 只读 CPU MSR / RAPL 尚未在本机完成端到端验证 |
| 环境 | .NET 8 Desktop Runtime 存在，未发现 .NET SDK | 使用预编译兼容工具容易；开发编译需另配 SDK |
| 睡眠 | S0 Connected Standby + S4；无 S3 | SleepStudy/DRIPS 可用于事后归因；深度 Sleep 不运行高频采样器 |
| 远程连接 | RDP 活动会话存在；SleepStudy 报告 `Remote Connection` 阶段 | 预期的工作负载模式，**不是自动调参需要修复的问题** |

注意：Windows 10 22H2 的维护 / 扩展安全更新及驱动支持状态需在长期部署前单独决策，不能把当前可运行等同于未来仍受支持。

## 2. Linux → Windows 缺口清单

状态说明：`接口可用` = 已确认至少一种 Windows 接口存在；`候选` = 开源实现存在但未经本机端到端验证；`需重新定义` = Linux 原合同没有同语义对应；`硬件决定` = API 能力取决于具体固件 / 驱动。**所有写入能力当前均为 NOT VALIDATED。**

| ID | 原能力 / 相关源码 | Windows 方案 | 状态 | 关闭缺口的验收证据 |
| --- | --- | --- | --- | --- |
| G01 | BAT 能量与瞬时功率；`telemetry.py`、`measurement.py` | `BatteryStatus` / `BatteryFullChargedCapacity`；必要时 Windows Power API | 接口可用，精度未验证 | 电池放电条件下验证 mWh/mW 单位、采样更新间隔、量化台阶和功率积分对能量差的一致性 |
| G02 | battery identity / cycle / epochs；`hardware.py`、`storage.py` | WMI 静态电池数据 + 可核对的固件 / 设备标识 | 候选 | 无稳定身份时 fail closed；更换电池能触发新 epoch；不依赖会变化的 WMI InstanceName |
| G03 | CPU Core / Package 温度；`telemetry.py`、`thermal.py` | LibreHardwareMonitor + 已审核底层读取接口（如 PawnIO） | 候选 | i5-1035G4 上真实样本、合理温度范围、压力响应、失效注入和读取开销 |
| G04 | Intel RAPL Package energy / power | Intel MSR 访问（LibreHardwareMonitor / PawnIO / Intel PCM） | 候选 | 寄存器可读、单位与 wrap 处理正确、连续读数可信、开销可接受；只作归因/约束 |
| G05 | Intel 核显温度、GPU 独立功率；`telemetry.py` | Intel IGCL + LibreHardwareMonitor；GPU Engine 性能计数器只作负载 | 硬件决定 | 列出真正存在的 sensor ID、单位和驱动版本；缺失即 `UNAVAILABLE`，不冒充 Package 温度 |
| G06 | CPU 频率、C-state、GPU 使用率、进程指标 | Windows perf counters、ETW、`psutil`，必要时只读 MSR | 部分接口可用 | 采样稳定、测量开销可知、时间戳对齐；支持缺省值 / 可用性元数据 |
| G07 | Linux CPU / IO / Memory PSI；`demand.py`、`experiments.py`、`scheduler.py` | ETW ready/wait、队列延迟、丢帧等 Windows proxy | **需重新定义** | 定义 proxy 物理意义、阈值/UX 关联验证及 veto；不得继承 Linux PSI 数值门槛 |
| G08 | `intel_pstate` 的 EPP / max_perf_pct / Turbo；`actuators/hwp.py` | `PowerRead/Write*ValueIndex`、`PowerSetActiveScheme`；受限 Windows actuator | 部分策略接口可用 | 新电源方案中可读/写/读回/恢复；DC/AC 隔离；和真实 CPU 行为建立映射 |
| G09 | HWP 真正生效 / 每核 read-back | 可信只读 MSR adapter（调查 `IA32_HWP_REQUEST` 等寄存器及 CPU 亲和性） | 候选 | 证明策略写入前后实际 HWP request 变化；无法确认则正常控制保持 `READ_ONLY` |
| G10 | 热安全与 throttling；`thermald` | 经验证温度来源 + Windows 节流信息 + 固件原生热保护 | **需重新定义** | 连续观测、传感器丢失/过期处理、上升斜率、过热主动停止并恢复 |
| G11 | Linux `systemd` / root helper / Unix socket | Windows Service/计划任务 + 限权 Named Pipe 或等效 IPC | 需替换基础设施 | 服务状态、ACL、调用者身份、请求白名单、审计、重启/崩溃时禁止任意写入 |
| G12 | Linux 设备 / Wi-Fi / 用户活动 / media | Windows 电源事件、WTS RDP 会话、WLAN / perf counters / ETW | 候选 | 能区分本地用户、RDP 活动、媒体、后台网络，保证上下文不误判 |
| G13 | suspend/resume / continuity | Windows power notifications、Kernel-Power 日志、SleepStudy | 接口可用、需整合 | suspend、AC/DC、重启、采样 gap 切断实验；唤醒后 grace 正常工作 |
| G14 | ownership / 事务回滚 / fixed-good audit；`runtime_audit.py` | Windows 活跃方案 + power mode + Surface/OEM 策略变化检测 | **需重新定义** | 同步写入检测、snapshot/read-back/rollback、崩溃恢复；回滚异常 latch |
| G15 | Linux evidence identity / StableReadiness | 将 OS/Windows build、BIOS、GPU / sensor driver、helper、power plan、actuator semantics 写入 hard evidence identity | 需扩充合同 | Windows 与 Linux 的 trial / epoch / candidate 不交叉复用，compatibility 变化能正确失效 |
| G16 | 10 秒 daemon / 固定 one-shot | Windows service + start/stop / login / resume hook | 候选 | 固定策略重启重用必须先 live validation；Stage E 测量纳入实际服务开销 |
| G17 | 非睡眠活动优化 + Modern Standby 归因 | 活动功率继续用 BAT；SleepStudy 只用于事后待机归因 | 接口可用 | **不**在 S0 深度 Sleep 时保活 Python；RDP 持续连接是明确的 `remote_active` 工作负载 |

### 无法承诺无损移植的边界

- **PSI**：Windows 可定义 pressure proxy，但与 Linux PSI 的 stall time 没有逐字段 / 逐阈值等价关系。
- **独立 GPU 数值**：集成 GPU 的物理计量域、传感器输出与 driver API 可能不存在；无法从 CPU Package 热量自动分离出“GPU 温度 / GPU 功率”。
- **Linux CPU 的 `max_perf_pct` 与 Windows `PROCTHROTTLEMAX`**：都影响性能上限，但语义 / 时序 / OS 仲裁不同，不可宣称完全等价。
- **S0 睡眠中的高频采样**：要求 CPU 持续采样与真正低功耗驻留相冲突；事后日志归因不等于逐秒在线测量。
- **OEM 风扇 / EC / 固件控制**：若 Surface 没有公开可验证的接口，不将其纳入必需 capability，更不会绕过现有热保护。

## 3. 建议架构与代码边界

```text
现有 Python Core
  measurement / evidence / experiments / scheduler / controller / storage / lifecycle
             │
  [新增 platform contract / capability manifest]
             │
       ┌─────┴───────────────────────┐
       │                             │
  LinuxBackend                  WindowsBackend
  sysfs / intel_pstate          WMI / Power API / ETW / WTS
  thermald / systemd            Sensor Broker / Windows Service
       │                             │
  Linux HWPActuator             WindowsPowerActuator
                                     │
                           限权、已验签的硬件只读适配器
```

拟新增实现目录（后续代码 PR，再决定准确接口）：

- `src/sp7_powerlab/platform/base.py`：结构化 capability / availability / provenance / freshness / units；禁止将 `UNAVAILABLE` 当成 0。
- `src/sp7_powerlab/platform/linux/*`：现有 Linux 行为薄封装；本计划 PR 不移动代码。
- `src/sp7_powerlab/platform/windows/battery.py`：`BatteryStatus` 等接口 + 单位校正 + identity。
- `src/sp7_powerlab/platform/windows/sensors.py`：CPU Package/Core/RAPL/GPU provider；sensor provenance 及版本。
- `src/sp7_powerlab/platform/windows/session.py`：WTS、RDP、本地活动、power transitions。
- `src/sp7_powerlab/platform/windows/pressure.py`：Windows 专用 ETW/UX pressure proxy。
- `src/sp7_powerlab/actuators/windows_power.py`：Windows power scheme actuator（transaction / read-back / rollback）。
- `src/sp7_powerlab/platform/windows/helper.py`：受限 IPC 客户端；底层驱动或用户态 broker 应作为独立部署组件。

共享 `measurement.py`、`experiments.py`、`controller.py` 必须通过显式 contract 获得字段能力，不能直接继续在 Windows 路径读取 `/sys` / `/proc`，也不能让“有温度数值”自动意味着热保护已验证。

## 4. Windows 写入控制合同（启用前必须满足）

1. **写入边界**：仅在用户同意的独立电源方案中调用 Power API，初始仅调 DC；明确区分存储的 plan index、激活策略、实时 HWP/MSR request，不把任一层冒充另一层。
2. **EPP 映射**：先单参数固定实验确定 Windows 数值有效范围、性能/能耗/延迟特性；Linux `power`、`balance_power` 等名称不得直接转为已验证 Windows envelope。
3. **所有权与冲突**：记录活跃 power scheme / source / mode、Surface/OEM 工具、其他电源写入器；若 read-back 或 policy ownership 意外变化，停止控制、撤销正常写权限并留审计。
4. **事务与回滚**：snapshot 必须包含准确方案 GUID、AC/DC 配置、OEM / power mode 相关上下文；apply → verify → rollback，rollback 失败触发完整性 latch，不能自动继续下一候选。
5. **热保护**：经过验证的 CPU Package 传感器、节流证据及失败保护先于写入；`MSAcpi_ThermalZoneTemperature` 不是自动通过证明。
6. **受限驱动**：传感器 MSR 读取和 HWP request 读取优先 **只读**；禁止向可控用户进程暴露任意 MSR/物理内存读写、任意内核 IOCTL 或通用 shell。
7. **恢复验证**：重启、锁屏/解锁、USB/电源改变、休眠/唤醒、RDP 工作负载变更都必须重新核对真实控制状态；休眠阶段不强求持续采样。
8. **证据身份**：Windows 的 sensor/driver/provider version、OS build、firmware、actuator semantics 纳入 hard evidence / code identity。Windows Stage A–E 从零建立。

## 5. 驱动开发与签名交付计划（用户可自行推进）

**可行性假设**：用户愿意自行制作、签名、向微软相关流程提交 Windows 驱动。这是可独立推进的工程路径，但**自签名 ≠ 能在默认 Secure Boot 的正式 Windows 上加载**，也不代表已经通过微软签名或安全审核。

### 5.1 优先顺序

1. **先复用成熟源码 / 库**：
   - [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) / `LibreHardwareMonitorLib`：Intel 温度、RAPL 的传感器实现；
   - [PawnIO](https://github.com/namazso/PawnIO) 与 [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules)：受限硬件寄存器访问；优先研究模块白名单、更新及安全边界；
   - [Intel PCM](https://github.com/intel/pcm)：Windows RAPL / 性能计数参考实现（部署与签名另行评估）。
2. **不够时补最小能力**：优先对现有受限模块增加必要的只读 HWP MSR / CPU 亲和性接口，并上游审查；不创建无约束 `RWEverything` / `WinRing0` 风格通用驱动。
3. **单独构建签名 / 部署矩阵**：记录 Windows build、Secure Boot、HVCI / memory integrity、驱动签名来源、驱动版本、哈希、负载限制及是否真实加载。严禁“靠关闭保护永久解决”。
4. **合法信任路径**：开发自签测试与生产受信任签名区分。对正式机器优先核验 [Microsoft Hardware Dev Center 驱动签名选项](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings)，包括 attestation / HLK/WHQL 的现行条件；Partner Center 提交资格、证书、目标 OS 及兼容性需实际核实。
5. **验证门槛**：签名验证、安装/卸载与升级、服务 ACL、压力读数、坏输入、程序崩溃、睡眠/唤醒、驱动阻止规则、性能开销和事件日志；通过前只作为隔离测试，不成为 `CONTROL_ALLOWED` 依赖。

微软官方资料：[Driver signing](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-signing)、[Driver signing options](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings)、[Driver security checklist](https://learn.microsoft.com/en-us/windows-hardware/drivers/driversecurity/driver-security-checklist)。

### 5.2 用户可交付的驱动侧材料

- 实际签名 / 加载成功的驱动版本、原始工程链接、许可、SHA-256；不向仓库提交私钥、证书密码或签名凭据。
- 支持的只读 MSR 列表及 CPU 亲和性规则、温度/RAPL传感器样本和物理单位。
- Windows 10/11 + Secure Boot + HVCI 的适配矩阵、签名路径证明、驱动阻止列表 / Defender 兼容性结果。
- 停止 / 删除驱动后，PowerLab 应自动进入 `READ_ONLY` 的证据。

## 6. 分阶段交付（PR / 里程碑）

### W0 — 合同与最小探针（文档 / 软件）

- [ ] 明确 Windows capability manifest、provider API、`UNAVAILABLE` 语义和 unit/provenance/freshness。
- [ ] 只读收集 CPU/OS/BIOS/GPU/电池接口、电源方案、RDP 与睡眠能力；不得修改电源策略。
- [ ] Windows 平台单独 evidence identity，声明旧 Linux 数据绝不用于 Windows `VERIFIED`。
- **Exit**：只读 `doctor` / capability report 不崩溃；缺失传感器明确显示不可用；既有 Linux 单测仍通过。

### W1 — BAT Measurement Trust（先完成）

- [ ] Windows battery provider，记录 `RemainingCapacity` / `DischargeRate` / full charge、供电状态、计时信息。
- [ ] 在真实电池放电中完成 quantization、update cadence、积分-vs-能量差、最长 gap、resuming/grace 的验证。
- [ ] identity 不可靠时停止跨实验延续而不是生成假 battery epoch。
- **Exit**：至少按照 `PLAN2.md` Stage A 的可验证窗口、noise、量化条件获得当前 Windows 电池专属 Measurement Trust；否则停 `READ_ONLY`。

### W2 — 安全传感器（内核访问可选但受限）

- [ ] 使用 LHM/PawnIO 等验证 Core / Package 温度、RAPL；GPU 独立温度/功率可选。
- [ ] Windows 实时 throttling/thermal safety、driver/source 崩溃和故障注入。
- [ ] 测量采集程序自身 CPU、BAT 开销；缺失数据不越权放行。
- **Exit**：可在真机证明安全热保护，sensor/driver change 刷新 hard evidence；无法验证则只读运行。

### W3 — Windows CPU 单参数实验与回滚

- [ ] 限定的 power scheme actuator：EPP 单参数开始，其他参数待建立证据再增加。
- [ ] read-back 分为 policy index 与实际 HWP request 两层；后者接口不可用时，不把 policy index 假称“硬件生效”。
- [ ] 权限 helper、竞争写入检测、可重复 snapshot / rollback / failure latch。
- **Exit**：管理员隔离测试、受控 DC 供电、故障注入、热保护和回滚在 SP7 上通过；才准进入候选搜索。

### W4 — Windows Pressure / Workload / Trials

- [ ] ETW / performance counter 的 Windows UX pressure proxy；验证卡顿、I/O stall、媒体、GPU 的体验约束。
- [ ] RDP 视为正常远程 workload；采样上下文/实验对照中显式标记，不建议强制睡眠。
- [ ] A/B/A + independent revalidation、candidate negative outcomes 留痕、热/UX veto。
- **Exit**：Linux 测试无回归，Windows 软件与实机实验均有完整 evidence scope，可重复 rollback。

### W5 — 生产运行与净收益

- [ ] Windows Service / fixed one-shot，必要时使用受限 broker；服务崩溃/重启/睡眠后状态可恢复。
- [ ] Windows Stage D 使用覆盖与 Stage E `A1-B1-B2-A2` 的固定 vs 动态净收益实验。
- [ ] 实机验收 `KEEP_DYNAMIC_CONTROLLER` / `FIXED_GOOD_ENVELOPE` / `NEED_MORE_DATA` 三选一。
- **Exit**：系统自身运行开销计入 BAT 净收益；控制策略、现场硬件、证据身份与 StableReadiness 一致后才称 `REAL-HARDWARE-VALIDATED`。

## 7. 测试矩阵与阻断规则

- [ ] **供电**：AC、DC、DC 切 AC、AC 切 DC、满电和低电量；AC 数据不得充当放电 reward。
- [ ] **活动**：本地空闲、Chrome/视频、轻量交互、CPU/GPU 负载、正常 RDP 会话；相互分层比较。
- [ ] **睡眠**：关屏 → S0、S4、唤醒、快速重新连接、长时待机；不得使常驻监测器破坏深度 Sleep。
- [ ] **系统变化**：Windows 更新、Surface BIOS / Intel GPU driver、供电模式、active power scheme、外部屏幕/USB/网络。
- [ ] **缺失/故障**：WMI 返回空/错误、传感器异常、driver 被拦截、IPC 未授权、温度过期、并发电源写入、apply 部分成功、rollback 失败。
- [ ] **指标**：整机 BAT Wh/W、一致性、功耗/UX/thermal outcome、样本丢失、时戳漂移、helper/daemon 自身开销、睡眠 DRIPS。
- [ ] **安全**：任何热保护或测量前提不满足时 `READ_ONLY`；不可因开发环境、管理员权限或已有签名而绕过。

## 8. 代码/用户职责与下一行动

| 负责方 | 工作 | 证据 / 交付 |
| --- | --- | --- |
| Windows 软件适配 | capability/provider 接口、BAT 采样、Power API wrapper、实验隔离、Windows tests | 独立软件 PR 与可运行的只读诊断 |
| 驱动/硬件侧（用户可承担） | LHM/PawnIO 实机安装与观测；若必要，受限驱动构建和 Microsoft 签名流程 | 原工程、传感器清单、签名 / 加载结果、兼容性矩阵 |
| 联合验证 | Battery Measurement Trust、热安全、EPP read-back、rollback 与 Stage A–E | 真机日志/实验结果；有效 evidence identity |
| 仓库维护 | 保留 Linux contract、单测回归、后续 PR 各自明确版本与 scope | 不能从计划文档或静态源码推断“已完成” |

**第一个后续代码 PR**：实现跨平台 `BatteryProvider` + Windows `READ_ONLY` 监测 CLI，并添加 WMI 错误与 gauge quantization 测试。**第二个** 才引入可信传感器及热保护；**第三个** 再考虑 Windows CPU 写入。

本计划 PR 的交付范围仅为 **根目录的这份缺口 / 路线文档**，不安装/加载驱动、不更改现有机器的电源方案、不启动自动优化、不修改 `PLAN2.md` / Linux 运行时。

## 9. 参考资料（实现时以具体版本 / 代码审查为准）

- 本仓库：`AGENTS.md`、`PLAN2.md`、`docs/PROJECT_STATUS.md`、`docs/PROJECT_MAP.md`、`docs/FIRST_RUN.md`。
- [LibreHardwareMonitor / LibreHardwareMonitorLib](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)。
- [PawnIO](https://github.com/namazso/PawnIO) 与 [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules)。
- [Intel Performance Counter Monitor](https://github.com/intel/pcm)。
- [PowerWriteDCValueIndex / Power API](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powerwritedcvalueindex)（修改后激活方案的要求）。
- [Windows power management functions](https://learn.microsoft.com/en-us/windows/win32/power/power-management-functions)。
- [微软驱动签名政策与选项](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings)。
