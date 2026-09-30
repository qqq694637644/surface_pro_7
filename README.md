# Surface Pro 7 PowerLab

Surface Pro 7 PowerLab 是一个面向个人 Linux 设备的**持续、场景化、可回滚电量优化系统**。

它不是另一个“把所有节能开关一次打开”的脚本。PowerLab 把工作拆成两条时间尺度：

- **本地实时层**：持续记录真实电池功耗、应用/进程/温度/亮度/网络等信息，并只在已经验证过的 profile 之间切换。
- **慢速 LLM 层**：大约每 1 小时读取聚合后的使用历史，决定不动作、继续收集、调查回归、提出一个受控 trial、回滚或晋升已验证策略。

LLM 不负责毫秒级调频，也不能把“我觉得变好了”当作 reward。真正的效果由电池侧能量、任务完成时间、数据质量、场景可比性和用户反馈共同决定。

## v1.0 已实现

### 连续观测

- Surface Pro 7 DMI、Kernel、BIOS、系统版本
- BAT 整机 `power_now / energy_now`
- 电池健康度、容量、循环次数
- CPU governor / EPP / 频率 / Turbo / cpuidle
- PSI
- thermal
- brightness
- Wi-Fi 流量
- Intel RAPL
- DRM/i915 可用 GPU 指标
- 进程 CPU / RSS / I/O
- ActivityWatch / awatcher 前台应用、窗口标题、AFK
- MPRIS/playerctl 播放状态
- 活跃应用版本（能安全取得时）

### 场景识别

当前内置场景：

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

系统允许 `unknown` 和 `mixed`，不会为了“智能”而强行分类。

### 数据层

SQLite/WAL 连续数据库保存：

- raw samples
- app events
- process samples
- contexts
- sessions
- minute rollups
- profiles
- context policies
- trials
- trial results
- task runs
- user feedback
- LLM runs
- decisions
- system version drift
- battery health
- rejection memory

高频 raw 数据默认保留 30 天；分钟 rollup、trial/策略知识可以长期保留。

### 测量可信度

- 时间加权能量积分
- 非 Discharging 区间不进入续航 objective
- suspend/collector gap 不被当作有效运行时间
- 核心功耗缺失 => `INSUFFICIENT_DATA`
- 候选与 baseline 做场景/亮度/Kernel/温度/负载等可比性检查
- 交互场景要求多个有效 session
- 固定任务按 **Wh/task + 完成时间** 比较，而不是只比平均 W
- 第一次胜出 => `REVALIDATION`
- 新数据再次胜出后才成为 `CANDIDATE_WINNER`

### 策略与执行器

支持：

- Power Options：`power-daemon-mgr`
- power-profiles-daemon：`powerprofilesctl`
- 受限 sysfs 参数执行器
- no-op safe baseline

PowerLab 强制“单一写入者”。`policy.actuator = "auto"` 时选择顺序为：

```text
Power Options
→ power-profiles-daemon
→ sysfs
```

如果选择外部 power manager，直接 EPP/max-frequency sysfs trial 会被阻止，避免两个 daemon 互相覆盖设置。

### 低风险 root helper

需要无人值守修改 EPP/频率等参数时，可以安装独立 root helper。

它通过 Unix socket 只接受 PowerLab 声明过的参数和值范围，不提供任意 root shell。

### Trial 状态机

```text
WAITING_FOR_CONTEXT
        ↓
SNAPSHOTTED
        ↓
APPLIED
        ↓
MEASURING
        ↓
EVALUATING
   ┌────┼──────────┐
   │    │          │
REJECT  INSUFFICIENT
   │               │
rollback       keep observing
                 REVALIDATION
              ↓
      CANDIDATE_WINNER
              ↓
           PROMOTED
```

如果 LLM 提案时当前场景不匹配，trial 可以停在 `WAITING_FOR_CONTEXT`；collector 以后检测到匹配场景再启动，不要求用户配合实验。

Trial 不只支持 direct sysfs 参数，也支持当前唯一执行器上的外部 profile A/B：

```json
{
  "change": {
    "parameter": "profile.id",
    "from": "ppd-balanced",
    "to": "ppd-power-saver"
  }
}
```

外部 profile 默认必须人工批准。只有候选 profile 明确写入：

```toml
[evidence]
auto_trial_allowed = true
```

并且场景/电量/风险门槛同时满足时，才允许无人值守启动。

### 回滚

每个 sysfs trial 在应用前保存真实状态。

- apply/read-back 失败：立即恢复
- 自动评价失败：恢复
- 用户手动 override：恢复 trial
- thermal emergency：恢复
- collector 重启发现未完成 trial lock：恢复
- sysfs profile 场景切换：恢复上一个 profile 应用前的状态

## 安装

Python 3.10+：

