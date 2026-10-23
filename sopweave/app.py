import atexit
import hashlib
import os

from flask import Flask, jsonify, request, send_from_directory

from agent_runtime.definitions.agent_registry import AgentRegistry
from config.settings import (
    BASE_DIR,
    DATA_DIR,
    FRONTEND_DIST_DIR,
    HOST,
    PORT,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from routes import register_blueprints
from storage.db import get_db, init_db
from storage.model_store import ModelStore
from storage.session_store import SessionStore
from storage.tmp_db import cleanup_stale


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
        template_folder=str(TEMPLATES_DIR),
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    agent_registry = AgentRegistry()
    agent_registry.list_enabled()
    app.config["AGENT_REGISTRY"] = agent_registry
    init_db(app)
    from services.model_usage import get_usage_recorder
    get_usage_recorder()
    from config.settings import DB_PATH, USER_SKILLS_ENABLED
    from services.skills.library import SkillLibrary
    from agent_runtime.definitions.skill_registry import SkillRegistry
    app.config['USER_SKILLS_ENABLED'] = USER_SKILLS_ENABLED
    if USER_SKILLS_ENABLED:
        library = SkillLibrary(DB_PATH)
        app.config['SKILL_LIBRARY'] = library
        app.config['SKILL_REGISTRY'] = SkillRegistry(agent_registry=agent_registry, library=library)
    with app.app_context():
        from storage.workbench_store import WorkbenchStore
        WorkbenchStore(get_db()).recover_interrupted()
    register_blueprints(app)
    # 阶段 4：启动时清理超龄的临时运行目录（防崩溃残留堆积，单机离线适用）
    try:
        removed = cleanup_stale()
        if removed:
            print(f"[cleanup] 已清理 {removed} 个超龄临时运行目录", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] 清理临时运行目录失败（忽略）: {e}", flush=True)
    from storage.task_store import TaskStore
    TaskStore(app.config.get("TASK_DB_PATH", DATA_DIR / "tasks.sqlite3")).recover_interrupted()

    # ------------------------------------------------------------------
    # 工作台总览 D1：软件打开会话计时器（打开即计时、关闭即结束）。
    # 时间口径 v1.2 定稿：app_sessions 计时器，纯离线、从安装起累计。
    # - 启动：插一行 open 会话（内部先 _close_stale 收尾上次强杀残留）
    # - 退出：atexit 写 end_at（Ctrl+C / 正常退出 / 崩溃均触发；
    #   use_reloader=False 单进程无干扰）
    # - 心跳：before_request 每 ≥60s 更新 last_seen_at（防强杀丢失）
    # ------------------------------------------------------------------
    _session_store = SessionStore()
    _session = _session_store.start()
    app.config["APP_SESSION_ID"] = _session["id"]

    def _close_session() -> None:
        _session_store.close(app.config.get("APP_SESSION_ID", ""))

    atexit.register(_close_session)

    @app.before_request
    def _session_heartbeat() -> None:
        _session_store.heartbeat(app.config.get("APP_SESSION_ID", ""))

    # ------------------------------------------------------------------
    # 新前端（React SPA）页面路由：所有页面路由统一返回 SPA 入口，
    # 前端内部分路由（/、/projects、/workspace、/settings）。
    # Vite 产物（frontend/dist）由 FRONTEND_DIST_DIR 托管。
    # 旧版模板已删除（阶段 7：新架构建成后旧版完全删除）。
    # ------------------------------------------------------------------

    def _serve_spa():
        """返回 SPA 入口；产物缺失时给出构建提示。"""
        if not (FRONTEND_DIST_DIR / "index.html").exists():
            return jsonify({
                "success": False,
                "message": "前端产物未构建：请先执行 cd frontend && npm run build",
            }), 503
        return send_from_directory(str(FRONTEND_DIST_DIR), "index.html")

    @app.get('/api/health')
    def application_health():
        # An opaque identity prevents the launcher from opening a different
        # installation or data profile that happens to occupy the same port.
        from config.brand import APP_SLUG
        paths = '\n'.join(
            os.path.normcase(str(path.resolve())).rstrip('\\/')
            for path in (BASE_DIR, DATA_DIR)
        )
        ready = (FRONTEND_DIST_DIR / 'index.html').is_file()
        return jsonify(success=ready, data={
            'product': APP_SLUG,
            'protocol': 1,
            'status': 'ready' if ready else 'missing_frontend',
            'installation': hashlib.sha256(paths.encode('utf-8')).hexdigest(),
            'pid': os.getpid(),
        }), 200 if ready else 503

    @app.get("/")
    def dashboard():
        return _serve_spa()

    @app.get("/projects")
    def projects():
        return _serve_spa()

    @app.get("/workspace")
    def workspace():
        return _serve_spa()

    @app.get('/projects/<project_id>')
    @app.get('/settings/models')
    @app.get('/settings/agent')
    @app.get('/apps/<app_kind>')
    @app.get('/sops')
    def workbench_page(**_params):
        return _serve_spa()

    # SPA 静态资源：frontend/dist/assets/*（base="/static/app/" 已映射，但此处兜底）
    @app.get("/static/app/<path:filename>")
    def spa_static(filename: str):
        return send_from_directory(str(FRONTEND_DIST_DIR), filename)

    @app.post("/api/browse-folder")
    def browse_folder():
        """选择工作目录（阶段 7.9.7.3 恢复服务端方式）。

        旧框架同款方案：服务端弹 Windows 原生文件夹对话框，返回绝对路径。
        但弃 PowerShell 冷启动（1-3s），改用 Python ctypes 直调
        SHBrowseForFolderW（毫秒级启动），在后台线程运行避免阻塞 Flask worker。
        【适用场景】服务器=本机且有交互桌面（本应用默认部署方式）。
        【注意】现代 Chrome(120+) 已移除 File.path，webkitdirectory 拿不到
        绝对路径，故必须走服务端。
        """
        import threading
        import ctypes
        from ctypes import wintypes

        result: dict = {}

        def _win_browse(title: str = "选择工作目录") -> str | None:
            """ctypes SHBrowseForFolderW：Windows 经典文件夹对话框，返回绝对路径。"""
            BIF_RETURNONLYFSDIRS = 0x00000001
            BIF_NEWDIALOGSTYLE = 0x00000040

            class BROWSEINFO(ctypes.Structure):
                _fields_ = [
                    ("hwndOwner", wintypes.HWND),
                    ("pidlRoot", ctypes.c_void_p),
                    ("pszDisplayName", wintypes.LPWSTR),
                    ("lpszTitle", wintypes.LPCWSTR),
                    ("ulFlags", wintypes.UINT),
                    ("lpfn", ctypes.c_void_p),
                    ("lParam", wintypes.LPARAM),
                    ("iImage", ctypes.c_int),
                ]

            display = ctypes.create_unicode_buffer(260)
            bi = BROWSEINFO()
            bi.hwndOwner = None
            bi.pidlRoot = None
            # ctypes.cast：create_unicode_buffer 是数组类型，LPWSTR 字段需要显式转换
            bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
            bi.lpszTitle = title
            bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
            bi.lpfn = None
            bi.lParam = 0

            pidl = ctypes.windll.shell32.SHBrowseForFolderW(ctypes.byref(bi))
            if not pidl:
                return None
            buf = ctypes.create_unicode_buffer(260)
            ok = ctypes.windll.shell32.SHGetPathFromIDListW(pidl, buf)
            ctypes.windll.ole32.CoTaskMemFree(pidl)
            return buf.value if ok else None

        def _run():
            try:
                result["path"] = _win_browse()
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=90)  # 用户操作时限（超时返回提示，对话框由用户手动关闭）
        if t.is_alive():
            return jsonify({"success": False, "message": "文件夹选择超时（90 秒），请重试"}), 504
        if "error" in result:
            return jsonify({"success": False, "message": f"打开文件夹选择器失败：{result['error']}"}), 500
        if result.get("path"):
            return jsonify({"success": True, "data": {"path": result["path"]}})
        return jsonify({"success": False, "message": "未选择文件夹"})

    @app.get("/api/browse/tree")
    def browse_tree():
        """目录树浏览（阶段 7.9.6 方案 B）：列出任意磁盘目录的子目录，供前端自研选择器。
        参数：path=绝对路径（缺省=用户主目录）。
        返回：{path, name, parent, entries:[{name, path}]}（仅目录，隐藏 . 开头）。"""
        from pathlib import Path

        raw = (request.args.get("path") or "").strip()
        start = Path(raw).expanduser() if raw else Path.home()
        try:
            start = start.resolve()
        except OSError:
            return jsonify({"success": False, "message": "无法解析路径"}), 400
        if not start.is_dir():
            return jsonify({"success": False, "message": "目标不是文件夹"}), 400
        entries = []
        try:
            for child in sorted(start.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                if child.name.startswith("."):
                    continue
                try:
                    # 阶段 7.9.8.2：文件与目录都返回（对齐 Windows「选择文件夹」对话框——
                    # 文件灰显不可选，避免用户看到「空白」困惑）
                    if child.is_dir():
                        entries.append({"name": child.name, "path": str(child), "type": "directory"})
                    elif child.is_file():
                        entries.append({"name": child.name, "path": str(child), "type": "file"})
                except OSError:
                    continue
        except (PermissionError, OSError) as exc:
            return jsonify({"success": False, "message": f"读取目录失败：{exc}"}), 500
        parent = str(start.parent) if start.parent != start else None
        return jsonify({
            "success": True,
            "data": {
                "path": str(start),
                "name": start.name or str(start),
                "parent": parent,
                "entries": entries,
            },
        })

    @app.get("/api/browse/drives")
    def browse_drives():
        """驱动器 + 常用位置（阶段 7.9.8：Windows 风格自研目录选择器侧栏）。

        返回磁盘驱动器列表（C:/D:…）与用户常用位置。
        常用位置从注册表 User Shell Folders 读取【真实路径】——
        兼容 OneDrive 桌面重定向 + 本地化目录名（桌面→OneDrive/桌面、
        文档→OneDrive/ドキュメント 等），不再硬编码 home/Desktop。
        另返回 start（初始目录 = 真实桌面），供前端选择器默认打开。
        """
        import os
        from pathlib import Path

        def _shell_folder(reg_name: str) -> str | None:
            """读注册表 User Shell Folders 的常用文件夹真实路径（展开环境变量）。"""
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, reg_name)
            except (OSError, FileNotFoundError):
                return None
            if not val:
                return None
            # 展开 %USERPROFILE% 等环境变量
            val = os.path.expandvars(val)
            return val if os.path.isdir(val) else None

        drives: list[dict] = []
        try:
            # Python 3.12+：os.listdrives() → ["C:\\", "D:\\"]
            raw = list(os.listdrives())
        except AttributeError:
            raw = []
            for letter in "CDEFGHIJK":
                p = f"{letter}:\\"
                if os.path.exists(p):
                    raw.append(p)
        for d in raw:
            name = d.rstrip("\\")  # "C:"
            # 3=固定盘 2=可移动 4=网络 5=光驱（当前仅标记盘符，不区分类型）
            drives.append({"name": name, "path": d, "type": "drive"})

        # 常用位置：注册表真实路径（兼容 OneDrive 重定向 + 本地化目录名）
        locations: list[dict] = []
        # 显示名 → (注册表键, 兜底 home 子目录)
        folder_map = [
            ("桌面", "Desktop", "Desktop"),
            ("文档", "Personal", "Documents"),
            ("下载", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
            ("图片", "My Pictures", "Pictures"),
            ("音乐", "My Music", "Music"),
            ("视频", "My Video", "Videos"),
        ]
        home = Path.home()
        for loc_name, reg_key, fallback in folder_map:
            p = _shell_folder(reg_key)
            if not p:
                fb = home / fallback
                p = str(fb) if fb.is_dir() else None
            if p:
                locations.append({"name": loc_name, "path": p, "type": "location"})

        # 初始目录 = 真实桌面（OneDrive 桌面优先），兜底主目录
        start = _shell_folder("Desktop") or str(home)

        return jsonify({"success": True, "data": {"drives": drives, "locations": locations, "start": start}})

    @app.get("/api/options")
    def app_options():
        db = get_db()
        model_store = ModelStore(db)
        model_fields = (
            "id", "provider_id", "name", "type", "capabilities",
            "context_window", "is_default", "status", "icon",
        )
        models = [
            {field: model.get(field) for field in model_fields}
            for model in model_store.list_available_models()
        ]
        agents = agent_registry.list_enabled()
        return jsonify({
            "success": True,
            "data": {
                "models": models,
                "agents": [agent.to_dict() for agent in agents],
                "agent_definition_errors": agent_registry.diagnostics(),
            },
        })

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"success": False, "message": "接口不存在"}), 404

    @app.errorhandler(500)
    def server_error(error):
        return jsonify({"success": False, "message": "服务暂时不可用"}), 500

    return app


if __name__ == "__main__":
    import sys as _sys
    import uvicorn as _uvicorn

    _port = int(os.environ.get("PORT", PORT))
    for _i, _arg in enumerate(_sys.argv):
        if _arg == "--port" and _i + 1 < len(_sys.argv):
            try:
                _port = int(_sys.argv[_i + 1])
            except ValueError:
                _port = PORT
    _uvicorn.run("asgi:app", host=HOST, port=_port, reload=False)
