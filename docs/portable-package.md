# SlideGuard one-folder 便携包

这个包解决一件事：用户不必先安装 Python。解压后，`SlideGuard.exe` 同时承载命令行和图形界面入口。

```powershell
.\SlideGuard.exe --version
.\SlideGuard.exe doctor --json
.\SlideGuard.exe gui
```

## 包内与包外的边界

包内包含 Python 运行时、SlideGuard、Qt 图形界面、CairoSVG 和导出验收所需的 Python 库。确切版本写在 `sbom.cdx.json`，对应许可文本放在 `licenses/`。

以下程序不随包分发：

- Microsoft PowerPoint：用户或所在组织自行安装并授权。SlideGuard 通过本机 COM 接口调用它。
- Windows PowerShell：使用 Windows 提供的运行环境。
- Poppler：完整导出需要 `pdftocairo.exe`、`pdftoppm.exe` 和 `pdfinfo.exe`。默认包不复制 Poppler。
- Chromium 系浏览器：可选。找到 Edge、Chrome 或 Chromium 时优先使用；找不到时回退到包内 CairoSVG。

如需随目录携带已经审查过的 Poppler 构建，把文件放在以下任一目录：

```text
external/poppler/Library/bin/
external/poppler/bin/
```

`SlideGuard.exe` 启动时会把这两个目录临时加到当前进程的 `PATH`。它不会修改用户或系统环境变量。若后续发布包实际放入 Poppler，发布人必须把该构建的版本、来源、校验和和全部许可文件补进 SBOM 与 notices，不能沿用默认包的声明。

## 构建

联网构建使用独立虚拟环境：

```powershell
.\scripts\portable-build.ps1
```

离线构建先准备 wheelhouse，然后运行：

```powershell
.\scripts\portable-build.ps1 -Wheelhouse C:\path\to\wheelhouse
```

脚本会安装 `.[gui,portable]`，先确认 Qt 可以载入，再由 PyInstaller 生成 `%LOCALAPPDATA%\SlideGuard\portable-build\dist\SlideGuard\`。打包结束后，它会在离屏模式启动 GUI 三秒；如果进程提前退出，整个构建直接失败。PyInstaller 的 bootloader 嵌入 `SlideGuard.exe`，因此作为随包组件单独列出并复制许可文本；其余构建依赖只记录在 SBOM 的 `metadata.tools`。可用 `-OutputRoot` 指定发布目录，用 `-BuildRoot` 指定另一个短工作目录。

便携构建目前把 PySide6 固定为 6.9.2。2026-09-04 的本机试验中，6.11.2 wheel 的 `Qt6Core.dll` 要求 `icuuc.dll`，但隔离环境里没有该文件；PyInstaller 只给出警告并继续，最后得到 CLI 可运行而 GUI 固定报错的目录。版本不能放开，除非新的候选版本先通过 Qt import 和成品 GUI 启动两道检查。

工作目录刻意放在短路径。PySide6 包含很深的 QML 文件层级；若在已经很长的 OneDrive 或中文项目路径下创建虚拟环境，即使源文件本身正常，Windows 仍可能在安装阶段报 `No such file or directory`。不要靠删减 Qt 文件绕过它，那会留下只能启动部分页面的包。

## 包内审计文件

- `BUILD-INFO.json`：SlideGuard 版本、源提交、Python 和 PyInstaller 版本。
- `COMPONENT_BOUNDARIES.json`：哪些组件随包分发，哪些由用户、组织或操作系统提供。
- `sbom.cdx.json`：CycloneDX 1.5 组件表，列出运行时 Python 发行包和外部程序边界。
- `THIRD_PARTY_NOTICES.md` 与 `licenses/`：包元数据里的许可声明和复制出的许可文本。
- `MANIFEST.json`：包内载荷的相对路径、字节数和 SHA-256。
- `SHA256SUMS`：发布目录除自身外每个文件的 SHA-256，也包括 `MANIFEST.json`。

`MANIFEST.json` 不把自身和 `SHA256SUMS` 放进载荷表，这是为了避免自引用。文件顺序按不区分大小写的相对路径固定；`SHA256SUMS` 再对生成后的 manifest 做校验。

## 离线冒烟测试

基础检查不调用网络，也不调用系统 Python、pip 或 Git：

```powershell
.\scripts\portable-smoke.ps1 `
  -PackageRoot "$env:LOCALAPPDATA\SlideGuard\portable-build\dist\SlideGuard" `
  -CoreOnly
```

完整检查需要本机已有授权 PowerPoint、Poppler 和一个本地 PPTX 困难样本：

```powershell
.\scripts\portable-smoke.ps1 `
  -PackageRoot "$env:LOCALAPPDATA\SlideGuard\portable-build\dist\SlideGuard" `
  -Fixture C:\fixtures\effects-and-overlap.pptx
```

完整脚本依次核对发布校验和、启动 CLI、运行 doctor、导出第一页，再用包内 `verify` 检查结果。脚本本身不会下载组件。要证明断网可运行，必须在关闭网络且没有 Python、pip、Git 的干净 Windows 用户或虚拟机中执行，并保存终端输出、系统版本和 doctor JSON。

当前仓库只提供构建与测试基础，尚未声称通过上述干净机器检查。对应 Linear 项在拿到实机证据前应保持未完成。
