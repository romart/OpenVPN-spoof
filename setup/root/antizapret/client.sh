#!/bin/bash
#
# Добавление/удаление клиента (* - только для OpenVPN)
#
# chmod +x client.sh && ./client.sh [1-4] [имя_клиента] [срок_действия*]
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

getClientName() {
	if ! [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; then
		echo ""
		echo "Enter the client's name"
		echo "The client's name must consist of 1 to 32 alphanumeric characters, it may also include an underscore or a dash"
		until [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; do
			read -rp "Client name: " -e CLIENT_NAME
		done
	fi
}

getClientCertExpire(){
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
	   [[ ! -f ./pki/issued/server.crt ]] || \
	   [[ ! -f ./pki/private/server.key ]]; then
		rm -rf ./pki/
		/usr/share/easy-rsa/easyrsa init-pki
		EASYRSA_CA_EXPIRE=3650 /usr/share/easy-rsa/easyrsa --batch --req-cn="OpenVPN CA" build-ca nopass
		EASYRSA_CERT_EXPIRE=3650 /usr/share/easy-rsa/easyrsa --batch build-server-full "server" nopass
		echo "Created new PKI and CA"
	fi

	if [[ ! -f ./pki/crl.pem ]]; then
		EASYRSA_CRL_DAYS=3650 /usr/share/easy-rsa/easyrsa gen-crl
		echo "Created new CRL"
	fi

	if [[ ! -f /etc/openvpn/server/keys/ca.crt ]] || \
	   [[ ! -f /etc/openvpn/server/keys/server.crt ]] || \
	   [[ ! -f /etc/openvpn/server/keys/server.key ]] || \
	   [[ ! -f ./pki/crl.pem ]]; then
		cp ./pki/ca.crt /etc/openvpn/server/keys/ca.crt
		cp ./pki/issued/server.crt /etc/openvpn/server/keys/server.crt
		cp ./pki/private/server.key /etc/openvpn/server/keys/server.key
		cp ./pki/crl.pem /etc/openvpn/server/keys/crl.pem
	fi

	if [[ ! -f ./pki/issued/$CLIENT_NAME.crt ]] || \
	   [[ ! -f ./pki/private/$CLIENT_NAME.key ]]; then
		getClientCertExpire
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

	FILE_NAME="${CLIENT_NAME#}"
	FILE_NAME="${FILE_NAME#vpn-}"
	FILE_NAME="${FILE_NAME}-${SERVER_IP}"
	render "/etc/openvpn/client/templates/vpn-udp.conf" > "/root/antizapret/client/openvpn/vpn-udp/vpn-$FILE_NAME-udp.ovpn"

	echo "OpenVPN profile files for the client '$CLIENT_NAME' has been (re)created at '/root/antizapret/client/openvpn'"
}

deleteOpenVPN(){
	cd /etc/openvpn/easyrsa3

	/usr/share/easy-rsa/easyrsa --batch revoke $CLIENT_NAME
	if [[ $? -ne 0 ]]; then
		echo "Failed to revoke certificate for client '$CLIENT_NAME', please check if the client exists"
		exit 12
	fi

	EASYRSA_CRL_DAYS=3650 /usr/share/easy-rsa/easyrsa gen-crl
	cp ./pki/crl.pem /etc/openvpn/server/keys/crl.pem
	if [[ $? -ne 0 ]]; then
		echo "Failed to update CRL"
		exit 13
	fi

	FILE_NAME="${CLIENT_NAME#}"
	FILE_NAME="${FILE_NAME#vpn-}"
	FILE_NAME="${FILE_NAME}-${SERVER_IP}"

	rm -f /root/antizapret/client/openvpn/{antizapret,udp,tcp}/$FILE_NAME.ovpn
	rm -f /root/antizapret/client/openvpn/{vpn,vpn-udp,vpn-tcp}/vpn-$FILE_NAME.ovpn
	rm -f /etc/openvpn/client/keys/$CLIENT_NAME.crt
	rm -f /etc/openvpn/client/keys/$CLIENT_NAME.key

	echo "OpenVPN client '$CLIENT_NAME' successfull deleted"
}

listOpenVPN(){
	[[ -n "$CLIENT_NAME" ]] && return
	echo ""
	echo "OpenVPN existing client names:"
	ls /etc/openvpn/easyrsa3/pki/issued | sed 's/\.crt$//' | grep -v "^server$" | sort
}


recreate(){
	# OpenVPN
	if [[ -f /etc/openvpn/easyrsa3/pki/index.txt ]]; then
		ls /etc/openvpn/easyrsa3/pki/issued | sed 's/\.crt$//' | grep -v "^server$" | sort | while read -r CLIENT_NAME; do
			if [[ "$CLIENT_NAME" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]; then
				addOpenVPN >/dev/null
				echo "OpenVPN profile files for the client '$CLIENT_NAME' has been recreated"
			else
				echo "Client name '$CLIENT_NAME' format is invalid"
			fi
		done
	else
		CLIENT_NAME="client"
		CLIENT_CERT_EXPIRE=3650
		addOpenVPN >/dev/null
	fi
}

OPTION=$1
if ! [[ "$OPTION" =~ ^[1-4]$ ]]; then
	echo ""
	echo "Please choose an option:"
	echo "	1) OpenVPN - Add client"
	echo "	2) OpenVPN - Delete client"
	echo "	3) OpenVPN - List clients"
	echo "	4) (Re)create client profile files"
	until [[ "$OPTION" =~ ^[1-4]$ ]]; do
		read -rp "Option choice [1-4]: " -e OPTION
	done
	echo ""
fi

CLIENT_NAME=$2
CLIENT_CERT_EXPIRE=$3

case "$OPTION" in
	1)
		echo "OpenVPN - Add client $CLIENT_NAME $CLIENT_CERT_EXPIRE"
		getServerIP
		getClientName
		addOpenVPN
		;;
	2)
		echo "OpenVPN - Delete client $CLIENT_NAME"
		listOpenVPN
		getServerIP
		getClientName
		deleteOpenVPN
		;;
	3)
		echo "OpenVPN - List clients"
		listOpenVPN
		;;
	4)
		echo "(Re)create client profile files"
		getServerIP
		recreate
		;;
esac
