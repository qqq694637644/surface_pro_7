# AI 持续优化循环

## 两个时间尺度

### 实时：本机 deterministic

collector 持续采样，并在已验证 profile 之间切换。

实时路径不调用 LLM。

### 慢速：约每小时 LLM

```text
collector
   ↓
SQLite
   ↓
sessions + minute rollups
   ↓
knowledge-pack
   ↓
LLM
   ↓
structured decision
   ↓
validator
   ↓
trial / no-op / rollback / investigation
```

## 每小时流程

### 1. PowerLab 自检

```bash
sp7-powerlab hourly --output runtime/hourly-pack.json
```

它会：

- 检查版本漂移
- 评价正在运行的 trial
- 推进 REVALIDATION
- 根据配置决定是否晋升
- 生成新的 context pack

### 2. LLM 阅读 pack

pack 包括：

- 当前样本/场景
- 最近 1h / 24h scene 能耗
- 活跃应用占比
- 回归趋势
- battery health
- system version
- profiles
- context policies
- active/recent trials
- rejection memory
- human feedback
- recent decisions/system events
- 自动化开关

LLM 不需要读取整个高频 SQLite。

需要更多细节时可以通过 Bash MCP 调用：

```bash
sp7-powerlab observe --hours 6
sp7-powerlab contexts --hours 24 --scene coding_interactive
sp7-powerlab current
sp7-powerlab trial status
sp7-powerlab profile list
```

### 3. LLM 只能选择有限动作

```text
NO_CHANGE
NEED_MORE_DATA
PROPOSE_TRIAL
ROLLBACK_TRIAL
PROMOTE_PROFILE
INVESTIGATE_REGRESSION
UPDATE_CONTEXT_RULE
```

### 4. Validator 决定能否执行

```bash
sp7-powerlab llm-apply runtime/llm-decision.json
```

LLM 不能绕过：

- parameter registry
- range/enum validation
- single-writer policy
- context match
- confidence
- active trial lock
- auto-run 开关
- human approval requirement
- revalidation

## 最常见的正确动作：NO_CHANGE

持续优化不等于每小时调一次参数。

以下情况应该不动作：

- 没有新增可比数据
- 当前在 video call 等高风险体验场景
- trial 正在收集
- 当前场景未知
- 发生 Kernel/软件升级，需要先重建 baseline
- 观察到的功耗变化可以由 brightness/负载解释
- 候选收益小于测量噪声

## PROPOSE_TRIAL

示例：

```json
{
  "action": "PROPOSE_TRIAL",
  "reason": "reading 场景已有稳定 baseline，EPP 尚未测试",
  "payload": {
    "human_approved": false,
    "proposal": {
      "schema_version": 2,
      "source": "llm",
      "title": "reading: balance_power -> power",
      "rationale": "测试阅读场景更偏向能效的 EPP",
      "context": {
        "scene": "reading",
        "min_confidence": 0.8
      },
      "change": {
        "parameter": "cpu.epp",
        "from": "balance_power",
        "to": "power"
      },
      "objective": {
        "type": "auto"
      },
      "expected_effect": {
        "average_power_delta_w": {
          "min": -0.4,
          "max": -0.05
        },
        "usability": "不应影响阅读/轻交互",
        "confidence": "medium"
      },
      "risk": {
        "level": "low",
        "notes": []
      },
      "rollback": {
        "instruction": "PowerLab 自动恢复 pre-trial snapshot"
      },
      "validation": {
        "workload": "reading",
        "duration_seconds": 900,
        "min_valid_minutes": 60,
        "baseline_lookback_hours": 168,
        "acceptance": {
          "max_average_power_delta_w": -0.05,
          "max_temperature_increase_c": 2.0,
          "max_task_duration_ratio": 1.1
        }
      }
    }
  }
}
```

如果自动 trial 关闭，这个决定只保存 proposal。

如果已批准但当前不是 reading，则进入：

```text
WAITING_FOR_CONTEXT
```

以后 collector 识别到 reading 才开始。

## 固定任务

编译、转码、导出等使用：

```bash
sp7-powerlab task-run --scene compile --label build -- make -j4
```

LLM 应比较：

```text
Wh/task
+
duration
```

而不是只比较 W。

## 外部 profile trial

当唯一 writer 是 Power Options 或 PPD 时，不需要绕过它直接写 sysfs。LLM 可以提出：

```json
{
  "change": {
    "parameter": "profile.id",
    "from": "baseline-profile",
    "to": "candidate-profile"
  }
}
```

PowerLab 会把外部 profile trial 放进相同的：

```text
snapshot
→ apply
→ settle
→ measure
→ revalidation
→ rollback/promote
```

PPD 回滚使用实际 current-profile read-back。Power Options 当前 CLI 不暴露临时 override 的读取接口，因此回滚会重放 proposal 中声明的 baseline profile；没有 baseline 时 reset override。

外部 profile 默认不允许无人值守 trial；只有 profile 的 `evidence.auto_trial_allowed=true` 才能 opt-in。

## 媒体质量门槛

`media_playback` 不会只因为平均功耗下降就判定胜出。默认要求 MPRIS `playing` 在测量窗口内至少占 90%，还可在 proposal 中增加：

```json
{
  "validation": {
    "quality_constraints": {
      "min_media_playing_fraction": 0.95,
      "max_temperature_c": 75,
      "max_average_cpu_usage_percent": 45
    }
  }
}
```

这些是最低连续播放/资源门槛，不等价于逐帧 dropped-frame 检测；后者仍需真机播放器/浏览器专用 telemetry 才能进一步增强。

## 胜出不是结束

第一次：

```text
CANDIDATE_WINNER
→ REVALIDATION
```

必须有新的独立 session/task 再次满足要求：

```text
REVALIDATION
→ CANDIDATE_WINNER
→ PROMOTED
```

## 人工反馈优先

```bash
sp7-powerlab feedback rejected   --trial-id t-...   --responsiveness 2   --notes "虽然省 0.3W，但交互明显卡"
```

这种 trial 进入 rejection memory。以后 LLM 不应只因为功耗好看就重复相同 context+parameter+value。

## Context rule 更新

LLM 可以输出 `UPDATE_CONTEXT_RULE`，但 PowerLab 不自动改 `config/contexts.toml`。

建议会保存到：

```text
proposals/context-rules/
```

审核后再修改规则并提交 Git。
