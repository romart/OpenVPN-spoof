# AntiZapret VPN + обычный VPN

Скрипт для автоматического развертывания AntiZapret VPN + обычный VPN на своем сервере

Через AntiZapret VPN работают только:
- Заблокированные сайты из единого реестра РФ, список автоматически обновляется раз в 6 часов
- Сайты к которым ограничивается доступ без судебного решения (например youtube.com)
- Сайты ограничивающие доступ из России (например intel.com, chatgpt.com)

Список сайтов для AntiZapret VPN предзаполнен (include-hosts-dist.txt)\
Доступно ручное добавление своих сайтов (include-hosts-custom.txt)

Все остальные сайты работают через вашего провайдера с максимальной доступной вам скоростью

**Внимание!** Для правильной работы AntiZapret VPN нужно [отключить DNS в браузере](https://www.google.ru/search?q=отключить+DNS+в+браузере)

Через обычный VPN работают все сайты, доступные с вашего сервера

Ваш сервер должен находиться за пределами России, в противном случае разблокировка сайтов не гарантируется

AntiZapret VPN (antizapret-\*.ovpn) и обычный VPN (vpn-\*.ovpn) работают через [OpenVPN Connect](https://openvpn.net/client)\
Поддерживается подключение по UDP и TCP, или только по UDP (\*-udp.ovpn) или только по TCP (\*-tcp.ovpn)\
Используются порты 50080 и 50443 и резервные порты 80 и 443 для обхода блокировок по портам

OpenVPN позволяет нескольким клиентам использовать один и тот же файл подключения (\*.ovpn) для подключения к серверу

По умолчанию используется Cloudflare DNS, опционально можно включить AdGuard DNS - он используется для блокировки рекламы, отслеживающих модулей и фишинга

За основу взяты [эти исходники](https://bitbucket.org/anticensority/antizapret-vpn-container/src/master) разработанные ValdikSS

Протестировано на Ubuntu 22.04/24.04 и Debian 11/12 - Процессор: 1 core Память: 1 Gb Хранилище: 10 Gb
***
### Установка:
1. Устанавливать на чистую Ubuntu 22.04/24.04 или Debian 11/12 (рекомендуется Ubuntu 24.04)
2. В терминале под root выполнить
```sh
apt update && apt install -y git && git clone https://github.com/GubernievS/AntiZapret-VPN.git tmp && chmod +x tmp/setup.sh && tmp/setup.sh
```
3. Дождаться перезагрузки сервера и скопировать файлы подключений (*.ovpn) с сервера из папки /root

Опционально можно:
1. Установить патч для обхода блокировки протокола OpenVPN
2. Включить DCO
3. Включить AdGuard DNS для: AntiZapret/обычного VPN (только при установке)
4. Использовать альтернативные диапазоны IP-адресов: 172... вместо 10... (только при установке)
5. Добавить клиентов (только после установки)
***
Установить патч для обхода блокировки протокола OpenVPN (работает только для UDP соединений)
```sh
./patch-openvpn.sh
```
***
Включить [DCO](https://community.openvpn.net/openvpn/wiki/DataChannelOffload) (он заметно снижает нагрузку на CPU сервера и клиента - это экономит аккумулятор мобильных устройств и увеличивает скорость передачи данных через OpenVPN)
```sh
./enable-openvpn-dco.sh
```
Выключить DCO
```sh
./disable-openvpn-dco.sh
```
***
Добавить нового клиента
```sh
./add-client.sh [имя_клиента]
```
Удалить клиента
```sh
./delete-client.sh [имя_клиента]
```
После добавления нового клиента скопируйте новые файлы подключений (*.ovpn) с сервера из папки /root
***
Добавить свои сайты в список антизапрета (include-hosts-custom.txt)
```sh
nano /root/antizapret/config/include-hosts-custom.txt
```
Добавлять нужно только домены, например:
>subdomain.example.com\
example.com\
com

После этого нужно обновить список антизапрета
```sh
/root/antizapret/doall.sh
```
***
Обсуждение скрипта на [ntc.party](https://ntc.party/t/9270)
***
Инструкция по настройке на роутерах [Keenetic](./Keenetic.md) и [TP-Link](./TP-Link.md)
***
### Где купить сервер?
Хостинги в Европе для VPN принимающие рубли: [vdsina.com](https://www.vdsina.com/?partner=9br77jaat2) с бонусом 10% и [aeza.net](https://aeza.net/?ref=529527) с бонусом 15% (если пополнение сделать в течении 24 часов с момента регистрации)
***
### FAQ
1. Как переустановить сервер и сохранить работоспособность ранее созданных файлов подключений (\*.ovpn)
> Скачать с сервера папку /root/easyrsa3\
Переустановить сервер\
Обратно на сервер закачать папку /root/easyrsa3\
Запустить скрипт установки

2. Как посмотреть активные соединения?

> Посмотреть активные соединения можно в логах \*-status.log в папке /etc/openvpn/server/logs\
Логи обновляются каждые 30 секунд
***
[![donate](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://pay.cloudtips.ru/p/b3f20611)
