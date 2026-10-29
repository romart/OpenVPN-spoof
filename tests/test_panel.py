import http.client
from contextlib import closing
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))
sys.path.insert(0, str(ROOT))
import app
import monitor
import pam_auth


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('panel.example:46724', timeout=5)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vpn = self.root / 'openvpn'
        self.project = self.root / 'project'
        self.pki = self.vpn / 'easyrsa3/pki'
        (self.pki / 'issued').mkdir(parents=True)
        (self.pki / 'private').mkdir()
        for name in ('ca.crt', 'private/ca.key', 'issued/server.crt', 'private/server.key'):
            (self.pki / name).write_text('existing server identity')
        (self.pki / 'index.txt').write_text('')
        self.profiles = self.project / 'client/openvpn/vpn-udp'
        self.profiles.mkdir(parents=True)
        self.panel = app.Panel(self.vpn, self.project, self.root / 'state.json',
                               auth=lambda user, password: user == 'root' and password == 'test-password',
                               fallback=self.root / 'missing-status.log')
        self.socket = self.root / 'server.sock'
        self.server = app.Server(str(self.socket), app.Handler)
        self.server.panel = self.panel
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''
        self.csrf = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, csrf=True, origin='https://panel.example:46724'):
        headers = {'Cookie': self.cookie, 'Origin': origin}
        body = None
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
            if csrf:
                headers['X-CSRF-Token'] = self.csrf
            body = json.dumps(data or {})
        with closing(UnixConnection(self.socket)) as connection:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.getheader('Set-Cookie'):
                self.cookie = response.getheader('Set-Cookie').split(';')[0]
            value = json.loads(raw) if raw and response.getheader('Content-Type') == 'application/json' else raw
            return response.status, value, dict(response.getheaders())

    def login(self):
        status, data, headers = self.request('/api/session')
        self.assertEqual(status, 200)
        self.csrf = data['csrf']
        if 'Set-Cookie' in headers:
            self.assertIn('Secure; HttpOnly; SameSite=Strict', headers['Set-Cookie'])
        old_cookie = self.cookie
        status, data, _ = self.request('/api/login', 'POST', {'username': 'root', 'password': 'test-password'})
        self.assertEqual(status, 200)
        self.csrf = data['csrf']
        self.assertNotEqual(self.cookie, old_cookie)

    def add_client(self, name='phone', state='V', expiry='360101000000Z'):
        cert = f'-----BEGIN CERTIFICATE-----\n{name}\n-----END CERTIFICATE-----'
        (self.pki / 'issued' / (name + '.crt')).write_text(cert)
        with (self.pki / 'index.txt').open('a') as index:
            index.write(f'{state}\t{expiry}\t\t01\tunknown\t/CN={name}\n')
        for suffix in ('', '-routed'):
            (self.profiles / f'vpn-{name}-192.0.2.1-udp{suffix}.ovpn').write_text(
                'client\n<cert>\n' + cert + '\n</cert>\n<key>private</key>\n')

    def test_authentication_csrf_logout_and_expiry(self):
        for path in ('/api/clients', '/api/usage', '/api/download?file=anything.ovpn'):
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request('/usage')[0], 303)
        _, data, _ = self.request('/api/session')
        self.csrf = data['csrf']
        self.assertEqual(self.request('/api/clients', 'POST', {'name': 'phone', 'days': 365})[0], 401)
        self.assertEqual(self.request('/api/login', 'POST', {'username': 'root', 'password': 'test-password'}, csrf=False)[0], 403)
        self.assertEqual(self.request('/api/login', 'POST', {'username': 'root', 'password': 'test-password'}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('/api/login', 'POST', {'username': 'root', 'password': 'wrong'})[0], 401)
        self.login()
        self.assertEqual(self.request('/api/usage')[0], 200)
        token = self.cookie.split('=', 1)[1]
        self.panel.sessions[token].touched -= 1801
        self.assertEqual(self.request('/api/usage')[0], 401)
        self.login()
        self.assertEqual(self.request('/api/logout', 'POST')[0], 200)
        self.assertEqual(self.request('/api/clients')[0], 401)

    def test_catalog_downloads_reject_revoked_expired_traversal_and_symlinks(self):
        self.add_client()
        self.add_client('revoked', 'R')
        self.add_client('expired', expiry='200101000000Z')
        (self.profiles / 'leak.ovpn').symlink_to(self.pki / 'private/ca.key')
        self.login()
        status, result, _ = self.request('/api/clients')
        self.assertEqual(status, 200)
        self.assertEqual([c['name'] for c in result['clients']], ['phone'])
        self.assertEqual(len(result['clients'][0]['profiles']), 2)
        file = result['clients'][0]['profiles'][0]['file']
        status, contents, headers = self.request('/api/download?file=' + file)
        self.assertEqual(status, 200)
        self.assertIn(b'<key>private</key>', contents)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        for file, expected in (('../../ca.key.ovpn', 400), ('leak.ovpn', 404), ('vpn-revoked-192.0.2.1-udp.ovpn', 404)):
            self.assertEqual(self.request('/api/download?file=' + file)[0], expected)

    def test_create_validates_input_and_executes_only_fixed_script(self):
        self.login()
        with patch.object(app.subprocess, 'run') as run:
            for name, days in (('bad;command', 365), ('server', 365), ('phone', 0), ('phone', True), ('phone', 3651)):
                self.assertEqual(self.request('/api/clients', 'POST', {'name': name, 'days': days})[0], 400)
            run.assert_not_called()

            def create(*args, **kwargs):
                self.assertEqual(args[0], ['/bin/bash', str(self.project / 'client.sh'), '1', 'phone', '365'])
                self.assertNotIn('shell', kwargs)
                self.add_client()
                return app.subprocess.CompletedProcess(args[0], 0, '')

            run.side_effect = create
            self.assertEqual(self.request('/api/clients', 'POST', {'name': 'phone', 'days': 365})[0], 201)
            self.assertEqual(self.request('/api/clients', 'POST', {'name': 'vpn-phone', 'days': 365})[0], 409)
            self.assertEqual(run.call_count, 1)
        (self.pki / 'issued/server.crt').unlink()
        with patch.object(app.subprocess, 'run') as run:
            self.assertEqual(self.request('/api/clients', 'POST', {'name': 'other', 'days': 365})[0], 400)
            run.assert_not_called()

    def test_login_rate_limit_and_root_only(self):
        self.assertFalse(pam_auth.authenticate('other', 'anything'))
        self.assertFalse(pam_auth.authenticate('root', ''))
        for _ in range(5):
            self.assertFalse(self.panel.login('root', 'wrong'))
        self.assertIsNone(self.panel.login('root', 'test-password'))

    def test_shared_usage_accounting_and_persistence(self):
        log = self.vpn / 'server/logs/vpn-udp-status.log'
        log.parent.mkdir(parents=True)
        def write(upload, download, peer=1, since='2030-01-01 00:00:00'):
            log.write_text('HEADER,CLIENT_LIST,Common Name\n'
                           f'CLIENT_LIST,phone,192.0.2.5:1234,10.28.0.2,,{upload},{download},{since},1893456000,,1,{peer},AES-128-GCM\n'
                           'HEADER,ROUTING_TABLE,Virtual Address\n')
        write(100, 200)
        self.panel.sample()
        write(150, 300)
        self.panel.sample()
        client = self.panel.usage()['clients'][0]
        self.assertEqual((client['upload'], client['download'], client['online']), (50, 100, True))
        write(5, 10, 2, '2030-01-01 01:00:00')
        self.panel.sample()
        client = self.panel.usage()['clients'][0]
        self.assertEqual((client['upload'], client['download']), (50, 100))
        restored = monitor.load_state(str(self.root / 'state.json'))
        self.assertEqual(restored['phone'].total_rx(), 100)
        log.unlink()
        self.panel.sample()
        self.assertFalse(self.panel.usage()['clients'][0]['online'])
        self.assertIsNotNone(self.panel.usage()['warning'])

    def test_usage_includes_never_connected_offline_and_historical_clients(self):
        self.add_client('never-connected')
        self.add_client('expired', expiry='200101000000Z')
        self.add_client('revoked', state='R')
        history = monitor.ClientState()
        history.acc_rx, history.acc_tx = 500, 100
        self.panel.states['historical'] = history
        self.login()
        status, data, _ = self.request('/api/usage')
        self.assertEqual(status, 200)
        self.assertEqual(data['clients'][0]['name'], 'historical')
        clients = {client['name']: client for client in data['clients']}
        self.assertEqual(set(clients), {'never-connected', 'expired', 'revoked', 'historical'})
        for name in ('never-connected', 'expired', 'revoked'):
            self.assertFalse(clients[name]['online'])
            self.assertEqual((clients[name]['download'], clients[name]['upload']), (0, 0))
        # Rendering a zero row must not create a fake sampling baseline.
        self.assertNotIn('never-connected', self.panel.states)


if __name__ == '__main__':
    unittest.main()
