# Operations

## 日常状态

~~~bash
sp7-powerlab service status
sp7-powerlab observe now
sp7-powerlab observe power --hours 24
sp7-powerlab incidents --hours 24
~~~

## Waste incident

PowerLab 优先查“低 demand + 明显高于个人历史 baseline 的 BAT W”。

incident 会附带 top processes、RAPL、brightness、network、GPU hint 和 system fingerprint。

正确顺序是先找浪费原因，不是先降低 max_perf_pct。

## Media decode

媒体播放同时出现高 CPU、高 RAPL、GPU decode activity 不明显时，会记录 suspected media software decode waste incident。

这是诊断 hint，不是自动控制信号。

## Thermal incident

THERMAL_PRESSURE / THROTTLING 会让 trial 自动 rollback，controller 优先 THERMAL_SAFE，thermald 继续作为独立安全层。

## Suspend/resume

超过 collector gap 后 RAPL/temp rolling window 重建，进入 resume grace，grace 内不自动控制，也不把 suspend 计入有效实验时长。

## Manual override

只接受 verified envelope：

~~~bash
sp7-powerlab envelope override INTERACTIVE_EFFICIENT
sp7-powerlab envelope clear-override
~~~

## Feedback

~~~bash
sp7-powerlab feedback good --envelope INTERACTIVE_EFFICIENT
sp7-powerlab feedback sluggish --trial-id trial-xxxx --notes "..."
~~~

负面 trial feedback 会立即回滚。

如果负面反馈发生在 revalidation 之后、promotion 之前，该 VERIFIED_WINNER
也会被改成 REJECTED，不能继续 promotion。

## Trial promotion

~~~bash
sp7-powerlab trial status --trial-id trial-xxxx
sp7-powerlab trial promote trial-xxxx
~~~

只有 VERIFIED_WINNER 能 promotion；Level 0/1 不允许。

## Runtime reset

只在 v1→v2 或明确丢弃本地 DB 时：

~~~bash
sp7-powerlab reset-runtime --yes
~~~

## Git knowledge

~~~bash
bash scripts/commit-knowledge.sh
~~~

默认不会自动 push。
