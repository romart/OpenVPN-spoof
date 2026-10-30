#!/bin/bash
# Configure OpenVPN 2.6 DCO: y enables, n disables.
# Exit 2 means a module is ready for an installed kernel; reboot is required.
set -e

handle_error() {
    echo "Error occurred at line $1 while executing: $2" >&2
    exit 1
}
trap 'handle_error $LINENO "$BASH_COMMAND"' ERR

VPN_CONFIG=${VPN_CONFIG:-/etc/openvpn/server/vpn-udp.conf}
MODULES_DIR=${MODULES_DIR:-/lib/modules}
KERNEL=$(uname -r)
VERSION=$(openvpn --version | head -n 1 | awk '{print $2}')
if [[ ! "$VERSION" =~ ^2\.6([.]|$) ]]; then
    echo 'OpenVPN version 2.6 is required.' >&2
    exit 1
fi

DCO=${1:-}
if [[ "$DCO" != [yn] ]]; then
    until [[ "$DCO" == [yn] ]]; do
        read -rp 'Turn on OpenVPN DCO? [y/n]: ' -e -i y DCO
    done
fi

has_candidate() {
    local candidate
    candidate=$(apt-cache policy "$1" | awk '/Candidate:/ {print $2; exit}')
    [[ -n "$candidate" && "$candidate" != '(none)' ]]
}

configure_dco() {
    sed -i '/^[[:space:]]*\(data-ciphers\|disable-dco\)\([[:space:]]\|$\)/d' "$VPN_CONFIG"
    if [[ "$DCO" == y ]]; then
        printf '\ndata-ciphers "AES-128-GCM:AES-256-GCM:CHACHA20-POLY1305"\n' >> "$VPN_CONFIG"
    else
        printf '\ndata-ciphers "AES-128-GCM:AES-256-GCM:CHACHA20-POLY1305:AES-128-CBC:AES-192-CBC:AES-256-CBC"\ndisable-dco\n' >> "$VPN_CONFIG"
    fi
}

restart_active_server() {
    if systemctl is-active --quiet openvpn-server@vpn-udp.service; then
        systemctl restart openvpn-server@vpn-udp.service
    fi
}

if [[ "$DCO" == n ]]; then
    configure_dco
    restart_active_server
    echo 'OpenVPN DCO disabled.'
    exit 0
fi

export LC_ALL=C
apt-get update
# A module or build tree already present for this kernel does not require
# downloading old headers which may have disappeared from the apt repositories.
if ! modinfo -k "$KERNEL" ovpn_dco_v2 >/dev/null 2>&1 && [[ ! -f "$MODULES_DIR/$KERNEL/build/Makefile" ]]; then
    if has_candidate "linux-headers-$KERNEL"; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-headers-$KERNEL"
    else
        OS=$(lsb_release -si | tr '[:upper:]' '[:lower:]')
        if [[ "$OS" != debian ]]; then
            echo "Headers for $KERNEL are unavailable. Install a supported kernel and its matching headers, reboot, then rerun this script." >&2
            exit 1
        fi
        case "$KERNEL" in
            *-cloud-amd64) FLAVOR=cloud-amd64 ;;
            *-cloud-arm64) FLAVOR=cloud-arm64 ;;
            *-amd64) FLAVOR=amd64 ;;
            *-arm64) FLAVOR=arm64 ;;
            *-686-pae) FLAVOR=686-pae ;;
            *-686) FLAVOR=686 ;;
            *) echo "Cannot select matching kernel packages for $KERNEL. Install its headers manually." >&2; exit 1 ;;
        esac
        for package in "linux-image-$FLAVOR" "linux-headers-$FLAVOR"; do
            if ! has_candidate "$package"; then
                echo "No apt candidate for $package. Check your Debian repositories." >&2
                exit 1
            fi
        done
        echo "Headers for running kernel $KERNEL are unavailable; installing the latest $FLAVOR kernel and headers."
        DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-image-$FLAVOR" "linux-headers-$FLAVOR"
    fi
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y openvpn-dco-dkms

if modinfo -k "$KERNEL" ovpn_dco_v2 >/dev/null 2>&1; then
    # Do not unload the module: it may be serving existing VPN connections.
    modprobe ovpn_dco_v2
    configure_dco
    restart_active_server
    echo 'OpenVPN DCO enabled.'
    exit 0
fi

# DKMS can build for an installed newer kernel without headers for the old,
# currently running kernel. OpenVPN will use DCO after booting the new kernel.
for directory in "$MODULES_DIR"/*; do
    [[ -d "$directory" && "${directory##*/}" != "$KERNEL" ]] || continue
    if modinfo -k "${directory##*/}" ovpn_dco_v2 >/dev/null 2>&1; then
        configure_dco
        echo "DCO is ready for kernel ${directory##*/}. Reboot into that kernel to enable it."
        exit 2
    fi
done

echo "DCO module is unavailable for installed kernels. Check 'dkms status' and the DKMS build logs." >&2
exit 1
