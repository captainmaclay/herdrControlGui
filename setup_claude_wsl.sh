#!/bin/bash
# Скрипт подготовки Изолированного Claude GUI (Zero-Trust) внутри WSL
echo "=== Установка Chromium (как GUI враппера для Claude) ==="
sudo apt-get update
sudo apt-get install -y chromium-browser iptables

echo "=== Создание скрипта запуска ==="
cat << 'EOF' > ~/start_claude_isolated.sh
#!/bin/bash
# 1. Получаем IP-адрес хоста Windows (так как WSL использует виртуальную сеть)
WINDOWS_HOST_IP=$(ip route show default | awk '{print $3}')
PROXY_PORT=1015

echo "Host IP: $WINDOWS_HOST_IP"

# 2. Настраиваем жесткую тюрьму iptables
sudo iptables -F
sudo iptables -X

# Политика по умолчанию: БЛОКИРОВАТЬ ВСЁ (Входящие, Исходящие и Транзитные)
sudo iptables -P INPUT DROP
sudo iptables -P FORWARD DROP
sudo iptables -P OUTPUT DROP

# Разрешаем локальный loopback (внутри Linux)
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A OUTPUT -o lo -j ACCEPT

# Разрешаем исходящие ДО Proxy на Windows Host (Порт 1015)
sudo iptables -A OUTPUT -p tcp -d $WINDOWS_HOST_IP --dport $PROXY_PORT -j ACCEPT
# Разрешаем ответные пакеты ОТ Proxy
sudo iptables -A INPUT -p tcp -s $WINDOWS_HOST_IP --sport $PROXY_PORT -m state --state ESTABLISHED,RELATED -j ACCEPT

echo "Сетевая тюрьма iptables активна! Весь трафик кроме порта $PROXY_PORT физически заблокирован ядром."

# 3. Запускаем Claude AI GUI как системное окно (-app убирает вкладки браузера)
# Включаем принудительный прокси, включая DNS через SOCKS5
chromium-browser \
    --app="https://claude.ai" \
    --proxy-server="socks5://$WINDOWS_HOST_IP:$PROXY_PORT" \
    --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE $WINDOWS_HOST_IP" \
    >/dev/null 2>&1 &
EOF

chmod +x ~/start_claude_isolated.sh
echo "Готово! Скрипт запуска создан."
