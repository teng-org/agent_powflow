# MinIO 有界压力冒烟 v1

## 用途

`tools/smoke/minio-bounded-pressure-smoke.ps1` 用于在开发或预生产 TLS 栈中建立可重复的对象存储基线，覆盖：

- 单个较大对象上传、下载和 SHA-256 校验；
- 多线程并发对象上传与下载校验；
- 开启 versioning 后，同一键连续覆盖产生的物理版本数量；
- 压力前后的 Prometheus MinIO HTTPS target 和容量指标可用性；
- 随机唯一前缀全部版本的严格清理。

该脚本直接使用 Execution API 容器中的 Application 身份和 TLS CA，不调用文档业务 API、Worker、LLM 或 Embedding Provider，因此不会产生外部模型费用。

## 默认规模与安全上限

默认命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\smoke\minio-bounded-pressure-smoke.ps1
```

默认参数：

| 参数 | 默认值 | 硬上限 |
| --- | ---: | ---: |
| `LargeObjectMiB` | 16 MiB | 32 MiB |
| `ConcurrentObjects` | 16 | 32 |
| `ConcurrentObjectMiB` | 1 MiB | 4 MiB |
| `VersionCount` | 10 | 32 |
| `Workers` | 8 | 16 |
| 本次总写入估算 | 约 33 MiB | 192 MiB |

脚本拒绝未启用 TLS 的 Execution 配置。测试路径固定为：

```text
v1/system/bounded-pressure-smoke/<随机 32 位十六进制 ID>/
```

清理前会复读版本清单；`finally` 只对本次已校验的唯一前缀执行 `mc rm --recursive --versions`，随后再次枚举，非零残留会令测试失败。

## 2026-08-04 本地 TLS 基线

默认参数实测：

- 16 MiB 单对象 put/get/checksum：0.265 秒；
- 16 个 1 MiB 对象、8 workers 并发 put/get/checksum：0.135 秒；
- 同一键连续写入 10 个版本，服务端版本复读为 10；
- 本次前缀物理版本总数 27；
- 探针总耗时 0.524 秒；
- 清理后该压力前缀版本数为 0；
- Prometheus firing 告警为 0，MinIO target 保持 `up`，scrape URL 为 HTTPS。

这些数字只代表当前单节点 Docker Desktop 环境，不是容量规划结论，也不能与生产硬件直接比较。

## 不覆盖的场景

有界冒烟本身不会主动填满磁盘、停止存储节点或制造网络分区。单节点真实存储满拒绝和释放后恢复已由独立的 `minio-disk-full-smoke.ps1` 在 128 MiB tmpfs 中验证；以下项目仍必须在隔离、可恢复且经过容量审批的专用环境完成：

1. 正式业务 API 在接近配额/物理满时的 507/503 映射、告警提前量和端到端恢复时间；
2. 200 MiB 以上文件、长时间持续并发和 Worker/API 内存峰值；
3. 多节点、纠删码、驱动离线、网络分区和重平衡；
4. 生命周期扫描期间的版本删除吞吐与前台请求延迟；
5. PostgreSQL 租户 advisory lock、业务上传、Qdrant 发布和补偿账本的端到端吞吐。

专用压力环境不得复用生产 bucket、生产 root 凭据或真实 Provider 配置。磁盘填充文件和故障注入资源必须使用独立卷、明确容量上限和可验证清理流程。
