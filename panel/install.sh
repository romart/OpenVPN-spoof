#!/bin/bash
# Run on the VPN VPS: sudo bash panel/install.sh --domain vpn.example.com --email admin@example.com
set -euo pipefail
DOMAIN=''
IP=''
EMAIL=''
PORT=443
while (( $# )); do
    case "$1" in
        --domain|--ip|--email|--port)
            (( $# >= 2 )) || { echo "Missing value for $1" >&2; exit 1; }
            case "$1" in
                --domain) DOMAIN=$2 ;;
                --ip) IP=$2 ;;
                --email) EMAIL=$2 ;;
                --port) PORT=$2 ;;
            esac
            shift 2 ;;
        *) echo "Usage: $0 (--domain vpn.example.com | --ip PUBLIC_IPV4) --email admin@example.com [--port 443]" >&2; exit 1 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run this installer as root.' >&2; exit 1; }
if [[ -n "$IP" ]]; then
    [[ -z "$DOMAIN" ]] || { echo 'Use either --domain or --ip.' >&2; exit 1; }
    python3 -c 'import ipaddress,sys; address=ipaddress.IPv4Address(sys.argv[1]); sys.exit(0 if address.is_global else 1)' "$IP" || { echo 'Supply a public IPv4 address.' >&2; exit 1; }
    DOMAIN=$IP
    CERT_ARGS=(--ip-address "$IP" --preferred-profile shortlived)
else
    [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && "$DOMAIN" == *.* && "$DOMAIN" != *..* ]] || { echo 'Supply a DNS hostname with --domain, or use --ip.' >&2; exit 1; }
    CERT_ARGS=(-d "$DOMAIN")
