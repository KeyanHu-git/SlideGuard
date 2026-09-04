# 恢复租约契约

`resume plan` 是只读判断。真正开始复用、重算或发布之前，进程还要拿到工作区里的 writer 租约。没有租约就不能写。

固定租约文件是 `.slideguard-resume-lease.json`，互斥文件是 `.slideguard-resume.lock`。两者只保存在 owner marker 已验证的 export workspace 内。JSON 字段包括 task ID、workspace nonce、lease nonce、PID 和进程启动 token，不记录工作区的绝对路径。

## 如何选出一个 writer

候选进程先打开固定互斥文件，并尝试锁住首字节。拿到锁的进程才可以读取或更新租约 JSON；其他进程立即返回 `RESUME_IN_PROGRESS`。租约 JSON 先写到同目录 nonce 唯一的 candidate，执行 `fsync`，再用 `os.replace` 原子替换固定入口。

winner 在整个恢复和发布期间保持互斥文件的操作系统锁。租约 JSON 本身不加字节锁，因此可以被诊断工具只读检查，也可以在 Windows 上由持锁者安全删除或原子替换。拿不到互斥锁的进程不能启动 PowerPoint、改 checkpoint 或改租约。

## 崩溃和陈旧租约

进程崩溃后，Windows 或 POSIX 会释放文件锁。下一进程仍不能直接覆盖旧文件。它要先完成下面的检查：

1. 工作区 owner marker 的 task ID、workspace nonce 和类型仍与调用参数相同。
2. 租约是普通文件，不是 symlink、junction 或其他 reparse point。
3. JSON 完整，字段集合和 `resume-lease.schema.json` 一致。
4. 旧 PID 与启动 token 不再指向同一个活动进程。
5. 互斥文件的已锁句柄仍指向同一个普通文件；租约 JSON 在旧 owner 检查前后字节一致。

任一项无法确认都会保留原文件并拒绝写入。旧 owner 确认退出后，持锁进程才可原子替换固定租约。所有接管判断都在同一把互斥锁内完成，因此并发恢复仍只有一个 writer。

## 发布前复核

租约对象提供 `assert_current()`。续跑执行器在启动外部程序前、提交 checkpoint 前和 atomic publish 前都要调用它。复核内容包括工作区归属、互斥文件身份、完整 JSON 和 lease nonce。互斥文件或租约 JSON 被替换、删除或改写时返回 `RESUME_LEASE_LOST`，当前进程立即停止写入。

正常退出调用 `release()`。程序先确认自己仍持有同一互斥文件并且租约 JSON 仍属于自己，随后删除固定租约，再释放文件锁。互斥文件作为工作区内的稳定锁点保留到 owner-gated 工作区清理；程序不会按通配符清理 candidate，也不会递归删除工作区。

## 稳定错误码

| code | 含义 |
|---|---|
| `RESUME_IN_PROGRESS` | 另一个 writer 持有锁、旧 owner 仍活动或陈旧接管竞争失败 |
| `RESUME_LEASE_INVALID` | 工作区归属、租约类型、Schema 或 nonce 无法验证 |
| `RESUME_LEASE_LOST` | 已持有的租约入口被删除、替换或改写 |
| `RESUME_LEASE_FAILED` | 互斥文件、candidate 写入或原子替换失败 |

KEY-199 只负责单写者和所有权。阶段复用、重算和再次崩溃后的收敛由 KEY-200 验收。
