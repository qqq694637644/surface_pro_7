# Deployment

## User service

~~~bash
bash scripts/install-user-services.sh
~~~

安装用户级 `sp7-powerlab.service` 和最小 `sp7-powerlab-fixed.service` oneshot。旧的 scheduled review
hourly service/timer 已删除；installer 会停止并移除遗留 unit。fixed oneshot 只有 selected runtime 已经
切到 FIXED_GOOD 时才 enable；重新运行 installer 会保留此前 Dynamic/Fixed 选择，不会无条件重新启用主 daemon。

主 service 负责 telemetry、demand、thermal、controller、trial tick、UnexpectedPower
detection，以及在 automation level 3+ 下受 gate 约束的 Candidate Scheduler。

需要历史摘要时按需运行 `sp7-powerlab review-pack`。正式 Stage E capture 会 fail-closed 检查遗留 hourly
unit，若旧 unit 仍 active 则拒绝采集。

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
6. `inspect` 暴露 protocol version 和当前 `helper.py + actuators/hwp.py` implementation identity。
7. helper 不保留 CAP_SYS_ADMIN，CapabilityBoundingSet 和 AmbientCapabilities 均为空。

日常 Git checkout 的修改不会自动变成 root 代码。

因此修改 `helper.py` / `actuators/hwp.py` 或升级包含它们的新提交后，必须重新运行：

~~~bash
bash scripts/install-root-helper.sh
~~~

主 runtime 会把 socket helper 的 protocol/implementation identity 与当前源码计算值比较。两边不一致时
直接保持 READ_ONLY / `root-helper-mismatch`；不兼容旧 root wheel，也不会把旧 helper 当成当前 actuator。
`install-root-helper.sh` 完成更新后，如果 persistent fixed-good oneshot 已启用，会自动 restart 该 user unit，
确保新的 matching helper 真正重新应用 selected fixed envelope。

主 service 每次 hardware-contract refresh 都会重新 discovery/bind actuator。若 user service 启动时
root helper 尚未就绪，PowerLab 会先保持 READ_ONLY；helper 稍后恢复后不需要重启 user service，
下一次 refresh 会重新绑定。是否恢复 CONTROL_ALLOWED 仍由 calibration/thermal/telemetry/rollback
integrity 等 gate 决定。

已经绑定可用 backend 后，一次明确 helper probe failure 就立即撤销正常写权限并进入只读安全路径。
reconnect-capable client object 可以保留用于下一次 probe/reconnect；active trial 期间 backend identity
冻结，不能静默换成另一个 actuator backend。恢复成功后仍需重新通过 Control Safety gate。

## Breaking runtime schema

项目不维护旧 SQLite runtime schema 迁移。schema mismatch 时 `sp7-powerlab service run` 使用 exit
status 78，user systemd unit 的 `RestartPreventExitStatus=78` 会阻止 5 秒一次的 crash-loop。

升级后若看到 schema mismatch，明确执行：

~~~bash
sp7-powerlab reset-runtime --yes
systemctl --user disable --now sp7-powerlab-fixed.service 2>/dev/null || true
systemctl --user enable --now sp7-powerlab.service
~~~

这是破坏式 reset；不要自动兜底删除 runtime。旧 DB 中的 fixed-good selection 也会被删除，因此必须显式
关闭 fixed oneshot 并回到 main service，重新从新 schema/current evidence 建立验证后再选择最终 runtime。

在非 SP7 开发机或纯仓库工程任务中，本地 runtime DB 过旧不等于必须 reset。只有任务确实针对该
runtime，且用户明确允许丢弃旧数据时，才执行上述破坏式操作。不要为了让开发环境的
`agent-context` 看起来 healthy 而重置数据或放宽硬件契约。

本地提交/部署前可运行完整软件质量门：

~~~bash
bash scripts/quality-gate.sh
~~~

它覆盖 pytest、ruff check/format、wheel build + packaged schema smoke、shell syntax 和 `git diff --check`。

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

个人设备建议先停留在 0–2，等 Measurement Trust、rollback、thermal preemption、
independent revalidation 和 stop rules 都经过真机验证后再提高。

个人使用时由 GPT-5.6 Sol 直接通过 Bash/workspace 使用主 CLI、SQLite、journal 和 sysfs；仓库不再维护
第二套 Agent action DSL。无论 Agent 具有什么用户态能力，root helper 仍只暴露有限 HWP 操作，不提供
unrestricted root shell。
