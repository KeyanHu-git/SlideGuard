# Tauri 桌面迁移与实现审查

日期：2026-09-05。主工作包：[KEY-262](https://linear.app/keyanhu/issue/KEY-262)。状态：开发候选，不替换已发布版本。

## 结论与边界

新桌面采用 Tauri 2、React 和 TypeScript。Python 保留裁剪模型、PowerPoint 原生导出和独立验收。切换界面框架不改变源图，也不把位图变成矢量。

本轮审查按代码和运行结果判断，不以此前使用的模型判断质量。旧 Qt Quick 方案保留作回退与交互参照；其架构文档不再代表未来默认桌面方案。已有 CLI、请求 Schema、导出内核继续有效。

当前工作树有三份此前已存在的全 NUL 文件：`src/slideguard/engine.py`、`src/slideguard/execution.py`、`tests/test_execution.py`。本轮没有覆盖、暂存或提交它们。构建使用 Git `4fd39eb` 的隔离快照叠加本轮新增代码。工作树修复仍追踪 KEY-249，因此不能声称直接在当前受损工作树中运行完整测试通过。

## 审查发现

|问题|证据与影响|处理|
|---|---|---|
|标题栏与工作区分离|用户提供的旧界面截图；视觉系统没有覆盖窗口外壳|Tauri 无系统装饰窗口，应用标题栏、工具栏和窗口按钮统一设计|
|换壳不等于轻量|旧 Qt 便携目录展开约244MiB，其中 PySide6 约90.9MiB；SciPy/NumPy 等仍是大项|先移除界面依赖，分别测主程序、worker、完整目录；没有等价测试前不移除验收依赖|
|前端和后台可能各算一套边界|新框架天然引入跨语言状态分裂风险|React 只保存视口与临时指针；所有裁剪、扩边、撤销、请求均复用 Python CropEditor|
|拖动没有连续反馈|Tauri 首版只在松手时修改边界|新增固定手势起点的实时拖动；合并最新位置；一次拖动对应一次撤销|
|整页按钮状态含糊|CropEditor 把 full 归一化为 manual|界面按实际边界识别整页，不改原请求契约|
|后台卡死可能拖住请求队列|Rust 管道最初没有读取截止时间|有界响应、30秒响应超时、传输失败后关闭当前连接并报错；不无声重建丢失会话|
|文件选择可能越过编辑|原生对话框调用最初不在前端队列中|选择文件/目录与编辑命令共享串行队列|
|旧结果可能被当作新结果|参数变更后上次产物仍可显示|结果绑定不可变请求；变更后明确显示“上次导出”|
|放大 PNG 不会增加细节|当前 Tauri 画布是4000px作者参考图和交付PNG|不宣称矢量预览已完成；交付PDF可见区域重渲染归 KEY-278|
|全图相似度可能漏掉局部细线|qa.py 相似度路径把最长边降至2048px|KEY-275补局部多尺度反例；这是待验证风险，不写成已确认漏检|
|打包可能暗依赖开发机|源码测试与冻结程序的导入、DLL、资源路径不同|独立测试冻结 worker；新机器离线验收仍是发布门槛|

## 模块职责

```text
React 工作区：窗口内容、指针、视口、快捷按钮、错误呈现
    ↓ 受限命令 / ↑ 带 revision 的状态
Tauri 主进程：原生文件选择、窗口控制、后台生命周期、管道边界
    ↓ 版本化 JSONL，stdin/stdout，无网络端口
Python DesktopSession：任务互斥、资源白名单、结果归属
    ├─ CropEditor → CropSpec / shared geometry
    └─ ExportService ← 既有 CLI / JSON 请求
         → PowerPoint 原生绘制 → 安全恢复图片 → 独立 QA → 原子发布
```

不引入 Node 后台服务。Vite 只用于开发；release 嵌入静态资源，不需要运行开发服务器。WebView2 是 Windows 运行依赖，不能从安装成本说明中消失。

前端不能直接执行 shell、读取任意文件或提供源稿路径。文件路径从 Rust 原生对话框进入；预览使用会话生成的不可猜资源 ID，仅开放 PNG/PDF。CSP 禁止远程脚本、嵌入网页和外部图像。此限制是应用权限边界，不等于 Python 子进程具备操作系统沙箱；进程仍拥有当前用户权限。

## 交互约定

- 页面在左，画布在中，导出设置在右。主操作固定在右下角，错误和任务阶段显示在底部。
- 使用低饱和深色面板、统一间距、同一套图标和明确焦点边框。不给“美观已完成”打自动勾；实际可读性、高DPI和用户视觉验收单独记录。
- 缩放以鼠标位置为锚点。平移/缩放只改变视口，不改变导出参数。
- 自动紧边、整页、手动裁剪均可直接选择。四边留白提供0/1/2/5%快捷值、滑杆、联动与独立模式；精确坐标折叠保留。
- 留白百分比按选区宽高计算，仍限制在原幻灯片内。页外透明扩边需要单独设计契约，不在迁移中悄悄改变含义。
- 蓝框是裁剪，绿框是最终范围。棋盘格只用于显示透明背景，不写入产物。
- 参数检查只证明请求有效。保真验收在导出后进行；文件完整性复核只证明已有产物没有改变。
- 工作中禁止换源稿、换页和改设置。手势未结束时禁止换页和导出。协作式取消不能冒充强制终止整个进程树的保证。

## 复现步骤

源码目录保持在工具库；`node_modules`、Rust target、Python环境、测试临时文件和私人稿件证据全部放本机 SlideGuard 开发目录，不进入 OneDrive 项目或 Git。

1. 检查 `git status --short`、源码非空且无 NUL、源稿哈希。不要覆盖未知修改。受损文件按 KEY-249 单独处理。
2. 在已验证的干净源码副本安装 Python 项目与开发依赖。机器接口/worker 不安装 `[gui]` 也不需要 Qt；冻结需要 PyInstaller。
3. 使用 Node/npm 和 Rust MSVC 工具链；在 `desktop` 下运行 `npm ci`，采用已提交的 package-lock.json 和 Cargo.lock。本次构建记录：Node24.14.0、npm11.9.0、Rust1.98.1、Python3.12.14、PyInstaller6.22.2。Python依赖完整锁定仍待发布工作包补齐，当前不保证字节级重建。
4. 在项目根运行 `python -m pytest -q`；在 desktop 运行 `npm run format:check`、`npm test` 和 `npm run build`；在 desktop/src-tauri 运行 `cargo fmt -- --check` 和 `cargo test --features tauri/custom-protocol`。最小Rust安装不附带格式化器，需要先 `rustup component add rustfmt`。
5. `npm run build` 会生成 Windows ICO，再构建前端。直接首次运行 cargo 而没有 ICO 会失败。
6. 开发运行：设置 `SLIDEGUARD_WORKER_PYTHON` 为所用 Python 可执行文件，`PYTHONPATH` 指向干净副本的 src；运行 `npm run tauri dev`。Vite仅监听127.0.0.1:1420。
7. release 候选：在 desktop 运行 `npm run tauri build -- --no-bundle`；在项目根运行 `python -m PyInstaller --noconfirm --distpath <本机构建输出> --workpath <本机构建缓存> packaging/desktop-worker.spec`。将 `slideguard-desktop.exe` 与输出中的 `worker/` 放在同一目录。该目录尚不是验收合格的正式便携包。
8. 协议端到端验收：`python scripts/desktop_smoke.py <授权源稿.pptx> --output <已有证据目录>`。脚本完成预览、连续裁剪、撤销、留白恢复、参数检查、导出、完整性复核和源稿哈希比较。
9. 冻结引擎再验：同一命令增加 `--worker <候选目录/worker/slideguard-worker.exe>`，避免源码导入成功掩盖冻结环境缺依赖。
10. 人工界面验收记录实际打开、拖动、撤销、缩放、留白、导出与透明结果。后台脚本成功不能替代这些记录。

## 本轮已取得的证据

- 最终隔离副本全量 Python 测试364项通过，其中桌面专项18项。
- 前端坐标测试3项通过；Rust响应分帧测试2项通过；TypeScript和Vite生产构建通过。
- 实际启动 Tauri debug 窗口，观察到自绘标题栏与完整三栏工作区。随后出现 Windows 安全选项页面，按电脑操作规则停止输入。尚未声称完成新版鼠标拖动和跨DPI验收。
- 源码 worker 真实稿件测试290秒完成，源哈希不变。PDF2,191,855字节、紧凑SVG2,163,257字节、完整SVG34,708,317字节、透明PNG6,032,164字节。现有QA结论PASS，包复核46项PASS。
- 无Qt冻结worker目录316个文件、161.76MiB，未发现Qt/PySide/shiboken文件。此数字不包含Tauri主程序、系统WebView2、Office和外部渲染工具，也不是压缩下载体积。
- release主程序5.90MiB，主程序加worker候选目录167.66MiB。仍未达到与旧版全部功能同等的交付范围，不能直接把目录差值当成完成全部迁移后的节省。
- 冻结worker真实测试295.7秒通过：PDF2,191,867字节、紧凑SVG2,163,257字节，现有QA与46项完整性复核通过，源稿不变。不同运行的PDF元数据可能使总字节数略有差异。
- 所有私人源稿、截图、PNG/PDF和验收包只保存在本机，不上传GitHub或Linear。

## 后续里程碑与发布门槛

|阶段|Linear|完成条件|
|---|---|---|
|A 证据审查与契约|KEY-263 / KEY-275|确认损坏源码处置；细线局部反例和覆盖差距可复现；旧契约变更有迁移说明|
|B 无Qt后台|KEY-264 / KEY-276|协议错误、超时、取消、强退、源稿变更、路径权限和子进程清理矩阵通过|
|C 桌面直接操控|KEY-265 / KEY-277 / KEY-278|实机手势、高DPI、窗口吸附、缩放PDF、逐页草稿和预设迁移；用户视觉验收|
|D 完整交付|KEY-266 / KEY-279|完整目录/压缩包/内存实测；依赖、许可、SBOM、干净机器离线验收；GitHub候选发布|

仍缺：交付PDF分块预览、完整逐页缩略图、持久草稿/预设、结果目录快捷打开、完整错误恢复、侧车进程树退出治理、安装器/签名与干净机器测试。不因为框架迁移而将这些原有需求标为完成。

## 踩坑与记录维护

本轮按步骤记录在 `docs/desktop-migration-log.json`，并补充到原有复现手册，不另建零散手册。`scripts/update_desktop_workbook.mjs` 只新增或重建本轮生成的工作表，不修改旧工作表；本轮表的修订先改JSON，不直接在生成表中维护。先导出到本机审核目录，验证旧工作表内容与格式未受影响后再替换文档。原版备份保留在本机构建资料目录，不放进项目根。

Codex Windows 容器可能把 LocalAppData 重定向到 Packages/LocalCache。Vite/Vitest 对逻辑路径和真实路径混用会出现“文件存在但无法加载”；本次 Vitest 改从解析后的真实目录运行后通过。不要通过移动用户源码或放宽测试断言来掩盖该问题。

首次新增GitHub桌面工作流在任何job启动前失败。job级env不能引用runner上下文，已改用github.workspace；依据 [GitHub上下文可用范围](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)。这类配置错误需要实际远端运行验证，本地构建不会发现。

第二次远端运行实际启动后，格式检查因Windows检出换行符与Prettier默认LF不一致失败。新增限定到desktop及工作流的.gitattributes固定LF，没有修改全仓库的换行策略，也没有关闭格式检查。

本轮后期Linear拒绝新增issue，返回工作区免费issue数量上限。没有升级套餐；后续发现先写入既有KEY-266评论和本日志，恢复新增额度后再补成子issue。
