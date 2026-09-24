#!/bin/bash
# Скрипт подготовки Изолированного Официального Claude (WSL Airgap Namespace)

# Для воздушного зазора нам понадобится утилита socat, поставим её, если нет
sudo apt-get update && sudo apt-get install -y socat

echo "=== Создание скрипта запуска и сетевой тюрьмы (РЕДАКЦИЯ 5: ВОЗДУШНЫЙ ЗАЗОР) ==="
cat << 'EOF' > ~/start_claude_isolated.sh
#!/bin/bash
# Запоминаем текущего пользователя и графические переменные для проброса внутрь изолятора
ORIG_USER=$USER
export DISPLAY=${DISPLAY:-:0}
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
PROXY_PORT=1015

echo "=== НАСТРОЙКА АБСОЛЮТНОЙ ИЗОЛЯЦИИ (AIRGAP) ==="
# Очищаем старые мосты
pkill -f "socat.*/tmp/claude_proxy.sock" 2>/dev/null
rm -f /tmp/claude_proxy.sock

# 1. Запускаем ретранслятор на хосте. Он забирает данные из изолированного сокета и отдает в ваш SOCKS5 в Windows.
echo "[1/3] Создание Unix-моста для прокси..."
socat UNIX-LISTEN:/tmp/claude_proxy.sock,fork,mode=777 TCP:127.0.0.1:$PROXY_PORT &
HOST_SOCAT_PID=$!

# 2. Создаем сценарий, который будет выполнен ВНУТРИ полностью изолированного сетевого пространства
cat << 'INNER' > /tmp/run_claude_jail.sh
#!/bin/bash
# Поднимаем внутреннюю петлю (чтобы 127.0.0.1 работал внутри капсулы)
ip link set lo up

# Запускаем внутренний ретранслятор (обманываем Клода, что прокси находится локально)
socat TCP-LISTEN:1015,fork UNIX-CLIENT:/tmp/claude_proxy.sock &
INNER_SOCAT_PID=$!

sleep 0.5
echo "[3/3] СЕТЕВАЯ ТЮРЬМА АКТИВНА! Ядро не имеет сетевых интерфейсов. Запуск Claude..."
# Передаем выполнение обратно от Рута к вашему пользователю, сохраняя среду (sudo -E)
sudo -E -u $ORIG_USER claude-desktop --no-sandbox --ozone-platform-hint=auto --proxy-server="socks5://127.0.0.1:1015" --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1" >/dev/null 2>&1

# Когда Клод закрывается - убираем за собой
kill $INNER_SOCAT_PID 2>/dev/null
INNER
chmod +x /tmp/run_claude_jail.sh

echo "[2/3] Отключаем ядро Linux от сети Windows (создаем пустой Network Namespace)..."
# unshare -n создает "сферический конь в вакууме": пространство БЕЗ сетевых карт и связи с внешним миром
sudo -E unshare -n /tmp/run_claude_jail.sh

kill $HOST_SOCAT_PID 2>/dev/null
rm -f /tmp/claude_proxy.sock
echo "Claude закрыт. Тюрьма расформирована."
EOF

chmod +x ~/start_claude_isolated.sh
echo "Готово! Установлен Непробиваемый Воздушный Зазор (Airgap)."
