# MCP contract

外部 Bash MCP 推荐每小时运行：

~~~bash
bash scripts/mcp-hourly.sh
~~~

LLM 返回结构化 JSON。

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
      "changes": {"max_perf_pct": 50},
      "validation": {
        "min_block_seconds": 300,
        "min_power_saving_w": 0.1
      }
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

应用：

~~~bash
sp7-powerlab llm-apply decision.json
~~~

LLM JSON 不能表达 shell command、任意 sysfs path、thermald hard trip、kernel cmdline 或 root command。

Level 2 的人工批准不是 JSON 字段，而是独立 CLI 动作：

~~~bash
sp7-powerlab llm-apply decision.json --approve
~~~

因此 LLM 无法在自己的 decision JSON 中伪造“已批准”。

PROPOSE_WASTE_FIX 和 PROPOSE_MANUAL_RECALIBRATION 只会保存成人工审核文件。

自动 trial 需要 automation level >= 3；auto promotion 还要求 level >= 4、auto_promote=true，而且 trial 已经是 VERIFIED_WINNER。
