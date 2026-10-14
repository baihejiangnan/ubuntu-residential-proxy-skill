import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'ubuntu-residential-proxy' / 'scripts'))
import common
import replace_proxy
from test_workflows import config, FakePanel

SELFTEST_BOTH = '== 节点自测 Reality：成功 ==\n== 节点自测 Hysteria2：成功 ==\n'


def old_config():
    old = config()
    outbound = old['outbounds'][1]
    outbound['settings']['servers'][0]['port'] = 1080
    outbound['settings']['servers'][0]['users'][0]['level'] = 8
    outbound['settings']['version'] = '5'
    outbound['streamSettings'] = {'sockopt': {'tcpFastOpen': True}}
    old['dns'] = {'servers': ['localhost']}
    old['routing']['rules'].append({'type': 'field', 'enabled': False, 'domain': ['domain:kept.example'], 'outboundTag': 'residential'})
    old['inbounds'] = [{'tag': 'do-not-change', 'settings': {'clients': [{'id': 'FIXTURE_NODE_ID'}]}}]
    return old


def proxy():
    return {'host': 'new-proxy.example.com', 'port': 12345, 'username': 'NEW_USER', 'password': 'NEW_TEST_ONLY\\"! '}


def no_verify(config, expected_exit=None):
    return 'fixture verified'


