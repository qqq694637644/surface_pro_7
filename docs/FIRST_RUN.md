# Surface Pro 7 第一次运行

第一次运行目标不是马上“榨续航”，而是确认**传感器、场景识别、数据库、执行器和采集开销都是可信的**。

## 1. 安装 PowerLab

```bash
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. 准备桌面活动数据

推荐运行：

- ActivityWatch server
- awatcher
- GNOME Wayland：focused-window / D-Bus 对应 GNOME 扩展

PowerLab 默认访问：

```text
http://127.0.0.1:5600
```

没有 ActivityWatch 也能运行，但场景识别会明显降级。

## 3. 真机传感器检查

拔掉外接电源：

```bash
sp7-powerlab doctor
```

重点检查：

- `dmi.is_surface_pro_7`
- battery present
- numeric battery power
- battery status = Discharging
- brightness
- thermal
- CPU driver/EPP
- Wi-Fi
- RAPL（可用时）
- GPU/DRM（可用时）

如果整机 battery power/energy 不可用，不要开启自动优化。

## 4. 检查执行器

```bash
sp7-powerlab actuator-inspect
```

默认 `policy.actuator = "auto"`。

PowerLab 只允许一个主要写入后端：

```text
Power Options > PPD > sysfs
```

如果自动发现 Power Options，就不要同时让另一个工具控制相同 EPP/频率参数。

## 5. 先测 collector 自己

```bash
sp7-powerlab collector-benchmark --samples 12
```

关注：

- mean collection time
- max collection time
- duty-cycle estimate

如果 collector 本身开销异常，先调大：

```toml
[collector]
system_interval_seconds = 10
process_interval_seconds = 20
```

再测试。

## 6. 单点 smoke test

```bash
sp7-powerlab collect-once
```

确认输出中能看到：

- `activity.source`
- `context.scene`
- `process_summary`
- battery power
- app/window
- media
- profile

## 7. 连续只读观察

直接前台运行：

```bash
sp7-powerlab service-run
```

另一个终端：

```bash
sp7-powerlab service-status
sp7-powerlab current
sp7-powerlab observe --hours 1
sp7-powerlab contexts --hours 6
```

第一阶段建议至少观察一个完整日常周期，再调整场景规则。

默认 profile 是 `safe-baseline`，不会启动新 trial。

## 8. 检查场景是否符合实际使用

正常使用：

- 浏览网页/PDF
- 编辑代码
- 编译
- 播放视频
- Office
- 文件下载
- AFK

然后：

```bash
sp7-powerlab contexts --hours 24
```

如果大量时间落入 `unknown` 或明显错分，先修改：

```text
config/contexts.toml
```

不要先调电源参数。

## 9. 安装常驻服务

只读观察确认后：

```bash
bash scripts/install-user-services.sh
```

检查：

```bash
systemctl --user status sp7-powerlab-collector
systemctl --user list-timers | grep sp7-powerlab
```

## 10. 建立第一批 verified profile

先查看：

```bash
sp7-powerlab profile list
```

仓库附带：

- safe-baseline：verified
- ppd-balanced：experimental
- ppd-power-saver：experimental

在 SP7 上实测一个 profile 后，才人工标记：

```bash
sp7-powerlab profile status ppd-balanced verified   --scene coding_interactive   --note "SP7 real-world validation"
```

之后本地 policy engine 才会在对应场景自动采用。

## 11. 每小时 MCP

先保持：

```toml
auto_run_low_risk_trials = false
auto_promote_profiles = false
```

让 MCP/LLM 只观察和提出建议。

```bash
bash scripts/mcp-hourly.sh
```

把 `runtime/hourly-pack.json` 交给 LLM。

## 12. 最后才启用自动 trial

如果决定使用 direct sysfs：

1. 停止会争抢同一参数的其他 power manager。
2. `policy.actuator = "sysfs"`
3. 安装 root helper：

```bash
bash scripts/install-root-helper.sh
```

4. 确认：

```bash
sp7-powerlab actuator-inspect
```

5. 先手工批准几轮 proposal。
6. 确认 rollback/revalidation 正常。
7. 最后才打开：

```toml
auto_run_low_risk_trials = true
```

`auto_promote_profiles` 建议再晚一步开启。