```bash
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

先检查：

```bash
sp7-powerlab doctor
sp7-powerlab actuator-inspect
sp7-powerlab collect-once
sp7-powerlab collector-benchmark --samples 12
```

详细步骤见：

- [docs/FIRST_RUN.md](docs/FIRST_RUN.md)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/MCP.md](docs/MCP.md)
- [docs/OPERATIONS.md](docs/OPERATIONS.md)
- [docs/AI_LOOP.md](docs/AI_LOOP.md)
- [PLAN.md](PLAN.md)

## ActivityWatch / awatcher

PowerLab 不重新实现桌面活动追踪。

建议运行 ActivityWatch server，并在 Linux Wayland 上使用 awatcher。GNOME Wayland 需要可提供 focused-window 信息的 GNOME 扩展/DBus 路径。

默认：

```toml
[activity]
enabled = true
server_url = "http://127.0.0.1:5600"
store_window_title = true
```

ActivityWatch 不可用时 collector 会继续运行，场景识别降级为进程/系统信息。

## 首次运行的安全默认值

默认：

```toml
[automation]
auto_switch_verified_profiles = true
auto_run_low_risk_trials = false
auto_promote_profiles = false
```

仓库自带的唯一 verified 默认 profile 是 `safe-baseline`。

这意味着安装后会**先观察，不会自己开始新参数实验**。

## 常驻服务

安装用户级 collector 和 hourly pack timer：

```bash
bash scripts/install-user-services.sh
```

查看：

```bash
systemctl --user status sp7-powerlab-collector.service
systemctl --user status sp7-powerlab-hourly.timer

sp7-powerlab service-status
sp7-powerlab observe --hours 1
sp7-powerlab contexts --hours 24
```

如果使用外部 Bash MCP 每小时调度 `scripts/mcp-hourly.sh`，可以关闭仓库自带 hourly timer，避免重复生成 pack；collector 仍保持常驻。

## 每小时 LLM/MCP

生成 context pack：

```bash
sp7-powerlab hourly --output runtime/hourly-pack.json
```

LLM 必须从以下动作选择一个：

```text
NO_CHANGE
NEED_MORE_DATA
PROPOSE_TRIAL
ROLLBACK_TRIAL
PROMOTE_PROFILE
INVESTIGATE_REGRESSION
UPDATE_CONTEXT_RULE
```

将结构化输出保存后：

```bash
sp7-powerlab llm-apply runtime/llm-decision.json
```

详细契约见 [docs/MCP.md](docs/MCP.md)。

## 固定任务：编译/导出/批处理

collector 正在运行时：

```bash
sp7-powerlab task-run   --scene compile   --label project-build   -- make -j4
```

PowerLab 记录：

```text
energy Wh
duration
exit code
profile
trial
data quality
```

同类任务用总能量和完成时间评价，不把更低平均 W 自动当作更省电。

## 人工反馈

```bash
sp7-powerlab feedback rejected   --trial-id t-xxxx   --responsiveness 2   --stability good   --notes "功耗下降，但滚动明显卡顿"
```

拒绝会进入长期 rejection memory，供后续 LLM 避免重复踩坑。

## 手动 override

```bash
sp7-powerlab override set safe-baseline
sp7-powerlab override status
sp7-powerlab override clear
```

手动 override 会中止正在执行的 trial。

## 无人值守 sysfs trial

只有当你明确选择 `sysfs` 为唯一 power writer 时才建议启用。

先停止/禁用会写同一参数的其他 power manager，然后：

```toml
[policy]
actuator = "sysfs"
```

安装受限 root helper：

```bash
bash scripts/install-root-helper.sh
sp7-powerlab actuator-inspect
```

安装脚本会先构建 wheel，再把 helper 安装到 root-owned 的：

```text
/opt/sp7-powerlab-helper/
```

systemd root service 不会直接执行你日常可写的 Git checkout 或 editable venv。升级 helper 时重新运行安装脚本即可。

确认 helper 可用后，才考虑：

```toml
[automation]
auto_run_low_risk_trials = true
```

不要在 Power Options、TLP、auto-cpufreq、PPD 等同时控制相同参数时打开 direct sysfs trial。

## 数据与 Git

`runtime/` 是本机高频数据库，不提交 Git。

长期值得提交：

```text
config/
proposals/
history/
schemas/
PLAN.md
```

`sp7-powerlab hourly` 会自动更新 `history/continuous/` 的紧凑知识快照。需要把这些长期知识同步到远端时，可按自己的节奏运行：

```bash
bash scripts/commit-knowledge.sh
# 或
bash scripts/commit-knowledge.sh --push
```

默认不会每小时自动 Git push。

本项目按个人设备使用设计，不实现日志脱敏流程。

## 旧 v0.2 CLI

`start / collect / finish / history / ai-pack` 仍保留兼容，但持续优化主路径已经切换为：

```text
service-run
observe / contexts
profile
trial
task-run
feedback
hourly
llm-apply
```

旧实验汇总也已经升级为时间加权和 gap-aware，证据不足不再通过验收。

## 当前验证边界

仓库可以在非 Linux 开发环境运行单元测试和降级 smoke test，但：

- BAT sysfs
- Intel RAPL/i915
- GNOME Wayland ActivityWatch/awatcher
- Power Options
- PPD
- root helper
- suspend/resume
- Surface Pro 7 实际电池曲线

必须在你的真实 SP7 Linux 上完成硬件验证之后，才应打开无人值守 trial/promote。

**代码支持自动化 ≠ 机器已经被验证适合自动化。**
