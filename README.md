# Linux Clash 自动部署

在 Linux 服务器上使用自己的 Clash/Mihomo 完整订阅：保留分流规则，设置用户级后台服务，每天更新订阅，新 SSH 会话自动使用代理。

已在 Ubuntu 24.04 x86_64 服务器实测。安装过程和验证范围见 [部署记录](docs/部署记录.md)。本仓库只提供工具，不包含作者的订阅、节点、机场地址、私人配置或服务器连接信息。每位使用者需要准备自己的订阅。

## 一键安装

需要 Linux、Python 3.10+、用户级 systemd，以及**包含节点、策略组和 rules 的 HTTPS Clash/Mihomo 完整订阅**。仅含节点的 Base64 通用订阅不适用。默认端口 7890、9090 必须空闲。

```bash
git clone https://github.com/KingYeon-Zoo/linux-clash-setup.git
cd linux-clash-setup
bash install.sh
```

按提示输入订阅链接，输入内容隐藏，不会写进命令历史。首次安装需要能访问 GitHub 官方发布页、订阅地址，以及配置引用的 GEO 数据；网络不通时使用下面的离线安装方式。

已经有一份完整 Clash 配置时：

```bash
bash install.sh --config "$HOME/private/clash.yaml"
```

仍会提示输入订阅链接，用于后续自动更新。无需 sudo；如果缺少 PyYAML，脚本尝试在用户目录创建虚拟环境并安装依赖。缺少 `python3-venv` 时，需要管理员先补齐该系统组件。

安装后重新连接 SSH 即可自动使用代理。安装当时的终端可以立即加载一次：

```bash
. "$HOME/.config/linux-clash/proxy.sh"
proxyctl test
```

**自动更新以订阅上游为准。** 每次下载完整订阅并保留其中的节点、策略组、分流规则与 DNS 设置；本地手改的运行配置会被下次更新替换。Clash Verge 的 JavaScript、合并补丁和界面设置不会自动迁移；如果规则来自这些本机定制，需要先将其整合到完整订阅中。首次部署记录中的源配置与本机生效规则已核对一致。

## 日常使用

```bash
proxyctl status       # 服务、定时器、下次更新、最近成功更新
proxyctl test         # 实际读取 GitHub、Hugging Face 小文件及国内站点
proxyctl update       # 立即更新订阅、校验、热加载
proxyctl restart     # 重启代理
proxyctl stop        # 暂停代理和更新定时器
proxyctl start       # 恢复代理和更新定时器
proxy_off            # 仅取消当前终端的代理环境变量
proxy_on             # 当前终端重新启用代理变量
```

默认使用**规则模式**。应用先连接本机代理，再由你的 Clash 规则决定直连、代理或拒绝。国内直连规则依然生效，设置代理环境变量不代表所有请求都经过海外节点。

该方案自动设置 `http_proxy`、`https_proxy`、`all_proxy` 及对应大写变量，适用于遵守这些变量的终端程序。它不是系统 TUN 全局接管：已有进程、忽略代理变量的程序、系统级服务、Docker daemon 和独立容器需要各自配置。SSH 和系统路由保持原有方式。

订阅每天**北京时间 04:00～04:05**更新一次。定时器启用 `Persistent=true`，关机期间错过的任务会在用户管理器恢复后补跑。订阅下载、解析或校验失败时继续使用旧配置；热加载失败时还原旧配置文件并尝试恢复加载，错误会记录在更新服务日志中。保留最近一次配置和源文件备份。

代理服务和定时器随用户管理器启动。脚本尝试执行 `loginctl enable-linger`；部分服务器允许用户自行启用，部分需要管理员操作。检查：

```bash
loginctl show-user "$USER" -p Linger
```

如果不是 `Linger=yes`，请管理员执行一次：

```bash
sudo loginctl enable-linger <你的用户名>
```

这一步用于退出所有 SSH 后继续运行，以及服务器重启后无需先登录即可启动服务。安装程序会明确报告是否成功。

## 规则集下载超时

某些配置将 `rule-providers` 的下载设为 `DIRECT`，但服务器无法直连对应 CDN。可以指定一个**已经存在的策略组名称**，让规则集下载使用它：

```bash
bash install.sh --config "$HOME/private/clash.yaml" --provider-proxy "节点选择"
```

将“节点选择”替换为你配置中的真实组名。该选项只修改规则集下载的出站方式，保留业务请求的分流规则。已有安装重新配置时加 `--reinstall`；这会重新生成本机 API 密钥并重启服务。

