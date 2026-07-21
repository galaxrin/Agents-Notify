#!/bin/sh
set -eu
umask 077

: "${CODEX_WATCH_NTFY_URL:?Set CODEX_WATCH_NTFY_URL first}"

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bin_dir="$HOME/.local/bin"
config_dir="$HOME/.config/codex-watch-notify"
log_dir="$HOME/Library/Logs/codex-watch-notify"
log_file="$log_dir/stderr.log"
label="com.codex.watch-notify"
plist="$HOME/Library/LaunchAgents/$label.plist"

mkdir -p "$bin_dir" "$config_dir" "$log_dir" "$HOME/Library/LaunchAgents"
chmod 700 "$config_dir" "$log_dir"
messages_file="$config_dir/messages.json"
if [ ! -e "$messages_file" ]; then
  install -m 600 "$root_dir/scripts/messages.json" "$messages_file"
fi
touch "$log_file"
chmod 600 "$log_file"
install -m 755 "$root_dir/src/codex_watch_notify.py" "$bin_dir/codex-watch-notify"
printf 'CODEX_WATCH_NTFY_URL=%s\nCODEX_WATCH_NTFY_TOKEN=%s\n' \
  "$CODEX_WATCH_NTFY_URL" "${CODEX_WATCH_NTFY_TOKEN:-}" > "$config_dir/env"
chmod 600 "$config_dir/env"

python3 "$root_dir/scripts/render_plist.py" \
  "$root_dir/scripts/com.codex.watch-notify.plist.in" \
  "$plist" \
  "$bin_dir/codex-watch-notify" \
  "$CODEX_WATCH_NTFY_URL" \
  "${CODEX_WATCH_NTFY_TOKEN:-}" \
  "$log_file"
chmod 600 "$plist"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
printf 'Installed %s\n' "$label"
