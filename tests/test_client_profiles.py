import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'setup/root/antizapret/client.sh'
TEMPLATE = REPO / 'setup/etc/openvpn/client/templates/vpn-udp.conf'


class ClientProfileTests(unittest.TestCase):
    def generate(self, directory, routes):
        env = dict(os.environ, ROUTES_FILE=str(routes), SERVER_IP='192.0.2.1',
                   CLIENT_NAME='test-client', CA_CERT='-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----',
                   CLIENT_CERT='-----BEGIN CERTIFICATE-----\nclient\n-----END CERTIFICATE-----',
                   CLIENT_KEY='-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----')
        return subprocess.run(
            ['bash', '-c', 'source "$1"; writeOpenVPNProfiles "$2" "$3" "$4"',
             'test', str(SCRIPT), str(TEMPLATE), str(directory / 'default.ovpn'),
             str(directory / 'routed.ovpn')], env=env, capture_output=True, text=True)

    def test_profiles_share_identity_and_only_routed_has_routes(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            routes = directory / 'custom routes.txt'
            routes.write_bytes(b'# Custom routes\r\nroute 203.0.113.0 255.255.255.0\r\nroute 198.51.100.5 255.255.255.255')
            result = self.generate(directory, routes)
            self.assertEqual(result.returncode, 0, result.stderr)
            default = (directory / 'default.ovpn').read_text()
            routed = (directory / 'routed.ovpn').read_text()
            self.assertTrue(routed.startswith(default))
            self.assertIn('remote 192.0.2.1 50080 udp', default)
            self.assertNotIn('${', default)
            self.assertNotIn('route-nopull', default)
            self.assertNotIn('route 203.0.113.0', default)
            self.assertIn('\nroute-nopull\n', routed)
            self.assertIn('route 203.0.113.0 255.255.255.0\n', routed)
            self.assertTrue(routed.endswith('route 198.51.100.5 255.255.255.255\n'))
            for tag in ('ca', 'cert', 'key'):
                identity = default.split(f'<{tag}>')[1].split(f'</{tag}>')[0]
                self.assertEqual(identity, routed.split(f'<{tag}>')[1].split(f'</{tag}>')[0])
            for name in ('default.ovpn', 'routed.ovpn'):
                self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
            # Recreating profiles replaces the embedded route list.
            routes.write_text('route 192.0.2.5 255.255.255.255\n')
            self.assertEqual(self.generate(directory, routes).returncode, 0)
            routed = (directory / 'routed.ovpn').read_text()
            self.assertIn('route 192.0.2.5', routed)
            self.assertNotIn('route 203.0.113.0', routed)

    def test_missing_routes_does_not_overwrite_existing_profiles(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            for name in ('default.ovpn', 'routed.ovpn'):
                (directory / name).write_text('existing profile')
            result = self.generate(directory, directory / 'missing.txt')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Cannot read routes file', result.stderr)
            for name in ('default.ovpn', 'routed.ovpn'):
                self.assertEqual((directory / name).read_text(), 'existing profile')


if __name__ == '__main__':
    unittest.main()
