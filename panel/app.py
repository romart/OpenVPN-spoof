#!/usr/bin/env python3
"""OpenVPN control panel. Only listens on a permission-restricted Unix socket."""
import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import socketserver
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

import monitor
from pam_auth import authenticate

LOG = logging.getLogger('openvpn-panel')
NAME = re.compile(r'[a-zA-Z0-9_-]{1,32}\Z')
COOKIE = '__Host-openvpn-panel'
STATIC = Path(__file__).parent / 'static'


def pem(text):
    match = re.search(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', text, re.S)
    return re.sub(r'\s+', '', match[0]) if match else None


def valid_clients(pki):
    clients = {}
    index = pki / 'index.txt'
    if not index.is_file():
        return clients
    for line in index.read_text().splitlines():
        fields = line.split('\t')
        if len(fields) < 6 or fields[0] != 'V':
            continue
        match = re.search(r'(?:^|/)CN=([^/]+)(?:/|$)', fields[5])
        if not match or match[1] == 'server' or not NAME.fullmatch(match[1]):
            continue
        try:
            expiry = datetime.strptime(fields[1], '%y%m%d%H%M%SZ' if len(fields[1]) == 13 else '%Y%m%d%H%M%SZ').replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                continue
            cert = pem((pki / 'issued' / (match[1] + '.crt')).read_text())
            if cert:
                clients[match[1]] = cert
        except (OSError, ValueError):
            continue
    return clients


@dataclass
class Session:
    csrf: str
    created: float
    touched: float
    authenticated: bool = False


class Panel:
    def __init__(self, vpn=Path('/etc/openvpn'), project=Path('/root/antizapret'),
                 state=Path('/var/lib/openvpn-panel/usage.json'), auth=authenticate,
                 fallback=Path('/run/openvpn-server/status-vpn-udp.log')):
        self.vpn, self.project, self.state_path, self.auth = vpn, project, state, auth
        self.status_fallback = fallback
        self.sessions = {}
        self.lock = threading.RLock()
        self.create_lock = threading.Lock()
        self.auth_lock = threading.Lock()
        self.attempts = deque()
        self.states = monitor.load_state(str(state))
        self.online = set()
        self.sampled_at = None
        self.sample_error = None

    def session(self, token):
        now = time.monotonic()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items()
                             if now - v.touched < 1800 and now - v.created < 28800}
            session = self.sessions.get(token)
            if session:
                session.touched = now
            return session

    def new_session(self, authenticated=False):
        self.session('')  # Prune before allocating.
        with self.lock:
            if len(self.sessions) >= 1024:
                raise ValueError('Too many sessions; try again later')
            token = secrets.token_urlsafe(32)
            now = time.monotonic()
            session = Session(secrets.token_urlsafe(32), now, now, authenticated)
            self.sessions[token] = session
            return token, session

    def login(self, username, password):
        # Bound PAM work and password guessing globally, including across IP changes.
        with self.auth_lock:
            now = time.monotonic()
            while self.attempts and now - self.attempts[0] > 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                return None
            self.attempts.append(now)
            return self.auth(username, password)

    def catalog(self):
        identities = valid_clients(self.vpn / 'easyrsa3/pki')
        by_cert = {cert: name for name, cert in identities.items()}
        result = {name: [] for name in identities}
        for path in sorted((self.project / 'client/openvpn/vpn-udp').glob('*.ovpn')):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeError):
                continue
            match = re.search(r'<cert>(.*?)</cert>', text, re.S)
            name = by_cert.get(pem(match[1])) if match else None
            if name:
                result[name].append({'file': path.name, 'variant': 'routed' if path.stem.endswith('-routed') else 'default'})
        return [{'name': name, 'profiles': profiles} for name, profiles in sorted(result.items())]

    def create(self, name, days):
        if not isinstance(name, str) or not NAME.fullmatch(name) or name == 'server':
            raise ValueError('Use 1–32 letters, digits, underscores or dashes; server is reserved.')
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError('Certificate lifetime must be 1–3650 days.')
        if not self.create_lock.acquire(blocking=False):
            raise BlockingIOError('Another client is being created. Try again shortly.')
        try:
            pki = self.vpn / 'easyrsa3/pki'
            required = ('ca.crt', 'private/ca.key', 'issued/server.crt', 'private/server.key', 'index.txt')
            if not all((pki / item).is_file() for item in required):
                raise ValueError('Existing VPN PKI is incomplete. Restore it before creating clients.')
            # The shell script strips vpn- from filenames: prevent filename collisions.
            base = name.removeprefix('vpn-')
            for cert in (pki / 'issued').glob('*.crt'):
                if cert.stem.removeprefix('vpn-') == base:
                    raise FileExistsError('That client name or profile filename already exists.')
            result = subprocess.run(['/bin/bash', str(self.project / 'client.sh'), '1', name, str(days)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=180, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root'})
            if result.returncode:
                LOG.error('Client generation failed for %s: %s', name, result.stdout[-2000:])
                raise RuntimeError('Client generation failed; see the panel service journal.')
            client = next((c for c in self.catalog() if c['name'] == name), None)
            if not client or len(client['profiles']) < 2:
                raise RuntimeError('Client created, but both profiles were not found. Install the updated client.sh and route file.')
            return client
        finally:
            self.create_lock.release()

    def sample(self):
        # Use the exact parser and accounting shared with monitor.py.
        preferred = self.vpn / 'server/logs/vpn-udp-status.log'
        path = preferred if preferred.exists() else self.status_fallback
        try:
            if not path.exists():
                raise FileNotFoundError('OpenVPN status file is not available yet.')
            snapshot = monitor.parse_status(str(path))
            with self.lock:
                monitor.update_states(self.states, snapshot)
                self.online = set(snapshot)
                self.sampled_at = int(time.time())
                self.sample_error = None
                monitor.save_state(str(self.state_path), self.states)
        except (OSError, ValueError) as error:
            with self.lock:
                self.online = set()
                self.sample_error = str(error)
            LOG.warning('Usage sampling failed: %s', error)

    def usage(self):
        # The live status file contains connected clients only. Include every
        # client in the CA index as well as retained monitoring history.
        names = set()
        index = self.vpn / 'easyrsa3/pki/index.txt'
        if index.is_file():
            for line in index.read_text().splitlines():
                fields = line.split('\t')
                match = re.search(r'(?:^|/)CN=([^/]+)(?:/|$)', fields[5]) if len(fields) >= 6 else None
                if match and match[1] != 'server' and NAME.fullmatch(match[1]):
                    names.add(match[1])
        with self.lock:
            states = {cn: self.states.get(cn, monitor.ClientState()) for cn in names | self.states.keys()}
            ordered = sorted(states.items(), key=lambda item: (-(item[1].total_rx() + item[1].total_tx()), item[0]))
            return {'sampled_at': self.sampled_at, 'warning': self.sample_error, 'clients': [
                {'name': cn, 'online': cn in self.online, 'download': st.total_rx(), 'upload': st.total_tx(),
                 'real': st.real if cn in self.online else '', 'virtual': st.virtual if cn in self.online else '',
                 'since': st.since.isoformat(sep=' ') if cn in self.online and st.since else ''}
                for cn, st in ordered]}


class Handler(BaseHTTPRequestHandler):
    server_version = 'OpenVPNPanel'

    def log_message(self, fmt, *args):
        LOG.info('%s %s', self.command, urlsplit(self.path).path)

    @property
    def panel(self):
        return self.server.panel

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception:
            return ''

    def reply(self, status, data=b'', kind='application/json', cookie=None, extra=None):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header('Set-Cookie', f'{COOKIE}={cookie}; Path=/; Secure; HttpOnly; SameSite=Strict' + ('; Max-Age=0' if not cookie else ''))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, ValueError):
            LOG.exception('Request failed')
            self.reply(500, {'error': 'Request failed; check the service journal.'})

    def get(self):
        url = urlsplit(self.path)
        session = self.panel.session(self.token())
        if url.path in ('/assets/app.js', '/assets/style.css'):
            name = url.path.rsplit('/', 1)[1]
            return self.reply(200, (STATIC / name).read_bytes(), 'text/javascript' if name.endswith('.js') else 'text/css')
        if url.path == '/login':
            if session and session.authenticated:
                return self.reply(303, extra={'Location': '/'})
            return self.reply(200, (STATIC / 'login.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/api/session':
            token = None
            if not session:
                token, session = self.panel.new_session()
            return self.reply(200, {'csrf': session.csrf, 'authenticated': session.authenticated}, cookie=token)
        if not session or not session.authenticated:
            return self.reply(401, {'error': 'Sign in required.'}) if url.path.startswith('/api/') else self.reply(303, extra={'Location': '/login'})
        if url.path in ('/', '/clients', '/usage'):
            return self.reply(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/api/clients':
            return self.reply(200, {'clients': self.panel.catalog()})
        if url.path == '/api/usage':
            return self.reply(200, self.panel.usage())
        if url.path == '/api/download':
            names = parse_qs(url.query).get('file', [])
            if len(names) != 1:
                return self.reply(400, {'error': 'Choose one profile.'})
            name = names[0]
            if not re.fullmatch(r'[a-zA-Z0-9_.:-]+\.ovpn', name):
                return self.reply(400, {'error': 'Invalid profile filename.'})
            available = {p['file'] for client in self.panel.catalog() for p in client['profiles']}
            if name not in available:
                return self.reply(404, {'error': 'Profile not found or certificate is revoked/expired.'})
            path = self.panel.project / 'client/openvpn/vpn-udp' / name
            if path.is_symlink():
                return self.reply(404, {'error': 'Profile not found.'})
            return self.reply(200, path.read_bytes(), 'application/x-openvpn-profile',
                              extra={'Content-Disposition': f'attachment; filename="{name}"'})
        self.reply(404, {'error': 'Not found.'})

    def do_POST(self):
        try:
            self.post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, TypeError, json.JSONDecodeError):
            self.reply(400, {'error': 'Invalid request.'})
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            LOG.exception('Operation failed')
            self.reply(500, {'error': 'Operation failed; check the service journal.'})

    def post(self):
        # Consume bounded bodies before replying, so an early rejection cannot
        # race clients that send headers and body in separate socket writes.
        length = int(self.headers.get('Content-Length', '0'))
        if not 1 <= length <= 8192:
            return self.reply(413, {'error': 'Request too large or empty.'})
        raw = self.rfile.read(length)
        session = self.panel.session(self.token())
        if not session:
            return self.reply(401, {'error': 'Sign in required.'})
        if not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), session.csrf):
            return self.reply(403, {'error': 'Refresh the page and try again.'})
        # nginx supplies Host including the custom HTTPS port. Reject cross-origin POSTs.
        if self.headers.get('Origin') != 'https://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'Invalid request origin.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'Expected JSON.'})
        data = json.loads(raw)
        if not isinstance(data, dict):
            return self.reply(400, {'error': 'Expected an object.'})
        route = urlsplit(self.path).path
        if route == '/api/login':
            username, password = data.get('username'), data.get('password')
            if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
                return self.reply(400, {'error': 'Invalid credentials.'})
            result = self.panel.login(username, password)
            if result is None:
                return self.reply(429, {'error': 'Too many login attempts. Wait a minute.'}, extra={'Retry-After': '60'})
            if not result:
                return self.reply(401, {'error': 'Invalid credentials or PAM account restrictions.'})
            with self.panel.lock:
                self.panel.sessions.pop(self.token(), None)
            token, new_session = self.panel.new_session(True)
            return self.reply(200, {'csrf': new_session.csrf}, cookie=token)
        if not session.authenticated:
            return self.reply(401, {'error': 'Sign in required.'})
        if route == '/api/logout':
            with self.panel.lock:
                self.panel.sessions.pop(self.token(), None)
            return self.reply(200, {'ok': True}, cookie='')
        if route == '/api/clients':
            try:
                return self.reply(201, self.panel.create(data.get('name'), data.get('days')))
            except FileExistsError as error:
                return self.reply(409, {'error': str(error)})
            except BlockingIOError as error:
                return self.reply(409, {'error': str(error)})
            except ValueError as error:
                return self.reply(400, {'error': str(error)})
        self.reply(404, {'error': 'Not found.'})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=Path('/run/openvpn-panel/backend.sock'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    panel = Panel()
    stop = threading.Event()

    def sample_loop():
        while not stop.is_set():
            panel.sample()
            stop.wait(5)

    gid = int(os.environ.get('PANEL_SOCKET_GID', '101'))
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    os.chown(args.socket.parent, 0, gid)
    os.chmod(args.socket.parent, 0o750)
    args.socket.unlink(missing_ok=True)
    with Server(str(args.socket), Handler) as server:
        server.panel = panel
        os.chown(args.socket, 0, gid)
        os.chmod(args.socket, 0o660)
        thread = threading.Thread(target=sample_loop, daemon=True)
        thread.start()

        def terminate(*_):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            server.serve_forever()
        finally:
            stop.set()
            thread.join(timeout=10)
            args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
