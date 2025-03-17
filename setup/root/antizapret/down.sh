#!/bin/bash

exec 2>/dev/null

if [[ -z "$1" ]]; then
	INTERFACE=$(ip route | grep '^default' | awk '{print $5}')
else
	INTERFACE=$1
fi

HOST_IP=$(ip -brief address show "$INTERFACE" | awk '{print $3}' | awk -F/ '{print $1}')

# filter
# INPUT connection tracking
iptables -w -D INPUT -m conntrack --ctstate INVALID -j DROP
# FORWARD connection tracking
iptables -w -D FORWARD -m conntrack --ctstate INVALID -j DROP
iptables -w -D FORWARD -m conntrack --ctstate RELATED,ESTABLISHED,DNAT -j ACCEPT
# ACCEPT all packets from VPN
iptables -w -D FORWARD -s 10.28.0.0/15 -j ACCEPT
iptables -w -D FORWARD -s 172.28.0.0/15 -j ACCEPT
# REJECT other packets
iptables -w -D FORWARD -j REJECT --reject-with icmp-port-unreachable
# OUTPUT connection tracking
iptables -w -D OUTPUT -m conntrack --ctstate INVALID -j DROP
#
# nat
# OpenVPN UDP port redirection for backup connections
iptables -w -t nat -D PREROUTING -i "$INTERFACE" -p udp --dport 80 -j REDIRECT --to-ports 50080
# MASQUERADE
iptables -w -t nat -D POSTROUTING -s 10.28.0.0/15 -o "$INTERFACE" -j SNAT --to-source $HOST_IP
iptables -w -t nat -D POSTROUTING -s 172.28.0.0/15 -o "$INTERFACE" -j SNAT --to-source $HOST_IP


