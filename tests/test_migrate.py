import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('migrate', Path(__file__).resolve().parents[1] / 'setup/root/antizapret/migrate.py')
migrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migrate)


class MigrationTests(unittest.TestCase):
    def test_round_trip_preserves_private_keys_and_updates_profiles(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / 'source'
            for name in migrate.REQUIRED:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'original identity\n')
                file.chmod(0o600)
            profile = root / 'root/antizapret/client/client.ovpn'
            profile.parent.mkdir()
            original = 'remote 192.0.2.1 50080 udp\nremote 192.0.2.1 80 udp\n<key>\nprivate-key\n</key>\n'
            profile.write_text(original)
            template = root / 'etc/openvpn/client/templates/vpn-udp.conf'
            template.parent.mkdir(parents=True)
            template.write_text('remote ${SERVER_IP} 50080 udp\n')
            archive = Path(work) / 'server.tar.gz'
            migrate.export_archive(archive, root)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            restored = Path(work) / 'restored'
            restored.mkdir()
            migrate.unpack(archive, restored)
            migrate.update_profiles(restored, 'vpn.example.org')
            self.assertEqual((restored / profile.relative_to(root)).read_text(), original.replace('192.0.2.1', 'vpn.example.org'))
            self.assertEqual((restored / template.relative_to(root)).read_text(), 'remote vpn.example.org 50080 udp\n')
            for name in migrate.REQUIRED:
                self.assertEqual((restored / name).read_bytes(), b'original identity\n')
                self.assertEqual((restored / name).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                migrate.export_archive(archive, root)

    def test_rejects_unsafe_archives_before_extracting(self):
        for name, kind in [('etc/openvpn/../../escape', tarfile.REGTYPE), ('etc/openvpn/link', tarfile.SYMTYPE), ('etc/shadow', tarfile.REGTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as work:
                archive = Path(work) / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as out:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = '/etc/shadow'
                    out.addfile(entry, io.BytesIO())
                restored = Path(work) / 'restored'
                restored.mkdir()
                with self.assertRaises(ValueError):
                    migrate.unpack(archive, restored)
                self.assertEqual(list(restored.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
