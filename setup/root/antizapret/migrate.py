#!/usr/bin/env python3
"""Export and restore this project's OpenVPN identity and configuration."""
import argparse
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

ROOTS = (
    'etc/openvpn',
    'root/antizapret',
    'etc/systemd/system/antizapret.service',
    'etc/systemd/system/openvpn-server@.service.d',
)
REQUIRED = (
    'etc/openvpn/easyrsa3/pki/ca.crt',
    'etc/openvpn/easyrsa3/pki/private/ca.key',
    'etc/openvpn/easyrsa3/pki/index.txt',
    'etc/openvpn/server/keys/server.key',
    'etc/openvpn/server/vpn-udp.conf',
    'root/antizapret/up.sh',
)


def run(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.DEVNULL)


def services(root=Path('/')):
    return ['antizapret.service'] + [
        f'openvpn-server@{p.stem}.service'
        for p in sorted((root / 'etc/openvpn/server').glob('*.conf'))
    ]


def allowed(name):
    return any(name == r or name.startswith(r + '/') for r in ROOTS)


def export_archive(output, root=Path('/'), require=True):
    output = output.resolve()
    if allowed(str(output).lstrip('/')):
        raise ValueError('Store the archive outside /etc/openvpn and /root/antizapret')
    if require:
        for name in REQUIRED:
            if not (root / name).is_file():
                raise ValueError(f'Missing server state: /{name}')
    # Exclusive creation prevents accidentally overwriting an existing backup.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            with tarfile.open(fileobj=stream, mode='w:gz', dereference=True) as archive:
                for name in ROOTS:
                    if (root / name).exists():
                        archive.add(root / name, arcname=name)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def unpack(archive_path, destination):
    # Do not use extractall: reject links, devices, traversal and unexpected paths.
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            name = member.name
            if (name in seen or PurePosixPath(name).is_absolute()
                    or '..' in PurePosixPath(name).parts or not allowed(name)
                    or not (member.isdir() or member.isfile())):
                raise ValueError(f'Unsafe or unsupported archive entry: {name}')
            seen.add(name)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as out:
                    shutil.copyfileobj(source, out)
            os.chmod(target, member.mode & 0o777)
            os.chown(target, member.uid, member.gid)
    for name in REQUIRED:
        if not (destination / name).is_file():
            raise ValueError(f'Archive is missing /{name}')


def replace_state(staging):
    for name in ROOTS:
        target = Path('/') / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        source = staging / name
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
            # copytree does not preserve ownership.
            for item in [source, *source.rglob('*')]:
                st = item.stat()
                os.chown(target / item.relative_to(source), st.st_uid, st.st_gid)
        elif source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            st = source.stat()
            os.chown(target, st.st_uid, st.st_gid)


def endpoint(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if len(value) > 253 or not all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
            for label in value.rstrip('.').split('.')
        ):
            raise argparse.ArgumentTypeError('Expected an IP address or DNS hostname')
    return value


def update_profiles(staging, address):
    # Preserve embedded certificates, custom options, ports and protocol settings.
    for profile in (staging / 'root/antizapret').rglob('*.ovpn'):
        text = profile.read_text()
        text = re.sub(r'(?m)^(\s*remote\s+)\S+', lambda m: m[1] + address, text)
        profile.write_text(text)
    template = staging / 'etc/openvpn/client/templates/vpn-udp.conf'
    if template.exists():
        text = template.read_text()
        text = re.sub(r'(?m)^(\s*remote\s+)\S+', lambda m: m[1] + address, text)
        template.write_text(text)


def restore(args):
    for command in ('systemctl', 'openvpn', 'iptables'):
        if not shutil.which(command):
            raise ValueError(f'Missing {command}; install this project on the destination first')
    for name in REQUIRED:
        if not (Path('/') / name).is_file():
            raise ValueError('Install this project on the destination before importing')
    with tempfile.TemporaryDirectory(prefix='openvpn-migrate-') as work:
        os.chmod(work, 0o700)
        staging = Path(work) / 'incoming'
        staging.mkdir()
        unpack(args.archive, staging)
        update_profiles(staging, args.endpoint)
        old_services = services()
        new_services = services(staging)
        units = sorted(set(old_services + new_services))
        active = [unit for unit in units if run('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0]
        enabled = [unit for unit in units if run('systemctl', 'is-enabled', '--quiet', unit, check=False).returncode == 0]
        backup = Path('/root') / ('openvpn-before-migration-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
        export_archive(backup)
        print(f'Destination backup: {backup}', flush=True)
        rollback = Path(work) / 'rollback'
        rollback.mkdir()
        unpack(backup, rollback)
        try:
            run('systemctl', 'stop', *units)
            replace_state(staging)
            run('systemctl', 'daemon-reload')
            for unit in set(old_services) - set(new_services):
                run('systemctl', 'disable', unit)
            run('systemctl', 'enable', *new_services)
            run('systemctl', 'start', *new_services)
            for unit in new_services:
                run('systemctl', 'is-active', '--quiet', unit)
        except BaseException:
            print('Import failed; restoring destination state.', file=sys.stderr)
            run('systemctl', 'stop', *units, check=False)
            replace_state(rollback)
            run('systemctl', 'daemon-reload')
            run('systemctl', 'disable', *units, check=False)
            if enabled:
                run('systemctl', 'enable', *enabled)
            if active:
                run('systemctl', 'start', *active)
            raise
    print('Import complete. Distribute the updated .ovpn files from /root/antizapret/client.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='Create a private migration archive')
    export.add_argument('archive', type=Path)
    restore_parser = commands.add_parser('import', help='Replace destination VPN state (creates a rollback backup)')
    restore_parser.add_argument('archive', type=Path)
    restore_parser.add_argument('--endpoint', type=endpoint, required=True, help='New public IP or DNS hostname')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root')
    if args.command == 'export':
        export_archive(args.archive)
        print(f'Exported to {args.archive}. This archive contains private keys; transfer it securely.')
    else:
        restore(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        sys.exit(f'Migration failed: {error}')
