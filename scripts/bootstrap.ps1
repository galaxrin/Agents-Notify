$ErrorActionPreference = "Stop"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python 3.10+ is required: https://www.python.org/downloads/"
}

& $python.Source -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10+ is required."
}

& $python.Source -m pip install --user --upgrade "https://github.com/galaxrin/Agents-Notify/archive/refs/heads/main.zip"
if ($LASTEXITCODE -ne 0) {
    throw "Installation failed."
}

& $python.Source -m agent_watch_notify --install
if ($LASTEXITCODE -ne 0) {
    throw "Configuration failed."
}
