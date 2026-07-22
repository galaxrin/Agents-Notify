#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Python 3.10+ is required." >&2
  exit 1
fi

python3 -m pip install --user --upgrade https://github.com/galaxrin/Agents-Notify/archive/refs/heads/main.zip
python3 -m agent_watch_notify --install