fi
[[ "$EMAIL" == *@*.* && "$EMAIL" != -* ]] || { echo 'Supply a contact email with --email.' >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo 'Invalid port.' >&2; exit 1; }
PORT=$((10#$PORT))
(( PORT == 443 || (PORT >= 1024 && PORT <= 65535) )) || { echo 'Choose port 443 or a port from 1024 to 65535.' >&2; exit 1; }
[[ -f /etc/debian_version ]] || { echo 'Debian/Ubuntu are supported.' >&2; exit 1; }
[[ -f /root/antizapret/client.sh && -f /etc/openvpn/easyrsa3/pki/private/ca.key ]] || { echo 'Install the VPN server first.' >&2; exit 1; }
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$HERE/../monitor.py" ]] || { echo 'Run from a full checkout of this repository.' >&2; exit 1; }
exec 9>/run/lock/openvpn-panel-install.lock
flock -n 9 || { echo 'Another panel installation is running.' >&2; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv libpam0g
if ! command -v docker >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose
fi
systemctl enable --now docker
install -d -m 755 /opt/openvpn-panel /opt/openvpn-panel/static /opt/openvpn-panel/nginx
install -d -m 700 /var/lib/openvpn-panel
install -d -m 755 /var/lib/openvpn-panel/acme /var/lib/openvpn-panel/letsencrypt
install -d -m 700 /var/lib/openvpn-panel/backups
cp -a /root/antizapret/client.sh "/var/lib/openvpn-panel/backups/client.sh-$(date -u +%Y%m%dT%H%M%S%N)"
# IP webroot validation requires Certbot >=5.4; isolate it from the OS packages
# and keep its certificate store separate from an older system Certbot timer.
python3 -m venv /opt/openvpn-panel/certbot-venv
/opt/openvpn-panel/certbot-venv/bin/pip install 'certbot>=5.4,<6'
# Only update the client generator, preserving this server's routes and VPN settings.
install -m 755 "$HERE/../setup/root/antizapret/client.sh" /root/antizapret/client.sh
if [[ ! -f /root/antizapret/wl_routes.txt ]]; then
    install -m 600 "$HERE/../setup/root/antizapret/wl_routes.txt" /root/antizapret/wl_routes.txt
fi
install -m 644 "$HERE/app.py" "$HERE/pam_auth.py" "$HERE/../monitor.py" /opt/openvpn-panel/
install -m 644 "$HERE"/static/* /opt/openvpn-panel/static/
install -m 644 "$HERE/compose.yaml" /opt/openvpn-panel/compose.yaml
install -m 755 "$HERE/compose.sh" "$HERE/reload-nginx.sh" "$HERE/certbot.sh" /opt/openvpn-panel/
install -m 644 "$HERE/openvpn-panel.service" /etc/systemd/system/openvpn-panel.service
cat > /etc/pam.d/openvpn-panel <<'PAM'
# Verify the host's password and account policy; app permits only username root.
@include common-auth
@include common-account
PAM
chmod 644 /etc/pam.d/openvpn-panel
/opt/openvpn-panel/compose.sh pull nginx
SOCKET_GID=$(docker run --rm --entrypoint sh nginx:stable-alpine -c 'id -g nginx')
[[ "$SOCKET_GID" =~ ^[0-9]+$ ]] || { echo 'Cannot determine nginx socket group.' >&2; exit 1; }
printf 'PANEL_SOCKET_GID=%s\n' "$SOCKET_GID" > /etc/openvpn-panel.env
chmod 600 /etc/openvpn-panel.env
systemctl daemon-reload
systemctl enable --now openvpn-panel
systemctl restart openvpn-panel
for attempt in {1..30}; do
    [[ -S /run/openvpn-panel/backend.sock ]] && break
    sleep 1
done
[[ -S /run/openvpn-panel/backend.sock ]] || { echo 'Backend failed; inspect journalctl -u openvpn-panel.' >&2; exit 1; }
# Retain a working nginx configuration on repeat installs until TLS is ready.
if [[ ! -f /opt/openvpn-panel/nginx/panel.conf ]]; then
    cat > /opt/openvpn-panel/nginx/panel.conf <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 404; }
}
EOF
fi
/opt/openvpn-panel/compose.sh up -d nginx
# The public DNS A/AAAA records must reach this VPS on TCP port 80.
/opt/openvpn-panel/certbot.sh certonly --non-interactive --agree-tos --email "$EMAIL" \
    --webroot -w /var/lib/openvpn-panel/acme --cert-name "$DOMAIN" "${CERT_ARGS[@]}" \
    --deploy-hook /opt/openvpn-panel/reload-nginx.sh
cat > /opt/openvpn-panel/nginx/panel.conf.next <<EOF
limit_req_zone \$binary_remote_addr zone=panel_requests:10m rate=10r/s;
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 404; }
}
server {
    listen $PORT ssl;
    server_name $DOMAIN;
    server_tokens off;
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 8k;
    # Requests, including downloads and usage, go through the authenticated app.
    location / {
        limit_req zone=panel_requests burst=20 nodelay;
        limit_req_status 429;
        proxy_pass http://unix:/run/openvpn-panel/backend.sock;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 190s;
        proxy_buffering off;
        add_header Strict-Transport-Security "max-age=31536000" always;
    }
}
EOF
cp -a /opt/openvpn-panel/nginx/panel.conf /opt/openvpn-panel/nginx/panel.conf.previous
mv /opt/openvpn-panel/nginx/panel.conf.next /opt/openvpn-panel/nginx/panel.conf
if ! /opt/openvpn-panel/reload-nginx.sh; then
    mv /opt/openvpn-panel/nginx/panel.conf.previous /opt/openvpn-panel/nginx/panel.conf
    /opt/openvpn-panel/reload-nginx.sh
    exit 1
fi
install -m 644 "$HERE/openvpn-panel-renew.service" "$HERE/openvpn-panel-renew.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openvpn-panel-renew.timer
URL="https://$DOMAIN"
[[ "$PORT" == 443 ]] || URL+=":$PORT"
printf '\nPanel ready: %s\nSign in as root using this VPS password.\n' "$URL"
