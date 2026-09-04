# SlideGuard 离线诊断包

状态：v0.2 离线数据契约与命令行入口。

## 用途

诊断核心接收 doctor 结果、一次失败的结构化错误，以及用户明确选择的 QA 报告。它在本机生成一个小型 JSON 文档，给出固定修复步骤。判断过程不联网，也不调用 AI。

诊断包只记录定位问题所需的元数据。它不装入 PPTX、导出的 PDF/SVG/PNG、截图、绝对路径、环境变量或原始异常文本。报告不是默认输入。只有调用方把 `include_report` 设为 `true` 时，核心才读取报告，并且只保留 verdict、finding 计数、finding code、artifact kind 和合法的配置指纹。

## 固定结构

公开 Schema 是 `diagnostic-bundle.schema.json`，版本为 `1.0`。顶层字段如下。

| 字段 | 内容 |
|---|---|
| `tool` | SlideGuard 版本和流水线修订号 |
| `doctor` | 平台类别、PowerPoint 可用性、依赖可用性和固定问题码 |
| `events` | 错误码、阶段、退出码、是否可重试、是否仍有后台收尾风险 |
| `configFingerprint` | 合法时保留 `sha256:` 指纹，否则为 `null` |
| `report` | 用户选择报告后生成的固定摘要，否则为 `null` |
| `recommendations` | 由规则表生成的本地修复步骤 |
| `safety` | 元数据策略、两次隐私处理和 262,144 字节上限 |

整个 JSON 编码后不得超过 256 KiB。调用方可以设置更小的上限，但不能提高产品上限。

## 安全流程

生成过程有两道相同的门。

1. 核心先把依赖路径投影成“是否存在”的布尔值，不保留路径文本，再把输入交给 `privacy.redact_for_sharing`。秘密扫描器只返回类别，不返回命中的原值。仍有命中时，核心拒绝生成。
2. 核心从脱敏输入中挑选白名单字段并生成摘要，再执行一次脱敏、Schema 校验、内容策略检查、秘密扫描和字节计数。

任一门发现用户名、凭据、UNC 路径、本机路径或环境值时，错误只包含类别，例如 `API_TOKEN` 或 `UNC_PATH`。错误消息不会回显令牌、用户名或文件名。

最终内容策略会再次拒绝以下内容：

- Windows、UNC、Linux 和 macOS 用户目录的绝对路径；
- `.pptx`、`.ppt`、PDF、SVG、PNG、JPEG、GIF、BMP、WebP 和 TIFF 文件名；
- `environment`、`sourcePath`、`imageData`、`base64` 等禁止字段；
- 无法编码成严格 JSON 的 NaN、Infinity、自定义对象或其他值。

## 固定诊断规则

相同输入会得到相同顺序和相同建议。没有时间戳或随机 ID。

| 问题码 | 本地建议 |
|---|---|
| `POWERPOINT_UNAVAILABLE` | 安装或修复当前用户的桌面版 PowerPoint，再运行 doctor |
| `POPPLER_UNAVAILABLE` | 修复 SlideGuard 随包提供的 PDF 工具，再运行 doctor |
| `POWERSHELL_UNAVAILABLE` | 检查受支持 Windows 中的 PowerShell，再运行 doctor |
| `POWERPOINT_VERSION_UNSUPPORTED` | 对照支持矩阵，更新或修复 PowerPoint |
| `PERMISSION_DENIED` | 检查 PPTX 读取权限和输出目录写入权限，不要求管理员权限 |
| `INPUT_INVALID` | 用 PowerPoint 另存一份新 PPTX，并检查页码选择 |
| `EXPORT_FAILED` | 关闭 PowerPoint 模态对话框后重试一次，不结束进程 |
| `POWERPOINT_CLEANUP_PENDING` | 保持 PowerPoint 打开，等待 worker 安全收尾后再运行 doctor |

未知错误仍进入事件摘要，但不会凭空生成修复步骤。以后可以加入新规则，而不改变已有错误码的含义。

## 开发接口

```python
from slideguard.diagnosis import build_diagnostic_bundle

bundle = build_diagnostic_bundle(
    doctor_result,
    machine_error,
    qa_report,
    include_report=True,
)
```

`build_diagnostic_bundle`只处理内存中的对象，不写文件。命令行入口负责征得授权、读取输入和原子保存结果。

## 命令行入口

生成动作必须带 `--consent`。不带该标志时，命令立即返回结构化失败，不读取任何输入，也不创建输出文件。

```powershell
slideguard diagnose --consent --doctor doctor.json
```

默认只把一份严格 JSON 写到 stdout。若需要文件，指定 `--out`；文件通过同目录临时文件和原子替换发布，成功时 stdout 和 stderr 均为空。
输出文件不能与 doctor、error 或 report 输入指向同一文件，防止覆盖原始材料。

```powershell
slideguard diagnose --consent --doctor doctor.json --error error.json --out diagnostic.json
```

QA 报告不会自动查找或读取。只有明确提供 `--report` 时，命令才读取它并加入固定摘要：

```powershell
slideguard diagnose --consent --doctor doctor.json --report qa-report.json --out diagnostic.json
```

`doctor`、`error` 和 `report` 输入都必须是严格 UTF-8 编码的 JSON 对象。重复键、NaN、Infinity、数组根节点和损坏的 UTF-8 会被拒绝。失败仍在 stdout 返回一份现有格式的结构化错误，退出码为 30；错误不会回显输入或输出的绝对路径。

此命令只在本机读取和写入指定文件，不包含上传或网络步骤。输出仍经过核心的两次脱敏、两次秘密扫描、元数据内容策略、Schema 校验和 256 KiB 限制。

## 验证

运行：

```powershell
python -m pytest -q tests/test_diagnosis.py tests/test_diagnostic_cli.py
```

测试覆盖 Schema、固定建议、两次扫描、报告选择、大小限制和对抗输入。对抗样本含用户名、令牌、UNC 路径及带中文文件名的图片路径；断言同时检查拒绝错误没有原值。
