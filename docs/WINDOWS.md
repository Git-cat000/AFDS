# AFDS Studio 1.0 · Windows 安装与使用

适用于 Windows 10 / 11 x64。安装使用当前用户权限，自带 Python 运行时和编译好的 Studio 界面。

1. 从 [GitHub Releases](https://github.com/Git-cat000/AFDS/releases) 下载 `AFDS-Studio-1.0.0-Windows-x64-Setup.exe`，用 `Get-FileHash <安装文件> -Algorithm SHA256` 与相邻校验文件比较。安装程序尚未签署 Authenticode。
2. 在安装向导中选择是否由安装器下载 FFmpeg。此选项需要互联网，下载来自其 [Windows 构建发行页](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2)，内容经 SHA-256 校验。也可取消选择并使用已经在 PATH 中的 `ffmpeg.exe` 和 `ffprobe.exe`。
3. 从开始菜单打开 **AFDS Studio**。启动窗口会打开默认浏览器中的本地工作室。需要重新打开页面时，点击“打开工作室”。关闭启动窗口会停止本机服务。
4. 创建项目并使用离线 Mock 流程可生成演示成片。有真实模型服务时，在“模型与工具”配置连接和项目预算。

安装器默认将程序装在 `%LOCALAPPDATA%\Programs\AFDS`，数据写入 `%LOCALAPPDATA%\AFDS\Workspace`。升级和卸载会保留工作区。旧源码工作区可先导出 ZIP，再在安装版中恢复。启动失败时可从启动窗口打开数据目录，查看 `studio.log` 和 `worker.log`。默认工作室端口为 8765，遇到占用时可关闭占用程序后重新打开。

Windows 安装版是个人工作室，仅在可信的单用户电脑上运行。团队生产部署的 Docker、TLS、PostgreSQL 恢复和跨主机 Worker 仍需目标环境验收。详见 [部署指南](https://github.com/Git-cat000/AFDS/blob/main/docs/DEPLOYMENT.md)。

## 从源码构建安装程序

在 Windows x64 上准备 Node.js、项目 Python 虚拟环境、.NET Framework 编译器和 [Inno Setup 6.7.3](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)。构建使用锁定的 Python 依赖和经校验的 [Python 3.13.17 运行时](https://www.python.org/downloads/release/python-31317/)。FFmpeg 由安装器按选项直接下载。

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.venv/Scripts/python.exe scripts/build_windows.py --iscc "C:/Path to Inno Setup/ISCC.exe"
```

输出位于 `dist`，包括安装程序与 SHA-256 文件。
