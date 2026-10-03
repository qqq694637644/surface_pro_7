# PowerLab Project Status

本文件只回答两个问题：

1. 当前软件实现成熟到哪里；
2. 真实 Surface Pro 7 还缺哪些验证。

实时机器状态不要从这里推断。真实 SP7 上运行：

```bash
sp7-powerlab agent-context
```

正式设计语义见 `PLAN2.md`；具体操作见 `docs/OPERATIONS.md`。

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

- `AGENTS.md`；
- `sp7-powerlab agent-context`；
- `docs/AI_LOOP.md`。

## 状态定义

- **IMPLEMENTED**：主 runtime 已接入；
- **SOFTWARE-VALIDATED**：已有自动化测试、静态检查或软件 smoke；
- **REAL-HARDWARE-VALIDATED**：已在目标 SP7 + 当前有效 battery/evidence context 上验证；
- **BLOCKED-ON-HARDWARE**：软件可继续维护，但结论依赖目标机器数据。

除非明确写 REAL-HARDWARE-VALIDATED，否则不要把 CI/pytest 当成真机结论。

## 当前状态矩阵

| 领域 | 软件状态 | 真机状态 | 下一验证 |
| --- | --- | --- | --- |
| Hardware contract / ownership | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | Stage A doctor、thermald、writer conflict |
| BAT Measurement Trust | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | gauge cadence、quantization、power/energy consistency |
| Calibration | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | cold idle / interactive / media / bounded burst |
| Battery / hard evidence epochs | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 新电池 identity、energy_full、kernel/firmware 变化 |
| Frozen Reference / Recent Noise | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实自然 workload 的 reference/noise |
| HWP actuator / rollback | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | SP7 transactional apply/read-back/rollback |
| Control Safety / Lifecycle | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | Level 0→1、thermal/helper failure behavior |
| Trial / Evidence Engine | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | A/B/A + independent revalidation |
| Candidate Scheduler | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实 headroom、budget、stop rules |
| UnexpectedPower / Attribution | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实 browser/media/network/device regressions |
| Stage D usage coverage | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | representative usage days / trusted fraction |
| Stage E Net Benefit | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | Dynamic vs Fixed A1-B1-B2-A2 |
| Persistent fixed-good | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | login/reboot、HWP persistence、battery/hard revalidation |
| Level-1 Dynamic runtime | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | monitoring overhead、real UX / BAT benefit |
| Agent context / review pack | SOFTWARE-VALIDATED | BLOCKED-ON-HARDWARE | 真实长期调教可用性 |
| Level 3/4 automation | IMPLEMENTED | BLOCKED-ON-HARDWARE | 先证明 Level 0–2 全链可靠 |

## 已闭合的软件合同

软件侧目前由测试保护的关键 invariant：

- normal HWP write 只允许在 live hardware/control context 有效时发生；
- root helper protocol / implementation identity 必须与主 runtime 匹配；
- Measurement Trust 绑定当前 battery / hard evidence context；
- Charging、suspend/resume、超限 gap 或 epoch change 不能过滤后重新拼成连续实验；
- Reference/Noise 绑定 envelope content hash 与 compatibility generation；
- trial evidence 使用独立 evidence_scope_key，不跨 baseline/workload/policy 串证据；
- candidate 造成的 thermal / PSI / media / UX 坏结果必须保留；
- Stage E 使用固定本地 contract、连续 Discharging block 和 current treatment identity；
- Dynamic runtime 必须证明 daemon 实际加载的 code/config identity；
- fixed-good 写入前重新验证 live hard + BAT identity，并要求 actual HWP read-back；
- runtime mode 切换不以旧 heartbeat 冒充新进程 ready；
- StableReadiness 同时检查 selected policy identity 和 actual runtime reality。

实现细节不要复制到本文件；查源码位置见 `docs/PROJECT_MAP.md`，查设计原因见 `PLAN2.md`。

## 当前最重要的下一里程碑

**真实新电池 Stage A。**

建议顺序：

1. 安装/更新 main service 与 root helper；
2. Level 0；
3. `sp7-powerlab doctor`；
4. 建立新 battery epoch；
5. 收集真实 Discharging telemetry；
6. 测 BAT gauge cadence / quantum；
7. preliminary Measurement Trust；
8. calibration；
9. current-epoch Measurement Trust；
10. Stage B natural reference/noise；
11. 只有真机事实支持时才进入 Stage C/D/E。

唯一首次运行顺序见 `docs/FIRST_RUN.md`。

## 当前明确 defer 到真机的数据问题

以下不继续靠源码猜测：

- Stage E practical threshold 0.10W 是否需要绑定 empirical paired noise/MUE；
- suspend/resume 后 SP7 是否需要额外 fixed resume hook；
- Level 3/4 是否值得长期保留；
- monitoring / Level-1 daemon 的真实整机 overhead；
- Dynamic Controller 是否真的比 fixed-good 有净收益；
- BAT gauge 在新电池上的真实 cadence、quantization 和最短可辨效果。

真机数据若证明当前假设不成立，再重新打开对应架构。

## 当前不能声称

在真实 Stage A–E 完成前，不要声称：

- 已找到 SP7 最省电参数；
- 当前 noise/MUE 已充分真机校准；
- 当前 minimum arm duration 已真机确认；
- Dynamic Controller 一定优于 fixed-good；
- Level 3/4 已适合长期无人监督。

## 刻意不做

当前不做：

- LLM per-sample controller；
- semantic scene taxonomy 作为实时控制核心；
- deep neural network / full RL 核心控制；
- Bayesian Optimization 作为默认搜索器；
- continuous arbitrary EPP search；
- 持续本地满载作为优化主目标；
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

普通 commit、测试数量变化、单次 trial 或当前机器 live state 不属于本文件。
