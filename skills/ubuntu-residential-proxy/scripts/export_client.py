"""Export only client-consumable files, never service private keys or API tokens."""
import argparse
import json
import os
import re
from pathlib import Path
from common import protect, write_private

NAMES = ('vless.txt', 'hy2.txt', 'clash.yaml')


def validate_export_values(values):
    if not values['vless.txt'].startswith('vless://') or not values['hy2.txt'].startswith('hysteria2://'):
        raise RuntimeError('Unexpected node share-link format')
    yaml = values['clash.yaml']
    for section in ['proxies', 'proxy-groups', 'rules']:
        if not re.search(r'^' + section + r':\s*$', yaml, re.MULTILINE):
            raise RuntimeError('Missing or empty Clash section: ' + section)
    for protocol in ['vless', 'hysteria2']:
        if not re.search(r'^\s+type:\s*' + protocol + r'\s*$', yaml, re.MULTILINE):
            raise RuntimeError('Clash configuration is missing node type: ' + protocol)


def export(source, output=None, stdout=False):
    source = Path(source)
    values = {}
    for name in NAMES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Missing client export: ' + name)
        values[name] = path.read_text(encoding='utf-8').strip() + '\n'
    validate_export_values(values)
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        protect(directory, directory=True)
        for name, content in values.items():
            write_private(directory / name, content)
        print(json.dumps({'directory': str(directory), 'files': list(NAMES)}))
    if stdout:
        for name, content in values.items():
            print('\n===== ' + name + ' =====\n' + content)


def main():
    parser = argparse.ArgumentParser(description='Export both share links and the complete Clash YAML for the owner')
    parser.add_argument('--output', help='New protected directory outside the skill/repository')
    parser.add_argument('--stdout', action='store_true', help='Print client credentials, only for the owner-authorized final delivery')
    args = parser.parse_args()
    if not args.output and not args.stdout:
        parser.error('Choose --output or --stdout')
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.error('Run on the server with sudo python3')
    export('/root', args.output, args.stdout)


if __name__ == '__main__':
    main()
