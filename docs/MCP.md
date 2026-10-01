# MCP / Structured Agent Interface

PowerLab 不要求 Agent 必须通过专用 MCP 才能工作。

用户可以给 GPT-5.6 或其他 Agent 提供：

- Bash
- workspace
- MCP
- Git
- 其他普通用户态能力

根目录 AGENTS.md 定义项目工作边界。

## 1. sp7-powerlab-agent

sp7-powerlab-agent 是便利的结构化入口，不是 capability sandbox。

~~~bash
sp7-powerlab-agent observe
sp7-powerlab-agent hourly
sp7-powerlab-agent submit-decision runtime/llm-decision.json
~~~

用途：

- 快速读取摘要
- 生成历史 knowledge pack
- 提交结构化 decision

如果 Agent 已有 Bash，也可以直接使用：

- sp7-powerlab agent-context
- 主 CLI
- SQLite
- journal
- /proc
- 可读 /sys
- Git
- tests

## 2. Decision contract

普通 no-op：

~~~json
{
  "action": "NO_CHANGE",
  "reason": "recent evidence is stable and no action has practical value",
  "payload": {}
}
~~~

Trial proposal：

~~~json
{
  "action": "PROPOSE_ENVELOPE_TRIAL",
  "reason": "test one lower-energy neighbor",
  "payload": {
    "proposal": {
      "kind": "envelope",
      "baseline_envelope": "INTERACTIVE_EFFICIENT",
      "changes": {
        "max_perf_pct": 55
      }
    }
  }
}
~~~

完整 named envelope 也可以作为 candidate。

## 3. Proposal 不能携带裁判标准

结构化 trial proposal 只描述 candidate intent。

不能覆盖：

- trial ID
- target matching
- block duration
- settling
- noise floor
- MUE
- PSI/thermal/media veto
- brightness/gap gate
- Evidence budget

这些由本地 config/runtime Evidence contract 生成。

## 4. Automation Level

Level 2：

- Scheduler/Agent 可以提出 candidate；
- daemon 不自动开始 trial。

Level 3：

- 全部 safety/evidence/budget gate 通过后 daemon 可以自动开始低风险 trial。

Level 4：

- 还要求 auto_promote=true；
- trial 必须已经是 VERIFIED_WINNER；
- 才允许自动 promotion。

Automation Level 是 daemon/Scheduler 的治理，不是同 UID Bash Agent 的安全边界。

## 5. 真正的硬边界

PowerLab 保留：

- validated thermal safety
- transactional HWP
- read-back
- rollback
- restricted root helper
- active trial deterministic Evidence contract

root helper 不接受任意 shell / arbitrary sysfs / arbitrary MSR。

## 6. 推荐 Agent 入口

如果 Agent 能执行 Bash：

~~~bash
cat AGENTS.md
cat docs/PROJECT_STATUS.md
sp7-powerlab agent-context
~~~

需要详细历史时再调用 knowledge pack。

避免每轮重复加载全部文档和全部 telemetry。
