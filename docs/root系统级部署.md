# 有 root 权限时部署 Mihomo

本指南面向需要在自己的 Linux 服务器上使用个人 Clash/Mihomo 完整订阅的使用者。使用自己的订阅，私有配置存放在仓库之外。

## 准备系统

需要 Linux、systemd、Python 3.10+ 和可用的完整 Clash/Mihomo HTTPS 订阅。有 root 权限时使用系统级服务，代理随系统启动，退出 SSH 后继续运行。

Ubuntu 24.04 可以使用系统自带的 Python，并补齐虚拟环境支持：

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
```

Alibaba Cloud Linux 3 的默认 `python3` 可能仍为 3.6。安装一个独立的新版本：

```bash
sudo dnf install -y git python3.11 python3.11-pip
python3.11 --version
```

安装器依次查找 Python 3.13、3.12、3.11、3.10 和满足要求的 `python3`。它在 `/opt/linux-clash/venv/` 中安装依赖，保留发行版原有的 Python 命令。需要指定可信 Python 镜像源时，可以通过标准 `PIP_INDEX_URL` 环境变量传入安装进程。

## 安装

```bash
git clone https://github.com/KingYeon-Zoo/linux-clash-setup.git
cd linux-clash-setup
sudo bash install.sh --system
```

已经以 root 登录时省去 `sudo`。订阅输入隐藏，安装脚本会下载官方 Mihomo 程序并校验发布元数据中的 SHA-256。首次安装需要能访问 GitHub、订阅与 GEO 数据地址。

首次联网不稳定时，可以先在能联网的电脑上准备并校验程序、完整配置、订阅文件和缓存，再上传到服务器的私有目录：

```bash
sudo bash install.sh --system \
  --config /root/private/clash-bootstrap/clash.yaml \
  --subscription-file /root/private/clash-bootstrap/subscription.txt \
  --binary /root/private/clash-bootstrap/mihomo \
  --binary-sha256 '<已解压程序的SHA-256>' \
  --assets-dir /root/private/clash-bootstrap/assets
```

这里的 SHA-256 是已经核验来源的解压后程序的哈希。官方压缩包的核对方式见 README 的离线安装部分。配置、订阅和缓存只通过自己的 SSH 连接传输，避免放入公开仓库。

原配置的规则集下载在服务器上无法直连时，可以加 `--provider-proxy '<订阅已有策略组名称>'`。原 DoH 不稳定且系统 DNS 正常时，可以加 `--direct-dns-system`。这两个适配都会保存，在后续订阅更新时继续应用。

## 文件与服务

| 位置 | 内容 |
|---|---|
| `/etc/linux-clash/` | 配置、订阅设置、API 密钥、运行缓存和更新备份 |
| `/opt/linux-clash/venv/` | 独立 Python 环境 |
| `/opt/linux-clash/app/linux_clash.py` | 管理与更新程序 |
| `/usr/local/bin/mihomo` | 官方 Mihomo 程序 |
| `/usr/local/bin/proxyctl` | 系统级管理入口，需要 root |
| `/etc/systemd/system/linux-clash.service` | 系统级代理服务 |
| `/etc/systemd/system/linux-clash-update.service` | 订阅更新任务 |
| `/etc/systemd/system/linux-clash-update.timer` | 每日更新定时器 |
| `/etc/profile.d/linux-clash.sh` | 登录会话使用的代理环境变量与开关函数 |

私有配置和程序数据目录权限为 700，配置文件默认 600。Mihomo 服务使用 `NoNewPrivileges`、只读系统目录、隐藏用户目录和空的能力集合，运行时只允许写入自己的配置与缓存目录。服务使用回环代理端口，HTTP/SOCKS 端口是 `127.0.0.1:7890`，带随机密钥的控制 API 是 `127.0.0.1:9090`。

## 验证与管理

```bash
. /etc/profile.d/linux-clash.sh
proxyctl status
proxyctl test
systemctl is-enabled linux-clash.service linux-clash-update.timer
systemctl list-timers linux-clash-update.timer
```

在 sudo 会话中管理时使用 `sudo proxyctl ...`。新 SSH 会话会加载代理变量，包括遵守这些变量的非交互终端命令。已有进程、独立系统服务和容器可分别设置自己的代理环境。

手动触发与定时器相同的更新任务：

```bash
sudo systemctl start linux-clash-update.service
sudo journalctl -u linux-clash-update.service -n 20
sudo proxyctl status
```

每天北京时间 04:00 起随机延迟最多 5 分钟运行，错过的更新在系统恢复后补跑。候选订阅必须通过解析和 Mihomo 校验，热加载失败时恢复旧配置。仍存在的策略组选择会保留。

核心日志可能包含节点名、域名或配置地址，公开分享前需要脱敏。`proxyctl status` 显示服务、定时器和最近成功更新时间，可用它区分“定时器已启用”和“订阅更新实际成功”。

```bash
sudo proxyctl stop
sudo proxyctl start
sudo proxyctl restart
sudo proxyctl uninstall
```

卸载会移除系统级服务和本工具添加的登录代理环境，保留配置、程序与备份，方便恢复。当前终端可以执行 `proxy_off` 清除代理变量。

## 阿里云首次部署遇到的问题

2026-11-18，在 Alibaba Cloud Linux 3 x86_64 轻量实例上部署。实例使用预装应用镜像，默认 Python 为 3.6.8。

1. **有 root 密码仍无法使用 SSH 密码登录。** SSH 服务可达，但只公布公钥等认证方式。命令助手检查显示 `passwordauthentication no`。通过已登录的云控制台命令助手添加本机专用公钥后，密钥登录成功。专用公钥使用 `restrict` 限制转发与伪终端能力，正常远程命令和文件传输可用。私钥仅保存在客户端。
2. **预装应用镜像与计划中的 Ubuntu 不同。** 读取 `/etc/os-release` 后确认实际发行版，再使用它的包管理器。已有应用端口需要先检查，新增服务使用空闲端口。
3. **默认 Python 过旧。** 从发行版官方软件源安装 Python 3.11 和 pip，系统级安装器选择新解释器并创建独立虚拟环境。
4. **首次引导需要可达的程序与数据源。** 使用已核验 SHA-256 的官方 Mihomo v1.19.32 程序，并复用本机已有 GEO 数据与规则缓存，减少首次启动的外部下载依赖。
5. **将普通用户部署直接套在 root 上会保留用户管理器依赖。** 本次增加系统级安装入口，共用原来的订阅校验与回滚逻辑，使用系统级服务和定时器，服务目标为 `multi-user.target`。
6. **进程启动与控制 API 就绪存在时间差。** 自动恢复测试中，systemd 已显示 `active`，紧接着调用控制 API 仍短暂失败。等待 API 就绪后重新执行验证，代理和网站访问恢复。排查时应同时检查进程状态和代理实际请求结果。

实际验证结果以部署记录为准。公开文档中的命令使用示例路径，不记录真实服务器地址、订阅、节点凭据、密钥或未经脱敏的日志。

参考 [Mihomo systemd 运行服务](https://wiki.metacubex.one/startup/service/) 和 [全局配置](https://wiki.metacubex.one/config/general/)。
