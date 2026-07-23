from setuptools import setup

setup(
    app=[{"script": "agent_watch_notify/desktop.py", "dest_base": "Agents Notify"}],
    name="Agents Notify",
    data_files=[
        ("agent_watch_notify", ["agent_watch_notify/config.html"]),
        ("agent_watch_notify/assets", [
            "agent_watch_notify/assets/galaxrin-agents-notify-logo.png"
        ]),
    ],
    options={"py2app": {
        "argv_emulation": False,
        "packages": ["agent_watch_notify", "certifi", "PIL", "pystray", "webview"],
        "plist": {
            "CFBundleDisplayName": "Agents Notify",
            "CFBundleIdentifier": "com.galaxrin.agents-notify",
            "CFBundleShortVersionString": "1.0.0",
            "LSMinimumSystemVersion": "12.0",
        },
    }},
)
