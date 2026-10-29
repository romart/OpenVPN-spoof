# OpenVPN web control panel

A small administration panel for the OpenVPN server in this repository:

- Sign in with username `root` and this VPS's root password, verified by Linux
  PAM. The panel does not store the password. SSH keys alone cannot sign in;
  the root account must have a usable password and pass the host's account policy.
- Create clients using the existing `client.sh`, with a name and certificate lifetime.
- List existing valid clients and download their default and routed `.ovpn`
  profiles. Both profiles use the same certificate/key. Revoked and expired
  certificates are excluded from downloads. Existing files are matched to client
  certificates, so filenames retained after migration are supported.
- Revoke a client and delete all of its generated default/routed profiles,
  including copies with old endpoint filenames. The action requires confirmation,
  updates the server CRL atomically, and removes exported client keys. CA records
  and usage history remain available for auditing; existing backups are retained.
- View online/offline status, addresses, and persistent download/upload totals
  using the parser and delta accounting shared with `monitor.py`.

## Install on the VPS

Run from a checkout of this repository **on the VPN server**. This installer is
separate from `setup.sh`; it does not reinstall OpenVPN, modify its server
configuration, or restart the VPN service. It updates `client.sh` to the version
in this checkout and preserves an existing `wl_routes.txt`.

Choose either a DNS hostname pointing to the VPS, or its public IPv4 address:

```sh
sudo bash panel/install.sh --domain vpn.example.com --email you@example.com
```

Or, without a domain:

```sh
sudo bash panel/install.sh --ip YOUR_PUBLIC_IPV4 --email you@example.com
```

Open `https://vpn.example.com` or `https://YOUR_PUBLIC_IPV4` and sign
in. Choose another HTTPS port with `--port 46724` if needed.

The installer installs Docker/Compose if absent, Python, PAM runtime support,
and an isolated Certbot version >=5.4. It starts a host backend service and an
nginx container, obtains a trusted Let's Encrypt certificate, then enables HTTPS.
An installation that fails certificate validation exposes no panel over HTTP;
fix the DNS/firewall problem and rerun the same command.

Allow inbound **TCP 80** for certificate issuance/renewal and **TCP 443** for
the panel in the VPS provider firewall and any host firewall. Existing UDP VPN
ports remain needed. Port 80 serves only the ACME challenge directory; other
HTTP paths return 404. An existing service occupying either TCP port must be
resolved first. DNS A records must point to this VPS; remove incorrect AAAA
records (this installer currently listens on IPv4).

Let's Encrypt IP certificates require the `shortlived` profile and expire after
about six days. The installer configures checks every six hours for both IP and
DNS certificates, with an nginx reload after successful renewal. Keep TCP 80
reachable. See the [official IP certificate instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).

For a free DNS hostname instead, [DuckDNS](https://www.duckdns.org/) lets you
choose a name under `duckdns.org` and set its IPv4 address to the VPS's public IP.

## Operation

Use **Revoke & delete** on the Configs page to revoke the shared certificate for
both profile variants. This prevents future connections with downloaded copies
as well. An already connected session may remain connected until it disconnects
or renegotiates; this action does not restart the VPN or disconnect other clients.
See [OpenVPN's revocation behavior](https://openvpn.net/community-docs/revoking-certificates.html).
If generating/publishing the CRL fails, inspect the backend journal and retry the
same operation; already-revoked certificates are handled without revoking twice.

```sh
# Backend and PAM/client-generation errors
sudo journalctl -u openvpn-panel -n 100 --no-pager

# nginx container logs
sudo /opt/openvpn-panel/compose.sh logs --tail=100 nginx

# Restart backend; existing browser sessions will need to sign in again
sudo systemctl restart openvpn-panel

# Certificate renewal timer and a validation test
sudo systemctl list-timers openvpn-panel-renew.timer
sudo /opt/openvpn-panel/certbot.sh renew --dry-run

# Manually check renewal and reload nginx if a certificate is renewed
sudo systemctl start openvpn-panel-renew.service
```

Edit `/root/antizapret/wl_routes.txt` and run `/root/antizapret/client.sh 4` to
regenerate existing profiles after route-list changes. New profiles created in
the panel automatically include the current route file. Download updated routed
profiles again on devices after regeneration.

Usage is sampled every five seconds, including when no browser is open. Totals
begin at the first sample and persist at `/var/lib/openvpn-panel/usage.json`.
The usage table includes all clients from the CA index and retained monitoring
history, including offline, expired and revoked clients. Clients not yet observed
have zero recorded usage. Rows are sorted by total traffic, as in the CLI viewer.
They use the CLI monitor's accounting semantics: the first sample of a session
establishes a baseline, and subsequent samples add deltas. Connections entirely
between samples are not counted. The panel maintains its own state, so it can
run alongside the CLI monitor. Duplicate simultaneous connections sharing a
certificate have the same per-CN limitation as `monitor.py` (one connection's
counters per sample); use separate clients for separate devices.

## Deployment layout and authentication

nginx is the only container and listens on TCP 80/443 using host networking,
which avoids a Docker bridge for this deployment. It proxies all panel routes
(including downloads) to a Unix socket with restricted permissions. No backend
TCP port, Docker socket, or VPN private-key directory is exposed to the container.
nginx receives read-only access to its TLS certificate store and local backend
socket directory. The Python host service runs as root because PAM authentication
and the existing client generator need that access; systemd restricts filesystem
writes to the VPN/project directories, runtime socket and panel state.

Sessions use random tokens, secure HttpOnly/SameSite cookies, 30-minute idle and
8-hour absolute limits, and are cleared on backend restart. Mutations require a
CSRF token and matching HTTPS origin. Login attempts are globally limited to
five per minute in addition to the host's PAM policy. Login page/assets and an
anonymous CSRF bootstrap endpoint are public; client lists, usage, profile
contents and administrative operations require authentication.

Certificates live in `/var/lib/openvpn-panel/letsencrypt`, separate from any
system Certbot installation. nginx configuration is at
`/opt/openvpn-panel/nginx/panel.conf`; its certificate mount includes the full
store so renewals remain visible. The backend socket directory is retained across
service restarts to keep the nginx mount connected.

The VPN migration archive does not include `/opt/openvpn-panel`, its certificate
store or panel service files. Install the panel on a new VPS after migrating VPN
state, and issue a certificate for that VPS's hostname/IP.

## Local checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
node --check panel/static/app.js
bash -n panel/*.sh
```

Tests exercise the HTTP API over a temporary Unix socket with an injected
password verifier and temporary PKI/profile fixtures. Real PAM authentication,
Docker/nginx startup, public certificate issuance and live VPN connections must
be checked on the target VPS.
