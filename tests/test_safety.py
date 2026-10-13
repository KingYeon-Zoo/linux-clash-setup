"""校验配置兼容性、更新失败恢复和登录脚本幂等性；不使用真实订阅。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("linux_clash", Path(__file__).resolve().parents[1] / "linux_clash.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

SAMPLE = b'''find-process-mode: off
mixed-port: 7897
allow-lan: true
tun: {enable: true}
proxies:
  - {name: demo, type: socks5, server: example.invalid, port: 1080}
proxy-groups:
  - {name: PROXY, type: select, proxies: [demo]}
rule-providers:
  domestic: {type: http, behavior: domain, url: 'https://example.invalid/domestic', proxy: DIRECT}
rules: ["RULE-SET,domestic,DIRECT", "MATCH,PROXY"]
'''


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.config = self.home / ".config/linux-clash"
        self.config.mkdir(parents=True)
        self.paths = patch.multiple(app, HOME=self.home, CONFIG=self.config, SETTINGS=self.config / "settings.json")
        self.paths.start()
        app.atomic(app.SETTINGS, json.dumps({"url": "https://example.invalid/private?token=TEST_SENTINEL", "secret": "TEST_SENTINEL"}))
        app.atomic(self.config / "config.yaml", b"old-config\n")
        app.atomic(self.config / "source.yaml", b"old-source\n")

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_yaml_off_stays_string_and_true_stays_bool(self):
        doc = app.parse_config(SAMPLE)
        self.assertEqual(doc["find-process-mode"], "off")
        self.assertIs(doc["allow-lan"], True)

    def test_server_overrides_preserve_routing(self):
        source = app.parse_config(SAMPLE)
        result = app.normalized(source, "DEMO", "PROXY")
        for key in ("proxies", "proxy-groups", "rules"):
            self.assertEqual(source[key], result[key])
        self.assertEqual(result["rule-providers"]["domestic"]["proxy"], "PROXY")
        self.assertEqual(source["rule-providers"]["domestic"]["proxy"], "DIRECT")
        self.assertEqual(result["bind-address"], "127.0.0.1")
        self.assertFalse(result["allow-lan"])
        self.assertFalse(result["tun"]["enable"])

    def assert_old_untouched(self):
        self.assertEqual((self.config / "config.yaml").read_bytes(), b"old-config\n")
        self.assertEqual((self.config / "source.yaml").read_bytes(), b"old-source\n")

    def test_bad_subscription_never_replaces_working_config(self):
        with patch.object(app, "fetch", return_value=b"<html>expired TEST_SENTINEL</html>"), patch.object(app, "api") as api:
            with self.assertRaises(app.SetupError) as caught:
                app.update()
            self.assertNotIn("TEST_SENTINEL", str(caught.exception))
            api.assert_not_called()
        self.assert_old_untouched()

    def test_validation_failure_never_reloads(self):
        with patch.object(app, "fetch", return_value=SAMPLE), patch.object(app, "validate", side_effect=app.SetupError("invalid")), patch.object(app, "api") as api:
            with self.assertRaises(app.SetupError):
                app.update()
            api.assert_not_called()
        self.assert_old_untouched()

    def test_reload_failure_restores_old_config_and_retries_restore(self):
        with patch.object(app, "fetch", return_value=SAMPLE), patch.object(app, "validate"), patch.object(app, "api", side_effect=[app.SetupError("down"), {}]) as api:
            with self.assertRaises(app.SetupError):
                app.update()
            self.assertEqual(api.call_count, 2)
        self.assert_old_untouched()

    def test_successful_update_retains_previous_version_and_timestamp(self):
        with patch.object(app, "fetch", return_value=SAMPLE), patch.object(app, "validate"), patch.object(app, "api", return_value={}):
            with contextlib.redirect_stdout(io.StringIO()):
                app.update()
        self.assertEqual((self.config / "source.yaml").read_bytes(), SAMPLE)
        self.assertEqual((self.config / "config.previous.yaml").read_bytes(), b"old-config\n")
        self.assertTrue((self.config / "last-update.json").exists())
        self.assertEqual((self.config / "config.yaml").stat().st_mode & 0o777, 0o600)

    def test_shell_install_idempotent_and_before_noninteractive_guard(self):
        original = 'case $- in *i*) ;; *) return;; esac\n# existing customization\n'
        (self.home / ".bashrc").write_text(original)
        app.setup_shell()
        app.setup_shell()
        result = (self.home / ".bashrc").read_text()
        self.assertEqual(result.count(app.MARK_START), 1)
        self.assertTrue(result.endswith(original))
        self.assertLess(result.index(app.MARK_START), result.index("return"))
        self.assertEqual((self.config / ".bashrc.before-linux-clash").read_text(), original)

    def test_nonexistent_provider_group_is_rejected(self):
        with self.assertRaises(app.SetupError):
            app.normalized(app.parse_config(SAMPLE), "DEMO", "MISSING")

    def test_system_dns_override_only_changes_direct_dns(self):
        source = app.parse_config(SAMPLE)
        source["dns"] = {"enable": True, "nameserver": ["https://example.invalid/dns-query"],
                         "direct-nameserver": ["https://example.invalid/direct"]}
        result = app.normalized(source, "DEMO", direct_dns_system=True)
        self.assertEqual(result["dns"]["direct-nameserver"], ["system"])
        self.assertEqual(result["dns"]["nameserver"], source["dns"]["nameserver"])
        self.assertEqual(result["rules"], source["rules"])
        self.assertNotEqual(source["dns"]["direct-nameserver"], ["system"])


if __name__ == "__main__":
    unittest.main()
