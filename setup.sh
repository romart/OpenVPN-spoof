#!/bin/bash
#
# Скрипт для установки на своём сервере AntiZapret VPN и обычного VPN
#
# https://github.com/GubernievS/AntiZapret-VPN
#

#
# Проверка прав root
if [[ "$EUID" -ne 0 ]]; then
	echo "Error: You need to run this as root!"
	exit 1
fi

cd /root

#
# Проверка на OpenVZ и LXC
if [[ "$(systemd-detect-virt)" == "openvz" || "$(systemd-detect-virt)" == "lxc" ]]; then
	echo "Error: OpenVZ and LXC are not supported!"
	exit 2
fi

#
# Проверка версии системы
OS=$(lsb_release -si | tr '[:upper:]' '[:lower:]')
VERSION=$(lsb_release -rs | cut -d '.' -f1)

if [[ $OS == "debian" ]]; then
	if [[ $VERSION -lt 11 ]]; then
		echo "Error: Your version of Debian is not supported!"
		exit 3
	fi
elif [[ $OS == "ubuntu" ]]; then
	if [[ $VERSION -lt 22 ]]; then
		echo "Error: Your version of Ubuntu is not supported!"
		exit 4
	fi
elif [[ $OS != "debian" ]] && [[ $OS != "ubuntu" ]]; then
	echo "Error: Your version of Linux is not supported!"
	exit 5
fi

#
# Проверка свободного места (минимум 2Гб)
if [[ $(df --output=avail / | tail -n 1) -lt $((2 * 1024 * 1024)) ]]; then
	echo "Error: Low disk space! You need 2GB of free space!"
	exit 6
fi

echo ""
echo -e "\e[1;32mInstalling VPN...\e[0m"
echo "OpenVPN"

#
# Спрашиваем о настройках
echo ""
echo "Choose a version of the anti-censorship patch for OpenVPN (UDP only):"
echo "    0) None       - Do not install the anti-censorship patch, or remove it if already installed"
echo "    1) Strong     - Recommended by default"
echo "    2) Error-free - Use it if the Strong patch causes a connection error, recommended for Mikrotik routers"
until [[ "$OPENVPN_PATCH" =~ ^[0-2]$ ]]; do
	read -rp "Version choice [0-2]: " -e -i 1 OPENVPN_PATCH
done
echo ""
echo "OpenVPN DCO lowers CPU load, saves battery on mobile devices, boosts data speeds, and only supports AES-128-GCM, AES-256-GCM and CHACHA20-POLY1305 encryption protocols"
until [[ "$OPENVPN_DCO" =~ (y|n) ]]; do
	read -rp "Turn on OpenVPN DCO? [y/n]: " -e -i y OPENVPN_DCO
done
echo ""
echo "Default IP address range:      10.28.0.0/14"
echo "Alternative IP address range: 172.28.0.0/14"
until [[ "$ALTERNATIVE_IP" =~ (y|n) ]]; do
	read -rp "Use alternative range of IP addresses? [y/n]: " -e -i n ALTERNATIVE_IP
done
echo ""
until [[ "$OPENVPN_80_443_UDP" =~ (y|n) ]]; do
	read -rp "Use UDP ports 80 and 443 as backup for OpenVPN connections? [y/n]: " -e -i y OPENVPN_80_443_UDP
done
echo ""
until [[ "$OPENVPN_DUPLICATE" =~ (y|n) ]]; do
	read -rp "Allow multiple clients connecting to OpenVPN using the same profile file (*.ovpn)? [y/n]: " -e -i y OPENVPN_DUPLICATE
done
echo ""
until [[ "$OPENVPN_LOG" =~ (y|n) ]]; do
	read -rp "Enable detailed logs in OpenVPN? [y/n]: " -e -i n OPENVPN_LOG
done
echo ""

#
# Ожидание пока выполняется apt-get
while pidof apt-get &>/dev/null; do
	echo "Waiting for apt-get to finish...";
	sleep 5;
done

echo "Preparing for installation, please wait..."

#
# Удаление или перемещение файлов и папок при обновлении
systemctl stop openvpn-generate-keys &>/dev/null
systemctl disable openvpn-generate-keys &>/dev/null
systemctl stop openvpn-server@antizapret &>/dev/null
systemctl disable openvpn-server@antizapret &>/dev/null
systemctl stop dnsmap &>/dev/null
systemctl disable dnsmap &>/dev/null
systemctl stop ferm &>/dev/null
systemctl disable ferm &>/dev/null
systemctl stop openvpn-server@antizapret-no-cipher &>/dev/null
systemctl disable openvpn-server@antizapret-no-cipher &>/dev/null

