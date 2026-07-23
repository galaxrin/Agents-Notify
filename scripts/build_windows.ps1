$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

pyinstaller --noconfirm --clean --onefile --windowed `
  --name "Agents-Notify" `
  --add-data "agent_watch_notify/config.html;agent_watch_notify" `
  --add-data "agent_watch_notify/assets;agent_watch_notify/assets" `
  --hidden-import "pystray._win32" `
  --hidden-import "webview.platforms.edgechromium" `
  agent_watch_notify/desktop.py
