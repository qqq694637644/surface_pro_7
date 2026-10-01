# Surface Pro 7 PowerLab

PowerLab 是一个只针对 **Microsoft Surface Pro 7 / Intel Core i5-1035G4 / Linux** 的个人续航优化实验系统。

目标：

> 在用户体验良好、系统稳定、热状态可持续的前提下，尽量降低真实整机 BAT 放电功率，提高实际续航。

它不是“让 LLM 每 10 秒调 CPU”的系统。实时控制保持本地确定性；AI 负责长期观察、调查异常、提出和审查实验、修改代码和配置，以及判断继续优化是否值得。

## 当前成熟度

软件侧已经实现：

- Measurement Trust
- Frozen Reference / Recent Noise
- Evidence Engine
- evidence scope isolation（epoch + compatibility + baseline + workload/reference strata + candidate）
- A/B/A + independent revalidation
- Control Safety / Learning Lifecycle / Investigation
- UnexpectedPower + Attribution
- finite Candidate Scheduler
- tiered telemetry / Diagnostic Burst
- Net Benefit / complexity deletion
- bounded A1-B1-B2-A2 Net Benefit campaign + runtime policy fingerprint/covariate gates
- STABLE denominator floor（usage/trusted time + distinct days + observation span）
- Automation Level 0–4
- AI runtime context

但**真实 Surface Pro 7 新电池 Stage A 尚未完成**。

因此当前不能声称：

- 已找到 SP7 最省电参数；
- 已真机校准 noise / minimum arm duration；
- Dynamic Controller 一定优于 fixed envelope。

完整进度：

docs/PROJECT_STATUS.md

## AI / Agent 从哪里开始

任何拥有 Bash/workspace 能力的 AI 先读：

AGENTS.md

然后运行：

~~~bash
sp7-powerlab agent-context
~~~

它会给出当前：

- Git/schema
- 硬件契约
- Control Safety
- Learning Lifecycle
- battery epoch
- calibration
- Measurement Trust
- Evidence/ref/noise
- active trial/investigation
- Scheduler blockers
- usage coverage
- Net Benefit
- runtime stage
- recommended next actions

源码和 CLI 导航：

docs/PROJECT_MAP.md

Agent 调教纪律：

docs/LLM_BEHAVIOR.md

## 核心模型

~~~
Telemetry
   |
   v
Measurement Trust
   |
   v
current hard evidence epoch
   |
   v
Reference + Noise (envelope revision aware)
   |
   v
Bounded Candidate Scheduler
   |
   v
Trial + deterministic Evidence
   |
   v
Stage D real-usage validation/burn-in
   |
   v
Stage E end-to-end Net Benefit
   |
   v
STABLE
~~~

异常功耗走另一条链：

~~~
UnexpectedPower / Drift
   |
   v
Investigation
   |
   v
Attribution
   |
   v
verify hypothesis
   |
   +--> expected workload / no action
   |
   +--> confirmed regression / reopen optimization
~~~

## 为什么特别重视热

Surface Pro 7 的 i5-1035G4 在持续高本地功耗下容易明显升温，随后可能进入热压和降频。

本项目的实际使用假设是：

- 日常交互和轻量本地工作在 SP7 上完成；
- 持续编译、训练、长计算通常放远程服务器；
- 目标不是让 SP7 长时间满载跑得更猛，而是在真实轻中负载下找到更好的续航/体验平衡。

## 安装

~~~bash
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
~~~

默认：

- automation.level = 0
- 没有有效 calibration 时控制保持只读
- 未建立 Measurement Trust 时 Scheduler 不探索

## 第一次运行

先读：

docs/FIRST_RUN.md

最少先做：

~~~bash
sp7-powerlab doctor
sp7-powerlab agent-context
~~~

安装用户服务：

~~~bash
bash scripts/install-user-services.sh
~~~

安装 root helper：

~~~bash
bash scripts/install-root-helper.sh
~~~

