"""Run ASGI with a Windows shutdown event owned by the native launcher."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--shutdown-event')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')

    import uvicorn
    from config.settings import HOST

    server = uvicorn.Server(uvicorn.Config(
        'asgi:app', host=HOST, port=args.port,
        access_log=False, timeout_graceful_shutdown=10,
    ))

    if args.shutdown_event:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        event = kernel.OpenEventW(0x00100000, False, args.shutdown_event)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())

        def watch_launcher() -> None:
            # Avoid a blocking CRT stdin read while NumPy/native modules load.
            # An OS event neither locks Python streams nor needs an HTTP stop API.
            try:
                if kernel.WaitForSingleObject(event, 0xFFFFFFFF) == 0:
                    server.should_exit = True
            finally:
                kernel.CloseHandle(event)

        threading.Thread(target=watch_launcher, daemon=True).start()

    server.run()


if __name__ == '__main__':
    main()
