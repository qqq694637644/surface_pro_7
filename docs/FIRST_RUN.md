# First run

## 1. 旧 v1 数据

v2 不迁移 v1 SQLite。若 doctor 报 legacy database：

~~~bash
sp7-powerlab reset-runtime --yes
~~~

## 2. 硬件契约

~~~bash
sp7-powerlab doctor
~~~

自动写入要求 Surface Pro 7、i5-1035G4、`intel_pstate=active`、HWP/EPP、`no_turbo` 控制、BAT、RAPL、thermal sensor、systemd、thermald active，并且没有冲突 power writer。

错误硬件仍可只读诊断，不允许写参数。

## 3. thermald

~~~bash
systemctl status thermald
journalctl -u thermald -b
~~~

首次部署不要修改 thermald hard limits。

## 4. 只读服务

默认 automation.level=0。

~~~bash
bash scripts/install-user-services.sh
sp7-powerlab observe power --hours 6
sp7-powerlab observe thermal --hours 6
sp7-powerlab incidents --hours 24
~~~

先确认 collector 本身没有明显额外耗电。

## 5. 更换电池

~~~bash
sp7-powerlab calibrate new-battery
~~~

会创建新的 battery epoch，并让 calibration 失效。

## 6. Calibration

按 cold_idle、normal_interactive、media、bounded_burst 顺序完成。

bounded_burst 只测热惯性，不测极限性能。达到 bootstrap 温度、最大时长或检测到 throttling 时，service 会中止 calibration。

## 7. Verified envelope

不要直接把仓库里的 candidate 参数标成 VERIFIED。

建议把**当前真实 HWP 状态**先收编为 INTERACTIVE_EFFICIENT：

~~~bash
sp7-powerlab envelope adopt-current INTERACTIVE_EFFICIENT --note "current real HWP baseline"
~~~

这会读取 root helper 的实际 snapshot，只有硬件契约、thermald、ownership 和 calibration 都正常时才允许执行。

然后才把 automation.level 从 0 调到 1。

其他 ECO_IDLE / REMOTE_EFFICIENT / MEDIA_EFFICIENT / THERMAL_SAFE 使用
`candidate_envelope` trial 验证，不使用“手工 verify TOML”捷径。

## 8. Assisted trial

先使用 level 2。LLM 只提 proposal，由用户批准。

等 snapshot、rollback、revalidation、thermal preemption 都真机验证后，再讨论 level 3。

实验完成为 VERIFIED_WINNER 后，人工确认并 promotion：

~~~bash
sp7-powerlab trial promote trial-xxxx
~~~
