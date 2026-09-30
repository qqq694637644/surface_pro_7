# Linux 部署

## 1. 基础依赖

Debian/Ubuntu 类系统通常需要：

```bash
sudo apt install   python3 python3-venv git   playerctl iw rfkill brightnessctl
```

然后：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. ActivityWatch + awatcher

PowerLab 使用 ActivityWatch HTTP API，不需要把 ActivityWatch Python 包作为依赖安装到 PowerLab venv。

建议：

1. 安装并运行 ActivityWatch server。
2. Linux Wayland 使用 awatcher。
3. GNOME Wayland 安装 awatcher 文档要求的 focused-window/DBus GNOME 扩展。

项目：

- https://activitywatch.net/
- https://github.com/ActivityWatch/activitywatch
- https://github.com/2e3s/awatcher

检查：

```bash
curl -s http://127.0.0.1:5600/api/0/buckets/ | head
sp7-powerlab collect-once
```

输出中 `activity.source` 应为 `activitywatch`。

## 3. 选择唯一执行器

默认：

```toml
[policy]
actuator = "auto"
```

自动优先顺序：

1. Power Options
2. power-profiles-daemon
3. sysfs

查看：

```bash
sp7-powerlab actuator-inspect
```

### Power Options

如果安装 Power Options：

```bash
power-daemon-mgr list-profiles
```

复制：

```text
config/profiles/power-options-example.toml.example
```

为真实 profile 文件，填入真实 backend profile 名。

PowerLab 使用：

```text
power-daemon-mgr set-profile-override
power-daemon-mgr reset-profile-override
```

### power-profiles-daemon

仓库附带 experimental：

- ppd-balanced
- ppd-power-saver

先实测再标记 verified。

### direct sysfs

只有明确选择：

```toml
actuator = "sysfs"
```

PowerLab 才允许直接参数 trial。

在此模式下应停止/禁用会修改同一参数的 TLP、auto-cpufreq、Power Options、PPD 等竞争写入者。

## 4. Root helper

user service 没有权限写大多数 cpufreq sysfs。

安装受限 helper：

```bash
bash scripts/install-root-helper.sh
```

安装过程会：

1. 从当前已审核源码构建 wheel + 依赖 wheel；
2. 在 `/opt/sp7-powerlab-helper/venv` 创建独立环境；
3. 由 root 安装并持有该副本；
4. systemd 只执行这份 root-owned 安装。

这样日常用户对 Git checkout/开发 venv 的修改不会在 helper 重启或开机时自动变成 root 代码。

检查：

```bash
systemctl status sp7-powerlab-root-helper
sp7-powerlab actuator-inspect
```

应该看到：

```text
root_helper.available = true
parameter_backend = root-helper
```

helper 只接受 PowerLab 参数 registry，不执行任意 shell 命令。

重新升级 PowerLab 后，如果 helper 代码也需要升级，重新执行：

```bash
bash scripts/install-root-helper.sh
```

卸载：

```bash
bash scripts/uninstall-root-helper.sh
```

## 5. 用户服务

```bash
bash scripts/install-user-services.sh
```

会安装：

```text
sp7-powerlab-collector.service
sp7-powerlab-hourly.service
sp7-powerlab-hourly.timer
```

查看：

```bash
systemctl --user status sp7-powerlab-collector
systemctl --user status sp7-powerlab-hourly.timer
journalctl --user -u sp7-powerlab-collector -f
```

卸载：

```bash
bash scripts/uninstall-user-services.sh
```

## 6. 外部 Bash MCP

如果你的 MCP 自己每约 1 小时调用：

```bash
bash scripts/mcp-hourly.sh
```

可以禁用内建 hourly timer：

```bash
systemctl --user disable --now sp7-powerlab-hourly.timer
```

collector 不要关闭。

## 7. 自动化开关

初始：

```toml
auto_switch_verified_profiles = true
auto_run_low_risk_trials = false
auto_promote_profiles = false
```

推荐顺序：

1. 只读 collector。
2. 验证场景。
3. 人工验证 profile。
4. 自动切 verified profile。
5. 手工批准 trial。
6. 验证 rollback/revalidation。
7. 打开 auto low-risk trial。
8. 最后再考虑 auto promote。

## 8. Suspend/resume

PowerLab 使用采样 gap 检测 suspend/collector interruption。

resume 后不会把 suspend 时间算入有效运行时长。

如果发生 crash 且留下 `runtime/trial.lock`，collector 启动会优先恢复 trial snapshot。

## 9. 数据目录

```text
runtime/powerlab.sqlite3
runtime/powerlab.sqlite3-wal
runtime/powerlab.sqlite3-shm
runtime/trial.lock
runtime/profile-state.json
runtime/manual-override
runtime/hourly-pack.json
```

`runtime/` 已加入 .gitignore。
