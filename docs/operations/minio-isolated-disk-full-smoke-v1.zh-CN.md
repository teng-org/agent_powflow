# MinIO 隔离磁盘满与恢复演练 v1

## 1. 安全模型

`tools/smoke/minio-disk-full-smoke.ps1` 不向正式 MinIO volume 写填充数据。脚本启动一个随机命名的临时 MinIO 容器，`/data` 使用默认 128 MiB 的 Docker tmpfs，并设置内存、CPU、PID、capability 与 `no-new-privileges` 边界。测试容器只接入 Execution 所在 Docker network，不映射宿主机端口。

脚本在删除测试容器前再次核对精确容器名；Python helper 只复制到 Execution 容器中随机、正则校验的 `/tmp` 文件，最终以 root 删除该精确文件，以兼容 `docker cp` 的 root 所有权。正式 MinIO 在演练前后都必须保持 running。

## 2. 验收语义

演练必须同时证明：

1. 有至少一个对象成功写入，避免把“容量小到无法启动”误判为磁盘满恢复；
2. 持续写入最终返回 MinIO `XMinioStorageFull`，其他错误码均失败；
3. 删除全部填充对象并复查零残留；
4. 再写入、复读 recovery 对象并核对 SHA-256；
5. 删除测试 bucket、helper 和整个 tmpfs 容器；
6. 正式 MinIO 仍处于 running。

## 3. 2026-08-08 实测

默认 128 MiB tmpfs、4 MiB 对象下，31 个对象成功写入，共 130,023,424 bytes；下一次写入返回 `XMinioStorageFull`。释放填充对象后 recovery 写入与复读成功，测试 bucket、临时容器、helper 均为零残留，正式 MinIO 未停止。

```powershell
.\tools\smoke\minio-disk-full-smoke.ps1
```

## 4. 尚未覆盖

该演练关闭单节点“真实存储满拒绝并释放后恢复”的机制验证，但不代表生产容量规划。仍需在专用环境验证持续饱和时的延迟/背压、告警提前量、多磁盘离线、多节点/纠删码、网络分区、重平衡与业务端 507/503 映射。
