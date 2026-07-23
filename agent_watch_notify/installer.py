"""Cross-platform installer for agent-watch-notify daemon."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_watch_notify._config import read_env_file, write_env_file
from agent_watch_notify.events import guess_agent_name
from agent_watch_notify.notifier import DEFAULT_MESSAGES
from agent_watch_notify.watcher import discover_session_dirs

_LABEL = "com.agent.watch-notify"
_TASK_NAME = "agent-watch-notify"
_CONFIG_DIR_NAME = "agent-watch-notify"
_STATE_DIR_NAME = "agent-watch-notify"


def _config_dir() -> Path:
    return Path.home() / ".config" / _CONFIG_DIR_NAME


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / _STATE_DIR_NAME


def _bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def _log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _CONFIG_DIR_NAME / "stderr.log"
    return _state_dir() / "stderr.log"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _wrapper_path() -> Path:
    if sys.platform == "win32":
        return _bin_dir() / "agent-watch-notify.cmd"
    return _bin_dir() / "agent-watch-notify"


def _env_path() -> Path:
    return _config_dir() / "env"


def _find_scripts_dir() -> Path | None:
    """Find the scripts/ directory relative to the package or repo root."""
    # Try relative to this file (repo checkout / editable install)
    repo = Path(__file__).resolve().parent.parent / "scripts"
    if (repo / "messages.json").is_file():
        return repo
    return None


def _install_message_files() -> None:
    """Copy default message templates to config dir (skip existing)."""
    dest = _config_dir() / "messages.json"
    scripts = _find_scripts_dir()
    if scripts is None:
        # pip install: write built-in defaults
        if not dest.exists():
            dest.write_text(
                json.dumps(DEFAULT_MESSAGES, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        for path in discover_session_dirs():
            name = guess_agent_name(path)
            if not name:
                continue
            agent_dest = _config_dir() / f"messages.{name}.json"
            if agent_dest.exists():
                continue
            display_name = name[0].upper() + name[1:]
            messages = DEFAULT_MESSAGES | {
                "display_name": display_name,
                "title_separator": "·",
                "complete_title": "已完成",
                "complete_body": f"{display_name} 任务已结束",
                "approval_title": "等待审核",
                "approval_body": f"请回到 {display_name} 处理",
            }
            agent_dest.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return
    # Repo checkout: copy all message files
    if not dest.exists():
        shutil.copy2(scripts / "messages.json", dest)
    for f in scripts.glob("messages.*.json"):
        d = _config_dir() / f.name
        if not d.exists():
            shutil.copy2(f, d)


def _write_env(url: str, token: str) -> None:
    """Write the env config file."""
    write_env_file(_env_path(), {
        "AGENT_WATCH_NTFY_URL": url,
        "AGENT_WATCH_NTFY_TOKEN": token,
        "AGENT_WATCH_SESSIONS_DIR": "",
        "AGENT_WATCH_APPROVAL_DELAY": "10",
        "AGENT_WATCH_POLL_INTERVAL": "1",
        "CODEX_WATCH_NTFY_URL": url,
        "CODEX_WATCH_NTFY_TOKEN": token,
    })


def _register_daemon() -> None:
    """Register platform-specific auto-start daemon."""
    if sys.platform == "darwin":
        _register_launchd()
    elif sys.platform == "win32":
        _register_task_scheduler()
    else:
        print("自动启动注册暂不支持此平台，请手动配置 systemd 或 cron。")


def _register_launchd() -> None:
    """Create plist and load via launchctl."""
    import plistlib

    plist = {
        "Label": _LABEL,
        "ProgramArguments": [str(_wrapper_path())],
        "EnvironmentVariables": {
            "AGENT_WATCH_NTFY_URL": "",
            "AGENT_WATCH_NTFY_TOKEN": "",
            "AGENT_WATCH_SESSIONS_DIR": "",
            "AGENT_WATCH_APPROVAL_DELAY": "10",
            "AGENT_WATCH_POLL_INTERVAL": "1",
        },
        "RunAtLoad": True,
        "StandardErrorPath": str(_log_path()),
    }
    # Read env to populate EnvironmentVariables
    env = _read_env()
    for key in plist["EnvironmentVariables"]:
        plist["EnvironmentVariables"][key] = env.get(key, "")

    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as f:
        plistlib.dump(plist, f, sort_keys=False)
    os.chmod(plist_path, 0o600)

    # Unload existing, then load new
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LABEL}"],
                   capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                   check=True)
    print(f"已注册 LaunchAgent: {_LABEL}")


def _register_task_scheduler() -> None:
    """Register Windows Task Scheduler task."""
    wrapper = _wrapper_path()
    try:
        subprocess.run(
            ["schtasks", "/create", "/tn", _TASK_NAME,
             "/tr", f'"{wrapper}"', "/sc", "ONLOGON", "/rl", "LIMITED", "/f"],
            check=True, capture_output=True,
        )
        print(f"已注册计划任务: {_TASK_NAME}")
    except subprocess.CalledProcessError as e:
        print(f"注册计划任务失败: {e}")
        print(f'手动注册: schtasks /create /tn "{_TASK_NAME}" /tr "{wrapper}" /sc ONLOGON /rl LIMITED /f')


def _unregister_daemon() -> None:
    """Unregister platform-specific daemon."""
    if sys.platform == "darwin":
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LABEL}"],
                       capture_output=True)
        plist = _plist_path()
        if plist.exists():
            plist.unlink()
        print(f"已注销 LaunchAgent: {_LABEL}")
    elif sys.platform == "win32":
        subprocess.run(["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
                       capture_output=True)
        print(f"已注销计划任务: {_TASK_NAME}")


def _read_env() -> dict[str, str]:
    return read_env_file(_env_path())


def do_install(url: str, token: str) -> None:
    """Full install: write config, copy messages, register daemon."""
    _config_dir().mkdir(parents=True, exist_ok=True)
    _state_dir().mkdir(parents=True, exist_ok=True)
    _bin_dir().mkdir(parents=True, exist_ok=True)
    # Log directory
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.touch()

    _write_env(url, token)
    _install_message_files()

    # Generate wrapper script for daemon
    _write_wrapper()

    _register_daemon()
    print(f"配置目录: {_config_dir()}")
    print(f"日志文件: {log}")
    print("运行 'agent-watch-notify --test' 验证通知")


def do_uninstall() -> None:
    """Full uninstall: unregister daemon, remove config and state."""
    _unregister_daemon()

    removed = []
    for p in [_env_path(), _config_dir() / "messages.json", _wrapper_path()]:
        if p.exists():
            p.unlink()
            removed.append(str(p))
    # Remove per-agent message files
    cfg = _config_dir()
    if cfg.exists():
        for f in cfg.glob("messages.*.json"):
            f.unlink()
            removed.append(str(f))
    # Remove config dir if empty
    if cfg.exists() and not any(cfg.iterdir()):
        cfg.rmdir()
        removed.append(str(cfg))
    # Remove log
    log = _log_path()
    if log.exists():
        log.unlink()
        removed.append(str(log))
    # Remove log dir if empty and distinct from config/state
    log_dir = log.parent
    if log_dir.exists() and log_dir != cfg and not any(log_dir.iterdir()):
        log_dir.rmdir()
        removed.append(str(log_dir))

    if removed:
        print("已删除:")
        for p in removed:
            print(f"  {p}")
    print("卸载完成")


def _write_wrapper() -> None:
    """Generate the daemon wrapper script."""
    wrapper = _wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        content = (
            "@echo off\r\n"
            'for /f "usebackq tokens=1,* delims==" %%a in ("%USERPROFILE%\\.config\\agent-watch-notify\\env") do (\r\n'
            '    set "%%a=%%b"\r\n'
            ")\r\n"
            'python -m agent_watch_notify >> "%USERPROFILE%\\.local\\state\\agent-watch-notify\\stderr.log" 2>&1\r\n'
        )
        wrapper.write_text(content, encoding="ascii")
    else:
        env_file = _env_path()
        content = (
            "#!/bin/sh\n"
            f'set -a && . "{env_file}" && set +a\n'
            'exec python3 -m agent_watch_notify "$@"\n'
        )
        wrapper.write_text(content, encoding="utf-8")
        os.chmod(wrapper, 0o755)
