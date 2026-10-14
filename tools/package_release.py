"""Build deterministic skill/runtime archives and a checksum-pinned server launcher."""
import gzip
import hashlib
import io
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v0.2.0'
NAME = 'ubuntu-residential-proxy'
SKILL = ROOT / 'skills' / NAME


def files():
    return sorted(p for p in SKILL.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))


def main():
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / (NAME + '-' + VERSION + '.tar.gz')
    with archive.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as tar:
                for path in files():
                    info = tar.gettarinfo(str(path), arcname=NAME + '/' + path.relative_to(SKILL).as_posix())
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = 'root'
                    info.mode = 0o644
                    data = path.read_bytes().replace(b'\r\n', b'\n')
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
    zipped = output / (NAME + '-' + VERSION + '.zip')
    with zipfile.ZipFile(zipped, 'w', compression=zipfile.ZIP_DEFLATED) as zipout:
        for path in files():
            info = zipfile.ZipInfo(NAME + '/' + path.relative_to(SKILL).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipout.writestr(info, path.read_bytes().replace(b'\r\n', b'\n'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    launcher = '''#!/usr/bin/env bash
set -euo pipefail
umask 077
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo/root'; exit 1; fi
if [ -e /etc/x-ui ] || [ -e /etc/reality-setup ]; then echo 'Existing deployment: use maintenance, not reinstall'; exit 1; fi
if [ "$#" -ne 2 ] || [ "$1" != --input ]; then echo 'Usage: bash install.sh --input /root/proxyops-input.json'; exit 2; fi
input_file=$(realpath -- "$2")
stage=$(mktemp -d /root/proxyops-bundle.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
curl -fL --retry 2 --max-time 120 'https://github.com/baihejiangnan/ubuntu-residential-proxy-skill/releases/download/VERSION/ARCHIVE' -o "$stage/bundle.tar.gz"
printf '%s  %s\\n' 'DIGEST' "$stage/bundle.tar.gz" | sha256sum -c -
tar -xzf "$stage/bundle.tar.gz" -C "$stage" --no-same-owner --no-same-permissions
python3 "$stage/ubuntu-residential-proxy/scripts/install.py" --input "$input_file"
'''.replace('VERSION', VERSION).replace('ARCHIVE', archive.name).replace('DIGEST', digest)
    (ROOT / 'install.sh').write_text(launcher, encoding='utf-8', newline='\n')
    checksums = '\n'.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name for p in [archive, zipped]) + '\n'
    (output / 'SHA256SUMS').write_text(checksums, encoding='utf-8', newline='\n')
    print(checksums, end='')


if __name__ == '__main__':
    main()
