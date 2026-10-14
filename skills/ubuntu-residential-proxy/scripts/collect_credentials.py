"""Run in the user's interactive terminal, not an agent-captured prompt."""
import argparse
import getpass
import ipaddress
import json
from pathlib import Path
import sys
from common import write_private


def main():
    parser = argparse.ArgumentParser(description='Collect residential SOCKS5 credentials without echoing passwords')
    parser.add_argument('--server-ip', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    ipaddress.IPv4Address(args.server_ip)
    if not sys.stdin.isatty():
        parser.error('Open this collector in the user interactive terminal')
    host = input('Residential SOCKS5 hostname/IP: ').strip()
    port = int(input('Residential SOCKS5 port: '))
    username = input('Residential SOCKS5 username: ')
    password = getpass.getpass('Residential SOCKS5 password (hidden): ')
    if not all([host, username, password]) or any('\n' in s or '\r' in s for s in [host, username, password]) or not 1 <= port <= 65535:
        raise ValueError('Invalid proxy input')
    config = {'server_ip': args.server_ip, 'proxy': {'host': host, 'port': port, 'username': username, 'password': password}}
    write_private(Path(args.output), json.dumps(config, ensure_ascii=False))
    print('Credentials saved to a protected local file. No password was printed.')


if __name__ == '__main__':
    main()
