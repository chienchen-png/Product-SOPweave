# 离线安装介质

源码发布包包含后端、前端源码和构建结果、Agent/Skill、测试、MIT 许可证与第三方声明。wheels 包只包含 `requirements.lock` 中通过 SHA256 校验的依赖，不携带虚拟环境、用户数据库或凭据。

## 在目标 Windows 电脑安装

1. 提前安装 Windows x64 CPython 3.14。
2. 解压源码包，将 wheels 包内 `offline_packages` 放到 `sopweave/offline_packages`。
3. 在应用目录运行 `SOPweave.bat --offline`；首次创建 `.runtime` 并离线安装依赖。以后双击 `SOPweave.exe`，默认浏览器会在服务就绪后打开。
4. 在 `http://127.0.0.1:8080` 配置自己的模型供应商。关闭浏览器不停止后台服务；从托盘菜单选择“退出并停止服务”。

前端构建结果已随候选包附带，使用者不需要 Node.js。修改前端源码的开发者仍需 Node 和 `npm ci`。

## Word 导出

Word 导出需要独立程序 Pandoc。当前已验证版本为 [Pandoc 3.10.2 官方发行版](https://github.com/jgm/pandoc/releases/tag/3.10.2)。从官方发行版下载 Windows zip，在联网机器校验并准备好后，将 `pandoc.exe` 和原始版权/许可文件放在 `offline_packages/pandoc`；也支持已安装到 PATH 的 Pandoc。保留现有 `reference-template.docx` 可使用自定义模板；需要重新生成模板时运行 `python scripts/build_reference_docx.py`（要求项目内已有 Pandoc）。

自动生成的源码/wheels 包不重新分发 Pandoc 可执行文件；维护者准备完整离线介质时，应同时处理其独立的许可证和对应源码义务。模型调用还需要网络，或可从本机访问的兼容模型服务。

## 生成与验证

```powershell
.\.runtime\Scripts\python.exe scripts/generate_third_party_notices.py
powershell -NoProfile -File scripts/build_launcher.ps1
.\.runtime\Scripts\python.exe scripts/build_release.py
.\.runtime\Scripts\python.exe scripts/verify_release.py
```

每个 zip 内有逐文件 `MANIFEST.json`，目录外有 `SHA256SUMS.txt` 和 `RELEASE.json`。发布前必须通过完整回归、生产构建、离线恢复和远端 CI。