class ReplacementTests(unittest.TestCase):
    def test_only_four_connection_fields_change(self):
        old = old_config()
        expected = copy.deepcopy(old)
        server = expected['outbounds'][1]['settings']['servers'][0]
        server['address'], server['port'] = proxy()['host'], proxy()['port']
        server['users'][0]['user'], server['users'][0]['pass'] = proxy()['username'], proxy()['password']
        new = replace_proxy.replace_connection(old, proxy())
        self.assertEqual(new, expected)
        self.assertEqual(old, old_config())
        self.assertEqual(new['routing'], old['routing'])
        self.assertEqual(new['inbounds'], old['inbounds'])

    def test_ambiguous_endpoints_and_wrong_protocol_are_rejected(self):
        for problem in ['extra-server', 'extra-user', 'extra-tag', 'wrong-protocol']:
            old = old_config()
            outbound = old['outbounds'][1]
            if problem == 'extra-server':
                outbound['settings']['servers'].append(copy.deepcopy(outbound['settings']['servers'][0]))
            elif problem == 'extra-user':
                outbound['settings']['servers'][0]['users'].append({'user': 'OTHER', 'pass': 'FIXTURE'})
            elif problem == 'extra-tag':
                old['outbounds'].append(copy.deepcopy(outbound))
            else:
                outbound['protocol'] = 'http'
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                replace_proxy.replace_connection(old, proxy())

    def test_preflight_failure_never_backs_up_writes_or_restarts(self):
        panel = FakePanel(old_config())
        backup = MagicMock()
        probe = MagicMock(side_effect=RuntimeError('TEST_PREFLIGHT_FAILURE'))
        with self.assertRaisesRegex(RuntimeError, 'TEST_PREFLIGHT_FAILURE'):
            replace_proxy.replace(panel, proxy(), common.revision(panel.value), probe_fn=probe, verify_fn=no_verify, backup_fn=backup)
        backup.assert_not_called()
        self.assertEqual(panel.writes, [])
        self.assertEqual(panel.restarts, 0)

    def test_promised_exit_mismatch_never_writes(self):
        panel = FakePanel(old_config())
        backup = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'requested IP'):
            replace_proxy.replace(panel, proxy(), common.revision(panel.value), expected_exit='192.0.2.99',
                                  probe_fn=lambda _: '192.0.2.10', verify_fn=no_verify, backup_fn=backup)
        backup.assert_not_called()
        self.assertEqual(panel.writes, [])

    @patch('proxyctl.time.sleep')
    def test_success_orders_preflight_backup_apply_and_verification(self, _sleep):
        old = old_config()
        panel = FakePanel(old)
        events = []
        def probe(outbound):
            events.append('preflight')
            self.assertEqual(panel.writes, [])
            self.assertEqual(outbound['settings']['servers'][0]['address'], proxy()['host'])
            return '192.0.2.10'
        def backup(value):
            events.append('backup')
            self.assertEqual(value, old)
            self.assertEqual(panel.writes, [])
            return 'fixture-backup'
        def verify(value, exit_ip):
            events.append('verify')
            self.assertEqual(value, panel.value)
            self.assertEqual(exit_ip, '192.0.2.10')
            return 'fixture verified'
        result = replace_proxy.replace(panel, proxy(), common.revision(old), probe_fn=probe, verify_fn=verify, backup_fn=backup)
        self.assertEqual(events, ['preflight', 'backup', 'verify'])
        self.assertEqual(panel.value['routing'], old['routing'])
        self.assertTrue(result['routes_preserved'])
        self.assertEqual(result['preflight_exit_ip'], '192.0.2.10')
        self.assertNotIn(proxy()['password'], json.dumps(result))
        self.assertNotIn(proxy()['username'], json.dumps(result))

    @patch('proxyctl.time.sleep')
    def test_failed_verification_restores_credentials_without_comparing_old_to_new_ip(self, _sleep):
        old = old_config()
        panel = FakePanel(old)
        verified_exits = []
        def verify(value, exit_ip):
            verified_exits.append(exit_ip)
            if value != old:
                raise RuntimeError('TEST_NODE_EXIT_MISMATCH')
            return 'restored'
        with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
            replace_proxy.replace(panel, proxy(), common.revision(old), probe_fn=lambda _: '192.0.2.10',
                                  verify_fn=verify, backup_fn=lambda _: 'fixture-backup')
        self.assertEqual(verified_exits, ['192.0.2.10', None])
        self.assertEqual(panel.value, old)
        self.assertEqual(panel.restarts, 2)

    @patch('proxyctl.time.sleep')
    def test_failed_restart_restores_old_connection(self, _sleep):
        panel = FakePanel(old_config())
        old = copy.deepcopy(panel.value)
        panel.fail_restart = True
        with self.assertRaisesRegex(RuntimeError, 'restored and verified'):
            replace_proxy.replace(panel, proxy(), common.revision(old), probe_fn=lambda _: '192.0.2.10',
                                  verify_fn=no_verify, backup_fn=lambda _: 'fixture-backup')
        self.assertEqual(panel.value, old)

    def test_stale_revision_stops_before_preflight(self):
        panel = FakePanel(old_config())
        probe = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'Configuration changed'):
            replace_proxy.replace(panel, proxy(), 'STALE_REVISION', probe_fn=probe)
        probe.assert_not_called()
        self.assertEqual(panel.writes, [])

    def test_backend_exit_change_needs_no_configuration_write(self):
        old = old_config()
        server = old['outbounds'][1]['settings']['servers'][0]
        same = {'host': server['address'], 'port': server['port'], 'username': server['users'][0]['user'], 'password': server['users'][0]['pass']}
        panel = FakePanel(old)
        backup = MagicMock()
        verify = MagicMock(return_value='fixture verified')
        result = replace_proxy.replace(panel, same, common.revision(old), probe_fn=lambda _: '192.0.2.20', verify_fn=verify, backup_fn=backup)
        self.assertFalse(result['changed'])
        self.assertEqual(panel.writes, [])
        self.assertEqual(panel.restarts, 0)
        backup.assert_not_called()
        verify.assert_called_once_with(old, '192.0.2.20')

    def test_concurrent_edit_during_probe_is_not_overwritten(self):
        panel = FakePanel(old_config())
        old = copy.deepcopy(panel.value)
        def probe(_):
            panel.value['dns'] = {'servers': ['changed.example']}
            return '192.0.2.10'
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            replace_proxy.replace(panel, proxy(), common.revision(old), probe_fn=probe, verify_fn=no_verify, backup_fn=lambda _: 'backup')
        self.assertEqual(panel.writes, [])
        self.assertEqual(panel.value['dns']['servers'], ['changed.example'])

    def test_host_and_port_validation(self):
        value = proxy()
        value['host'] = '2001:db8::10'
        self.assertEqual(replace_proxy.validate_proxy(value), value)
        for host in ['socks5://proxy.example.com', 'proxy.example.com/path', '--option', 'bad..example.com']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                replace_proxy.validate_proxy(dict(value, host=host))
        for port in [True, 0, 65536, '1080']:
            with self.subTest(port=port), self.assertRaises(ValueError):
                replace_proxy.validate_proxy(dict(value, port=port))

    @patch.object(replace_proxy, 'verify_service')
    @patch.object(replace_proxy.subprocess, 'run')
    def test_runtime_and_node_exit_verification_and_no_rule_insertion(self, run, service):
        old = old_config()
        new = replace_proxy.replace_connection(old, proxy())
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / 'runtime.json'
            runtime.write_text(json.dumps(new), encoding='utf-8')
            run.return_value = subprocess.CompletedProcess([], 0, SELFTEST_BOTH + '== 走节点访问 ipinfo.io 的出口 IP：192.0.2.10 ==\n', '')
            scope = replace_proxy.verify_replacement(new, '192.0.2.10', runtime)
            self.assertIn('reality-residential-exit', scope)
            self.assertEqual(run.call_args.args[0], ['node-selftest', '--ip'])
            run.return_value.stdout = SELFTEST_BOTH + '== 走节点访问 ipinfo.io 的出口 IP：192.0.2.99 ==\n'
            with self.assertRaisesRegex(RuntimeError, 'did not match'):
                replace_proxy.verify_replacement(new, '192.0.2.10', runtime)
            new['routing']['rules'][1]['domain'] = ['geosite:anthropic']
            runtime.write_text(json.dumps(new), encoding='utf-8')
            scope = replace_proxy.verify_replacement(new, '192.0.2.10', runtime)
            self.assertIn('no routed exit comparison', scope)
            self.assertEqual(run.call_args.args[0], ['node-selftest'])
            self.assertEqual(new['routing']['rules'][1]['domain'], ['geosite:anthropic'])

    def test_exit_comparison_respects_ordering_and_additional_conditions(self):
        original = old_config()
        self.assertTrue(replace_proxy.routes_ipinfo(original))
        blocked = copy.deepcopy(original)
        blocked['routing']['rules'].insert(1, {'type': 'field', 'domain': ['full:ipinfo.io'], 'outboundTag': 'direct'})
        self.assertFalse(replace_proxy.routes_ipinfo(blocked))
        for condition in [{'inboundTag': ['only-other-node']}, {'sourceIP': ['192.0.2.1']}, {'port': '80'}]:
            limited = copy.deepcopy(original)
            limited['routing']['rules'][1].update(condition)
            self.assertFalse(replace_proxy.routes_ipinfo(limited))
        opaque = copy.deepcopy(original)
        opaque['routing']['rules'].insert(1, {'type': 'field', 'domain': ['geosite:unknown'], 'outboundTag': 'direct'})
        self.assertFalse(replace_proxy.routes_ipinfo(opaque))
        unrelated = copy.deepcopy(original)
        unrelated['routing']['rules'].insert(1, {'type': 'field', 'domain': ['domain:other.example'], 'outboundTag': 'direct'})
        self.assertTrue(replace_proxy.routes_ipinfo(unrelated))

    @patch.object(replace_proxy, 'verify_service')
    @patch.object(replace_proxy.subprocess, 'run')
    def test_reality_only_success_is_not_reported_as_both_nodes(self, run, service):
        current = old_config()
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / 'runtime.json'
            runtime.write_text(json.dumps(current), encoding='utf-8')
            run.return_value = subprocess.CompletedProcess([], 0, '== 节点自测 Reality：成功 ==\n', '')
            with self.assertRaisesRegex(RuntimeError, 'Both Reality and Hysteria2'):
                replace_proxy.verify_replacement(current, None, runtime)

    @patch.object(replace_proxy.Path, 'glob', return_value=[Path('fixture-xray')])
    @patch.object(replace_proxy.socket, 'create_connection')
    @patch.object(replace_proxy.subprocess, 'Popen')
    @patch.object(replace_proxy.subprocess, 'run')
    def test_probe_keeps_credentials_out_of_argv_and_cleans_up(self, run, popen, connection, glob):
        process = popen.return_value
        process.poll.return_value = None
        run.return_value = subprocess.CompletedProcess([], 0, '192.0.2.10', '')
        paths = []
        def write(path, content):
            paths.append(path)
            path.write_text(content, encoding='utf-8')
            self.assertEqual(json.loads(content)['inbounds'][0]['listen'], '127.0.0.1')
        outbound = replace_proxy.residential(replace_proxy.replace_connection(old_config(), proxy()))[1]
        with patch.object(replace_proxy, 'write_private', side_effect=write):
            self.assertEqual(replace_proxy.probe(outbound), '192.0.2.10')
        process.terminate.assert_called_once()
        self.assertTrue(all(not path.exists() for path in paths))
        for command in [run.call_args.args[0], popen.call_args.args[0]]:
            self.assertNotIn(proxy()['password'], str(command))
            self.assertNotIn(proxy()['username'], str(command))

    @patch.object(replace_proxy.Path, 'glob', return_value=[Path('fixture-xray')])
    @patch.object(replace_proxy.socket, 'create_connection')
    @patch.object(replace_proxy.subprocess, 'Popen')
    @patch.object(replace_proxy.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl', 25))
    def test_probe_timeout_still_cleans_up(self, run, popen, connection, glob):
        process = popen.return_value
        process.poll.return_value = None
        paths = []
        def write(path, content):
            paths.append(path)
            path.write_text(content, encoding='utf-8')
        outbound = replace_proxy.residential(old_config())[1]
        with patch.object(replace_proxy, 'write_private', side_effect=write), self.assertRaises(subprocess.TimeoutExpired):
            replace_proxy.probe(outbound)
        process.terminate.assert_called_once()
        self.assertTrue(all(not path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main()
