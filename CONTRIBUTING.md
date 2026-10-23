# 开发指南

## 环境与启动

支持 Windows x64 / CPython 3.14，前端使用 Node.js 24 或 25。进入 `sopweave`：

```powershell
python -m venv .runtime
.\.runtime\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
npm ci --prefix frontend
npm run build --prefix frontend
powershell -NoProfile -File scripts\build_launcher.ps1
.\SOPweave.exe
```

离线安装将完整 wheels 放在 `offline_packages`，使用 `pip install --no-index --find-links offline_packages --require-hashes -r requirements.lock`。不要复制其他电脑的虚拟环境。

## 修改与验收

1. 先阅读 [架构说明](sopweave/docs/ARCHITECTURE.md)和 [Agent 运行时](sopweave/docs/AGENT_RUNTIME.md)，确定职责归属及受影响调用方。
2. 业务规则放入纯计算内核或服务；路由只负责校验与响应。工具定义以 catalog 为准，Agent/Skill 文档同步更新。
3. 修复缺陷时补能够复现业务问题的回归测试；保持原接口及已有用户数据兼容。
4. 执行下列门槛，记录失败原因并修复后继续：

```powershell
.\.runtime\Scripts\python.exe -m unittest discover -s tests -t .
npm test --prefix frontend
npm run build --prefix frontend
.\SOPweave.bat --check
.\.runtime\Scripts\python.exe -m pip check
```

也可执行 `scripts/verify_agent_runtime.ps1`。GitHub Windows CI 执行同类门槛。

启动器构建后，后端测试会额外执行 EXE 实际启动、重复启动、退出清理、端口冲突和目录迁移测试。单独验证可运行 `python -m unittest tests.test_launcher_health tests.test_native_launcher -v`。交付前应先构建前端和启动器，详见[启动器说明](sopweave/docs/LAUNCHER.md)。

## 数据与密钥

测试使用临时数据库和人工样例；通过 `SOPWEAVE_DATA_DIR` 隔离运行数据。API Key 只保存在自己的本机配置中。提交前检查新增文件、密钥、业务文件和生成物；忽略规则不能替代检查已跟踪文件。

## 依赖与发布

Python 完整版本与哈希以 `requirements.lock` 为准；SDK 小锁用于升级约束。前端使用 `npm ci`。更新依赖后重新生成第三方 notices、离线 wheels 并通过完整门槛。源码包和离线依赖包分别生成，按清单校验 SHA256。

项目采用 [MIT License](LICENSE)。公开发布前必须通过完整回归、生产构建、离线恢复检查和远端 Windows CI。发布边界见 [支持说明](sopweave/docs/SUPPORT.md)，安装介质说明见 [离线安装](sopweave/docs/OFFLINE.md)。
