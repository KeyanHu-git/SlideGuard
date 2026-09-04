# 中断矩阵与恢复判定

这套门禁回答两个问题：任务在某一阶段突然停下时，磁盘上还能信什么；再次启动后，程序会从哪里重算。

测试不依赖 PowerPoint，也不需要人盯着进度。它用两页固定合成数据跑完 12 个 checkpoint 边界，每个边界分别注入协作式取消、普通 Python 异常和外部进程终止。固定 seed 是 `20260904`。

## 阶段序列

| sequence | phase | cursor |
|---:|---|---|
| 0 | `DISCOVER` | 无 |
| 1 | `PREFLIGHT` | 无 |
| 2 | `INVENTORY` | 无 |
| 3 | `NATIVE_EXPORT` | 第 1 个输出页 |
| 4 | `PATCH` | 第 1 个输出页 |
| 5 | `VALIDATE` | 第 1 个输出页 |
| 6 | `NATIVE_EXPORT` | 第 2 个输出页 |
| 7 | `PATCH` | 第 2 个输出页 |
| 8 | `VALIDATE` | 第 2 个输出页 |
| 9 | `PACKAGE` | 无 |
| 10 | `PUBLISH/pending` | 无 |
| 11 | `PUBLISH/complete` | 无 |

页数变化时，中间三步按选中页顺序重复，后面的 `PACKAGE` 和 `PUBLISH` 顺延。序号由状态机计算，不读取文件时间，也不根据目录是否存在猜测。

## 注入方法

协作式取消和 Python 异常都发生在阶段工作已经写入临时工作区、checkpoint 尚未提交的位置。测试随后确认这些未登记文件不会被当成可信产物。

进程终止用独立 Python 子进程。子进程先把下一份 checkpoint 写入同目录临时文件并执行 `fsync`，然后写出屏障信号并停住。父进程收到信号后调用系统级 kill。这样每次终止都落在 atomic rename 之前，不靠毫秒级 sleep 碰运气。重新读取时只能看到上一份完整 checkpoint；那份已落盘但没有 rename 的临时 JSON 必须被忽略。

原子发布另测四种组合：取消发生在 rename 前、取消发生在 rename 后、进程在 rename 前被终止、进程在 rename 后被终止。前一侧的正式目录必须不存在，后一侧只能是逐文件哈希完整的成功包。再次调用发布时，最终文件清单、哈希和 `PASS` 判定必须与干净运行相同。

## 恢复计划判定

每个中断样本都会调用两次 `build_resume_plan`。两次结果要逐字节等价，并且调用前后的工作区文件树哈希不能变化。

- 没有完整 checkpoint：拒绝恢复，返回 `CHECKPOINT_READ_FAILED`。调用方可新建任务，但不能复用当前目录。
- 普通已完成阶段：复用到当前 sequence，从下一 sequence 重算。
- `PUBLISH/pending`：这一步本身不复用，仍从 pending publication 重做 atomic publish。
- 未登记文件：忽略。即使它看起来完整，也不能把恢复点向后推。
- checkpoint 截断且已登记产物同时被改写：整份恢复计划拒绝，不复用任何阶段。

当前矩阵还同时启动两个只读 planner。两份计划必须相同，工作区不得变化。这个用例只证明并发读取；它不声称两个 writer 已经安全。

## 运行与报告

本地运行：

```powershell
python scripts/interruption_matrix.py --output .test-results/interruption-matrix.json
```

脚本只在系统临时目录中创建合成工作区。最终 JSON 记录 commit SHA、运行环境指纹、注入点、观察到的 checkpoint sequence、恢复起点和每例判定。内容不写绝对工作路径。CI 对 Python 3.10 和 3.12 分别生成报告并上传为构建附件。

单元门禁：

```powershell
python -m pytest -q tests/test_interruption_matrix.py
```

## 尚未完成的部分

KEY-176 不能因为这张矩阵通过就直接关闭。下面三项仍有各自的实现和验收：

- KEY-199：跨进程恢复租约。两个真实续跑进程只能有一个 writer，另一个返回 `RESUME_IN_PROGRESS`，而且不能启动 PowerPoint。
- KEY-200：按 plan 执行实际复用与重算。恢复期间再次崩溃后仍要收敛到与干净运行相同的产物哈希和 QA 判定。
- KEY-201：安全处理 rename 前终止遗留的 `.sg-publish-*` 暂存目录。当前正式目录隔离正确，但暂存目录没有足够的归属证据，不能仅凭前缀删除。

这三个缺口都有单独的 Linear 验收条件。缺少其中任何一个，都不能把“只读恢复判定通过”表述成“端到端续跑完成”。
