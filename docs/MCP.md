# MCP contract

## Optional structured interface

`sp7-powerlab-agent` 是常用高层 workflow 的便利入口，不是 GPT-5.6 唯一允许使用的
capability，也不是安全沙箱。个人使用场景可以由用户向 Agent 提供 Bash/workspace/MCP 等
通用用户态能力。

便利入口包括：

~~~bash
sp7-powerlab-agent observe
sp7-powerlab-agent hourly
sp7-powerlab-agent submit-decision runtime/llm-decision.json
~~~

`scripts/mcp-hourly.sh` 可以调用这个 agent executable。agent 不接受 alternate config，
`hourly` 不接受任意 output path，`submit-decision` 只允许读取项目 `runtime/` 下的 JSON。

Automation Level 是 PowerLab daemon / Scheduler 的默认治理策略，不应被描述为对同 UID
Bash Agent 技术上不可绕过的权限隔离。

真正的硬边界仍是 root side：PowerLab 不主动提供 unrestricted root shell；root helper
只接受有限、校验后的 HWP inspect/snapshot/apply/restore 操作。

## Decision contract

LLM 返回结构化 JSON。它没有 human approval 字段。

普通 no-op：

~~~json
{
  "action": "NO_CHANGE",
  "reason": "recent power and thermal behavior are stable",
  "payload": {}
}
~~~

Envelope trial：

~~~json
{
  "action": "PROPOSE_ENVELOPE_TRIAL",
  "reason": "test a slightly lower max_perf_pct",
  "payload": {
    "proposal": {
      "kind": "envelope",
      "baseline_envelope": "INTERACTIVE_EFFICIENT",
      "changes": {"max_perf_pct": 50}
    }
  }
}
~~~

完整命名 envelope 也可以作为 candidate：

~~~json
{
  "action": "PROPOSE_ENVELOPE_TRIAL",
  "reason": "validate the complete remote envelope",
  "payload": {
    "proposal": {
      "kind": "envelope",
      "baseline_envelope": "INTERACTIVE_EFFICIENT",
      "candidate_envelope": "REMOTE_EFFICIENT"
    }
  }
}
~~~

提交：

~~~bash
sp7-powerlab-agent submit-decision runtime/llm-decision.json
~~~

LLM JSON 不能表达 shell command、任意 sysfs path、thermald hard trip、kernel cmdline
或 root command。实验 JSON 会在运行时经过 packaged JSON Schema；实验 target、trial
ID 和全部 validation/settling/通过门槛都只由本地代码与 config 生成，proposal 无权
覆盖裁判标准。

## Level 2 default workflow

Level 2 时，`submit-decision` 默认只把合法 proposal 保存到 `proposals/`，不会开始
trial。常规工作流由用户审核后运行：

~~~bash
sp7-powerlab trial start proposals/<reviewed-proposal>.json
~~~

`VERIFIED_WINNER` 的人工 promotion 同样使用：

~~~bash
sp7-powerlab trial promote trial-xxxx
~~~

PROPOSE_POWER_FIX 和 PROPOSE_MANUAL_RECALIBRATION 只会保存成人工审核文件。

自动 trial 需要 automation level >= 3；auto promotion 还要求 level >= 4、
auto_promote=true，而且 trial 已经是 VERIFIED_WINNER。
