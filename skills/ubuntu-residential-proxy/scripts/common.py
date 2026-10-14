"""Shared standard-library helpers; never print credentials."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.parse
import urllib.request


@contextmanager
def maintenance_lock():
    """Serialize this skill's route and endpoint changes on the server."""
    import fcntl
    with open('/run/lock/ubuntu-residential-proxy.maintenance.lock', 'a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another maintenance change is running; retry after it completes') from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def protect(path, directory=False):
    path = Path(path)
    if os.name == 'nt':
        user = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', user + ':F'],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        path.chmod(0o700 if directory else 0o600)


def write_private(path, content):
    path = Path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        protect(path)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            fd = None
            stream.write(content)
    finally:
        if fd is not None:
            os.close(fd)


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_assignments(path):
    """Parse installer shell assignments without executing code."""
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        tokens = shlex.split(value, comments=True)
        values[key.strip()] = tokens[0] if tokens else ''
    return values


class Panel:
    def __init__(self, base, token):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Panel API must use server loopback')
        self.base, self.token = base.rstrip('/'), token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def local(cls):
        values = read_assignments('/etc/x-ui/install-result.env')
        port = int(values.get('XUI_PANEL_PORT', '8080'))
        base = 'http://127.0.0.1:' + str(port) + '/' + values['XUI_WEB_BASE_PATH'].strip('/')
        return cls(base, values['XUI_API_TOKEN'])

    def call(self, path, form=None):
        data = urllib.parse.urlencode(form or {}).encode()
        request = urllib.request.Request(self.base + path, data=data, method='POST',
                                         headers={'Authorization': 'Bearer ' + self.token})
        with self.opener.open(request, timeout=60) as response:
            result = json.load(response)
        if not result.get('success'):
            raise RuntimeError('Panel operation failed: ' + path)
        return result.get('obj')

    def config(self):
        obj = self.call('/panel/api/xray/')
        obj = json.loads(obj) if isinstance(obj, str) else obj
        value = obj['xraySetting']
        return json.loads(value) if isinstance(value, str) else value

    def save(self, config):
        self.call('/panel/api/xray/update', {'xraySetting': json.dumps(config)})

    def restart(self):
        self.call('/panel/api/server/restartXrayService')
