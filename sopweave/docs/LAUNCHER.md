# Windows 启动器

`SOPweave.exe` 只管理本地服务进程并打开浏览器。Python、业务源码、前端资源和用户数据仍在应用目录中。当前未制作安装程序，运行环境需事先准备好。

## 使用

双击应用目录中的 `SOPweave.exe`。启动进度窗口会在服务就绪后隐藏，默认浏览器自动打开 `http://127.0.0.1:8080/`。服务只绑定本机回环地址。

- 再次双击：打开已有服务，不重复创建后端。
- 关闭浏览器：后台服务继续运行。
- 托盘双击或“打开 SOPweave”：重新打开页面。
- 托盘“查看启动日志”：查看本次启动和服务日志。
- 托盘“退出并停止服务”：正常停止服务和正在进行的任务，再退出启动器。
- 启动器异常退出：Windows Job Object 清理该启动器拥有的后端及其子进程。

EXE 必须与 `asgi.py`、`scripts`、`frontend` 等应用文件放在一起。桌面入口使用指向 EXE 的快捷方式；单独复制 EXE 到桌面不包含软件本体。

当前日志写入数据目录的 `logs/launcher-日期时间-进程号.log`。数据目录不可写时，启动器日志回退到本机 `%LOCALAPPDATA%\SOPweave\logs`，错误窗口会显示具体位置。日志不作为公开发布材料。

已有其他程序或其他应用副本占用端口时，启动器会提示冲突，不终止该程序。已有相同安装及数据目录的服务时可复用；健康检查通过不表示模型供应商已配置或可访问。

## 构建与桌面入口

在应用目录执行：

```powershell
powershell -NoProfile -File scripts\build_launcher.ps1 -DesktopShortcut
```

使用 Windows .NET Framework 自带 C# 编译器，输出约几十 KB 的 Windows x64 GUI EXE；没有 Node、Electron 或第三方 NuGet 依赖。源码位于 `launcher/`，图标复用 `static/img/favicon.ico`。

`-DesktopShortcut` 在 Windows 当前用户的实际桌面目录创建 `SOPweave.lnk`。省略该参数只生成 EXE；软件目录移动后重新生成快捷方式即可。源码仓库忽略生成的 EXE，后续发行清单会纳入构建后的启动器和对应源码。当前本地 EXE 尚未签名。

环境查找顺序：应用 `.runtime`、应用 `.venv`、上级 `.venv`、PATH 中真实的 `python.exe`。忽略 WindowsApps 安装别名，要求 CPython 3.14 x64；复用现有依赖锁检查，不自动安装包或修改系统环境。

## 命令行

```powershell
.\SOPweave.exe --port 8081
.\SOPweave.exe --stop --port 8081
.\SOPweave.exe --headless --port 8081
.\SOPweave.exe --app-dir "D:\Applications\SOPweave\sopweave"
```

- `--port` 覆盖 `PORT` 环境变量，默认 8080。
- `--stop` 通知相同目录、数据配置与端口的启动器停止；0 表示通知成功，2 表示没有对应实例。停止完成可能需要等待正在退出的服务释放资源。
- `--headless` 不显示窗口、托盘或打开浏览器，保留相同服务生命周期，供自动化验收使用。
- `--app-dir` 用于明确指定应用目录；默认从 EXE 所在目录定位，不依赖当前工作目录。
- `SOPWEAVE_DATA_DIR` 继续控制应用数据目录；相对值以应用目录为基准。

`SOPweave.bat` 在 EXE 存在时转交普通启动。`--check`、`--offline` 保留原有命令行检查和环境准备流程。

## 实现与验收

启动器通过 `/api/health` 核对产品、协议版本、应用/数据目录摘要及前端入口，避免把端口上的其他网页误认为本应用。等待就绪上限为 90 秒，失败显示日志位置。

服务由 `scripts/serve.py` 启动。正常退出通过本机 Windows 事件通知 Uvicorn；不提供网络关闭接口。就绪时校验实际服务进程属于启动器的 Job，兼容 Windows venv 的解释器转发进程。停止超过 25 秒时清理归属 Job，仅影响该启动器创建的进程树。

```powershell
powershell -NoProfile -File scripts\build_launcher.ps1
.\.runtime\Scripts\python.exe -m unittest tests.test_launcher_health tests.test_native_launcher -v
```

测试使用临时数据、随机端口和实际编译的 EXE，覆盖真实 ASGI 启动、重复启动、正常退出、强制退出清理、错误应用身份、缺失文件/Python、从其他工作目录启动，以及中文空格路径移动。测试不调用模型 API。
