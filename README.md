# Surface Pro 7 PowerLab v2

PowerLab v2 是一个只针对 **Surface Pro 7 / Intel Core i5-1035G4** 的个人续航优化实验系统。

它的唯一主目标是：

> 在用户体验不出现可感知退化、系统稳定且热状态可持续的前提下，尽可能降低真实整机 BAT 放电功率，从而延长续航。

v2 不再做 reading/coding/web 语义场景分类，也不让 LLM 实时控制 CPU。

## 核心结构

~~~
Telemetry
   ↓
Demand Observer + Thermal Observer
   ↓
Battery-Life Controller
   ↓
Verified HWP Envelope
   ↓
intel_pstate / Intel HWP
   ↓
真实 BAT W / Thermal result
   ↓
SQLite
   ↓
Hourly LLM analysis
   ↓
Reversible A/B/A trials
~~~

thermald 独立作为热安全层。

## 关键原则

- BAT 整机功耗是主 reward。
- 先消除浪费，再考虑限制性能。
- 用户体验和热稳定是硬约束。
- Linux scheduler / Intel HWP 负责瞬时 DVFS。
- PowerLab 只表达较慢的 EPP / max_perf_pct / Turbo 意图。
- ActivityWatch 只做 attribution，不是 controller 依赖。
- LLM 只做慢速分析、解释和实验提案。
- v2.0 不使用 ML 参与实时控制。
- v1 SQLite、CLI、scene/profile/policy 全部不兼容。

## 默认安全状态

仓库默认 automation.level = 0，并且 calibration.valid = false。

因此首次安装只读，不会自动修改 CPU 参数。

## 安装

~~~bash
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
~~~

如果旧 v1 runtime/powerlab.sqlite3 还存在：

~~~bash
sp7-powerlab reset-runtime --yes
~~~

v2 明确不迁移 v1 数据库。

## 第一次检查

~~~bash
sp7-powerlab doctor
bash scripts/check-integrations.sh
~~~

重点检查 Surface Pro 7 DMI、i5-1035G4、`intel_pstate=active`、HWP/EPP、Turbo control、BAT、RAPL、thermal sensor、thermald，以及是否存在 TLP / auto-cpufreq / Power Options / PPD 等冲突写入者。

## 只读 burn-in

~~~bash
bash scripts/install-user-services.sh
sp7-powerlab service status
sp7-powerlab observe now
sp7-powerlab observe power --hours 24
sp7-powerlab incidents --hours 24
~~~

建议首先只读运行约一周。

## Calibration

依次完成：

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

四阶段完成后才会将 calibration.valid 设为 true。

## Envelopes

初始 envelope：

- ECO_IDLE
- INTERACTIVE_EFFICIENT
- REMOTE_EFFICIENT
- MEDIA_EFFICIENT
- THERMAL_SAFE

配置中的默认参数都只是 candidate，不能直接“打标签”变成 VERIFIED。

Calibration 完成并安装 root helper 后，先把**机器当前真实 HWP 状态**收编成第一个已验证基线：

~~~bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT --note "当前 stock/HWP 状态作为首个真实基线"
~~~

这条命令读取真实 sysfs/HWP snapshot，并把实际 EPP / max_perf_pct / Turbo 记录为 VERIFIED；它不会把未应用过的 TOML candidate 冒充成已验证配置。

然后才考虑把 automation.level 调到 1。

## Root helper

~~~bash
bash scripts/install-root-helper.sh
~~~

helper 安装到 root-owned /opt/sp7-powerlab-helper，只接受固定 HWP 操作，不提供任意 root shell。

## Trial

proposal 示例：

~~~json
{
  "kind": "envelope",
  "baseline_envelope": "INTERACTIVE_EFFICIENT",
  "changes": {"max_perf_pct": 50}
}
~~~

实验判定阈值由本机 \`config/powerlab.toml\` 和本地代码控制。proposal 不能携带
validation、target 或自定义 trial ID，也不能覆盖 block 时长、settling、最小节能收益、
PSI/thermal/brightness/gap 门槛或实验匹配条件。

启动：

~~~bash
# assisted trial requires automation.level >= 2
sp7-powerlab trial start proposals/example.json
~~~

v2 使用 A1 baseline → B1 candidate → A2 baseline 完成第一次 crossover；第一次胜出后，
再重新采 A3 baseline → B2 candidate。B2 只和同一轮 A3 比，B1 的结果不会进入 B2
判分。

要验证另一个完整命名 envelope（例如 REMOTE_EFFICIENT），使用：

~~~json
{
  "kind": "envelope",
  "baseline_envelope": "INTERACTIVE_EFFICIENT",
  "candidate_envelope": "REMOTE_EFFICIENT"
}
~~~

命名 envelope 允许把完整参数组作为一个不可拆 candidate，但会自动要求更长的测量 block。

thermal pressure、thermald 异常、低电量、suspend/resume、不可比窗口和负面用户反馈都会阻止或回滚实验。

只有完成独立 revalidation、状态成为 `VERIFIED_WINNER` 后才能显式 promotion：

~~~bash
sp7-powerlab trial promote trial-xxxx
~~~

promotion 会把最优参数以 CANDIDATE 形式回写 `config/envelopes.toml` 供 Git 保存；当前机器的 VERIFIED 身份仍只存在于带 battery epoch / fingerprint / calibration 的 SQLite 验证记录中。Level 2 允许人工 promotion；未人工批准的自动 promotion 只在 Level 4 且 auto_promote=true 时允许。

## 用户反馈

~~~bash
sp7-powerlab feedback sluggish --trial-id trial-xxxx --notes "滚动明显变慢"
~~~

负面反馈是硬 reject 条件。

## 每小时 LLM

~~~bash
sp7-powerlab-agent hourly > runtime/hourly-pack-v2.json
sp7-powerlab-agent submit-decision runtime/llm-decision.json
~~~

LLM 允许动作：

- NO_CHANGE
- NEED_MORE_DATA
- INVESTIGATE_POWER_SPIKE
- INVESTIGATE_THERMAL_EVENT
- PROPOSE_WASTE_FIX
- PROPOSE_ENVELOPE_TRIAL
- ROLLBACK_TRIAL
- PROMOTE_ENVELOPE
- PROPOSE_MANUAL_RECALIBRATION

**不要把任意 Bash/Python、主 \`sp7-powerlab\` CLI 或 root-helper socket 暴露给
LLM/MCP。** LLM-facing capability 只允许调用 \`sp7-powerlab-agent\` 的
\`observe\` / \`hourly\` / \`submit-decision\`。

Level 2 下，agent 提交的 trial proposal 只会保存成待审核文件。真正的人类批准通过
LLM 不可调用的主 CLI 完成：

~~~bash
sp7-powerlab trial start proposals/<reviewed-proposal>.json
sp7-powerlab trial promote trial-xxxx
~~~

如果 MCP 拥有同 UID 的任意 shell/code execution，它就天然可以调用主 CLI 或直接连接
helper socket；这种部署**不具备**“人工批准”的技术安全边界，不能用于 Level 2 assisted
trial。

## 长期知识

~~~bash
sp7-powerlab knowledge-export
bash scripts/commit-knowledge.sh
~~~

高频 SQLite 不提交 Git。

## 文档

- [PLAN.md](PLAN.md)
- [docs/FIRST_RUN.md](docs/FIRST_RUN.md)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/OPERATIONS.md](docs/OPERATIONS.md)
- [docs/AI_LOOP.md](docs/AI_LOOP.md)
- [docs/MCP.md](docs/MCP.md)
