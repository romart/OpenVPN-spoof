import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'setup/root/antizapret/openvpn-dco.sh'
CURRENT = '6.12.41+deb13-cloud-amd64'
NEW = '6.12.57+deb13-cloud-amd64'

MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, sys
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as stream:
    stream.write(json.dumps([command, *args]) + '\\n')
if command == 'uname':
    print(os.environ['TEST_KERNEL'])
elif command == 'openvpn':
    print('OpenVPN 2.6.22 test')
elif command == 'lsb_release':
    print(os.environ.get('TEST_OS', 'Debian'))
elif command == 'apt-cache':
    candidate = json.loads(os.environ['APT_CANDIDATES']).get(args[-1], '(none)')
    print('  Installed: 1.0\\n  Candidate: ' + candidate)
elif command == 'apt-get':
    if 'openvpn-dco-dkms' in args:
        pathlib.Path(os.environ['INSTALL_MARKER']).touch()
elif command == 'modinfo':
    available = json.loads(os.environ['AVAILABLE_MODULES'])
    present = args[1] in available and (os.environ.get('PREINSTALLED') == 'yes' or pathlib.Path(os.environ['INSTALL_MARKER']).exists())
    sys.exit(0 if present else 1)
elif command == 'systemctl' and args[0] == 'is-active':
    sys.exit(0 if os.environ.get('ACTIVE') == 'yes' else 3)
'''


class DcoSetupTests(unittest.TestCase):
    def run_script(self, choice='y', candidates=None, modules=(), preinstalled=False, active=True, kernel=CURRENT, os_name='Debian'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        mock = bin_dir / 'mock'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        for command in ('uname', 'openvpn', 'lsb_release', 'apt-cache', 'apt-get', 'modinfo', 'modprobe', 'systemctl'):
            (bin_dir / command).symlink_to(mock)
        config = root / 'vpn.conf'
        config.write_text('port 50080\ndata-ciphers "AES-128-CBC"\ndisable-dco\n')
        module_dir = root / 'modules'
        module_dir.mkdir()
        for version in set(modules) | {kernel}:
            (module_dir / version).mkdir()
        log = root / 'calls.jsonl'
        env = dict(os.environ, PATH=str(bin_dir) + ':' + os.environ['PATH'],
                   VPN_CONFIG=str(config), MODULES_DIR=str(module_dir), CALL_LOG=str(log),
                   TEST_KERNEL=kernel, TEST_OS=os_name,
                   APT_CANDIDATES=json.dumps(candidates or {}), AVAILABLE_MODULES=json.dumps(modules),
                   INSTALL_MARKER=str(root / 'installed'), PREINSTALLED='yes' if preinstalled else 'no',
                   ACTIVE='yes' if active else 'no')
        result = subprocess.run(['bash', str(SCRIPT), choice], env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, config.read_text(), calls

    def test_exact_running_headers_are_installed_when_available(self):
        result, config, calls = self.run_script(candidates={'linux-headers-' + CURRENT: '1.0'}, modules=[CURRENT])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['apt-get', 'install', '-y', 'linux-headers-' + CURRENT], calls)
        self.assertIn(['modprobe', 'ovpn_dco_v2'], calls)
        self.assertNotIn('disable-dco', config)
        self.assertIn(['systemctl', 'restart', 'openvpn-server@vpn-udp.service'], calls)
        self.assertFalse(any('-r' in call for call in calls if call[0] == 'modprobe'))

    def test_missing_cloud_headers_installs_matching_meta_packages_and_requires_reboot(self):
        result, config, calls = self.run_script(candidates={'linux-image-cloud-amd64': '1.0', 'linux-headers-cloud-amd64': '1.0'}, modules=[NEW])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Reboot into that kernel', result.stdout)
        self.assertIn(['apt-get', 'install', '-y', 'linux-image-cloud-amd64', 'linux-headers-cloud-amd64'], calls)
        self.assertFalse(any('linux-headers-' + CURRENT in call for call in calls if call[0] == 'apt-get'))
        self.assertNotIn('disable-dco', config)
        self.assertFalse(any(call[:2] == ['systemctl', 'restart'] for call in calls))

    def test_existing_module_does_not_require_removed_headers(self):
        result, config, calls = self.run_script(modules=[CURRENT], preinstalled=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] == 'apt-cache' for call in calls))
        self.assertNotIn('disable-dco', config)

    def test_missing_module_does_not_claim_success_or_change_config(self):
        result, config, calls = self.run_script(candidates={'linux-image-cloud-amd64': '1.0', 'linux-headers-cloud-amd64': '1.0'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('DCO module is unavailable', result.stderr)
        self.assertIn('disable-dco', config)
        self.assertFalse(any(call[:2] == ['systemctl', 'restart'] for call in calls))

    def test_disable_requires_no_package_changes_or_module_unload(self):
        result, config, calls = self.run_script(choice='n', active=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.count('disable-dco'), 1)
        self.assertFalse(any(call[0] in ('apt-get', 'apt-cache', 'modprobe') for call in calls))
        self.assertFalse(any(call[:2] == ['systemctl', 'restart'] for call in calls))

    def test_ubuntu_keeps_using_available_exact_kernel_headers(self):
        kernel = '6.8.0-90-generic'
        result, _, calls = self.run_script(candidates={'linux-headers-' + kernel: '1.0'}, modules=[kernel], kernel=kernel, os_name='Ubuntu')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['apt-get', 'install', '-y', 'linux-headers-' + kernel], calls)
        self.assertFalse(any('linux-headers-cloud-amd64' in call for call in calls))

    def test_setup_passes_every_patch_choice_and_both_dco_choices_unchanged(self):
        source = (ROOT / 'setup.sh').read_text()
        block = source[source.index('ERRORS=""'):source.index('# Если есть ошибки')]
        # Run only the helper invocation block, replacing host paths with harmless stubs.
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            patch = root / 'patch'
            patch.write_text('#!/bin/bash\nprintf "patch:%s\\n" "$1"\n')
            dco = root / 'dco'
            dco.write_text('#!/bin/bash\nprintf "dco:%s\\n" "$1"\nexit "${DCO_EXIT:-0}"\n')
            patch.chmod(0o755)
            dco.chmod(0o755)
            block = block.replace('/root/antizapret/patch-openvpn.sh', str(patch)).replace('/root/antizapret/openvpn-dco.sh', str(dco))
            for choice in ('1', '2', '3', '4'):
                for mode in ('y', 'n'):
                    env = dict(os.environ, OPENVPN_PATCH=choice, OPENVPN_DCO=mode)
                    result = subprocess.run(['bash', '-e', '-c', block], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('patch:' + choice, result.stdout)
                    self.assertIn('dco:' + mode, result.stdout)
            env = dict(os.environ, OPENVPN_PATCH='2', OPENVPN_DCO='y', DCO_EXIT='2')
            result = subprocess.run(['bash', '-e', '-c', block], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('available after reboot', result.stdout)


if __name__ == '__main__':
    unittest.main()
