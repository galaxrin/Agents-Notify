#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
rm -rf build dist
python3 packaging/macos_setup.py py2app
mkdir -p build/dmg
cp -R dist/agent-watch-notify.app "build/dmg/Agents Notify.app"
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Agents Notify" -srcfolder build/dmg \
  -ov -format UDZO "dist/Agents-Notify-macOS-arm64.dmg"