rm -f /etc/sysctl.d/10-conntrack.conf
rm -f /etc/sysctl.d/20-network.conf
rm -f /etc/sysctl.d/99-antizapret.conf
rm -f /etc/systemd/network/eth.network
rm -f /etc/systemd/network/host.network
rm -f /etc/systemd/system/openvpn-generate-keys.service
rm -f /etc/systemd/system/dnsmap.service
#rm -f /etc/apt/sources.list.d/amnezia*
#rm -f /usr/share/keyrings/amnezia.gpg
rm -f /root/upgrade.sh
rm -f /root/generate.sh
rm -f /root/Enable-OpenVPN-DCO.sh
rm -f /root/upgrade-openvpn.sh
rm -f /root/create-swap.sh
rm -f /root/disable-openvpn-dco.sh
rm -f /root/enable-openvpn-dco.sh
rm -f /root/patch-openvpn.sh
rm -f /root/add-client.sh
rm -f /root/delete-client.sh
rm -f /root/*.ovpn
rm -f /root/*.conf

if [[ -d "/root/easy-rsa-ipsec/easyrsa3/pki" ]]; then
	mkdir -p /root/easyrsa3
	mv -f /root/easy-rsa-ipsec/easyrsa3/pki /root/easyrsa3/pki &>/dev/null
fi
mv -f /root/antizapret/custom.sh /root/antizapret/custom-doall.sh &>/dev/null

rm -rf /root/vpn
rm -rf /root/easy-rsa-ipsec
rm -rf /root/.gnupg
rm -rf /root/dnsmap
rm -rf /root/openvpn
rm -rf /etc/ferm

apt-get purge -y python3-dnslib &>/dev/null
apt-get purge -y gnupg2 &>/dev/null
apt-get purge -y ferm &>/dev/null
apt-get purge -y libpam0g-dev &>/dev/null
#apt-get purge -y amneziawg &>/dev/null

#
# Остановим и выключим службы
systemctl stop antizapret &>/dev/null
systemctl stop openvpn-server@vpn-udp &>/dev/null

systemctl disable antizapret &>/dev/null
systemctl disable openvpn-server@vpn-udp &>/dev/null

#
# Удаляем старые файлы openvpn
rm -rf /etc/openvpn/server/*
rm -rf /etc/openvpn/client/*

#
# Удалим скомпилированный патченный OpenVPN
make -C /usr/local/src/openvpn uninstall &>/dev/null
rm -rf /usr/local/src/openvpn

#
# Обработка ошибок
handle_error() {
	echo ""
	echo -e "\e[1;31mError occurred at line $1 while executing: $2\e[0m"
	echo ""
	echo "$(lsb_release -d | awk -F'\t' '{print $2}') $(uname -r) $(date)"
	exit 7
}
trap 'handle_error $LINENO "$BASH_COMMAND"' ERR

#
# Завершим выполнение скрипта при ошибке
set -e

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y curl gpg procps

#
# Отключим IPv6 на время установки
if [[ -f /proc/sys/net/ipv6/conf/all/disable_ipv6 ]]; then
	sysctl -w net.ipv6.conf.all.disable_ipv6=1
fi

#
# Добавляем репозитории
mkdir -p /etc/apt/keyrings

#
# OpenVPN
curl -fsSL https://swupdate.openvpn.net/repos/repo-public.gpg | gpg --dearmor > /etc/apt/keyrings/openvpn-repo-public.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/openvpn-repo-public.gpg] https://build.openvpn.net/debian/openvpn/release/2.6 $(lsb_release -cs) main" > /etc/apt/sources.list.d/openvpn-aptrepo.list

#
# Добавим репозиторий Debian Backports
if [[ $OS == "debian" ]]; then
	echo "deb http://deb.debian.org/debian $(lsb_release -cs)-backports main" > /etc/apt/sources.list.d/backports.list
fi

#
# Обновляем систему
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

#
# Ставим необходимые пакеты
DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y git openvpn iptables easy-rsa gawk idn sipcalc python3-pip diffutils dnsutils socat lua-cqueues
apt-get autoremove -y
apt-get autoclean
PIP_BREAK_SYSTEM_PACKAGES=1 pip3 install --force-reinstall dnslib

#
# Клонируем репозиторий
rm -rf /tmp/antizapret
git clone https://github.com/GubernievS/AntiZapret-VPN.git /tmp/antizapret

#
# Сохраняем пользовательские настройки и пользовательские обработчики custom*.sh
mv -f /root/antizapret/config/* /tmp/antizapret/setup/root/antizapret/config &>/dev/null || true
mv -f /root/antizapret/custom*.sh /tmp/antizapret/setup/root/antizapret &>/dev/null || true

#
# Восстанавливаем из бэкапа пользователей vpn
mv -f /root/easyrsa3 /tmp/antizapret/setup/etc/openvpn &>/dev/null || true

#
# Выставляем разрешения
find /tmp/antizapret -type f -exec chmod 644 {} +
find /tmp/antizapret -type d -exec chmod 755 {} +
find /tmp/antizapret -type f \( -name "*.sh" -o -name "*.py" \) -execdir chmod +x {} +

# Копируем нужное, удаляем не нужное
find /tmp/antizapret -name '.gitkeep' -delete
rm -rf /root/antizapret
cp -r /tmp/antizapret/setup/* /
rm -rf /tmp/antizapret

#
# Используем альтернативные диапазоны ip-адресов
# 10.28.0.0/14 => 172.28.0.0/14
if [[ "$ALTERNATIVE_IP" == "y" ]]; then
	sed -i 's/10\./172\./g' /etc/openvpn/server/*.conf
fi

#
# Не используем резервные порты 80 и 443 для OpenVPN UDP
if [[ "$OPENVPN_80_443_UDP" == "n" ]]; then
	sed -i '/ \(80\|443\) udp/s/^/#/' /etc/openvpn/client/templates/*.conf
	sed -i '/udp.* \(80\|443\) /s/^/#/' /root/antizapret/up.sh
fi

#
# Запрещаем несколько одновременных подключений к OpenVPN для одного клиента
if [[ "$OPENVPN_DUPLICATE" == "n" ]]; then
	sed -i '/duplicate-cn/s/^/#/' /etc/openvpn/server/*.conf
fi

#
# Включим подробные логи в OpenVPN
if [[ "$OPENVPN_LOG" == "y" ]]; then
	sed -i 's/^#//' /etc/openvpn/server/*.conf
fi

#
#
# Настраиваем сервера OpenVPN и WireGuard/AmneziaWG для первого запуска
# Пересоздаем для всех существующих пользователей файлы подключений
# Если пользователей нет, то создаем новых пользователей 'antizapret-client' для OpenVPN и WireGuard/AmneziaWG
/root/antizapret/client.sh 7

systemctl enable antizapret
systemctl enable openvpn-server@vpn-udp

#
# Отключим ненужные службы
systemctl disable ufw &>/dev/null || true
systemctl disable firewalld &>/dev/null || true

ERRORS=""

if [[ "$OPENVPN_PATCH" != "0" ]]; then
	if ! /root/antizapret/patch-openvpn.sh "$OPENVPN_PATCH"; then
		ERRORS+="\n\e[1;31mAnti-censorship patch for OpenVPN has not installed!\e[0m Please run '/root/antizapret/patch-openvpn.sh' after rebooting\n"
	fi
fi

if [[ "$OPENVPN_DCO" == "y" ]]; then
	if ! /root/antizapret/openvpn-dco.sh "y"; then
		ERRORS+="\n\e[1;31mOpenVPN DCO has not turn on!\e[0m Please run '/root/antizapret/openvpn-dco.sh' after rebooting\n"
	fi
fi

#
# Если есть ошибки, выводим их
if [[ -n "$ERRORS" ]]; then
	echo -e "$ERRORS"
fi

#
# Сохраняем настройки
echo "OPENVPN_PATCH=${OPENVPN_PATCH}
OPENVPN_DCO=${OPENVPN_DCO}
VPN_DNS=${VPN_DNS}
ALTERNATIVE_IP=${ALTERNATIVE_IP}
OPENVPN_80_443_UDP=${OPENVPN_80_443_UDP}
OPENVPN_DUPLICATE=${OPENVPN_DUPLICATE}
OPENVPN_LOG=${OPENVPN_LOG}
SETUP_DATE=$(date +"%d.%m.%Y %H:%M:%S %z")" > /root/antizapret/setup

#
# Создадим файл подкачки размером 512 Мб если его нет
if [[ -z "$(swapon --show)" ]]; then
	set +e
	SWAPFILE="/swapfile"
	SWAPSIZE=512
	dd if=/dev/zero of=$SWAPFILE bs=1M count=$SWAPSIZE
	chmod 600 "$SWAPFILE"
	mkswap "$SWAPFILE"
	swapon "$SWAPFILE"
	echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
fi

echo ""
echo -e "\e[1;32mVPN successful installation!\e[0m"
echo "Rebooting..."

#
# Перезагружаем
reboot
