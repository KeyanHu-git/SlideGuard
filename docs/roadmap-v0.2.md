# SlideGuard v0.2 需求树与里程碑

更新日期：2026-09-04

Linear 主需求：[KEY-57](https://linear.app/keyanhu/issue/KEY-57/slideguard-v02人类可视入口与-ai-机器接口)

## 发布边界

v0.1.0 已冻结。v0.2 的改动只通过新接口调用现有高保真内核，不重新绘制原稿中的虚线、阴影、公式、透明图片或遮挡关系。

首个公开候选版本定为 `v0.2.0-beta.1`，只承诺登记过的 Windows 11 x64 与 Microsoft 365 PowerPoint x64 组合。它必须同时提供版本化 JSON Schema、无需系统 Python 的便携包、当前用户安装器、SHA256SUMS、SBOM 和第三方许可说明。GA 需要第二套 Windows/Office 组合、正式签名策略和受保护的主分支门禁。

## 层级规则

- L1 只描述一个可交付产品结果。
- L2 对应一条相对独立的能力链。
- L3 对应可以单独安排开发和验收的工作包。
- L4 对应一个主要失败面和一组可执行验收条件。
- 如果一个 L4 仍需同时改变两个以上独立系统，继续拆分，不以“做完 GUI”“完成安装器”这类标题进入开发。

## M5 v0.2 需求与接口契约

L2 为 KEY-58。该里程碑冻结三入口共用的配置模型、公开字段、路径规则、版本兼容和能力边界。

当前完成：

- KEY-64：ExportRequest 和配置优先级。
- KEY-66：Result、Error、ProgressEvent Schema。
- KEY-67：兼容和弃用规则。
- KEY-135：归一化裁剪坐标模型。

仍需完成：

- KEY-65：完整的人类与 AI 支持矩阵。
- GUI 草稿、任务状态和批量信封的版本迁移规则。

## M6 AI 机器接口

L2 为 KEY-59。

已完成的最小链路：

- KEY-68、KEY-92、KEY-93、KEY-95：JSON 文件/stdin、Schema 校验、默认值和结果序列化。
- KEY-69：`export --json` 与 stdout/stderr 隔离。
- KEY-70：dry-run 和能力响应。
- KEY-71：示例与契约测试。
- KEY-94：无 GUI 依赖的应用服务入口。

下一批：

- KEY-72：批量、幂等和重试。
- KEY-153–KEY-156：批量失败隔离、信封 Schema、重试分类、复用前 checksum 校验。
- 取消、阶段检查点和可恢复执行进入 M7/M9，不在机器入口中伪造完成状态。

## M7 人类可视操作

L2 为 KEY-60。旧桌面端采用可选 PySide6 依赖，核心包不导入 Qt。2026-09-05 新增 KEY-262：迁移到 Tauri 2 + React，并保留共享 Python 内核。KEY-263–266负责审查、后台、工作区、交付四阶段；详见 [迁移审查](desktop-migration.md)。迁移切片不代表旧需求已完成，旧入口暂不删除。

已完成的 MVP：

- PowerPoint 单页 PNG 预览模式，不为预览额外生成 PDF。
- PPTX 选择和拖入、页码选择、异步预览缓存和 generation ID。
- 四边四角共八个裁剪手柄、框内整体移动、数值同步。
- 左、上、右、下独立扩展，固定 reference-pixel padding，蓝色手动框和绿色有效框。
- 2.5 MB PDF/紧凑 SVG 默认预算和统一应用服务导出。
- KEY-145：`CropSpec` 统一承载紧边、论文安全边距、自定义预设和按页复制；格式与迁移规则见 `docs/gui-crop-presets.md`。

尚未达到发布条件：

- KEY-98、KEY-150：键盘微调、提交和撤销。
- KEY-147：已加入 nonce 状态握手、协作式取消和有证据约束的单 PID 清理；GUI 的取消按钮与页面循环检查仍待完成。
- KEY-148、KEY-149：GUI 草稿和导出检查点恢复。
- KEY-151：跨屏真机 DPI 切换矩阵；固定 DPR 的布局基线与坐标属性测试已由 KEY-152 完成。
- KEY-141：带缩略图的页面列表；当前 MVP 为页码选择器。

## M8 安装与运行环境

L2 为 KEY-61。现有 `SlideGuard.cmd` 和源码 wheel 不等于安装软件。

工作包：

- KEY-108–KEY-111：支持矩阵、依赖版本、阻断级别和 clean-VM doctor。
- KEY-112–KEY-115：one-folder、组件边界、SBOM、离线冒烟和包校验和。
- KEY-100–KEY-103：最小权限安装、显式文件关联、卸载保留选择和安装前后差异。
- KEY-116–KEY-119：配置迁移、升级/降级和事务回滚。

硬规则：默认按当前用户安装，不申请管理员权限；不得静默接管 `.pptx`；不得结束用户已有 PowerPoint 会话；卸载残余必须落入明确白名单。

## M9 全链路回归与 v0.2 发布

L2 为 KEY-62 和 KEY-63。

测试与证据：

- KEY-120–KEY-123：CLI/JSON/GUI 指纹、产物、错误和前置失败一致。
- KEY-124–KEY-127：困难样本登记、期望矩阵、故障注入灵敏度和样本准入。
- KEY-128–KEY-131：不可变 v0.1 基线、允许漂移字段、差异报告和基线变更审批。
- KEY-104–KEY-107：Office Runner 主机、固定版本、证据上传、串行锁和失败恢复。
- KEY-88–KEY-91：日志脱敏、临时目录回收、输入/SVG/包安全和本地诊断包。

发布顺序：

1. KEY-132：构建与供应链门禁通过。
2. KEY-133：全新机器完成安装、doctor、固定三页导出、verify、迁移和卸载。
3. KEY-134：在前两项证据绑定当前提交后发布；同时完成撤回和热修演练。

任何 required check 未完成时，不创建 GitHub Release，也不移动已有 tag。

## 当前可验证状态

- 开发分支：`feature/key-58-v0.2-contract`
- 版本：`0.2.0.dev0`
- 测试：62 项通过，包括极端页码、严格 JSON、离线 Schema、8 个手柄页边约束和 GUI/核心同框计算。
- wheel：可构建；JSON Schema、GUI、应用服务和 PowerPoint worker 已进入包。
- 真实稿件：`AAAI_frame_draw_final1.pptx` 第一页 dry-run 通过；PowerPoint 预览模式生成 1600 × 900 PNG。
- PowerPoint 会话：独立 worker 会在结束后退出；PowerPoint 已打开时复用当前 COM 会话，但只打开并关闭 SlideGuard 的只读隐藏副本，恢复 AutomationSecurity，不退出用户进程。临时未保存演示文稿的数量、活动文稿和安全设置前后保持不变。
- 超时清理：worker 会在 COM 调用前写入 nonce 状态并轮询取消令牌。只有当前 worker 证明归属的自动化 PID 才能进入精确清理；既有或归属不明的 PowerPoint 不会被结束，错误结果会标明后台收尾风险。
- 未发布：当前结果是开发中间态，不是 beta 或 GA。
