"""Fresh Ubuntu deployment adapter for the pinned, unmodified upstream asset."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import re
from common import Panel, protect, write_private
from export_client import validate_export_values

SKILL = Path(__file__).resolve().parents[1]


def validate_input(value):
    ipaddress.IPv4Address(value['server_ip'])
    proxy = value['proxy']
    if not isinstance(proxy['port'], int) or isinstance(proxy['port'], bool) or not 1 <= proxy['port'] <= 65535:
        raise ValueError('Invalid residential SOCKS5 port')
    for key in ['host', 'username', 'password']:
        if not isinstance(proxy[key], str) or not proxy[key] or any(c in proxy[key] for c in ['\n', '\r', '\x00']):
            raise ValueError('Invalid residential SOCKS5 ' + key)
    return value


def preflight(etc=Path('/etc')):
    etc = Path(etc)
    if (etc / 'x-ui').exists() or (etc / 'reality-setup').exists():
        raise RuntimeError('Existing deployment detected; use maintenance instead of reinstalling')
    values = {}
    for line in (etc / 'os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"')
    if values.get('ID') != 'ubuntu':
        raise RuntimeError('This adapter supports Ubuntu only')
    busy = subprocess.run(['ss', '-H', '-lntup'], capture_output=True, text=True, check=True).stdout
    for line in busy.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4].endswith(':443'):
            raise RuntimeError('TCP/UDP 443 already in use; inspect the existing service')


def render(upstream, panel_password):
    """Exact-match adaptation; upstream bytes remain preserved in vendor/."""
    start = upstream.index('# ---- 装过的服务器')
    stop = upstream.index('DETECTED=', start)
    upstream = upstream[:start] + 'if [ -e /etc/x-ui ]; then echo "Existing panel; refusing reinstall"; exit 1; fi\nREINSTALL=\n\n' + upstream[stop:]
    start = upstream.index('if [ -n "$REINSTALL" ]; then')
    stop = upstream.index('apt update', start)
    upstream = upstream[:start] + upstream[stop:]
    upstream = upstream.replace('read -rp ', 'IFS= read -r -p ')
    replacements = {
        'PANEL_PATH=panel PANEL_USER=admin PANEL_PASS=123456': 'PANEL_PATH=panel PANEL_USER=admin PANEL_PASS=' + panel_password,
        '    xray = json.loads(api("/panel/api/xray/"))["xraySetting"]':
        '    saved = api("/panel/api/xray/")\n    saved = json.loads(saved) if isinstance(saved, str) else saved\n    xray = saved["xraySetting"]\n    xray = json.loads(xray) if isinstance(xray, str) else xray',
    }
    for old, new in replacements.items():
        if upstream.count(old) != 1:
            raise RuntimeError('Upstream adaptation anchor changed; refusing to guess')
        upstream = upstream.replace(old, new, 1)
    return upstream


def verify_deployment(panel, root=Path('/root'), runtime_path=Path('/usr/local/x-ui/bin/config.json')):
    values = {}
    for filename in ['vless.txt', 'hy2.txt', 'clash.yaml']:
        path = Path(root) / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError('Missing required client export: ' + filename)
        protect(path)
        values[filename] = path.read_text(encoding='utf-8')
    validate_export_values(values)
    runtime = json.loads(Path(runtime_path).read_text())
    protocols = {entry.get('protocol') for entry in runtime.get('inbounds', [])}
    if not {'vless', 'hysteria'} <= protocols:
        raise RuntimeError('Both Reality and Hysteria2 must exist')
    residential = next((o for o in panel.config().get('outbounds', []) if o.get('tag') == 'residential' and o.get('protocol') == 'socks'), None)
    if residential is None:
        raise RuntimeError('Residential SOCKS outbound missing')
    result = subprocess.run(['node-selftest', '--ip'], capture_output=True, text=True, timeout=90)
    if result.returncode or '取不到' in result.stdout or '失败' in result.stdout:
        raise RuntimeError('Both node self-tests and residential exit check must pass')
    server = residential['settings']['servers'][0]
    user = server['users'][0]
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    curl_config = '\n'.join([
        'url = "https://ipinfo.io/ip"',
        'proxy = ' + quote('socks5h://' + server['address'] + ':' + str(server['port'])),
        'proxy-user = ' + quote(user['user'] + ':' + user['pass']),
        'silent', 'show-error', 'fail', 'max-time = 20',
    ]) + '\n'
    direct = subprocess.run(['curl', '-q', '--config', '-'], input=curl_config, capture_output=True, text=True, timeout=25)
    if direct.returncode:
        raise RuntimeError('Residential exit comparison failed')
    exit_ip = str(ipaddress.ip_address(direct.stdout.strip()))
    ip_line = next((line for line in result.stdout.splitlines() if 'ipinfo.io' in line), '')
    if exit_ip not in re.findall(r'[0-9a-fA-F:.]+', ip_line):
        raise RuntimeError('Node exit differs from the residential SOCKS exit')
    print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description='Deploy both nodes on a fresh Ubuntu server; never reinstall')
    parser.add_argument('--input', required=True, help='Root-owned mode-600 JSON input; password is never an argument')
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.error('Run on the server as root or with sudo')
    import fcntl
    lock = open('/run/lock/ubuntu-residential-proxy.install.lock', 'w')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another installation is already running')
    source = Path(args.input)
    stat = source.stat()
    if source.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o077:
        parser.error('Input must be a root-owned regular file with mode 600')
    config = validate_input(json.loads(source.read_text()))
    preflight()
    vendor = SKILL / 'vendor' / 'reality-setup' / 'setup.sh'
    manifest = json.loads((SKILL / 'vendor' / 'manifest.json').read_text())
    raw = vendor.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files']['setup.sh']:
        raise RuntimeError('Vendored upstream checksum mismatch')
    generated = render(raw.decode('utf-8'), secrets.token_hex(24))
    state = Path('/var/lib/ubuntu-residential-proxy')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    protect(state, directory=True)
    stamp = str(time.time_ns())
    log = state / ('install-' + stamp + '.log')
    status = state / ('install-' + stamp + '.status.json')
    write_private(log, '')
    write_private(status, json.dumps({'status': 'running', 'log': str(log)}))
    proxy = config['proxy']
    answers = '\n'.join([config['server_ip'], proxy['host'], str(proxy['port']), proxy['username'], proxy['password']]) + '\n'
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='a', APT_LISTCHANGES_FRONTEND='none')
    try:
        with tempfile.TemporaryDirectory(prefix='proxyops-', dir=str(state)) as directory:
            script = Path(directory) / 'setup-agent.sh'
            write_private(script, generated)
            with log.open('a') as stream:
                completed = subprocess.run(['bash', str(script)], input=answers, text=True, stdout=stream, stderr=stream, env=env)
            if completed.returncode:
                raise RuntimeError('Installer exited with code ' + str(completed.returncode))
        verify_deployment(Panel.local())
        status.write_text(json.dumps({'status': 'complete', 'log': str(log), 'exports': ['/root/vless.txt', '/root/hy2.txt', '/root/clash.yaml']}))
        print('DEPLOYMENT_VERIFIED: both nodes. Export client files to finish the user task.')
    except Exception:
        status.write_text(json.dumps({'status': 'failed', 'log': str(log)}))
        print('Deployment incomplete. Inspect protected log locally on the server: ' + str(log))
        raise
    finally:
        source.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