root helper 只提供有限 HWP inspect/snapshot/apply/restore，不提供任意 root shell。

## 新电池 / 新 battery epoch

更换电池后：

先确认 service 已经记录新电池 telemetry，再执行：

~~~bash
sp7-powerlab calibrate new-battery
~~~

随后先收集只读 telemetry，建立 preliminary Measurement Trust：

~~~bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
~~~

preliminary Measurement Trust READY 后，再完成 calibration：

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

Calibration 改变 hard evidence context，因此完成后必须重新建立当前 epoch 的 Measurement Trust：

~~~bash
sp7-powerlab evidence gauge --hours 6
sp7-powerlab evidence trust --hours 6
~~~

只有这次 current-epoch Measurement Trust READY 后，才开始 frozen reference/noise、trial 或 Scheduler 学习。

具体流程和真机注意事项见 docs/FIRST_RUN.md。

## Verified Envelope

仓库中的 config/envelopes.toml 只是候选参数来源。

它不等于：

> 这个 envelope 已经在当前 battery epoch / calibration / hard evidence epoch 上被验证。

第一条真实 baseline 应从机器当前 HWP state 收编，例如：

~~~bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT \
  --note "current real HWP baseline"
~~~

正常 Controller 只使用 verified envelope。

## Trial

辅助实验要求 automation.level >= 2。

示例 proposal：

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

实验协议：

~~~
A1 baseline
B1 candidate
A2 baseline

A3 fresh baseline
B2 candidate
~~~

B2 必须独立再次达到 Minimum Useful Effect。

Candidate 造成的 PSI、thermal、media 或 UX 坏结果不能被过滤掉。

最终 verdict：

- WIN
- LOSE
- PRACTICALLY_EQUIVALENT
- INCONCLUSIVE

只有 VERIFIED_WINNER 才能 promotion。

## Automation Levels

- Level 0：只读
- Level 1：只切 verified envelope
- Level 2：Scheduler 可以提出 candidate，默认人工启动 trial
- Level 3：全部 gate 通过后 daemon 可以自动开始低风险 trial
- Level 4：在 Level 3 基础上，auto_promote=true 时允许自动 promotion

Automation Level 是 daemon/Scheduler 的默认治理，不是同 UID Bash Agent 的权限沙箱。

## 日常状态

优先：

~~~bash
sp7-powerlab agent-context
~~~

按问题继续：

~~~bash
sp7-powerlab lifecycle status
sp7-powerlab safety status
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab scheduler status
sp7-powerlab unexpected-power list
sp7-powerlab investigation list
sp7-powerlab overhead summary
~~~

详见 docs/OPERATIONS.md。

## PowerLab 自己也必须证明值得

最终不是只比较两个 envelope。

需要用 MinimalMeter 对照：

- fixed-good
- monitoring-only observer overhead
- dynamic controller

Automation Level 2+ 的 Scheduler/Agent 学习能力按需运行，不作为必须长期常开的第四种 Stage E treatment。

系统允许得出：

- KEEP_DYNAMIC_CONTROLLER
- FIXED_GOOD_ENVELOPE
- NEED_MORE_DATA

如果复杂系统没有足够实际净收益，回到 fixed-good 是正确结果。

## 文档

### AI

- AGENTS.md — AI 第一入口
- docs/PROJECT_MAP.md — 项目导航地图
- docs/PROJECT_STATUS.md — 当前实现/真机验证进度
- docs/LLM_BEHAVIOR.md — Agent 调教纪律
- docs/AI_LOOP.md — Agent/runtime 交互
- docs/MCP.md — 可选结构化接口

### 设计

- PLAN2.md — 正式设计合同

### 使用

- docs/FIRST_RUN.md — 首次真机部署
- docs/OPERATIONS.md — 日常运行
- docs/DEPLOYMENT.md — systemd/root helper

高频 SQLite runtime 数据不提交 Git。
