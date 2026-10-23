"""Black-box checks of the compiled Windows launcher and its real ASGI child."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'SOPweave.exe'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def get_health(port: int) -> dict:
    with OPENER.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
        return json.load(response)['data']


def logs(data: Path) -> str:
    return '\n'.join(path.read_text(encoding='utf-8') for path in (data / 'logs').glob('launcher-*.log'))


@unittest.skipUnless(os.name == 'nt' and EXE.is_file(), 'Build SOPweave.exe with scripts/build_launcher.ps1 first')
class NativeLauncherTests(unittest.TestCase):
    @contextmanager
    def running(self, directory: Path, *, app: Path = ROOT, data: str | None = None):
        port = free_port()
        environment = {**os.environ, 'SOPWEAVE_DATA_DIR': data or str(directory / 'data')}
        # Moving the application's source does not require copying a venv.
        environment['PATH'] = str(Path(os.sys.executable).parent) + os.pathsep + environment.get('PATH', '')
        executable = app / 'SOPweave.exe'
        process = subprocess.Popen(
            [str(executable), '--headless', '--port', str(port)], cwd=directory,
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        data_path = Path(environment['SOPWEAVE_DATA_DIR'])
        if not data_path.is_absolute():
            data_path = app / data_path
        try:
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(f'Launcher exited {process.returncode}\n{logs(data_path)}')
                try:
                    health = get_health(port)
                    break
                except (OSError, ValueError):
                    time.sleep(.15)
            else:
                self.fail('Launcher startup timed out\n' + logs(data_path))
            yield process, port, health, environment
        finally:
            if process.poll() is None:
                subprocess.run([str(executable), '--stop', '--port', str(port)], env=environment,
                               cwd=directory, timeout=10, check=False)
                try:
                    process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    def test_real_start_repeated_launch_and_graceful_stop(self):
        with tempfile.TemporaryDirectory(prefix='SOP launcher 用户目录 ') as folder:
            directory = Path(folder)
            with self.running(directory) as (process, port, health, environment):
                self.assertEqual(health['product'], 'sopweave')
                with OPENER.open(f'http://127.0.0.1:{port}/', timeout=5) as response:
                    self.assertIn('<title>SOPweave</title>', response.read().decode('utf-8'))
                duplicate = subprocess.run([str(EXE), '--headless', '--port', str(port)],
                                           cwd=directory, env=environment, timeout=15)
                self.assertEqual(duplicate.returncode, 0)
                self.assertEqual(get_health(port)['pid'], health['pid'])
                stopped = subprocess.run([str(EXE), '--stop', '--port', str(port)],
                                         cwd=directory, env=environment, timeout=10)
                self.assertEqual(stopped.returncode, 0)
                self.assertEqual(process.wait(timeout=35), 0)
                self.assertIn('Application shutdown complete', logs(directory / 'data'))
                with self.assertRaises(OSError):
                    get_health(port)

    def test_forced_launcher_exit_cleans_its_owned_service(self):
        import psutil

        with tempfile.TemporaryDirectory(prefix='SOP launcher ownership ') as folder:
            with self.running(Path(folder)) as (process, port, health, _):
                child = psutil.Process(health['pid'])
                process.kill()
                process.wait(timeout=10)
                child.wait(timeout=10)
                with self.assertRaises(OSError):
                    get_health(port)

    def test_foreign_service_is_not_opened_or_terminated(self):
        class ForeignHealth(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'success': True, 'data': {
                    'product': 'sopweave', 'status': 'ready', 'protocol': 1,
                    'installation': '0' * 64, 'pid': os.getpid(),
                }}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory(prefix='SOP launcher conflict ') as folder:
            server = ThreadingHTTPServer(('127.0.0.1', 0), ForeignHealth)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                data = Path(folder) / 'data'
                result = subprocess.run([str(EXE), '--headless', '--port', str(port)],
                                        env={**os.environ, 'SOPWEAVE_DATA_DIR': str(data)}, timeout=15)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(get_health(port)['pid'], os.getpid())
                self.assertIn('已被其他程序', logs(data))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_missing_files_and_missing_python_fail_without_installing(self):
        with tempfile.TemporaryDirectory(prefix='SOP launcher missing ') as folder:
            app = Path(folder) / 'app'
            app.mkdir()
            data = Path(folder) / 'data'
            environment = {**os.environ, 'SOPWEAVE_DATA_DIR': str(data), 'PATH': ''}
            command = [str(EXE), '--headless', '--app-dir', str(app), '--port', str(free_port())]
            missing = subprocess.run(command, env=environment, timeout=15)
            self.assertEqual(missing.returncode, 1)
            self.assertIn('应用文件不完整', logs(data))
            for name in ('asgi.py', 'scripts/serve.py', 'scripts/check_runtime.py', 'requirements.lock', 'frontend/dist/index.html'):
                path = app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('', encoding='utf-8')
            missing = subprocess.run(command, env=environment, timeout=15)
            self.assertEqual(missing.returncode, 1)
            self.assertIn('未找到可用的 Python', logs(data))
            self.assertFalse((app / '.runtime').exists())

    def test_relative_application_discovery_survives_a_chinese_directory_move(self):
        with tempfile.TemporaryDirectory(prefix='SOP launcher 路径迁移 ') as folder:
            base = Path(folder)
            app = base / '原始 软件'
            app.mkdir()
            for name in ('agent_runtime', 'agent_profiles', 'config', 'routes', 'services', 'storage', 'tools', 'skills'):
                shutil.copytree(ROOT / name, app / name, ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('app.py', 'asgi.py', 'requirements.lock', 'SOPweave.exe', 'scripts/serve.py', 'scripts/check_runtime.py'):
                destination = app / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, destination)
            (app / 'frontend/dist').mkdir(parents=True)
            (app / 'frontend/dist/index.html').write_text('<html><title>SOPweave</title></html>', encoding='utf-8')
            with self.running(base, app=app, data='data') as (_, _, before, _):
                marker = app / 'data' / 'preserved.txt'
                marker.write_text('keep', encoding='utf-8')
            moved = base / '移动后的 软件'
            app.rename(moved)
            with self.running(base, app=moved, data='data') as (_, _, after, _):
                self.assertNotEqual(before['installation'], after['installation'])
                self.assertEqual((moved / 'data/preserved.txt').read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
