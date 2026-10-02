#!/bin/bash
# Universal wrapper generator for WSL2 GUI / Electron apps
# Usage: ./generate_wrapper.sh <binary_name> <app_class_name> <config_dir_name> [output_path]
# Example: ./generate_wrapper.sh claude-desktop claude Claude /usr/local/bin/claude-gui

set -euo pipefail

APP_BIN="${1:-claude-desktop}"
APP_CLASS="${2:-claude}"
APP_CONFIG="${3:-Claude}"
OUT_PATH="${4:-/usr/local/bin/${APP_BIN}-gui}"

cat << 'EOF' > "$OUT_PATH"
#!/bin/bash
# Auto-generated GUI launcher for WSL2 / WSLg

# 1. Environment & Proxies
source /etc/profile.d/herdr_claude_env.sh 2>/dev/null || true

export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

# Ensure /run/user/<uid> directory exists
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
    mkdir -m 700 -p "$XDG_RUNTIME_DIR" 2>/dev/null || sudo mkdir -m 700 -p "$XDG_RUNTIME_DIR" 2>/dev/null
    sudo chown "$(id -u):$(id -g)" "$XDG_RUNTIME_DIR" 2>/dev/null || true
fi

# Ensure user DBus session is running for single-instance IPC
if [ ! -e "$XDG_RUNTIME_DIR/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
    dbus-daemon --session --fork --address="unix:path=$XDG_RUNTIME_DIR/bus" 2>/dev/null || true
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

# 2. Window Activation (Single Instance Focus)
if command -v xdotool >/dev/null 2>&1; then
    VISIBLE_WIN=$(xdotool search --onlyvisible --class "__APP_CLASS__" 2>/dev/null | head -n 1)
    if [ -n "$VISIBLE_WIN" ]; then
        xdotool windowactivate "$VISIBLE_WIN" 2>/dev/null
        exit 0
    fi
fi

# 3. Clean Stale Processes & Locks
killall -9 "__APP_BIN__" chrome_crashpad_handler 2>/dev/null || true
rm -f "$HOME/.config/__APP_CONFIG__/Singleton"* 2>/dev/null || true

if ! command -v "__APP_BIN__" >/dev/null 2>&1; then
    echo "Error: __APP_BIN__ not found in PATH" >&2
    exit 1
fi

# 4. Background Launch with Safe GPU Flags
"__APP_BIN__" --no-sandbox --ozone-platform=x11 --password-store=basic "$@" &
MAIN_PID=$!

# 5. Lifecycle Window Monitor
if command -v xdotool >/dev/null 2>&1; then
    WIN_ID=""
    for i in $(seq 1 30); do
        if ! kill -0 "$MAIN_PID" 2>/dev/null; then
            exit 1
        fi
        WIN_ID=$(xdotool search --onlyvisible --class "__APP_CLASS__" 2>/dev/null | head -n 1)
        if [ -n "$WIN_ID" ]; then
            break
        fi
        sleep 0.5
    done

    if [ -n "$WIN_ID" ]; then
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep 2
            CURRENT_WIN=$(xdotool search --onlyvisible --class "__APP_CLASS__" 2>/dev/null | head -n 1)
            if [ -z "$CURRENT_WIN" ]; then
                # Window closed: clean shutdown
                kill -TERM "$MAIN_PID" 2>/dev/null || true
                sleep 1
                killall -9 "__APP_BIN__" chrome_crashpad_handler 2>/dev/null || true
                rm -f "$HOME/.config/__APP_CONFIG__/Singleton"* 2>/dev/null || true
                break
            fi
        done
        exit 0
    fi
fi

wait "$MAIN_PID"
EOF

# Substitute placeholders
sed -i "s/__APP_BIN__/${APP_BIN}/g" "$OUT_PATH"
sed -i "s/__APP_CLASS__/${APP_CLASS}/g" "$OUT_PATH"
sed -i "s/__APP_CONFIG__/${APP_CONFIG}/g" "$OUT_PATH"
chmod +x "$OUT_PATH"

echo "Wrapper generated at $OUT_PATH"
