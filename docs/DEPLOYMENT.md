# Deployment

## User service

~~~bash
bash scripts/install-user-services.sh
~~~

安装用户级 sp7-powerlab.service、sp7-powerlab-hourly.service 和 timer。

主 service 负责 telemetry、demand、thermal、controller、trial tick、UnexpectedPower
detection，以及在 automation level 3+ 下受 gate 约束的 Candidate Scheduler。

## Root helper

~~~bash
bash scripts/install-root-helper.sh
~~~

安装过程：

1. 从当前源码构建 wheel。
2. 在 /opt/sp7-powerlab-helper/venv 创建独立 root-owned 环境。
3. systemd root service 只执行该副本。
4. Unix socket 只允许安装时的目标 UID。
5. helper 只暴露 inspect/snapshot/apply/restore HWP 操作。
6. helper 不保留 CAP_SYS_ADMIN，CapabilityBoundingSet 和 AmbientCapabilities 均为空。

日常 Git checkout 的修改不会自动变成 root 代码。

## thermald

thermald 是独立系统服务。PowerLab 不拥有 thermald hard trip 或 RAPL safety limit。

thermald 不 active 时，自动 trial 禁止，controller 写入降级并记录 incident。

## 冲突写入者

不要让 TLP、auto-cpufreq、Power Options、power-profiles-daemon 与 PowerLab 同时持续写 EPP/max_perf_pct。

doctor 会报告已知冲突。

## 自动化等级

- Level 0：只读。
- Level 1：只切 verified envelope。
- Level 2：Scheduler 可以提出 candidate；默认由用户审核后执行 trial / promotion。
- Level 3：全部 safety/evidence/budget gate 通过后，daemon 可自动开始低风险 trial。
- Level 4：满足条件且 `auto_promote=true` 时允许 auto-promotion。

个人设备建议长期停留在 1–2，等真机数据充分后再提高。

个人使用时可以由用户向 GPT-5.6 提供 Bash/workspace/MCP 等通用用户态能力。
`sp7-powerlab-agent` 只是便利入口，不是权限沙箱。无论 Agent 具有什么用户态能力，
root helper 仍只暴露有限 HWP 操作，不提供 unrestricted root shell。
