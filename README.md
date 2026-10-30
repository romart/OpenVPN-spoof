# OpenVPN-spoof

## Web control panel

The [web panel](panel/README.md) provides root-password login, client creation,
default/routed profile downloads, revocation/deletion, a config list, and live
usage from `monitor.py`.
It uses an nginx container with HTTPS on port `443` and automatic Let’s Encrypt
renewal. Install on the VPN VPS from this checkout:

```sh
sudo bash panel/install.sh --domain vpn.example.com --email you@example.com
```

Or use `--ip YOUR_PUBLIC_IPV4` instead of `--domain`. Allow inbound TCP ports 80
and 443; see the linked instructions for requirements and operations.

## Installer patch and DCO options

`setup.sh` and `patch-openvpn.sh` use the same patch choices:
`1` = None, `2` = Random (default), `3` = Strong, `4` = Error-Free.
Older setup records used a different numbering; use the new menu labels when
reinstalling. Choosing DCO off explicitly writes `disable-dco`.

DCO needs headers matching the kernel for which DKMS builds its module. If the
running Debian kernel is too old for its headers to remain in the repositories,
`openvpn-dco.sh y` installs the matching kernel/header meta-packages (for example,
`linux-image-cloud-amd64` and `linux-headers-cloud-amd64`) and builds DCO for the
updated kernel. Exit status `2` means the module is ready for an installed kernel
and a reboot into that kernel is required. `setup.sh` recognizes this status and
continues to its normal reboot. The standalone helper does not reboot the VPS.
Existing DCO modules are not unloaded while serving clients.

To recover a Debian 13 cloud-amd64 VPS with unavailable running-kernel headers:

```sh
sudo apt-get update
sudo apt-get install linux-image-cloud-amd64 linux-headers-cloud-amd64 openvpn-dco-dkms
sudo reboot
```

After reconnecting, run the updated helper if necessary:

```sh
sudo /root/antizapret/openvpn-dco.sh y
```

The helper installs exact running-kernel headers when available. If an Ubuntu or
custom kernel has neither usable headers nor a DCO module, it reports the missing
prerequisite rather than selecting an unrelated Debian kernel.

## Default and routed client profiles

Adding a client (`client.sh 1 NAME DAYS`) or recreating profiles (`client.sh 4`)
generates two files in `/root/antizapret/client/openvpn/vpn-udp/`:

- `vpn-NAME-SERVER_IP-udp.ovpn`: the existing default profile, using the server's
  full-tunnel routing settings.
- `vpn-NAME-SERVER_IP-udp-routed.ovpn`: a split-tunnel profile with the same
  embedded CA, client certificate and private key. It adds `route-nopull` and
  embeds the contents of `/root/antizapret/wl_routes.txt`.

Edit that separate route file using OpenVPN route directives, for example:

```text
# Send this network through the VPN
route 203.0.113.0 255.255.255.0
# Send a single address through the VPN
route 198.51.100.5 255.255.255.255
```

The routed profile ignores server-pushed routes (including default-gateway
redirection) and DNS settings; other destinations use the client's normal
connection. See the [OpenVPN route-nopull documentation](https://openvpn.net/community-docs/community-articles/openvpn-2-6-manual/).
Routes are embedded when generating the profile, so clients only need the
`.ovpn` file. After editing routes, regenerate and redistribute routed profiles:

```sh
sudo /root/antizapret/client.sh 4
```

To use another route file, specify an absolute path:

```sh
sudo env ROUTES_FILE=/root/my-routes.txt /root/antizapret/client.sh 4
```

The route file must exist and be readable. Removing a client revokes the shared
certificate for both profiles and deletes both generated files for the current
server address.

For an existing server, copy the updated `setup/root/antizapret/client.sh` to
`/root/antizapret/client.sh` and ensure `wl_routes.txt` exists alongside it
(the repository includes an initial list). Then run option `4`. A full server
reinstallation or service restart is not required.

## Move an existing server to another VPS

Use `setup/root/antizapret/migrate.py` from this repository. New installations
also install it as `/root/antizapret/migrate.py`. Run it with Python 3 as root.
It preserves `/etc/openvpn` (including the full Easy-RSA CA, private keys,
issued/revoked certificates, CRL, client keys, templates and server configs),
`/root/antizapret` (including generated profiles and custom settings/scripts),
and this project's systemd unit and OpenVPN override.

1. On the **old VPS**, copy the migration script there if it predates this feature.
   Avoid creating/revoking clients or changing configuration during export:

   ```sh
   sudo python3 /root/antizapret/migrate.py export /root/openvpn-migration.tar.gz
   ```

2. On the **new VPS**, install this project with `setup.sh` and let it reboot.
   Choose the same OpenVPN patch, DCO, address range and other installation
   options as on the old server (recorded in `/root/antizapret/setup`). Use a
   compatible Debian/Ubuntu version and CPU architecture. The migration does
   not install packages, copy patched binaries or change kernel modules.
   The destination VPN configuration will be replaced.

3. Transfer the archive and migration script securely, for example from your
   workstation using SSH accounts that can read/write these files:

   ```sh
   scp root@OLD_VPS:/root/openvpn-migration.tar.gz .
   scp openvpn-migration.tar.gz root@NEW_VPS:/root/
   scp setup/root/antizapret/migrate.py root@NEW_VPS:/root/migrate.py
   ```

4. On the **new VPS**, import using its public IP or DNS name:

   ```sh
   sudo python3 /root/migrate.py import /root/openvpn-migration.tar.gz --endpoint NEW_PUBLIC_IP_OR_HOSTNAME
   ```

   Import validates the archive, saves the destination's previous state to
   `/root/openvpn-before-migration-*.tar.gz`, stops VPN services, restores files,
   and enables/starts the migrated services. If restoring or starting services
   fails, it attempts to restore the previous files and service state. Keep the
   printed backup path until you have verified connections.

5. Allow the configured UDP ports in the new VPS provider's firewall. Download
   and distribute the updated `.ovpn` profiles from
   `/root/antizapret/client/`. Their filenames may still contain the old IP;
   their `remote` directives point to the new endpoint. Existing certificates
   and private keys are unchanged. The standard profile template also uses the
   specified endpoint for future clients, including servers behind NAT.

   Profiles already on devices must have their `remote` addresses updated, or
   be replaced with the updated files. If they already use a DNS hostname you
   control, moving its DNS record can avoid redistributing profiles.

Verify an actual client connection and access to the Internet before retiring
the old VPS. Service startup alone does not verify provider networking or patch
compatibility. The old server is left running by export.

Archives contain the CA private key and every client private key. Store and
transfer them privately, import only your own trusted archives (they contain
root-run scripts), and remove extra copies after verification. Provider network
configuration, SSH settings, unrelated firewall rules, and files outside the
paths above are not migrated. Custom scripts that hardcode the old interface or
IP, and configuration referencing external files, need manual adjustment.

Run the local archive/profile checks with:

```sh
python3 -m unittest discover -s tests
```
