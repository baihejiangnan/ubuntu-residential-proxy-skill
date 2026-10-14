"""Native OpenSSH bootstrap. Only .pub files are read by Python."""
import argparse
import base64
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from common import protect


def validate_target(host, user, port):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
            raise ValueError('Invalid SSH hostname')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user) or not 1 <= port <= 65535:
        raise ValueError('Invalid SSH user/port')


def options(args):
    return ['ssh', '-F', 'none', '-p', str(args.port), '-i', str(Path(args.key).expanduser()),
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'UserKnownHostsFile=' + str(Path(args.known_hosts).expanduser())]


def trust_host(args):
    path = Path(args.known_hosts).expanduser()
    lookup = args.host if args.port == 22 else '[' + args.host + ']:' + str(args.port)
    found = subprocess.run(['ssh-keygen', '-F', lookup, '-f', str(path)], capture_output=True)
    if found.returncode == 0:
        return
    if not sys.stdin.isatty():
        raise RuntimeError('Unknown host: use the user interactive bootstrap terminal')
    scanned = subprocess.run(['ssh-keyscan', '-T', '10', '-p', str(args.port), '-t', 'ed25519', args.host],
                             capture_output=True, text=True, check=True)
    lines = [s for s in scanned.stdout.splitlines() if s and not s.startswith('#')]
    if not lines:
        raise RuntimeError('No host key returned')
    fingerprints = subprocess.run(['ssh-keygen', '-lf', '-'], input='\n'.join(lines) + '\n',
                                   capture_output=True, text=True, check=True).stdout
    print('Candidate host fingerprint (compare with the cloud console):\n' + fingerprints)
    if args.host_fingerprint and args.host_fingerprint not in fingerprints.split():
        raise RuntimeError('Host fingerprint does not match the trusted fingerprint')
    if not args.host_fingerprint and input('After checking the fingerprint, type yes to trust: ') != 'yes':
        raise RuntimeError('Host trust not approved')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write('\n' + '\n'.join(lines) + '\n')
    protect(path)


def public_key_script(public):
    algorithm = r'(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)'
    if not re.fullmatch(algorithm + r' [A-Za-z0-9+/=]+(?: [^\r\n]*)?', public):
        raise ValueError('Expected one supported OpenSSH public key')
    return '''set -eu
umask 077
test ! -L "$HOME/.ssh"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
file="$HOME/.ssh/authorized_keys"
test ! -L "$file"
if [ -e "$file" ]; then test -f "$file"; cp -p "$file" "$file.backup.$(date +%Y%m%d%H%M%S).$$"; fi
touch "$file"
chmod 600 "$file"
key=''' + shlex.quote(public) + '''
if ! grep -qxF "$key" "$file"; then printf '\\n%s\\n' "$key" >> "$file"; fi
printf 'PUBLIC_KEY_INSTALLED\\n'
'''


def verify(args):
    result = subprocess.run(options(args) + ['-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
                                             '-o', 'IdentitiesOnly=yes', args.user + '@' + args.host,
                                             'id -un; uname -sr; if [ "$(id -u)" -ne 0 ]; then sudo -n true; fi'],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Key-only login or sudo verification failed; an encrypted key may need ssh-add in the user terminal.')
    print('KEY_LOGIN_VERIFIED\n' + result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description='Install a public key using native user-facing SSH prompts')
    parser.add_argument('action', choices=['bootstrap', 'verify'])
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', default='ubuntu')
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--key', required=True)
    parser.add_argument('--known-hosts', default=str(Path.home() / '.ssh' / 'known_hosts'))
    parser.add_argument('--host-fingerprint')
    parser.add_argument('--no-passphrase', action='store_true', help='Only when the user chose a dedicated unattended key')
    parser.add_argument('--status-file', help='Optional non-secret status JSON outside the repository')
    args = parser.parse_args()
    validate_target(args.host, args.user, args.port)
    try:
        if args.action == 'bootstrap':
            if not sys.stdin.isatty():
                raise RuntimeError('Bootstrap must run in the user interactive terminal')
            key = Path(args.key).expanduser()
            if not key.exists():
                key.parent.mkdir(parents=True, exist_ok=True)
                cmd = ['ssh-keygen', '-t', 'ed25519', '-f', str(key), '-C', 'ubuntu-proxy-ops']
                if args.no_passphrase:
                    cmd += ['-N', '']
                subprocess.run(cmd, check=True)
                protect(key)
            public = Path(str(key) + '.pub').read_text(encoding='utf-8').strip()
            trust_host(args)
            payload = base64.b64encode(public_key_script(public).encode()).decode()
            remote = 'printf %s ' + shlex.quote(payload) + ' | base64 -d | sh'
            subprocess.run(options(args) + ['-o', 'NumberOfPasswordPrompts=1', args.user + '@' + args.host, remote], check=True)
        verify(args)
        if args.status_file:
            Path(args.status_file).write_text(json.dumps({'status': 'complete', 'key_login': True}), encoding='utf-8')
    except Exception as exc:
        if args.status_file:
            Path(args.status_file).write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
