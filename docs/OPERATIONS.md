# 运维与故障恢复

## 状态

```bash
sp7-powerlab service-status
sp7-powerlab current
sp7-powerlab actuator-inspect
```

## 观察真实使用

```bash
sp7-powerlab observe --hours 1
sp7-powerlab observe --hours 24
sp7-powerlab contexts --hours 24
sp7-powerlab contexts --hours 168 --scene coding_interactive
```

## Profile

```bash
sp7-powerlab profile list
sp7-powerlab profile inspect ppd-balanced
sp7-powerlab profile apply ppd-balanced --reason "manual validation"
```

人工验证后：

```bash
sp7-powerlab profile status ppd-balanced verified   --scene coding_interactive   --note "validated on SP7"
```

标记：

```text
experimental
verified
needs_revalidation
deprecated
blocked
```

Kernel/工具版本漂移时，非 no-op verified profile 会自动转为 `needs_revalidation`。

## Manual override

```bash
sp7-powerlab override set safe-baseline
sp7-powerlab override status
sp7-powerlab override clear
```

设置 override 时，如果有 active trial，会先 rollback。

## Trial

查看：

```bash
sp7-powerlab trial status
```

人工启动：

```bash
sp7-powerlab trial start proposals/my-proposal.json
```

如果你明确知道场景条件：

```bash
sp7-powerlab trial start proposals/my-proposal.json --ignore-context
```

评价：

```bash
sp7-powerlab trial evaluate
```

回滚：

```bash
sp7-powerlab trial rollback --reason "manual stop"
```

晋升：

```bash
sp7-powerlab trial promote t-...
```

只有经过 revalidation 的 `CANDIDATE_WINNER` 能晋升。

外部 profile A/B 也走同一套 trial。proposal 中使用：

```text
change.parameter = profile.id
change.from      = baseline profile id
change.to        = candidate profile id
```

Power Options/PPD profile 不要通过 direct sysfs trial 绕过单一写入者规则。

## 固定工作量

collector 必须正在运行：

```bash
sp7-powerlab task-run   --scene compile   --label surface-build   -- make -j4
```

重复至少配置要求的次数。

每个 run 保存：

- duration
- battery energy
- average power
- exit code
- profile
- trial
- quality

## 人工体验

```bash
sp7-powerlab feedback accepted   --trial-id t-...   --responsiveness 5   --stability good   --suspend-wake good

sp7-powerlab feedback rejected   --trial-id t-...   --responsiveness 2   --notes "滚动卡顿"
```

## collector crash

systemd：

```bash
systemctl --user restart sp7-powerlab-collector
journalctl --user -u sp7-powerlab-collector -n 200
```

如果 trial 已经应用并留下 lock，collector 启动会尝试恢复 snapshot。

## profile state

sysfs verified profile 应用时，PowerLab 保存：

```text
runtime/profile-state.json
```

场景切走或 manual override 时，先恢复应用前的参数，再应用新 profile。

## thermal emergency

`config/powerlab.toml`：

```toml
thermal_emergency_c = 90
```

active trial 越过阈值时会自动 rollback。

## Root helper 升级

root helper 是 root-owned 的独立安装，不会自动跟随当前 Git checkout。

更新代码并审核完成后，显式重新安装：

```bash
bash scripts/install-root-helper.sh
```

检查：

```bash
systemctl status sp7-powerlab-root-helper
sp7-powerlab actuator-inspect
```

## ActivityWatch 不可用

collector 不退出。

检查：

```bash
curl http://127.0.0.1:5600/api/0/buckets/
sp7-powerlab collect-once
```

如果 `activity.source=activitywatch-unavailable`，修 ActivityWatch/awatcher；不要用这种数据做应用级结论。

## 数据库

默认：

```text
runtime/powerlab.sqlite3
```

WAL 模式。

备份前可先停止 collector：

```bash
systemctl --user stop sp7-powerlab-collector
cp runtime/powerlab.sqlite3 /safe/place/
systemctl --user start sp7-powerlab-collector
```

## 重置实验状态

优先：

```bash
sp7-powerlab trial rollback
sp7-powerlab override clear
```

不要直接删除 `trial.lock`，除非确认没有任何 trial 参数还留在系统上。

## 数据保留

`raw_retention_days` 控制高频：

- samples
- process samples
- app events
- system events

分钟 rollup、sessions、trial、反馈、知识记录继续保留。

## Git 长期知识

每次 hourly 会导出：

```text
history/continuous/knowledge.json
history/continuous/trials/*.json
history/continuous/daily/YYYY-MM-DD.json
```

按需提交：

```bash
bash scripts/commit-knowledge.sh
bash scripts/commit-knowledge.sh --push
```

默认不自动 commit/push。

## 更新仓库后

升级代码/Kernel/Power Options 等之后：

```bash
pip install -e .
systemctl --user restart sp7-powerlab-collector
sp7-powerlab hourly
```

版本 fingerprint 变化会触发旧 verified 策略重新验证。
