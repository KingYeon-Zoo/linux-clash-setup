#!/usr/bin/env python3
"""保留 Clash 分流配置的 Linux 用户级部署与订阅更新工具。"""
import argparse
import contextlib
import datetime as dt
import fcntl
import getpass
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import copy
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

VERSION = "v1.19.32"
HOME = Path.home()
CONFIG = HOME / ".config/linux-clash"
DATA = HOME / ".local/share/linux-clash"
BIN = HOME / ".local/bin"
UNITS = HOME / ".config/systemd/user"
SETTINGS = CONFIG / "settings.json"
MARK_START = "# >>> linux-clash 自动代理 >>>"
MARK_END = "# <<< linux-clash 自动代理 <<<"


class SetupError(Exception):
    pass


class ClashLoader(yaml.SafeLoader):
    """匹配 YAML 1.2：off/on 是字符串，不能误当作布尔值。"""


ClashLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for _key, _rules in ClashLoader.yaml_implicit_resolvers.items():
    ClashLoader.yaml_implicit_resolvers[_key] = [r for r in _rules if r[0] != "tag:yaml.org,2002:bool"]
ClashLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF"))


def atomic(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode() if isinstance(content, str) else content
    fd, name = tempfile.mkstemp(prefix=".linux-clash-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        # 禁止把可能包含订阅 URL 的底层异常或配置诊断写入终端。
        raise SetupError(f"命令失败：{args[0]}（退出码 {result.returncode}）")
    return result


def systemctl(*args, check=True):
    return run(["systemctl", "--user", *args], check=check)


def settings():
    if not SETTINGS.exists():
        raise SetupError("尚未安装；请先运行 bash install.sh。")
    return json.loads(SETTINGS.read_text())


def fetch(url, *, limit=32 * 1024 * 1024, timeout=25):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise SetupError("下载地址必须使用 HTTPS。")
    # 直连失败后借助已启动的本机代理；安装时也可使用已有代理环境变量。
    handlers = [urllib.request.ProxyHandler(), urllib.request.ProxyHandler({}),
                urllib.request.ProxyHandler({"http": "http://127.0.0.1:7890",
                                             "https": "http://127.0.0.1:7890"})]
    for handler in handlers:
        try:
            opener = urllib.request.build_opener(handler)
            request = urllib.request.Request(url, headers={"User-Agent": "mihomo/" + VERSION.lstrip("v")})
            with opener.open(request, timeout=timeout) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise SetupError("下载内容超过大小限制。")
                if not data:
                    raise SetupError("下载内容为空。")
                return data
        except (OSError, urllib.error.URLError, ValueError):
            continue
    raise SetupError("下载失败；已尝试直连和本机代理。旧配置保持可用，未输出私有地址。")


def parse_config(raw):
    try:
        doc = yaml.load(raw, Loader=ClashLoader)
    except (yaml.YAMLError, UnicodeError):
        raise SetupError("配置不是有效的 Clash/Mihomo YAML；请使用机场的 Clash 订阅。") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list) or not doc["rules"]:
        raise SetupError("需要包含 rules 的完整 Clash 配置，不能使用仅含节点的订阅。")
    if not doc.get("proxies") and not doc.get("proxy-providers"):
        raise SetupError("配置中没有节点或代理集合。")
    if not isinstance(doc.get("proxy-groups"), list):
        raise SetupError("配置缺少 proxy-groups。")
    return doc


def normalized(doc, secret, provider_proxy=None, direct_dns_system=False):
    # 只覆盖服务器运行方式；保留节点、分组、规则、DNS 及其相互引用。
    doc = json.loads(json.dumps(doc))
    for key in ("port", "socks-port", "redir-port", "tproxy-port", "listeners",
                "external-controller-unix", "external-controller-pipe", "external-controller-tls",
                "external-ui", "external-ui-url", "external-controller-cors", "authentication"):
        doc.pop(key, None)
    doc.update({"mixed-port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
                "mode": "rule", "external-controller": "127.0.0.1:9090", "secret": secret,
                "tun": {"enable": False}})
    doc.setdefault("profile", {})["store-selected"] = True
    if isinstance(doc.get("find-process-mode"), bool):
        doc["find-process-mode"] = "always" if doc["find-process-mode"] else "off"
    if isinstance(doc.get("dns"), dict):
        # 不向局域网开放 DNS 服务；核心仍按原 DNS 设置解析。
        doc["dns"].pop("listen", None)
        if direct_dns_system:
            doc["dns"]["direct-nameserver"] = ["system"]
    for provider in (doc.get("proxy-providers") or {}).values():
        if provider.get("type") == "http":
            provider["interval"] = 86400
    if provider_proxy:
        names = {g["name"] for g in doc["proxy-groups"]} | {"DIRECT"}
        if provider_proxy not in names:
            raise SetupError("规则集下载策略组不存在；旧配置保持可用。")
        for provider in (doc.get("rule-providers") or {}).values():
            if provider.get("type") == "http":
                provider["proxy"] = provider_proxy
    return doc


def validate(path):
    result = run([str(BIN / "mihomo"), "-t", "-d", str(CONFIG), "-f", str(path)], check=False)
    atomic(CONFIG / "last-validation.log", result.stdout + result.stderr)
    if result.returncode:
        raise SetupError("Mihomo 配置校验失败；诊断保存在私有目录的 last-validation.log。")


def api(method, endpoint, payload=None, timeout=30):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("http://127.0.0.1:9090" + endpoint, data=body, method=method,
                                     headers={"Authorization": "Bearer " + settings()["secret"],
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            data = response.read()
            return json.loads(data) if data else {}
    except (OSError, ValueError, urllib.error.URLError):
        raise SetupError("本机 Mihomo 控制接口不可用；请运行 proxyctl status。") from None


def download_core(binary=None, expected=None):
    if binary:
        raw = Path(binary).expanduser().read_bytes()
        if not expected:
            raise SetupError("离线安装必须提供 --binary-sha256，校验已解压的 Mihomo 程序。")
        if hashlib.sha256(raw).hexdigest() != expected:
            raise SetupError("离线 Mihomo 程序 SHA-256 不匹配。")
    else:
        arch = {"x86_64": "amd64-compatible", "aarch64": "arm64"}.get(platform.machine())
        if not arch:
            raise SetupError("当前架构未内置下载映射；请使用 --binary 离线安装。")
        release = json.loads(fetch(f"https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/{VERSION}"))
        name = f"mihomo-linux-{arch}-{VERSION}.gz"
        asset = next((a for a in release["assets"] if a["name"] == name), None)
        if not asset or not str(asset.get("digest", "")).startswith("sha256:"):
            raise SetupError("官方发布元数据缺少 SHA-256，停止安装；可改用校验过的离线程序。")
        compressed = fetch(asset["browser_download_url"], limit=100 * 1024 * 1024)
        if hashlib.sha256(compressed).hexdigest() != asset["digest"].split(":", 1)[1]:
            raise SetupError("官方发布程序 SHA-256 校验失败。")
        raw = gzip.decompress(compressed)
    atomic(BIN / "mihomo", raw, 0o700)


def prepare_assets(doc, directory=None):
    if directory:
        root = Path(directory).expanduser().resolve()
        for name in ("Country.mmdb", "country.mmdb", "geoip.dat", "geosite.dat", "GeoSite.dat", "ASN.mmdb"):
            if (root / name).is_file():
                target = {"country.mmdb": "Country.mmdb", "GeoSite.dat": "geosite.dat"}.get(name, name)
                atomic(CONFIG / target, (root / name).read_bytes())
        if (root / "ruleset").is_dir():
            shutil.copytree(root / "ruleset", CONFIG / "ruleset", dirs_exist_ok=True)
    # 首次启动前准备 GEOIP/GEOSITE，避免离线校验触发自动下载。
    urls = doc.get("geox-url") or {}
    needs_site = "geosite:" in json.dumps(doc.get("dns", {}), ensure_ascii=False).lower() or any(str(r).upper().startswith("GEOSITE,") for r in doc["rules"])
    needs_ip = any(str(r).upper().startswith("GEOIP,") for r in doc["rules"])
    wanted = []
    if needs_site:
        wanted.append(("geosite.dat", urls.get("geosite", "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat")))
    if needs_ip:
        if doc.get("geodata-mode"):
            wanted.append(("geoip.dat", urls.get("geoip", "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.dat")))
        else:
            wanted.append(("Country.mmdb", urls.get("mmdb", "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/country.mmdb")))
    for name, url in wanted:
        if not (CONFIG / name).exists():
            atomic(CONFIG / name, fetch(url, limit=100 * 1024 * 1024))


def setup_shell():
    env = '''# 自动生成；仅设置应用代理，不更改系统路由。
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export all_proxy=socks5h://127.0.0.1:7890
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
export no_proxy=localhost,127.0.0.1,::1
export NO_PROXY="$no_proxy"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
proxy_on() { . "$HOME/.config/linux-clash/proxy.sh"; }
proxy_off() { unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; }
'''
    atomic(CONFIG / "proxy.sh", env)
    block = f'{MARK_START}\n[ ! -r "$HOME/.config/linux-clash/proxy.sh" ] || . "$HOME/.config/linux-clash/proxy.sh"\n{MARK_END}\n'
    targets = [HOME / ".bashrc", HOME / ".profile"]
    targets += [HOME / n for n in (".bash_profile", ".bash_login", ".zshenv") if (HOME / n).exists()]
    if os.environ.get("SHELL", "").endswith("zsh") and HOME / ".zshenv" not in targets:
        targets.append(HOME / ".zshenv")
    for path in targets:
        old = path.read_text() if path.exists() else ""
        if MARK_START in old:
            continue
        if path.exists():
            backup = CONFIG / (path.name + ".before-linux-clash")
            if not backup.exists():
                atomic(backup, old)
        # 放在 bashrc 的交互判断之前，使 ssh host 'curl ...' 同样有效。
        atomic(path, block + old, path.stat().st_mode & 0o777 if path.exists() else 0o600)
    envd = HOME / ".config/environment.d/90-linux-clash.conf"
    atomic(envd, "\n".join(f"{k}={v}" for k, v in {
        "http_proxy": "http://127.0.0.1:7890", "https_proxy": "http://127.0.0.1:7890",
        "all_proxy": "socks5h://127.0.0.1:7890", "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890", "ALL_PROXY": "socks5h://127.0.0.1:7890",
        "no_proxy": "localhost,127.0.0.1,::1", "NO_PROXY": "localhost,127.0.0.1,::1"}.items()) + "\n")


def setup_units():
    UNITS.mkdir(parents=True, exist_ok=True)
    atomic(UNITS / "linux-clash.service", '''[Unit]
Description=用户级 Mihomo 分流代理
After=network-online.target

[Service]
Type=simple
UMask=0077
ExecStart="%h/.local/bin/mihomo" -d "%h/.config/linux-clash"
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
LimitNOFILE=65536

[Install]
WantedBy=default.target
''')
    atomic(UNITS / "linux-clash-update.service", '''[Unit]
Description=每日更新 Clash 完整订阅并校验、热加载
After=linux-clash.service
Requires=linux-clash.service

[Service]
Type=oneshot
UMask=0077
ExecStart="%h/.local/bin/proxyctl" update
TimeoutStartSec=600
NoNewPrivileges=true
''')
    atomic(UNITS / "linux-clash-update.timer", '''[Unit]
Description=每天北京时间凌晨更新 Clash 订阅

[Timer]
OnCalendar=*-*-* 04:00:00 Asia/Shanghai
RandomizedDelaySec=300
Persistent=true
Unit=linux-clash-update.service

[Install]
WantedBy=timers.target
''')
    systemctl("daemon-reload")
    systemctl("enable", "--now", "linux-clash.service", "linux-clash-update.timer")


def install(args):
    if platform.system() != "Linux" or sys.version_info < (3, 10):
        raise SetupError("需要 Linux 和 Python 3.10+。")
    if SETTINGS.exists() and not args.reinstall:
        raise SetupError("已有安装；请运行 proxyctl update，或显式使用 --reinstall。")
    if not SETTINGS.exists():
        if (BIN / "mihomo").exists() or (BIN / "proxyctl").exists():
            raise SetupError("用户目录已有 mihomo 或 proxyctl；请先处理名称冲突，避免覆盖现有工具。")
        for port in (7890, 9090):
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    raise SetupError(f"本机端口 {port} 已占用；请先处理端口冲突。") from None
    systemctl("show-environment")
    for path in (CONFIG, DATA, BIN):
        path.mkdir(parents=True, exist_ok=True)
    CONFIG.chmod(0o700)
    DATA.chmod(0o700)
    if args.subscription_file:
        text = Path(args.subscription_file).expanduser().read_text().strip()
        try:
            url = json.loads(text)["url"]
        except (ValueError, TypeError, KeyError):
            url = text
    else:
        url = getpass.getpass("请输入 Clash 完整订阅链接（隐藏输入）：").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SetupError("需要有效的 HTTPS Clash 完整订阅链接。")
    raw = Path(args.config).expanduser().read_bytes() if args.config else fetch(url)
    doc = parse_config(raw)
    secret = secrets.token_urlsafe(32)
    saved = {"url": url, "secret": secret, "version": VERSION, "provider_proxy": args.provider_proxy,
             "direct_dns_system": args.direct_dns_system,
             "installed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    # 在所有校验完成之前，不修改登录环境和服务。
    download_core(args.binary, args.binary_sha256)
    prepare_assets(doc, args.assets_dir)
    candidate = CONFIG / "candidate.yaml"
    atomic(candidate, yaml.safe_dump(normalized(doc, secret, args.provider_proxy, args.direct_dns_system), allow_unicode=True, sort_keys=False))
    validate(candidate)
    if (CONFIG / "config.yaml").exists():
        atomic(CONFIG / "config.before-reinstall.yaml", (CONFIG / "config.yaml").read_bytes())
    atomic(CONFIG / "source.yaml", raw)
    atomic(CONFIG / "config.yaml", candidate.read_bytes())
    candidate.unlink()
    atomic(SETTINGS, json.dumps(saved, indent=2) + "\n")
    app = DATA / "app/linux_clash.py"
    atomic(app, Path(__file__).read_bytes(), 0o700)
    atomic(BIN / "proxyctl", "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(app)) + ' "$@"\n', 0o700)
    setup_units()
    systemctl("restart", "linux-clash.service")
    # 尝试给当前用户启用 linger；部分服务器允许用户自行启用。
    result = run(["loginctl", "enable-linger"], check=False)
    setup_shell()
    linger = run(["loginctl", "show-user", str(os.getuid()), "-p", "Linger"], check=False).stdout.strip()
    print("安装完成；代理端口 127.0.0.1:7890，规则模式，服务与每日更新已启用。")
    if linger != "Linger=yes":
        print("尚未启用退出登录后常驻：请管理员执行 sudo loginctl enable-linger <你的用户名>。")
    else:
        print("Linger=yes：服务可在退出 SSH 后持续运行，并随用户管理器开机启动。")
    print('新 SSH 会话自动设置代理；当前会话执行：. "$HOME/.config/linux-clash/proxy.sh"')


def update():
    conf = settings()
    raw = fetch(conf["url"])
    doc = parse_config(raw)
    candidate = CONFIG / "candidate.yaml"
    atomic(candidate, yaml.safe_dump(normalized(doc, conf["secret"], conf.get("provider_proxy"), conf.get("direct_dns_system", False)), allow_unicode=True, sort_keys=False))
    validate(candidate)
    current = CONFIG / "config.yaml"
    old = current.read_bytes()
    old_source = (CONFIG / "source.yaml").read_bytes()
    atomic(CONFIG / "config.previous.yaml", old)
    atomic(CONFIG / "source.previous.yaml", old_source)
    atomic(current, candidate.read_bytes())
    try:
        api("PUT", "/configs?force=true", {"path": str(current)}, timeout=120)
    except SetupError:
        atomic(current, old)
        with contextlib.suppress(SetupError):
            api("PUT", "/configs?force=true", {"path": str(current)}, timeout=120)
        raise SetupError("新订阅热加载失败，已还原旧配置文件；请检查服务状态。") from None
    atomic(CONFIG / "source.yaml", raw)
    candidate.unlink(missing_ok=True)
    stamp = {"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "nodes": len(doc.get("proxies") or []), "groups": len(doc["proxy-groups"]), "rules": len(doc["rules"]),
             "source_sha256": hashlib.sha256(raw).hexdigest()}
    atomic(CONFIG / "last-update.json", json.dumps(stamp, indent=2) + "\n")
    print(f"订阅已更新并热加载：{stamp['nodes']} 个内置节点，{stamp['groups']} 个策略组，{stamp['rules']} 条规则。")


def status():
    for name in ("linux-clash.service", "linux-clash-update.timer"):
        enabled = systemctl("is-enabled", name, check=False).stdout.strip()
        active = systemctl("is-active", name, check=False).stdout.strip()
        print(f"{name}: {enabled}, {active}")
    print(run(["loginctl", "show-user", str(os.getuid()), "-p", "Linger"], check=False).stdout.strip())
    print(systemctl("show", "linux-clash-update.timer", "-p", "NextElapseUSecRealtime", check=False).stdout.strip())
    if (CONFIG / "last-update.json").exists():
        doc = json.loads((CONFIG / "last-update.json").read_text())
        print("最近成功更新：", doc["updated_at"])
    doc = api("GET", "/configs")
    print("代理模式：", doc.get("mode"), "；端口：", doc.get("mixed-port"))


def test():
    status()
    failed = False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"}))
    targets = {"GitHub": "https://github.com/", "Hugging Face 小文件": "https://huggingface.co/gpt2/resolve/main/config.json",
               "国内直连规则": "https://www.baidu.com/"}
    for label, url in targets.items():
        for attempt in range(1, 4):
            try:
                with opener.open(urllib.request.Request(url, headers={"User-Agent": "linux-clash-setup-test"}), timeout=30) as response:
                    body = response.read(4096)
                    if not body:
                        raise ValueError("empty")
                    if label.startswith("Hugging") and json.loads(body).get("model_type") != "gpt2":
                        raise ValueError("unexpected body")
                    print(f"通过：{label}，HTTP {response.status}，实际读取 {len(body)} 字节（第 {attempt} 次）。", flush=True)
                break
            except (OSError, ValueError, urllib.error.URLError):
                if attempt < 3:
                    print(f"{label} 第 {attempt} 次未通过，准备重试。", flush=True)
                    time.sleep(1)
                else:
                    failed = True
                    print(f"失败：{label}；请检查节点、DNS 和服务日志。", flush=True)
    if failed:
        raise SetupError("部分连通性测试未通过。")


def uninstall():
    systemctl("disable", "--now", "linux-clash-update.timer", check=False)
    systemctl("stop", "linux-clash-update.service", check=False)
    systemctl("disable", "--now", "linux-clash.service", check=False)
    for name in ("linux-clash.service", "linux-clash-update.service", "linux-clash-update.timer"):
        (UNITS / name).unlink(missing_ok=True)
    for name in (".bashrc", ".profile", ".bash_profile", ".bash_login", ".zshenv"):
        path = HOME / name
        if path.exists():
            text = path.read_text()
            start, end = text.find(MARK_START), text.find(MARK_END)
            if 0 <= start < end:
                new = text[:start] + text[end + len(MARK_END):].lstrip("\n")
                atomic(path, new, path.stat().st_mode & 0o777)
    (HOME / ".config/environment.d/90-linux-clash.conf").unlink(missing_ok=True)
    systemctl("daemon-reload")
    # 清理本工具设置的用户管理器变量，保留其他代理配置。
    ours = {"http://127.0.0.1:7890", "socks5h://127.0.0.1:7890", "localhost,127.0.0.1,::1"}
    environment = systemctl("show-environment", check=False).stdout
    names = [line.split("=", 1)[0] for line in environment.splitlines()
             if "=" in line and "proxy" in line.split("=", 1)[0].lower() and line.split("=", 1)[1] in ours]
    if names:
        systemctl("unset-environment", *names)
    print("已卸载服务和自动代理设置；保留私有配置、备份、程序和用户 Linger 设置。")
    print("当前终端请执行 proxy_off，或重新登录。重新安装请使用 --reinstall。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="安装用户级服务与自动代理环境")
    setup.add_argument("--config", help="现有完整 Clash 配置；未提供时从订阅下载")
    setup.add_argument("--subscription-file", help="私有订阅文件（纯 URL 或含 url 的 JSON）")
    setup.add_argument("--binary", help="离线、已解压的 Linux Mihomo 程序")
    setup.add_argument("--binary-sha256", help="离线程序的 SHA-256")
    setup.add_argument("--assets-dir", help="离线 GeoIP、GeoSite 和 ruleset 缓存目录")
    setup.add_argument("--provider-proxy", help="让规则集下载走指定策略组；不改变业务分流规则")
    setup.add_argument("--direct-dns-system", action="store_true", help="已匹配直连的请求使用系统 DNS，适合原 DoH 不稳定的服务器")
    setup.add_argument("--reinstall", action="store_true", help="显式覆盖现有安装")
    for name in ("update", "status", "test", "start", "stop", "restart", "uninstall"):
        commands.add_parser(name)
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.command == "install":
            install(args)
        elif args.command == "update":
            CONFIG.mkdir(parents=True, exist_ok=True)
            with (CONFIG / "update.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                update()
        elif args.command == "status":
            status()
        elif args.command == "test":
            test()
        elif args.command == "uninstall":
            uninstall()
        elif args.command == "stop":
            systemctl("stop", "linux-clash-update.timer", "linux-clash-update.service", "linux-clash.service")
        elif args.command == "start":
            systemctl("start", "linux-clash.service", "linux-clash-update.timer")
        else:
            systemctl(args.command, "linux-clash.service")
    except BlockingIOError:
        print("已有订阅更新任务正在运行。", file=sys.stderr)
        return 1
    except SetupError as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print("错误：文件、数据或运行环境异常。为保护订阅和节点凭据，未输出底层异常内容。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
