#!/usr/bin/env python3
import plistlib
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 7:
        raise SystemExit("usage: render_plist.py TEMPLATE OUTPUT PROGRAM URL TOKEN LOG")
    template, output, program, url, token, log = sys.argv[1:]
    with Path(template).open("rb") as source:
        plist = plistlib.load(source)
    plist["ProgramArguments"] = [program]
    plist["EnvironmentVariables"] = {
        "CODEX_WATCH_NTFY_URL": url,
        "CODEX_WATCH_NTFY_TOKEN": token,
    }
    plist["StandardErrorPath"] = log
    with Path(output).open("wb") as destination:
        plistlib.dump(plist, destination, sort_keys=False)


if __name__ == "__main__":
    main()
