# Surface Pro 7 PowerLab

PowerLab 是一个只针对 **Microsoft Surface Pro 7 / Intel Core i5-1035G4 / Linux** 的个人续航优化实验系统。

> 在用户体验良好、系统稳定、热状态可持续的前提下，降低真实整机 BAT 放电功率，提高实际续航。

实时控制保持本地、确定性、可回滚；GPT-5.6 Sol 通过 Bash/workspace 负责慢速研究、异常调查、实验设计、代码维护和复杂度决策。项目不维护第二套 Agent action DSL。

## 当前成熟度

当前软件侧已经实现并通过自动化验证的核心链路包括：

- Measurement Trust 与 BAT power/energy consistency；
- calibration、battery epoch、hard evidence epoch；
- Frozen Reference / Recent Noise；
- bounded Candidate Scheduler；
- transactional HWP apply / read-back / rollback；
- A/B/A + independent revalidation；
- Control Safety / Learning Lifecycle / Investigation；
- UnexpectedPower / Drift / Attribution；
- Stage D real-usage validation；
- Stage E Dynamic vs Fixed-good A1-B1-B2-A2 Net Benefit；
- persistent fixed-good one-shot 与 Level-1 Dynamic runtime；
- StableReadiness、complexity deletion 和 Agent runtime context。

**真实 Surface Pro 7 新电池 Stage A–E 尚未完成。**

因此当前只能称为 **SOFTWARE-VALIDATED**，不能声称：

- 已找到 SP7 最省电参数；
- 已真机校准 noise / MUE / minimum arm duration；
- Dynamic Controller 一定优于 fixed-good；
- Level 3/4 已适合长期无人监督。

完整成熟度和当前真机缺口见 `docs/PROJECT_STATUS.md`。

## Agent 从哪里开始

AI / coding agent / Bash agent 的第一入口：

`AGENTS.md`

真实 SP7 运维时随后运行：

```bash
sp7-powerlab agent-context
```

它汇总当前机器的 hardware contract、Control Safety、battery/evidence epoch、Measurement Trust、calibration、Reference/Noise、trial/investigation、usage coverage、Net Benefit、StableReadiness 和 recommended next actions。

长期调查与实验纪律见 `docs/AI_LOOP.md`；源码、数据和 CLI 导航见 `docs/PROJECT_MAP.md`。

## 核心架构

```text
Linux / Intel HWP
      ^
      | deterministic verified-envelope control
      |
Telemetry -> Demand/Thermal -> Controller
      |
      +-> Measurement Trust
      +-> Reference / Noise
      +-> Scheduler -> Trial -> Evidence
      +-> UnexpectedPower -> Investigation -> Attribution
      |
      +-> Stage D validation
      +-> Stage E Dynamic vs Fixed-good
      +-> StableReadiness -> STABLE
```

BAT 整机放电是主能量证据；RAPL、CPU、PSI、GPU、process attribution 等只作为解释变量或约束。

## 安装

```bash
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

bash scripts/install-user-services.sh
bash scripts/install-root-helper.sh
```

然后：

```bash
sp7-powerlab doctor
sp7-powerlab agent-context
```

systemd / root helper / breaking runtime 细节见 `docs/DEPLOYMENT.md`。

## 第一次运行 / 新电池

不要从 README 直接执行完整学习流程。按唯一顺序跟随：

`docs/FIRST_RUN.md`

生命周期骨架：

```text
Stage A  Measurement Trust + Calibration
Stage B  Verified Baseline + Natural Reference / Noise
Stage C  Bounded Coarse Search
Stage D  Independent Validation + Real-Usage Burn-in
Stage E  End-to-End Net Benefit / Complexity Selection
         -> StableReadiness -> STABLE
```

具体命令、trial、feedback、recovery、Stage E 和 runtime 切换的唯一详细 runbook 是 `docs/OPERATIONS.md`。

## 运行模式

默认从 `automation.level = 0` 开始。

正式 Stage E 最终只选择：

- `KEEP_DYNAMIC_CONTROLLER`：Level-1 main service 长期运行，只切 VERIFIED envelope；
- `FIXED_GOOD_ENVELOPE`：main daemon disabled，最小 fixed one-shot 在 login/reboot 重新应用已验证 envelope；
- `NEED_MORE_DATA`：证据不足，不强行选择。

最终切换使用：

```bash
sp7-powerlab envelope activate-dynamic
sp7-powerlab envelope activate-fixed-good
```

Automation Level 2+ 是按需学习能力，不是 formal Stage E 的长期 treatment。完整语义见 `PLAN2.md`。

## 常用入口

```bash
sp7-powerlab agent-context
sp7-powerlab doctor
sp7-powerlab lifecycle status
sp7-powerlab lifecycle readiness
sp7-powerlab safety status
sp7-powerlab evidence status
sp7-powerlab evidence noise
sp7-powerlab scheduler status
sp7-powerlab unexpected-power list
sp7-powerlab investigation list
sp7-powerlab net-benefit summary
sp7-powerlab review-pack
```

命令用途和调查路线见 `docs/PROJECT_MAP.md`；详细操作见 `docs/OPERATIONS.md`。

## 设计原则

- 不让 LLM 进入 10 秒级控制回路；
- 不让同名对象代替 evidence identity；
- 不跨 Charging / suspend / gap 重新拼实验；
- candidate 造成的 thermal / PSI / media / UX 坏结果必须留下；
- root helper 只暴露有限 HWP 操作，不提供任意 root shell；
- breaking schema / evidence 语义优先 fail-fast + explicit reset，不维护隐藏兼容层；
- 如果复杂系统没有可重复的实际净收益，删除复杂度是成功结果；
- 持续本地重计算通常应转移到远程机器。

正式设计合同见 `PLAN2.md`。

## 文档职责

| 文档 | 唯一职责 |
| --- | --- |
| `AGENTS.md` | AI 入口、truth hierarchy、不可绕过 contracts |
| `PLAN2.md` | 正式设计合同 |
| `docs/PROJECT_STATUS.md` | 当前软件成熟度与真机验证缺口 |
| `docs/PROJECT_MAP.md` | 源码 / SQLite / CLI / 调查导航 |
| `docs/AI_LOOP.md` | GPT-5.6 Sol 调查、实验和长期工作纪律 |
| `docs/FIRST_RUN.md` | 第一次部署 / 新电池从 Stage A 到 STABLE 的顺序 |
| `docs/OPERATIONS.md` | 已部署系统的唯一详细 runbook |
| `docs/DEPLOYMENT.md` | systemd / root helper / breaking runtime 部署 |

高频 SQLite runtime 数据不提交 Git。
