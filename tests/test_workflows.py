import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'ubuntu-residential-proxy'
sys.path.insert(0, str(SKILL / 'scripts'))
import common
import proxyctl
import export_client
import ssh_access
spec = importlib.util.spec_from_file_location('installer', SKILL / 'scripts' / 'install.py')
installer = importlib.util.module_from_spec(spec)
sys.modules['installer'] = installer
spec.loader.exec_module(installer)


def config():
    return {'outbounds': [{'tag': 'direct', 'protocol': 'freedom'},
                          {'tag': 'residential', 'protocol': 'socks', 'settings': {'servers': [{'address': 'proxy.example.com', 'users': [{'user': 'demo', 'pass': 'TEST_ONLY'}]}]}}],
            'routing': {'domainStrategy': 'AsIs', 'rules': [
                {'type': 'field', 'inboundTag': ['api'], 'outboundTag': 'api'},
                {'type': 'field', 'domain': ['geosite:anthropic', 'domain:ipinfo.io'], 'outboundTag': 'residential'},
                {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'blocked'}]}}


CLASH = 'proxies:\n  - name: main\n    type: vless\n  - name: backup\n    type: hysteria2\nproxy-groups:\n  - name: nodes\n    type: select\n    proxies: [main, backup]\nrules:\n  - MATCH,nodes\n'


class FakePanel:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.writes = []
        self.restarts = 0
        self.fail_restart = False

    def config(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))

    def restart(self):
        self.restarts += 1
        if self.fail_restart:
            self.fail_restart = False
            raise RuntimeError('Simulated restart failure')


