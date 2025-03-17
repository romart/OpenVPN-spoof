#!/bin/bash

INTERFACE=$(ip route | grep '^default' | awk '{print $5}')
HOST_IP=$(ip -brief address show "$INTERFACE" | awk '{print $3}' | awk -F/ '{print $1}')
if [[ -z "$INTERFACE" ]]; then
	echo "Default network interface not found!"
	exit 1
fi

./down.sh "$INTERFACE"

set -e

HERE="$(dirname "$(readlink -f "${0}")")"
cd "$HERE"

# Network parameters modification
sysctl -w net.ipv4.ip_forward=1
sysctl -w kernel.printk="3 4 1 3"
sysctl -w net.core.default_qdisc=fq
sysctl -w net.ipv4.tcp_congestion_control=bbr

exec 2>/dev/null

# filter
# INPUT connection tracking
iptables -w -A INPUT -m conntrack --ctstate INVALID -j DROP
# FORWARD connection tracking
iptables -w -A FORWARD -m conntrack --ctstate INVALID -j DROP
iptables -w -A FORWARD -m conntrack --ctstate RELATED,ESTABLISHED,DNAT -j ACCEPT
# ACCEPT all packets from VPN
iptables -w -A FORWARD -s 10.28.0.0/15 -j ACCEPT
# REJECT other packets
iptables -w -A FORWARD -j REJECT --reject-with icmp-port-unreachable
# OUTPUT connection tracking
iptables -w -A OUTPUT -m conntrack --ctstate INVALID -j DROP
#
# nat
# OpenVPN UDP port redirection for backup connections
iptables -w -t nat -A PREROUTING -i "$INTERFACE" -p udp --dport 80 -j REDIRECT --to-ports 50080
# MASQUERADE
iptables -w -t nat -A POSTROUTING -s 10.28.0.0/15 -o "$INTERFACE" -j SNAT --to-source $HOST_IP
#iptables -w -t nat -A POSTROUTING -s 10.28.0.0/15 -j MASQUERADE

