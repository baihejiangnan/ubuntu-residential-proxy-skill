"""Preflight and replace one residential SOCKS endpoint, preserving all routing."""
import argparse
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from common import Panel, maintenance_lock, revision, write_private
from proxyctl import apply_change, backup, verify_service


def validate_proxy(value):
    if not isinstance(value, dict):
        raise ValueError('Expected a proxy object')
    for key in ['host', 'username', 'password']:
        if not isinstance(value.get(key), str) or not value[key] or any(c in value[key] for c in ['\x00', '\r', '\n']):
            raise ValueError('Invalid proxy ' + key)
    port = value.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid proxy port')
    host = value['host']
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                                  for label in host.rstrip('.').split('.')):
            raise ValueError('Proxy host must be an IP or DNS hostname, without scheme or path')
    return {key: value[key] for key in ['host', 'port', 'username', 'password']}


def residential(config):
    matches = [(index, outbound) for index, outbound in enumerate(config.get('outbounds', []))
               if outbound.get('tag') == 'residential']
    if len(matches) != 1 or matches[0][1].get('protocol') != 'socks':
        raise ValueError('Expected exactly one SOCKS outbound tagged residential')
    index, outbound = matches[0]
    servers = outbound.get('settings', {}).get('servers', [])
    if len(servers) != 1 or len(servers[0].get('users', [])) > 1:
        raise ValueError('Multiple/missing servers or multiple users require explicit endpoint selection; refusing to guess')
    return index, outbound


def replace_connection(config, proxy):
    proxy = validate_proxy(proxy)
    index, _ = residential(config)
    new = copy.deepcopy(config)
    server = new['outbounds'][index]['settings']['servers'][0]
    server['address'], server['port'] = proxy['host'], proxy['port']
    users = server.setdefault('users', [])
    if not users:
        users.append({})
    users[0]['user'], users[0]['pass'] = proxy['username'], proxy['password']
    return new


def protected_input(path):
    path = Path(path)
    stat = path.stat()
    if path.is_symlink() or not path.is_file() or stat.st_uid != 0 or stat.st_mode & 0o077:
        raise ValueError('Credential input must be a root-owned regular file with mode 600')
    value = json.loads(path.read_text(encoding='utf-8'))
    return validate_proxy(value['proxy'])


