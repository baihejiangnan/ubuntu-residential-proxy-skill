#!/usr/bin/env bash
set -euo pipefail
umask 077
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo/root'; exit 1; fi
if [ -e /etc/x-ui ] || [ -e /etc/reality-setup ]; then echo 'Existing deployment: use maintenance, not reinstall'; exit 1; fi
if [ "$#" -ne 2 ] || [ "$1" != --input ]; then echo 'Usage: bash install.sh --input /root/proxyops-input.json'; exit 2; fi
input_file=$(realpath -- "$2")
stage=$(mktemp -d /root/proxyops-bundle.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
curl -fL --retry 2 --max-time 120 'https://github.com/baihejiangnan/ubuntu-residential-proxy-skill/releases/download/v0.1.0/ubuntu-residential-proxy-v0.1.0.tar.gz' -o "$stage/bundle.tar.gz"
printf '%s  %s\n' 'd3ec5af129ca2e2046bca8f73d47c490537614504b8d1651b016d22c22e82b76' "$stage/bundle.tar.gz" | sha256sum -c -
tar -xzf "$stage/bundle.tar.gz" -C "$stage" --no-same-owner --no-same-permissions
python3 "$stage/ubuntu-residential-proxy/scripts/install.py" --input "$input_file"
