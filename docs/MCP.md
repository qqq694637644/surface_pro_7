# MCP contract

## Capability boundary

PowerLab 不允许把任意 Bash、Python/code execution、主 `sp7-powerlab` CLI 或
`/run/sp7-powerlab/helper.sock` 暴露给 LLM。

LLM/MCP 只应得到一个显式白名单工具面，后端只能调用：

~~~bash
sp7-powerlab-agent observe
sp7-powerlab-agent hourly
sp7-powerlab-agent submit-decision runtime/llm-decision.json
~~~

`scripts/mcp-hourly.sh` 也只调用这个 agent executable。agent 不接受 alternate config，
`hourly` 不接受任意 output path，`submit-decision` 只允许读取项目 `runtime/` 下的 JSON。

如果一个 MCP 能以 PowerLab 用户身份执行任意命令，那么它可以直接调用人类 CLI 或
连接同 UID 的 helper socket；此时不存在可信的“人工批准”隔离。不要这样部署。

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

## Human approval

Level 2 时，`submit-decision` 只把合法 proposal 保存到 `proposals/`，不会开始
trial。用户审核文件后，在 LLM 不可访问的交互环境运行：

~~~bash
sp7-powerlab trial start proposals/<reviewed-proposal>.json
~~~

`VERIFIED_WINNER` 的人工 promotion 同样使用：

~~~bash
sp7-powerlab trial promote trial-xxxx
~~~

PROPOSE_WASTE_FIX 和 PROPOSE_MANUAL_RECALIBRATION 只会保存成人工审核文件。

自动 trial 需要 automation level >= 3；auto promotion 还要求 level >= 4、
auto_promote=true，而且 trial 已经是 VERIFIED_WINNER。
