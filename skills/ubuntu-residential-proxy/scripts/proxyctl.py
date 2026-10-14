"""Server-side residential route management; run with sudo python3."""
import argparse
import copy
from contextlib import nullcontext
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import urllib.parse
from common import Panel, maintenance_lock, protect, revision, write_private


def normalize(value):
    value = value.strip()
    if value.startswith('geosite:'):
        raise ValueError('Pass geosite entries only when deleting an existing exact entry')
    prefix = 'domain:'
    if value.startswith(('domain:', 'full:')):
        prefix, value = value.split(':', 1)
        prefix += ':'
    if '://' in value:
        url = urllib.parse.urlsplit(value)
        if url.scheme not in ('http', 'https') or url.username or url.password:
            raise ValueError('Expected a public http/https website URL')
        value = url.hostname or ''
    elif any(char in value for char in '/:?#@'):
        raise ValueError('Supply a domain or a complete http/https URL')
    value = value.rstrip('.').lower().encode('idna').decode('ascii')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('This command manages domains, not IP rules')
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('Invalid domain')
    return prefix + value


def residential_rules(config):
    return [(index, rule) for index, rule in enumerate(config.get('routing', {}).get('rules', []))
            if rule.get('outboundTag') == 'residential' and isinstance(rule.get('domain'), list)]


def listing(config):
    entries = []
    number = 0
    for index, rule in residential_rules(config):
        for domain in rule['domain']:
            number += 1
            entries.append({'number': number, 'rule_index': index, 'entry': domain,
                            'enabled': rule.get('enabled', True),
                            'kind': 'domain-set' if domain.startswith('geosite:') else 'domain'})
    return {'revision': revision(config), 'entries': entries}


def change(config, action, values, rule_index=None, create_rule=False):
    new = copy.deepcopy(config)
    candidates = residential_rules(new)
    if not candidates:
        if action != 'add' or not create_rule or rule_index is not None:
            raise ValueError('No residential domain rule exists; add --create-rule to create an explicit narrow rule')
        if not any(o.get('tag') == 'residential' for o in new.get('outbounds', [])):
            raise ValueError('Residential outbound is missing')
        domains = list(dict.fromkeys(normalize(value) for value in values))
        if not domains:
            raise ValueError('A new rule must contain explicit domains')
        rules = new.setdefault('routing', {}).setdefault('rules', [])
        index = 0
        while index < len(rules) and rules[index].get('outboundTag') == 'api':
            index += 1
        rules.insert(index, {'type': 'field', 'outboundTag': 'residential', 'domain': domains})
        return new
    if create_rule:
        raise ValueError('A residential domain rule already exists; edit it instead')
    if rule_index is None:
        if len(candidates) != 1:
            raise ValueError('Multiple residential domain rules: select --rule-index from list')
        index, rule = candidates[0]
    else:
        selected = [(i, r) for i, r in candidates if i == rule_index]
        if len(selected) != 1:
            raise ValueError('Selected rule is not a residential domain rule')
        index, rule = selected[0]
    if action == 'add':
        if rule.get('enabled') is False:
            raise ValueError('Selected rule is disabled; inspect before enabling or adding')
        for value in values:
            entry = normalize(value)
            if entry not in rule['domain']:
                rule['domain'].append(entry)
    elif action == 'remove':
        missing = [value for value in values if value not in rule['domain']]
        if missing:
            raise ValueError('Entry no longer exists in the selected rule; list again')
        rule['domain'] = [entry for entry in rule['domain'] if entry not in values]
        if not rule['domain']:
            # An empty domain field can become a catch-all rule. Remove the rule.
            del new['routing']['rules'][index]
    else:
        raise ValueError('Unknown change action')
    return new


def backup(config, root=Path('/var/backups/ubuntu-residential-proxy'), database=Path('/etc/x-ui/x-ui.db')):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect(root, directory=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = root / ('routes-' + stamp)
    directory.mkdir(mode=0o700)
    write_private(directory / 'xray-setting.json', json.dumps(config, ensure_ascii=False, indent=2))
    if Path(database).exists():
        source = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
        dest = sqlite3.connect(str(directory / 'x-ui.db'))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        protect(directory / 'x-ui.db')
    return directory


def verify_service(expected, runtime=Path('/usr/local/x-ui/bin/config.json'), selftest=True):
    subprocess.run(['systemctl', 'is-active', '--quiet', 'x-ui'], check=True)
    actual = json.loads(Path(runtime).read_text())
    def effective(rules):
        return [{k: v for k, v in rule.items() if k not in ('enabled', 'comment')}
                for rule in rules if rule.get('enabled') is not False]
    if effective(actual.get('routing', {}).get('rules', [])) != effective(expected.get('routing', {}).get('rules', [])):
        raise RuntimeError('Runtime routing differs from saved routing')
    if selftest:
        subprocess.run(['node-selftest'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)


def apply_change(panel, old, new, verify_fn=verify_service, backup_fn=backup):
    if old == new:
        return {'changed': False, 'revision': revision(old)}
    saved = backup_fn(old)
    if panel.config() != old:
        raise RuntimeError('Configuration changed concurrently; refusing overwrite')
    try:
        panel.save(new)
        if panel.config() != new:
            raise RuntimeError('Panel did not persist the intended configuration')
        panel.restart()
        time.sleep(2)
        verify_fn(new)
    except BaseException as error:
        try:
            panel.save(old)
            panel.restart()
            if panel.config() != old:
                raise RuntimeError('Restore persistence check failed')
            time.sleep(2)
            verify_fn(old)
        except BaseException:
            raise RuntimeError('Change failed and automatic rollback could not be verified; protected backup: ' + str(saved)) from None
        raise RuntimeError('Change failed; previous configuration restored and verified. Backup: ' + str(saved)) from None
    return {'changed': True, 'revision': revision(new), 'backup': str(saved), 'verified': 'saved+runtime+node-selftest'}


def main():
    parser = argparse.ArgumentParser(description='List, add or remove residential route domains without reinstalling')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    for name in ['add', 'remove']:
        command = sub.add_parser(name)
        command.add_argument('values', nargs='+', help='Add URLs/domains; remove exact entries returned by list')
        command.add_argument('--expect-revision', required=True, help='Revision from a fresh list; prevents stale choices')
        command.add_argument('--rule-index', type=int)
        if name == 'add':
            command.add_argument('--create-rule', action='store_true', help='Create a narrow rule only when no residential domain rule remains')
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.error('Run this server-side tool with sudo python3')
    panel = Panel.local()
    with nullcontext() if args.action == 'list' else maintenance_lock():
        old = panel.config()
        if args.action == 'list':
            print(json.dumps(listing(old), ensure_ascii=False, indent=2))
            return
        if revision(old) != args.expect_revision:
            parser.error('Configuration revision changed; list again before applying the selected edit')
        if not any(o.get('tag') == 'residential' for o in old.get('outbounds', [])):
            parser.error('Missing residential outbound')
        new = change(old, args.action, args.values, args.rule_index, getattr(args, 'create_rule', False))
        print(json.dumps(apply_change(panel, old, new), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
