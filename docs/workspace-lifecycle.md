# SlideGuard 工作目录归属与崩溃清理

SlideGuard 只管理 `%LOCALAPPDATA%\SlideGuard\w` 的直接子目录。GUI 裁剪草稿位于同级的 `gui-drafts`，不在扫描范围内；启动清理不会读取、改写或删除草稿、正式配置和正式导出包。

## 归属标记

每个导出、独立 doctor 和 GUI 预览工作区在写入其他文件前创建 `.slideguard-owner.json`。标记包含：

- 固定的 owner 和 Schema 版本；
- 工作区类别与任务 ID；
- 本次实例的随机 nonce；
- 工作根目录的 SHA-256 指纹，而不是个人绝对路径；
- PID 与进程启动 token；
- 创建时间、完成时间和 active/complete 状态。

标记用同目录临时文件、flush、fsync 和原子替换写入。标记写入失败时，只尝试移除刚创建且仍为空的目录，不执行递归清理。

正常导出完成后，SlideGuard 先用 task ID 和 nonce 把标记改为 complete，再走统一删除门禁。若程序恰好在两步之间退出，下次启动可以识别并清理这个完整工作区。

## 删除门禁

递归删除必须同时满足以下条件：

1. 目标是登记工作根目录的一个直接子目录，不能是工作根、用户目录、输出目录或任意更宽的路径。
2. 根目录、目标目录、标记文件和目录树中没有 symlink、junction 或其他 Windows reparse point。
3. 标记字段完整、owner 与版本正确、根指纹相符。
4. 调用方给出的 nonce 与标记一致。
5. 完成无链接遍历后再次读取标记；内容或 nonce 有任何变化都拒绝删除。

实际删除逐层使用 `lstat`，不会跟随链接。权限拒绝、目录占用或检查异常都按“保留”处理。SlideGuard 不会为了完成清理而结束未知进程，也不会按进程名结束 PowerPoint。

## 启动扫描规则

真实 CLI 进程和 GUI 启动时执行一次静默扫描。扫描不会创建缺失的根目录，也不会进入工作根以外的位置。

| 状态 | 动作 |
|---|---|
| PID 与进程启动 token 仍匹配 | 保留，视为活动任务 |
| 标记为 complete，原 owner 已退出 | 通过删除门禁后清理 |
| 存在可读取的 `job-state.json` | 保留，交给后续 resume plan 验证 |
| 导出或 doctor 目录含中间文件 | 作为失败证据保留 |
| 崩溃预览目录超过 7 天 | 通过删除门禁后清理 |
| 空工作目录超过 7 天 | 通过删除门禁后清理 |
| 缺标记、标记损坏、nonce/根指纹不符 | 保留并报告拒绝原因 |
| 包含链接、junction 或 reparse point | 保留并报告拒绝原因 |

PID 不能单独证明任务仍在运行，因为系统可能复用 PID。Windows 上必须同时匹配进程创建时间。若权限或平台限制使启动 token 无法读取，只要 PID 看起来仍存活就保留目录，不把检查失败当作删除许可。

## 草稿与断点续跑边界

GUI 草稿仍由 `GuiDraftStore` 单独管理。正常关闭、取消和失败时可保留，成功导出后才由 GUI 明确删除。工作区扫描不会用临时目录保留期限替代这套规则。

当前实现只负责识别并保留带 `job-state.json` 的候选目录；它不会自行相信 checkpoint，也不会跳过导出阶段。checkpoint 的内容校验与确定性续跑计划属于 KEY-174/175，必须在输入哈希、请求指纹、阶段依赖和产物哈希全部通过后才能复用。

## 维护验证

运行定向测试：

```powershell
python -m pytest tests/test_workspace.py tests/test_cancellation.py tests/test_doctor.py tests/test_gui_interactions.py -q
```

测试覆盖缺失/损坏标记、nonce 变化、标记检查期间被替换、链接或 junction 拒绝、活动 PID、过期预览、空目录、失败证据、checkpoint 和 GUI 草稿保留，以及标记原子写入失败。任何新增递归清理点都必须复用同一门禁并补相应故障样本；不能直接调用宽范围删除来绕过归属检查。