若系统直连正常，但原配置的 DoH 导致国内直连域名解析失败，可以额外加 `--direct-dns-system`。它仅将已匹配直连的请求改用系统 DNS；代理请求的解析和业务分流规则保持原配置。首次部署实测发现这类差异，因此在对应服务器启用了该选项；工具默认不修改它。

## 离线或服务器无法访问 GitHub

先在能联网的电脑上克隆本仓库、下载 [Mihomo 官方发布程序](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32)，再用 `scp` 上传。x86_64 推荐 `mihomo-linux-amd64-compatible-v1.19.32.gz`；ARM64 必须选择对应程序。

本版本 x86_64 压缩包的官方 SHA-256：

```text
ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20
```

使用 `sha256sum`（macOS 用 `shasum -a 256`）核对官方压缩包，然后解压：

```bash
gzip -dc mihomo-linux-amd64-compatible-v1.19.32.gz > mihomo
```

准备一个仓库之外的私有目录，例如 `~/private/clash-bootstrap/`，放入：

- 解压的 Linux `mihomo` 程序。
- 完整 `clash.yaml` 配置。
- `subscription.txt`，只含自己的订阅链接，权限设为 600。
- 可选 `assets/`：已有的 `Country.mmdb`、`geoip.dat`、`geosite.dat`、`ASN.mmdb` 和 `ruleset/` 缓存。可从已有客户端的数据目录复制；不复制整个客户端目录或数据库。缺少所需 GEO 文件时，安装程序会尝试联网下载。

以下已解压程序的 SHA-256 对应上面校验过的 x86_64 压缩包：

```bash
bash install.sh \
  --config "$HOME/private/clash-bootstrap/clash.yaml" \
  --subscription-file "$HOME/private/clash-bootstrap/subscription.txt" \
  --binary "$HOME/private/clash-bootstrap/mihomo" \
  --binary-sha256 7a0d59da2e678d56c899a3db996a2ad8963286c4f3634b0451435db248f13fa1 \
  --assets-dir "$HOME/private/clash-bootstrap/assets"
```

ARM64 请使用自己校验过的对应程序和 SHA-256。离线初装完成后还需要网络可达的有效节点，才能联网与更新订阅。

## 文件位置与隐私

| 位置 | 内容 |
|---|---|
| `~/.config/linux-clash/` | 私有源配置、订阅、节点凭据、本机 API 密钥、运行配置和备份 |
| `~/.local/bin/mihomo` | Mihomo 官方程序 |
| `~/.local/bin/proxyctl` | 管理命令 |
| `~/.local/share/linux-clash/` | 管理脚本及可选 Python 虚拟环境 |
| `~/.config/systemd/user/linux-clash*` | 用户级服务和更新定时器 |
| `~/.config/environment.d/90-linux-clash.conf` | 新用户服务的代理环境 |

私有配置目录权限为 700，配置、订阅和日志文件默认为 600。代理和控制 API 只监听 `127.0.0.1`，控制 API 使用随机密钥。脚本隐藏订阅输入，不打印下载 URL 或底层配置异常；详细校验诊断只保存在私有目录。

**不要把真实配置、订阅、节点列表、数据库或未经检查的日志上传到 GitHub。** `.gitignore` 只是辅助；主动 `git add -f` 仍可能上传敏感文件。本仓库的测试使用虚构配置，不需要真实订阅。

## 排查与卸载

```bash
systemctl --user status linux-clash.service
systemctl --user list-timers linux-clash-update.timer
journalctl --user -u linux-clash-update.service --since today
```

核心服务日志可能包含访问域名、节点名称，个别错误还会包含下载地址。分享日志前先脱敏。配置校验的详细诊断在 `~/.config/linux-clash/last-validation.log`。

卸载服务和自动代理环境：

```bash
proxyctl uninstall
proxy_off
```

保留私有配置、备份、程序和 Linger 设置，方便恢复；后续重装使用 `--reinstall`。卸载不会覆盖你在 shell 文件中添加的其他内容。请自行决定是否删除保留的私有文件。

## 开发与验证

```bash
bash -n install.sh
python3 -m unittest discover -s tests -v
```

测试覆盖 YAML `off` 兼容性、分流保留、无效订阅拒绝、校验失败保护、热加载失败恢复、成功更新备份、文件权限和登录脚本重复安装。

脚本以 MIT 许可证开源。Mihomo 和规则数据由各自上游维护，本仓库不打包分发其二进制或数据，也不自动升级核心程序。参考：[Mihomo 配置](https://wiki.metacubex.one/config/)、[代理集合](https://wiki.metacubex.one/config/proxy-providers/)、[规则集合](https://wiki.metacubex.one/config/rule-providers/)、[运行服务](https://wiki.metacubex.one/startup/service/)。
