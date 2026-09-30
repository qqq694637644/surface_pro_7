# Bash MCP / LLM 接口

本项目假设你会提供一个可以在 SP7 Linux 上执行 Bash 的 MCP。

推荐让 MCP 调用 PowerLab 的高层命令，而不是让 LLM 正常循环直接写 sysfs。

## 每小时入口

```bash
cd /path/to/surface_pro_7
bash scripts/mcp-hourly.sh
```

等价于：

```bash
sp7-powerlab hourly   --config config/powerlab.toml   --output runtime/hourly-pack.json
```

## LLM 输入

读取：

```text
runtime/hourly-pack.json
```

需要进一步调查时，可以调用：

```bash
sp7-powerlab service-status
sp7-powerlab current
sp7-powerlab observe --hours 1
sp7-powerlab observe --hours 24
sp7-powerlab contexts --hours 24
sp7-powerlab contexts --hours 168 --scene reading
sp7-powerlab trial status
sp7-powerlab profile list
sp7-powerlab actuator-inspect
```

## 输出契约

输出必须符合：

```text
schemas/llm-decision-v1.schema.json
```

最小 no-op：

```json
{
  "action": "NO_CHANGE",
  "reason": "没有足够的新证据",
  "payload": {}
}
```

## 应用

```bash
sp7-powerlab llm-apply runtime/llm-decision.json
```

如果想把 decision 绑定到刚生成的 pack：

```bash
sp7-powerlab llm-apply   runtime/llm-decision.json   --run-id llm-xxxxxxxx
```

run id 会记录 input hash、LLM output、实际 action/result。

## 可选动作

### NO_CHANGE

没有足够证据时优先。

### NEED_MORE_DATA

```json
{
  "action": "NEED_MORE_DATA",
  "reason": "compile 只有一次完整 task run",
  "payload": {
    "scene": "compile",
    "required": "at least 3 comparable task runs"
  }
}
```

### PROPOSE_TRIAL

trial proposal 使用 v2 schema。

如果：

```toml
auto_run_low_risk_trials = false
```

且没有：

```json
"human_approved": true
```

则只保存，不执行。

已批准但当前场景不匹配时会进入：

```text
WAITING_FOR_CONTEXT
```

如果当前 writer 是 Power Options/PPD，可使用 profile-level change：

```json
{
  "parameter": "profile.id",
  "from": "baseline-profile-id",
  "to": "candidate-profile-id"
}
```

候选必须存在于 profile registry，且 backend 必须与当前唯一 writer 一致。无人值守 profile trial 还要求候选 profile 的 `evidence.auto_trial_allowed=true`。

### ROLLBACK_TRIAL

```json
{
  "action": "ROLLBACK_TRIAL",
  "reason": "用户反馈明显卡顿",
  "payload": {
    "trial_id": "t-..."
  }
}
```

### PROMOTE_PROFILE

只有结果已经是 `CANDIDATE_WINNER` 才能晋升。

默认还需要 human approved，除非配置打开 auto promote。

### INVESTIGATE_REGRESSION

只记录调查决定，不乱改参数。

### UPDATE_CONTEXT_RULE

保存场景规则建议到：

```text
proposals/context-rules/
```

不会自动修改 `config/contexts.toml`。

## Root 权限

MCP 正常循环不应该直接运行：

```bash
sudo sh -c 'echo ... > /sys/...'
```

direct sysfs trial 通过：

```text
LLM
→ PowerLab validator
→ TrialManager
→ root-helper allowlist
→ sysfs
```

这样 before/readback/rollback/trial ID 才不会丢失。

## 推荐 LLM 系统指令核心

可以要求 LLM：

1. 先检查数据质量。
2. 不把相关性当因果。
3. 一次最多一个 primary parameter。
4. 先搜索 rejection memory。
5. 固定任务优化 Wh/task，不只优化 W。
6. unknown/mixed 不做激进 trial。
7. video_call 默认不自动探索。
8. 小于测量噪声的变化不晋升。
9. 能 NO_CHANGE 就不要为了“持续优化”强行实验。
10. 所有输出必须是 decision schema。
