# PowerLab Project Status

本文件只回答：

1. 软件实现成熟到哪里；
2. 真实 Surface Pro 7 还缺哪些验证。

实时机器状态看：

```bash
sp7-powerlab agent-context
```

设计语义见 `PLAN2.md`；代码/CLI 导航见 `docs/PROJECT_MAP.md`；具体操作见 `docs/OPERATIONS.md`。

## 当前软件基线

目标平台：

- Microsoft Surface Pro 7；
- Intel Core i5-1035G4；
- Linux + intel_pstate / Intel HWP。

当前持久 runtime：

- SQLite schema v10；
- evidence semantics v10；
- breaking schema / evidence changes 使用 fail-fast + explicit reset；
- 不维护旧 schema migration 或双语义 fallback。

AI 入口：

- `AGENTS.md`
- `sp7-powerlab agent-context`
- `docs/AI_LOOP.md`

## 状态定义

- **IMPLEMENTED**：主 runtime 已接入；
- **SOFTWARE-VALIDATED**：已有自动化测试、静态检查或软件 smoke；
- **REAL-HARDWARE-VALIDATED**：已在目标 SP7 + 当前有效 evidence context 上验证；
- **BLOCKED-ON-HARDWARE**：软件可继续维护，但结论依赖目标机器数据。

CI/pytest 通过不等于 REAL-HARDWARE-VALIDATED。

## 当前状态矩阵

| 领域 | 软件状态 | 真机状态 | 下一验证 |
| --- | --- | --- | --- |
| Hardware contract / ownership | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | doctor、thermald、writer conflict |
| BAT Measurement Trust | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | gauge cadence、quantization、power/energy consistency |
| Calibration | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | cold idle / interactive / media / bounded burst |
| Battery / hard evidence epochs | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 新电池、kernel/firmware 变化 |
| Frozen Reference / Recent Noise | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实 natural workload |
| HWP actuator / rollback | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | transactional apply/read-back/rollback |
| Control Safety / Lifecycle | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | Level 0→1、thermal/helper failure |
| Trial / Evidence Engine | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | A/B/A + independent revalidation |
| Candidate Scheduler | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实 headroom、budget、stop rules |
| UnexpectedPower / Attribution | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | browser/media/network/device regressions |
| Stage D usage coverage | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | representative usage |
| Stage E Net Benefit | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | Dynamic vs Fixed A1-B1-B2-A2 |
| Persistent fixed-good | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | login/reboot、HWP persistence |
| Level-1 Dynamic runtime | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | real overhead、UX、BAT benefit |
| Agent context / review pack | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 长期调教可用性 |
| Level 3/4 automation | IMPLEMENTED | BLOCKED-ON-HARDWARE | 先证明 Level 0–2 |

核心控制/evidence/runtime contracts 已有软件测试保护；详细 invariant 由 `AGENTS.md` 和 `PLAN2.md` 维护，本文件不再复制。

## 当前最重要的下一里程碑

**真实新电池 Stage A。**

按 `docs/FIRST_RUN.md`：

1. 安装/更新 main service 与 root helper；
2. Level 0；
3. `sp7-powerlab doctor`；
4. 建立新 battery epoch；
5. 收集真实 Discharging telemetry；
6. BAT gauge cadence / quantum；
7. preliminary Measurement Trust；
8. calibration；
9. current-epoch Measurement Trust；
10. Stage B natural reference/noise；
11. 真机事实支持后再进入 Stage C/D/E。

## 明确 defer 到真机

以下问题不继续靠源码猜：

- Stage E 0.10W threshold 是否需要绑定 empirical paired noise/MUE；
- suspend/resume 后是否需要 fixed resume hook；
- Level 3/4 是否值得保留；
- monitoring / Level-1 daemon 的真实整机 overhead；
- Dynamic Controller 是否比 fixed-good 有净收益；
- BAT gauge 的真实 cadence、quantization 和最短可辨效果。

真机数据证明当前假设不成立时，再重新打开对应架构。

## 当前不能声称

在真实 Stage A–E 完成前，不要声称：

- 已找到 SP7 最省电参数；
- noise/MUE 已充分真机校准；
- minimum arm duration 已真机确认；
- Dynamic Controller 一定优于 fixed-good；
- Level 3/4 已适合长期无人监督。

## 刻意不做

当前不做：

- LLM per-sample controller；
- semantic scene taxonomy 作为实时控制核心；
- deep neural network / full RL 核心控制；
- Bayesian Optimization 作为默认搜索器；
- continuous arbitrary EPP search；
- scheduled Agent review timer；
- 第二套 Agent action DSL；
- 旧 runtime schema 兼容层。

## 何时更新本文件

只在以下情况更新：

- schema / evidence semantics 改变；
- 核心能力实现状态改变；
- 完成新的真实 SP7 验证阶段；
- Stage E 得到新的真实结论；
- 新增或删除核心架构组件。

普通 commit、测试数量变化、单次 trial 或当前 live state 不属于本文件。
