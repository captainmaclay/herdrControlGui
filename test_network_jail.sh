#!/bin/bash
# Проверка жесткой изоляции сети (Airgap Namespace Test)
PROXY_PORT=1015

pkill -f "socat.*/tmp/test_proxy.sock" 2>/dev/null
rm -f /tmp/test_proxy.sock
socat UNIX-LISTEN:/tmp/test_proxy.sock,fork,mode=777 TCP:127.0.0.1:$PROXY_PORT 2>/dev/null &
HOST_SOCAT_PID=$!

cat << 'INNER_TEST' > /tmp/inner_test.sh
#!/bin/bash
ip link set lo up
socat TCP-LISTEN:1015,fork UNIX-CLIENT:/tmp/test_proxy.sock 2>/dev/null &
INNER_SOCAT_PID=$!
sleep 0.5 

echo "----------------------------------------"
echo "=== ПРОВЕРКА 1: Прямой выход (Ping 8.8.8.8) ==="
ping -c 1 -W 2 8.8.8.8 2>&1 | grep -q "Network is unreachable"
if [ $? -eq 0 ]; then
    echo -e "\e[32m[УСПЕХ]\e[0m Пинг отклонен. В камере нет сетевых карт."
else
    echo -e "\e[31m[ПРОВАЛ]\e[0m Пинг прошел или дал другую ошибку! Изоляция нарушена."
fi
echo "----------------------------------------"

echo "=== ПРОВЕРКА 2: Прямое HTTP-соединение ==="
curl -I --connect-timeout 2 https://claude.ai >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo -e "\e[32m[УСПЕХ]\e[0m Соединение отклонено (Ошибка выхода). Тюрьма работает."
else
    echo -e "\e[31m[ПРОВАЛ]\e[0m Соединение установлено! Изоляция нарушена."
fi
echo "----------------------------------------"

echo "=== ПРОВЕРКА 3: Доступ через SOCKS5 UNIX-мост ==="
curl --socks5-hostname "127.0.0.1:1015" -s --connect-timeout 5 http://ifconfig.me/ip > /tmp/test_ip.txt
if [ -s /tmp/test_ip.txt ]; then
    echo -e "\e[32m[УСПЕХ]\e[0m SOCKS5 прокси ответил! Ваш IP через туннель: $(cat /tmp/test_ip.txt)"
else
    echo -e "\e[31m[ПРОВАЛ]\e[0m Не удалось выйти через прокси."
fi
rm -f /tmp/test_ip.txt
echo "----------------------------------------"
echo "✅ ТЕСТ ЗАВЕРШЕН."
kill $INNER_SOCAT_PID 2>/dev/null
INNER_TEST

chmod +x /tmp/inner_test.sh
sudo -E unshare -n /tmp/inner_test.sh

kill $HOST_SOCAT_PID 2>/dev/null
rm -f /tmp/test_proxy.sock