class RouteTests(unittest.TestCase):
    def test_add_preserves_every_other_setting_and_is_idempotent(self):
        old = config()
        expected = copy.deepcopy(old)
        expected['routing']['rules'][1]['domain'].append('domain:ip.net.coffee')
        result = proxyctl.change(old, 'add', ['https://IP.NET.COFFEE/ip/'])
        self.assertEqual(result, expected)
        self.assertEqual(old, config())
        self.assertEqual(proxyctl.change(result, 'add', ['ip.net.coffee']), result)

    def test_delete_last_domain_removes_rule_instead_of_catchall(self):
        old = config()
        new = proxyctl.change(old, 'remove', ['geosite:anthropic', 'domain:ipinfo.io'])
        self.assertEqual(new['routing']['rules'], [old['routing']['rules'][0], old['routing']['rules'][2]])
        self.assertEqual(new['outbounds'], old['outbounds'])

    def test_add_after_removing_all_domains_requires_explicit_narrow_rule(self):
        empty = proxyctl.change(config(), 'remove', ['geosite:anthropic', 'domain:ipinfo.io'])
        with self.assertRaises(ValueError):
            proxyctl.change(empty, 'add', ['example.com'])
        restored = proxyctl.change(empty, 'add', ['example.com'], create_rule=True)
        self.assertEqual(restored['routing']['rules'][1], {'type': 'field', 'outboundTag': 'residential', 'domain': ['domain:example.com']})
        self.assertEqual(restored['outbounds'], empty['outbounds'])

    def test_list_has_stable_revision_without_proxy_secrets(self):
        result = proxyctl.listing(config())
        self.assertEqual(result['entries'][0]['kind'], 'domain-set')
        self.assertNotIn('TEST_ONLY', json.dumps(result))
        changed = proxyctl.change(config(), 'add', ['example.com'])
        self.assertNotEqual(result['revision'], common.revision(changed))

    def test_multiple_rules_require_selection(self):
        old = config()
        old['routing']['rules'].append({'domain': ['domain:second.example'], 'outboundTag': 'residential'})
        with self.assertRaises(ValueError):
            proxyctl.change(old, 'add', ['example.com'])
        self.assertIn('domain:example.com', proxyctl.change(old, 'add', ['example.com'], 3)['routing']['rules'][3]['domain'])

    def test_disabled_rule_is_not_silently_enabled(self):
        old = config()
        old['routing']['rules'][1]['enabled'] = False
        with self.assertRaises(ValueError):
            proxyctl.change(old, 'add', ['example.com'])

    def test_domain_scope_and_invalid_inputs(self):
        self.assertEqual(proxyctl.normalize('full:api.example.com'), 'full:api.example.com')
        self.assertEqual(proxyctl.normalize('https://example.com:8443/path?q=x'), 'domain:example.com')
        for value in ['192.0.2.1', '*.example.com', 'example.com/path', 'https://user:pass@example.com', 'geosite:cn', 'localhost', 'bad..example']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                proxyctl.normalize(value)

    @patch('proxyctl.time.sleep')
    def test_successful_apply_and_validation_failure_restore(self, _sleep):
        old = config()
        new = proxyctl.change(old, 'add', ['example.com'])
        panel = FakePanel(old)
        result = proxyctl.apply_change(panel, old, new, verify_fn=lambda _: None, backup_fn=lambda _: 'private-backup')
        self.assertTrue(result['changed'])
        self.assertEqual(panel.value, new)
        panel = FakePanel(old)
        def verify(value):
            if value == new:
                raise RuntimeError('Simulated runtime validation failure')
        with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
            proxyctl.apply_change(panel, old, new, verify_fn=verify, backup_fn=lambda _: 'private-backup')
        self.assertEqual(panel.value, old)
        self.assertEqual(panel.restarts, 2)

    @patch('proxyctl.time.sleep')
    def test_restart_failure_and_unverified_rollback_are_distinguished(self, _sleep):
        old, panel = config(), FakePanel(config())
        new = proxyctl.change(old, 'add', ['example.com'])
        panel.fail_restart = True
        with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
            proxyctl.apply_change(panel, old, new, verify_fn=lambda _: None, backup_fn=lambda _: 'backup')
        with self.assertRaisesRegex(RuntimeError, 'rollback could not be verified'):
            proxyctl.apply_change(panel, old, new, verify_fn=lambda _: (_ for _ in ()).throw(RuntimeError()), backup_fn=lambda _: 'backup')

    def test_concurrent_change_never_overwrites(self):
        old = config()
        new = proxyctl.change(old, 'add', ['example.com'])
        panel = FakePanel(proxyctl.change(old, 'add', ['other.example']))
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            proxyctl.apply_change(panel, old, new, verify_fn=lambda _: None, backup_fn=lambda _: 'backup')
        self.assertEqual(panel.writes, [])

    def test_online_sqlite_backup_is_consistent(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'source.sqlite'
            conn = sqlite3.connect(db)
            conn.execute('CREATE TABLE sample(value TEXT)')
            conn.execute("INSERT INTO sample VALUES ('fixture')")
            conn.commit()
            target = proxyctl.backup(config(), Path(temp) / 'backups', db)
            copydb = sqlite3.connect(target / 'x-ui.db')
            self.assertEqual(copydb.execute('SELECT value FROM sample').fetchone(), ('fixture',))
            self.assertEqual(json.loads((target / 'xray-setting.json').read_text()), config())
            copydb.close()
            conn.close()


class PanelTests(unittest.TestCase):
    def test_post_api_shapes_and_form_encoding(self):
        current = config()
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                nonlocal current
                body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
                self.assert_auth = self.headers.get('Authorization')
                requests.append((self.path, self.assert_auth))
                if self.path.endswith('/update'):
                    current = json.loads(urllib.parse.parse_qs(body)['xraySetting'][0])
                    obj = None
                elif self.path.endswith('/xray/'):
                    obj = json.dumps({'xraySetting': current})
                else:
                    obj = None
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'obj': obj}).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            panel = common.Panel('http://127.0.0.1:' + str(server.server_port) + '/panel/', 'TEST_TOKEN_ONLY')
            self.assertEqual(panel.config(), current)
            new = proxyctl.change(current, 'add', ['example.com'])
            panel.save(new)
            self.assertEqual(panel.config(), new)
            panel.restart()
            self.assertTrue(all(auth == 'Bearer TEST_TOKEN_ONLY' for _, auth in requests))
            self.assertTrue(all(path.startswith('/panel/panel/api/') for path, _ in requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_local_api_rejects_external_endpoint(self):
        with self.assertRaises(ValueError):
            common.Panel('https://example.com', 'fixture')


class DeploymentTests(unittest.TestCase):
    def test_vendor_checksums_and_adapter_preserves_original(self):
        manifest = json.loads((SKILL / 'vendor' / 'manifest.json').read_text())
        for name, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((SKILL / 'vendor' / 'reality-setup' / name).read_bytes()).hexdigest(), digest)
        source = (SKILL / 'vendor' / 'reality-setup' / 'setup.sh').read_text(encoding='utf-8')
        rendered = installer.render(source, 'a' * 48)
        self.assertNotIn('rm -rf /etc/x-ui', rendered)
        self.assertNotIn('PANEL_PASS=123456', rendered)
        self.assertIn('IFS= read -r -p', rendered)
        self.assertEqual(hashlib.sha256((SKILL / 'vendor' / 'reality-setup' / 'setup.sh').read_bytes()).hexdigest(), manifest['files']['setup.sh'])

    @patch('installer.subprocess.run')
    def test_fresh_ubuntu_guard_and_busy_port(self, run):
        with tempfile.TemporaryDirectory() as temp:
            etc = Path(temp)
            (etc / 'os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
            run.return_value.stdout = ''
            installer.preflight(etc)
            run.return_value.stdout = 'tcp LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n'
            with self.assertRaisesRegex(RuntimeError, 'already in use'):
                installer.preflight(etc)
            (etc / 'x-ui').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'Existing deployment'):
                installer.preflight(etc)

    def test_proxy_input_keeps_special_characters(self):
        value = {'server_ip': '203.0.113.10', 'proxy': {'host': 'proxy.example.com', 'port': 12345, 'username': ' user ', 'password': 'a\\b"c$! '}}
        self.assertEqual(installer.validate_input(value), value)
        value['proxy']['password'] = 'bad\nvalue'
        with self.assertRaises(ValueError):
            installer.validate_input(value)

    @patch('installer.subprocess.run')
    def test_deploy_validation_rejects_missing_secondary_and_wrong_residential_exit(self, run):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['vless.txt', 'clash.yaml']:
                (root / name).write_text('fixture')
            panel = FakePanel(config())
            with self.assertRaisesRegex(RuntimeError, 'hy2.txt'):
                installer.verify_deployment(panel, root)
            (root / 'hy2.txt').write_text('fixture')
            (root / 'vless.txt').write_text('vless://test@example.com:443')
            (root / 'hy2.txt').write_text('hysteria2://test@example.com:443')
            (root / 'clash.yaml').write_text(CLASH)
            runtime = root / 'runtime.json'
            runtime.write_text(json.dumps({'inbounds': [{'protocol': 'vless'}, {'protocol': 'hysteria'}]}))
            panel.value['outbounds'][1]['settings']['servers'][0]['port'] = 12345
            run.side_effect = [
                __import__('subprocess').CompletedProcess([], 0, 'Reality：成功\nHysteria2：成功\n== 走节点访问 ipinfo.io 的出口 IP：192.0.2.10 ==\n', ''),
                __import__('subprocess').CompletedProcess([], 0, '192.0.2.20', ''),
            ]
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                with patch.object(installer, 'protect'):
                    installer.verify_deployment(panel, root, runtime)

    def test_export_requires_both_nodes_and_full_clash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'vless.txt').write_text('vless://test-client@example.com:443\n')
            (source / 'clash.yaml').write_text(CLASH)
            with self.assertRaises(RuntimeError):
                export_client.export(source, root / 'out')
            (source / 'hy2.txt').write_text('hysteria2://test-client@example.com:443\n')
            export_client.export(source, root / 'out')
            self.assertEqual(sorted(p.name for p in (root / 'out').iterdir()), sorted(export_client.NAMES))

    def test_empty_or_single_node_clash_is_not_delivered(self):
        values = {'vless.txt': 'vless://fixture', 'hy2.txt': 'hysteria2://fixture', 'clash.yaml': 'proxies: []'}
        with self.assertRaises(RuntimeError):
            export_client.validate_export_values(values)
        values['clash.yaml'] = CLASH.replace('    type: hysteria2\n', '')
        with self.assertRaises(RuntimeError):
            export_client.validate_export_values(values)

    def test_public_key_install_script_does_not_overwrite_authorized_keys(self):
        script = ssh_access.public_key_script('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIXTURE user')
        self.assertIn('>> "$file"', script)
        self.assertIn('test ! -L', script)
        for kind in ['ssh-rsa', 'ecdsa-sha2-nistp256', 'sk-ssh-ed25519@openssh.com']:
            self.assertIn(kind, ssh_access.public_key_script(kind + ' AAAAFIXTURE existing-key'))
        for host in ['--option', 'host;command', 'x\ncommand']:
            with self.assertRaises(ValueError):
                ssh_access.validate_target(host, 'ubuntu', 22)


if __name__ == '__main__':
    unittest.main()
