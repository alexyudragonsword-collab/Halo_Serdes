"""Desktop entry point for the packaged (PyInstaller / Nuitka) build.

Ships **without numba** (``HALO_NO_JIT`` is forced before any framework import,
so the pure-Python kernels are used and llvmlite/numba need not be bundled) and
opens the Dash app in a native **pywebview** window instead of a browser tab.

Flow: start the Dash server on a free localhost port in a daemon thread, wait
until it answers, then open a pywebview window pointed at it. If no webview
backend is available (e.g. headless), fall back to the system browser.

``--browser``   force the browser fallback instead of a native window.
``--selfcheck`` start the server, verify it answers, print OK, and exit
                (used by CI to smoke-test the frozen bundle).
"""

from __future__ import annotations

import os

# Must precede any halo_serdes import: the packaged build has no numba.
os.environ.setdefault("HALO_NO_JIT", "1")

import socket  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402

TITLE = "Halo_Serdes — behavioral SerDes studio"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_up(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.15)
    return False


def start_server() -> str:
    """Launch the Dash server in a daemon thread; return its URL once up."""
    from halo_serdes_gui.app import build_app  # absolute: works frozen too

    app = build_app()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    def _serve():
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_up(url):
        raise RuntimeError("Dash server failed to start")
    return url


def main() -> None:
    argv = sys.argv[1:]
    try:
        url = start_server()
    except Exception as exc:  # pragma: no cover - startup failure
        print(f"failed to start server: {exc}", file=sys.stderr)
        sys.exit(1)

    if "--selfcheck" in argv:
        print(f"OK {url}")
        return

    if "--browser" not in argv:
        try:
            import webview  # pywebview

            webview.create_window(TITLE, url, width=1500, height=920,
                                  min_size=(1100, 720))
            webview.start()  # blocks on the main thread until the window closes
            return
        except Exception as exc:
            print(f"pywebview unavailable ({exc}); opening browser instead",
                  file=sys.stderr)

    import webbrowser

    webbrowser.open(url)
    print(f"Halo_Serdes GUI running at {url}  (Ctrl+C to quit)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
