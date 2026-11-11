"""Start: python3 -m oiforge [--port N] [--no-browser] [--browser] [--verbose]"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import webbrowser

from . import config, server
from .core import runner


def open_app_window(url: str, prefer_app: bool = True) -> None:
    br = config.find_browser() if prefer_app else None
    if br:
        profile = config.USER_DIR / "browser-profile"
        args = [br, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--window-size=1480,940", "--disable-features=Translate"]
        try:
            kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "posix":
                kw["start_new_session"] = True
            else:
                kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(args, **kw)
            return
        except OSError:
            pass
    webbrowser.open(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oiforge", description="OI Forge — lokalne studio do zadań olimpijskich")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("OIFORGE_PORT", config.DEFAULT_PORT)))
    ap.add_argument("--no-browser", action="store_true", help="nie otwieraj okna")
    ap.add_argument("--browser", action="store_true", help="otwórz w zwykłej przeglądarce zamiast osobnego okna")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-warmup", action="store_true", help="nie buduj prekompilowanego nagłówka przy starcie")
    a = ap.parse_args(argv)
    server.VERBOSE = a.verbose
    srv = server.make_server(a.host, a.port)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  ⚒  {config.APP_NAME} {config.VERSION} działa: {url}")
    print(f"     moduły: {len(server.TOOL_MODULES)} | platforma: {config.PLATFORM} | kompilator: {config.CXX or 'BRAK!'}")
    if server.TOOL_ERRORS:
        print(f"     ⚠ moduły z błędami: {', '.join(server.TOOL_ERRORS)}")
    print("     Zamknij: Ctrl+C\n")
    if not a.no_warmup:
        threading.Thread(target=runner.warm_up, daemon=True).start()
    if not a.no_browser:
        threading.Timer(0.4, open_app_window, args=(url, not a.browser)).start()
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nDo zobaczenia! 👋")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