def probe(outbound):
    """Use a temporary loopback-only Xray client; no password in process args."""
    binaries = sorted(Path('/usr/local/x-ui/bin').glob('xray-linux-*'))
    if len(binaries) != 1:
        raise RuntimeError('Cannot select the installed Xray binary')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    client = {'log': {'loglevel': 'none'},
              'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks'}],
              'outbounds': [outbound]}
    with tempfile.TemporaryDirectory(prefix='residential-preflight-') as directory:
        config_path = Path(directory) / 'client.json'
        write_private(config_path, json.dumps(client))
        process = subprocess.Popen([str(binaries[0]), 'run', '-c', str(config_path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Temporary proxy test client failed to start')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Temporary proxy test client did not become ready') from None
                    time.sleep(0.1)
            result = subprocess.run(['curl', '-q', '--silent', '--show-error', '--fail', '--max-time', '20',
                                     '--noproxy', '', '--proxy', 'socks5h://127.0.0.1:' + str(port),
                                     'https://ipinfo.io/ip'], capture_output=True, text=True, timeout=25)
            if result.returncode:
                raise RuntimeError('New proxy connection/authentication/exit test failed; current configuration unchanged')
            try:
                return str(ipaddress.ip_address(result.stdout.strip()))
            except ValueError:
                raise RuntimeError('Proxy test returned no valid exit IP') from None
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def routes_ipinfo(config):
    # Be conservative about first-match ordering, opaque sets and extra conditions.
    def domain_match(entries):
        if not isinstance(entries, list) or not entries:
            return None
        opaque = False
        for entry in entries:
            if not isinstance(entry, str):
                opaque = True
            elif entry.startswith('full:'):
                if entry[5:] == 'ipinfo.io':
                    return True
            elif entry.startswith('domain:'):
                if entry[7:] and ('ipinfo.io' == entry[7:] or 'ipinfo.io'.endswith('.' + entry[7:])):
                    return True
            elif entry.startswith('keyword:'):
                if entry[8:] in 'ipinfo.io':
                    return True
            elif ':' in entry:
                opaque = True
            elif entry in 'ipinfo.io':
                return True
        return None if opaque else False
    for rule in config.get('routing', {}).get('rules', []):
        if rule.get('enabled') is False:
            continue
        if rule.get('outboundTag') == 'api' and rule.get('inboundTag') == ['api']:
            continue
        matches = domain_match(rule.get('domain'))
        if matches is False:
            continue
        permitted = {'type', 'enabled', 'comment', 'ruleTag', 'domain', 'outboundTag'}
        if matches is None or set(rule) - permitted or rule.get('type', 'field') != 'field':
            return False
        return rule.get('outboundTag') == 'residential'
    return False


def verify_replacement(config, expected_exit=None, runtime_path=Path('/usr/local/x-ui/bin/config.json')):
    verify_service(config, runtime_path, selftest=False)
    actual = json.loads(Path(runtime_path).read_text(encoding='utf-8'))
    _, expected = residential(config)
    _, active = residential(actual)
    if active.get('settings') != expected.get('settings'):
        raise RuntimeError('Runtime residential connection differs from saved connection')
    compare_exit = expected_exit is not None and routes_ipinfo(config)
    command = ['node-selftest'] + (['--ip'] if compare_exit else [])
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Existing node connectivity test failed')
    succeeded = set(re.findall(r'^==\s*节点自测\s+(Reality|Hysteria2)\s*[:：]\s*成功\s*==\s*$', result.stdout, re.MULTILINE))
    if succeeded != {'Reality', 'Hysteria2'}:
        raise RuntimeError('Both Reality and Hysteria2 must report successful self-tests')
    if compare_exit:
        ip_line = next((line for line in result.stdout.splitlines() if 'ipinfo.io' in line), '')
        if expected_exit not in re.findall(r'[0-9a-fA-F:.]+', ip_line):
            raise RuntimeError('Node exit did not match the new proxy preflight exit')
        return 'runtime+both-node-selftests+reality-residential-exit'
    return 'runtime+both-node-selftests; no routed exit comparison'


def replace(panel, proxy, expect_revision, expected_exit=None, probe_fn=probe,
            verify_fn=verify_replacement, backup_fn=backup):
    old = panel.config()
    if revision(old) != expect_revision:
        raise RuntimeError('Configuration changed; read status again before replacing')
    new = replace_connection(old, proxy)
    # Preflight must finish before any backup/apply/restart operation.
    _, candidate = residential(new)
    tested_exit = probe_fn(candidate)
    tested_exit = str(ipaddress.ip_address(tested_exit))
    if expected_exit is not None and tested_exit != str(ipaddress.ip_address(expected_exit)):
        raise RuntimeError('New proxy exit differs from the requested IP; current configuration unchanged')
    if new.get('routing') != old.get('routing'):
        raise RuntimeError('Replacement would change routing; refusing to apply')
    verified = {}
    def validate(config):
        scope = verify_fn(config, tested_exit if config == new else None)
        if config == new:
            verified['scope'] = scope
    if new == old:
        if panel.config() != old:
            raise RuntimeError('Configuration changed during preflight; retry from a fresh status')
        validate(old)
        result = {'changed': False, 'revision': revision(old)}
    else:
        result = apply_change(panel, old, new, verify_fn=validate, backup_fn=backup_fn)
    result.update({'residential_tag': 'residential', 'routes_preserved': True,
                   'preflight_exit_ip': tested_exit, 'verified': verified['scope']})
    return result


def main():
    parser = argparse.ArgumentParser(description='Test/replace residential proxy credentials while retaining all routing and node identities')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status', help='Read non-secret config revision without listing domains')
    for action in ['test', 'replace']:
        command = sub.add_parser(action)
        command.add_argument('--input', required=True, help='Protected JSON credential file, not inline passwords')
        command.add_argument('--expected-exit-ip', help='Optional provider-promised static exit IP')
        if action == 'replace':
            command.add_argument('--expect-revision', required=True)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.error('Run on the server with sudo python3')
    panel = Panel.local()
    if args.action == 'status':
        old = panel.config()
        residential(old)
        print(json.dumps({'revision': revision(old), 'residential_tag': 'residential'}))
        return
    proxy = protected_input(args.input)
    if args.action == 'test':
        candidate = replace_connection(panel.config(), proxy)
        tested = probe(residential(candidate)[1])
        if args.expected_exit_ip and tested != str(ipaddress.ip_address(args.expected_exit_ip)):
            raise RuntimeError('Tested proxy exit differs from the requested IP')
        result = {'preflight_exit_ip': tested, 'changed': False}
    else:
        with maintenance_lock():
            result = replace(panel, proxy, args.expect_revision, args.expected_exit_ip)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error)) from None
