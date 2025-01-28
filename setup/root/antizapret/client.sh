#!/bin/bash
#
# Добавление/удаление клиента (* - только для OpenVPN)
#
# chmod +x client.sh && ./client.sh [1-7] [имя_клиента] [срок_действия*]
#
set -e

handle_error() {
	echo ""
	echo "Error occurred at line $1 while executing: $2"
	echo ""
	echo "$(lsb_release -d | awk -F'\t' '{print $2}') $(uname -r) $(date)"
	exit 1
}
trap 'handle_error $LINENO "$BASH_COMMAND"' ERR

getClientName(){
	CLIENT_NAME=$2
	if ! [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; then
		echo ""
		echo "Enter the client's name"
		echo "The client's name must consist of 1 to 32 alphanumeric characters, it may also include an underscore or a dash"
		until [[ $CLIENT_NAME =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; do
			read -rp "Client name: " -e CLIENT_NAME
		done
	fi
}

getClientCertExpire(){
	CLIENT_CERT_EXPIRE=$3
	if ! [[ "$CLIENT_CERT_EXPIRE" =~ ^[0-9]+$ ]] || (( CLIENT_CERT_EXPIRE <= 0 )) || (( CLIENT_CERT_EXPIRE > 3650 )); then
		echo ""
		echo "Enter a valid client certificate expiration period (1 to 3650 days)"
		until [[ "$CLIENT_CERT_EXPIRE" =~ ^[0-9]+$ ]] && (( CLIENT_CERT_EXPIRE > 0 )) && (( CLIENT_CERT_EXPIRE <= 3650 )); do
			read -rp "Certificate expiration days (1-3650): " -e -i 3650 CLIENT_CERT_EXPIRE
		done
	fi
}

getServerIP(){
	SERVER_IP=$(ip -4 addr | sed -ne 's|^.* inet \([^/]*\)/.* scope global.*$|\1|p' | awk '{print $1}' | head -1)
}

render() {
	local IFS=''
	local File="$1"
	while read -r line; do
		while [[ "$line" =~ (\$\{[a-zA-Z_][a-zA-Z_0-9]*\}) ]]; do
			local LHS=${BASH_REMATCH[1]}
			local RHS="$(eval echo "\"$LHS\"")"
			line=${line//$LHS/$RHS}
		done
		echo "$line"
	done < $File
}

addOpenVPN(){
	mkdir -p /etc/openvpn/easyrsa3
	cd /etc/openvpn/easyrsa3

	if [[ ! -f ./pki/ca.crt ]] || \
	   [[ ! -f ./pki/issued/antizapret-server.crt ]] || \
	   [[ ! -f ./pki/private/antizapret-server.key ]]; then
		rm -rf ./pki/
		/usr/share/easy-rsa/easyrsa init-pki
		EASYRSA_CA_EXPIRE=3650 /usr/share/easy-rsa/easyrsa --batch --req-cn="AntiZapret CA" build-ca nopass
		EASYRSA_CERT_EXPIRE=3650 /usr/share/easy-rsa/easyrsa --batch build-server-full "antizapret-server" nopass
		echo "Created new PKI and CA"
	fi

	if [[ ! -f ./pki/crl.pem ]]; then
		/usr/share/easy-rsa/easyrsa gen-crl
		echo "Created new CRL"
	fi

	if [[ ! -f /etc/openvpn/server/keys/ca.crt ]] || \
	   [[ ! -f /etc/openvpn/server/keys/antizapret-server.crt ]] || \
	   [[ ! -f /etc/openvpn/server/keys/antizapret-server.key ]] || \
	   [[ ! -f ./pki/crl.pem ]]; then
		cp ./pki/ca.crt /etc/openvpn/server/keys/ca.crt
		cp ./pki/issued/antizapret-server.crt /etc/openvpn/server/keys/antizapret-server.crt
		cp ./pki/private/antizapret-server.key /etc/openvpn/server/keys/antizapret-server.key
		cp ./pki/crl.pem /etc/openvpn/server/keys/crl.pem
	fi

	if [[ ! -f ./pki/issued/$CLIENT_NAME.crt ]] || \
	   [[ ! -f ./pki/private/$CLIENT_NAME.key ]]; then
		if [[ -z "$CLIENT_CERT_EXPIRE" ]]; then
			getClientCertExpire
		fi
		EASYRSA_CERT_EXPIRE=$CLIENT_CERT_EXPIRE /usr/share/easy-rsa/easyrsa --batch build-client-full $CLIENT_NAME nopass
		cp ./pki/issued/$CLIENT_NAME.crt /etc/openvpn/client/keys/$CLIENT_NAME.crt
		cp ./pki/private/$CLIENT_NAME.key /etc/openvpn/client/keys/$CLIENT_NAME.key
	else
		echo "A client with the specified name was already created, please choose another name"
	fi

	if [[ ! -f /etc/openvpn/client/keys/$CLIENT_NAME.crt ]] || \
	   [[ ! -f /etc/openvpn/client/keys/$CLIENT_NAME.key ]]; then
		cp ./pki/issued/$CLIENT_NAME.crt /etc/openvpn/client/keys/$CLIENT_NAME.crt
		cp ./pki/private/$CLIENT_NAME.key /etc/openvpn/client/keys/$CLIENT_NAME.key
	fi

	CA_CERT=$(grep -A 999 'BEGIN CERTIFICATE' -- "/etc/openvpn/server/keys/ca.crt")
	CLIENT_CERT=$(grep -A 999 'BEGIN CERTIFICATE' -- "/etc/openvpn/client/keys/$CLIENT_NAME.crt")
	CLIENT_KEY=$(cat -- "/etc/openvpn/client/keys/$CLIENT_NAME.key")
	if [[ ! "$CA_CERT" ]] || [[ ! "$CLIENT_CERT" ]] || [[ ! "$CLIENT_KEY" ]]; then
		echo "Can't load client keys!"
		exit 11
	fi

	FILE_NAME="${CLIENT_NAME#antizapret-}"
	FILE_NAME="${FILE_NAME#vpn-}"
	FILE_NAME="${FILE_NAME}-${SERVER_IP}"
	render "/etc/openvpn/client/templates/antizapret-udp.conf" > "/root/antizapret/client/openvpn/antizapret-udp/antizapret-$FILE_NAME-udp.ovpn"
	render "/etc/openvpn/client/templates/antizapret-tcp.conf" > "/root/antizapret/client/openvpn/antizapret-tcp/antizapret-$FILE_NAME-tcp.ovpn"
	render "/etc/openvpn/client/templates/antizapret.conf" > "/root/antizapret/client/openvpn/antizapret/antizapret-$FILE_NAME.ovpn"
	render "/etc/openvpn/client/templates/vpn-udp.conf" > "/root/antizapret/client/openvpn/vpn-udp/vpn-$FILE_NAME-udp.ovpn"
	render "/etc/openvpn/client/templates/vpn-tcp.conf" > "/root/antizapret/client/openvpn/vpn-tcp/vpn-$FILE_NAME-tcp.ovpn"
	render "/etc/openvpn/client/templates/vpn.conf" > "/root/antizapret/client/openvpn/vpn/vpn-$FILE_NAME.ovpn"

	echo "OpenVPN profile files for the client '$CLIENT_NAME' has been (re)created at '/root/antizapret/client/openvpn'"
}

deleteOpenVPN(){
	cd /etc/openvpn/easyrsa3

	/usr/share/easy-rsa/easyrsa --batch revoke $CLIENT_NAME
	if [[ $? -ne 0 ]]; then
		echo "Failed to revoke certificate for client '$CLIENT_NAME', please check if the client exists"
		exit 12
	fi

	/usr/share/easy-rsa/easyrsa gen-crl
	cp ./pki/crl.pem /etc/openvpn/server/keys/crl.pem
	if [[ $? -ne 0 ]]; then
		echo "Failed to update CRL"
		exit 13
	fi

	FILE_NAME="${CLIENT_NAME#antizapret-}"
	FILE_NAME="${FILE_NAME#vpn-}"

	rm -f /root/antizapret/client/openvpn/{antizapret,antizapret-udp,antizapret-tcp}/antizapret-$FILE_NAME-*.ovpn
	rm -f /root/antizapret/client/openvpn/{vpn,vpn-udp,vpn-tcp}/vpn-$FILE_NAME-*.ovpn
	rm -f /etc/openvpn/client/keys/$CLIENT_NAME.crt
	rm -f /etc/openvpn/client/keys/$CLIENT_NAME.key

	systemctl restart openvpn-server@*

	echo "OpenVPN client '$CLIENT_NAME' successfull deleted"
}

listOpenVPN(){
	echo ""
	echo "OpenVPN existing client names:"
	tail -n +2 /etc/openvpn/easyrsa3/pki/index.txt | grep "^V" | cut -d '=' -f 2 | sort -u
}

addWireGuard_AmneziaWG(){
	IPS=$(cat /etc/wireguard/ips)
	if [[ ! -f /etc/wireguard/key ]]; then
		PRIVATE_KEY=$(wg genkey)
		PUBLIC_KEY=$(echo "${PRIVATE_KEY}" | wg pubkey)
		echo "PRIVATE_KEY=${PRIVATE_KEY}
PUBLIC_KEY=${PUBLIC_KEY}" > /etc/wireguard/key
		render "/etc/wireguard/templates/antizapret.conf" > "/etc/wireguard/antizapret.conf"
		render "/etc/wireguard/templates/vpn.conf" > "/etc/wireguard/vpn.conf"
	else
		source /etc/wireguard/key
	fi

	CLIENT_BLOCK_ANTIZAPRET=$(sed -n "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/ {p; /^AllowedIPs/q}" /etc/wireguard/antizapret.conf)
	CLIENT_BLOCK_VPN=$(sed -n "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/ {p; /^AllowedIPs/q}" /etc/wireguard/vpn.conf)

	if [[ -n "$CLIENT_BLOCK_ANTIZAPRET" ]]; then
		CLIENT_PRIVATE_KEY=$(echo "$CLIENT_BLOCK_ANTIZAPRET" | grep '# PrivateKey =' | cut -d '=' -f 2- | sed 's/ //g')
		CLIENT_PUBLIC_KEY=$(echo "$CLIENT_BLOCK_ANTIZAPRET" | grep 'PublicKey =' | cut -d '=' -f 2- | sed 's/ //g')
		CLIENT_PRESHARED_KEY=$(echo "$CLIENT_BLOCK_ANTIZAPRET" | grep 'PresharedKey =' | cut -d '=' -f 2- | sed 's/ //g')
		echo "A client with the specified name was already created, please choose another name"
	elif [[ -n "$CLIENT_BLOCK_VPN" ]]; then
		CLIENT_PRIVATE_KEY=$(echo "$CLIENT_BLOCK_VPN" | grep '# PrivateKey =' | cut -d '=' -f 2- | sed 's/ //g')
		CLIENT_PUBLIC_KEY=$(echo "$CLIENT_BLOCK_VPN" | grep 'PublicKey =' | cut -d '=' -f 2- | sed 's/ //g')
		CLIENT_PRESHARED_KEY=$(echo "$CLIENT_BLOCK_VPN" | grep 'PresharedKey =' | cut -d '=' -f 2- | sed 's/ //g')
		echo "A client with the specified name was already created, please choose another name"
	else
		CLIENT_PRIVATE_KEY=$(wg genkey)
		CLIENT_PUBLIC_KEY=$(echo "${CLIENT_PRIVATE_KEY}" | wg pubkey)
		CLIENT_PRESHARED_KEY=$(wg genpsk)
	fi

	sed -i "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/d" /etc/wireguard/antizapret.conf
	sed -i "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/d" /etc/wireguard/vpn.conf

	sed -i '/^$/N;/^\n$/D' /etc/wireguard/antizapret.conf
	sed -i '/^$/N;/^\n$/D' /etc/wireguard/vpn.conf

	# AntiZapret

	BASE_CLIENT_IP=$(grep "^Address" /etc/wireguard/antizapret.conf | sed 's/.*= *//' | cut -d'.' -f1-3 | head -n 1)

	for i in {2..255}; do
		CLIENT_IP="${BASE_CLIENT_IP}.$i"
		if ! grep -q "$CLIENT_IP" /etc/wireguard/antizapret.conf; then
			break
		fi
		if [[ $i == 255 ]]; then
			echo "The WireGuard/AmneziaWG subnet can support only 253 clients"
			exit 21
		fi
	done

	FILE_NAME="${CLIENT_NAME#antizapret-}"
	FILE_NAME="${FILE_NAME#vpn-}"
	FILE_NAME="${FILE_NAME}-${SERVER_IP}"
	FILE_NAME="${FILE_NAME:0:18}"
	render "/etc/wireguard/templates/antizapret-client-wg.conf" > "/root/antizapret/client/wireguard/antizapret/antizapret-$FILE_NAME-wg.conf"
	render "/etc/wireguard/templates/antizapret-client-am.conf" > "/root/antizapret/client/amneziawg/antizapret/antizapret-$FILE_NAME-am.conf"

	echo "# Client = ${CLIENT_NAME}
# PrivateKey = ${CLIENT_PRIVATE_KEY}
[Peer]
PublicKey = ${CLIENT_PUBLIC_KEY}
PresharedKey = ${CLIENT_PRESHARED_KEY}
AllowedIPs = ${CLIENT_IP}/32
" >> "/etc/wireguard/antizapret.conf"

	if systemctl is-active --quiet wg-quick@antizapret; then
		wg syncconf antizapret <(wg-quick strip antizapret 2>/dev/null)
	fi

	# VPN

	BASE_CLIENT_IP=$(grep "^Address" /etc/wireguard/vpn.conf | sed 's/.*= *//' | cut -d'.' -f1-3 | head -n 1)

	for i in {2..255}; do
		CLIENT_IP="${BASE_CLIENT_IP}.$i"
		if ! grep -q "$CLIENT_IP" /etc/wireguard/vpn.conf; then
			break
		fi
		if [[ $i == 255 ]]; then
			echo "The WireGuard/AmneziaWG subnet can support only 253 clients"
			exit 22
		fi
	done

	FILE_NAME="${CLIENT_NAME#antizapret-}"
	FILE_NAME="${FILE_NAME#vpn-}"
	FILE_NAME="${FILE_NAME}-${SERVER_IP}"
	FILE_NAME="${FILE_NAME:0:25}"
	render "/etc/wireguard/templates/vpn-client-wg.conf" > "/root/antizapret/client/wireguard/vpn/vpn-$FILE_NAME-wg.conf"
	render "/etc/wireguard/templates/vpn-client-am.conf" > "/root/antizapret/client/amneziawg/vpn/vpn-$FILE_NAME-am.conf"

	echo "# Client = ${CLIENT_NAME}
# PrivateKey = ${CLIENT_PRIVATE_KEY}
[Peer]
PublicKey = ${CLIENT_PUBLIC_KEY}
PresharedKey = ${CLIENT_PRESHARED_KEY}
AllowedIPs = ${CLIENT_IP}/32
" >> "/etc/wireguard/vpn.conf"

	if systemctl is-active --quiet wg-quick@vpn; then
		wg syncconf vpn <(wg-quick strip vpn 2>/dev/null)
	fi

	echo "WireGuard/AmneziaWG profile files for the client '$CLIENT_NAME' has been (re)created at '/root/antizapret/client/wireguard' and '/root/antizapret/client/amneziawg'"
}

deleteWireGuard_AmneziaWG(){
	if ! grep -q "# Client = ${CLIENT_NAME}" "/etc/wireguard/antizapret.conf" && ! grep -q "# Client = ${CLIENT_NAME}" "/etc/wireguard/vpn.conf"; then
		echo "Failed to delete client '$CLIENT_NAME', please check if the client exists"
		exit 23
	fi

	sed -i "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/d" /etc/wireguard/antizapret.conf
	sed -i "/^# Client = ${CLIENT_NAME}\$/,/^AllowedIPs/d" /etc/wireguard/vpn.conf

	sed -i '/^$/N;/^\n$/D' /etc/wireguard/antizapret.conf
	sed -i '/^$/N;/^\n$/D' /etc/wireguard/vpn.conf

	FILE_NAME="${CLIENT_NAME#antizapret-}"
	FILE_NAME="${FILE_NAME#vpn-}"

	rm -f /root/antizapret/client/{wireguard,amneziawg}/antizapret/antizapret-"${FILE_NAME:0:18}"-*.conf
	rm -f /root/antizapret/client/{wireguard,amneziawg}/vpn/vpn-"${FILE_NAME:0:25}"-*.conf

	if systemctl is-active --quiet wg-quick@antizapret; then
		wg syncconf antizapret <(wg-quick strip antizapret 2>/dev/null)
	fi

	if systemctl is-active --quiet wg-quick@vpn; then
		wg syncconf vpn <(wg-quick strip vpn 2>/dev/null)
	fi

	echo "WireGuard/AmneziaWG client '$CLIENT_NAME' successfull deleted"
}

listWireGuard_AmneziaWG(){
	echo ""
	echo "WireGuard/AmneziaWG existing client names:"
	cat /etc/wireguard/antizapret.conf /etc/wireguard/vpn.conf | grep -E "^# Client" | cut -d '=' -f 2 | sed 's/ //g' | sort -u
}

recreate(){
	# OpenVPN
	if [[ -f /etc/openvpn/easyrsa3/pki/index.txt ]]; then
		tail -n +2 /etc/openvpn/easyrsa3/pki/index.txt | grep "^V" | cut -d '=' -f 2 | sort -u | while read -r CLIENT_NAME; do
			if [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; then
				addOpenVPN >/dev/null
				echo "OpenVPN profile files for the client '$CLIENT_NAME' has been recreated"
			else
				echo "Client name '$CLIENT_NAME' format is invalid"
			fi
		done
	else
		CLIENT_NAME="antizapret-client"
		CLIENT_CERT_EXPIRE=3650
		addOpenVPN >/dev/null
	fi

	# WireGuard/AmneziaWG
	if [[ -f /etc/wireguard/antizapret.conf && -f /etc/wireguard/vpn.conf ]]; then
		cat /etc/wireguard/antizapret.conf /etc/wireguard/vpn.conf | grep -E "^# Client" | cut -d '=' -f 2 | sed 's/ //g' | sort -u | while read -r CLIENT_NAME; do
			if [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; then
				addWireGuard_AmneziaWG >/dev/null
				echo "WireGuard/AmneziaWG profile files for the client '$CLIENT_NAME' has been recreated"
			else
				echo "Client name '$CLIENT_NAME' format is invalid"
			fi
		done
	else
		CLIENT_NAME="antizapret-client"
		addWireGuard_AmneziaWG >/dev/null
	fi
}

OPTION=$1
if ! [[ "$OPTION" =~ ^[1-7]$ ]]; then
	echo ""
	echo "Please choose an option:"
	echo "	1) OpenVPN - Add client"
	echo "	2) OpenVPN - Delete client"
	echo "	3) OpenVPN - List clients"
	echo "	4) WireGuard/AmneziaWG - Add client"
	echo "	5) WireGuard/AmneziaWG - Delete client"
	echo "	6) WireGuard/AmneziaWG - List clients"
	echo "	7) (Re)create client profile files"
	until [[ $OPTION =~ ^[1-7]$ ]]; do
		read -rp "Option choice [1-7]: " -e OPTION
	done
	echo ""
fi

case "$OPTION" in
	1)
		echo "OpenVPN - Add client"
		getServerIP
		getClientName
		addOpenVPN
		;;
	2)
		echo "OpenVPN - Delete client"
		listOpenVPN
		getClientName
		deleteOpenVPN
		;;
	3)
		echo "OpenVPN - List clients"
		listOpenVPN
		;;
	4)
		echo "WireGuard/AmneziaWG - Add client"
		getServerIP
		getClientName
		addWireGuard_AmneziaWG
		;;
	5)
		echo "WireGuard/AmneziaWG - Delete client"
		listWireGuard_AmneziaWG
		getClientName
		deleteWireGuard_AmneziaWG
		;;
	6)
		echo "WireGuard/AmneziaWG - List clients"
		listWireGuard_AmneziaWG
		;;
	7)
		echo "(Re)create client profile files"
		getServerIP
		recreate
		;;
esac