# Status — 系统状态与流水线指标

## Endpoint

| 项 | 值 |
| --- | --- |
| URI | `/v1/mems/status` |
| 方法 | `GET` |
| 默认 Base URL | `http://localhost:8210` |

返回四类信息：

1. 整体健康状态；
2. 各组件的健康检查（database / redis / qdrant / scheduler）；
3. 记忆流水线指标（待蒸馏、待归档、L2 计数、冲突数、陈旧知识数）；
4. 服务版本与时间戳。

## 请求参数

无。`GET` 请求，不需要请求体。

```bash
curl http://localhost:8210/v1/mems/status
```

## 响应

### HTTP 200

```json
{
  "status": "degraded",
  "version": "0.1.0",
  "timestamp": "2026-09-06T08:00:00Z",
  "checks": {
    "database": {"status": "healthy", "detail": null},
    "redis": {"status": "healthy", "detail": null},
    "qdrant": {"status": "healthy", "detail": null},
    "scheduler": {"status": "degraded", "detail": "scheduler stopped"}
  },
  "pipeline": {
    "pending_distill": 3,
    "pending_archive": 12,
    "recent_failures": 0,
    "l0_pipeline_enabled": false,
    "profile_items": 15,
    "fact_items": 28,
    "summary_items": 4,
    "conflict_count": 2,
    "stale_profile_items": 1,
    "stale_fact_items": 3,
    "stale_summary_items": 0
  }
}
```

### 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | string | 整体状态：`healthy` / `degraded` / `unhealthy`（计算规则见下） |
| `version` | string | Mems 服务版本 |
| `timestamp` | string | 状态采样时间（UTC ISO-8601） |
| `checks` | object | 组件健康检查，key 为 `database` / `redis` / `qdrant` / `scheduler` |
| `pipeline` | object | 记忆流水线指标 |

### checks 字段

每个检查项结构：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | string | `healthy` / `degraded` / `unhealthy` |
| `detail` | string \| null | 异常原因；健康时为 `null` |

| key | 检查内容 |
| --- | --- |
| `database` | SQL 连接与基础查询 |
| `redis` | L0 Redis ping |
| `qdrant` | Qdrant collection 读取 |
| `scheduler` | 调度器是否运行、归档任务是否注册（停止或缺任务时为 `degraded`） |

整体状态计算规则：

```text
任一 check 为 unhealthy → 整体 unhealthy
否则任一 check 为 degraded → 整体 degraded
否则 → healthy
```

### pipeline 字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `pending_distill` | number | 当前未蒸馏的 L1 记录数 |
| `pending_archive` | number | 已超过 `ARCHIVE_DAYS`（默认 30 天）仍未归档的 L1 记录数 |
| `recent_failures` | number | 近期失败数（当前版本恒为 0，预留给 Outbox 失败统计） |
| `l0_pipeline_enabled` | boolean | L0 记忆输出管道是否开启 |
| `profile_items` | number | 活跃 L2 Profile 数 |
| `fact_items` | number | 活跃 L2 Fact 数 |
| `summary_items` | number | L2 Summary 数 |
| `conflict_count` | number | 冲突日志总数（`mems_l2_conflict_log`） |
| `stale_profile_items` | number | 超过 365 天未验证的活跃 Profile 数 |
| `stale_fact_items` | number | 超过 365 天未验证的活跃 Fact 数 |
| `stale_summary_items` | number | 超过 365 天未验证的 Summary 数 |

注意：

- `pending_archive` 的「过期」指 `created_at < now - ARCHIVE_DAYS`；
- `stale_*` 的阈值固定为 365 天（`STALE_MEMORY_DAYS`），与验证衰减的排序影响
  对应；
- `summary_items` 是全部 Summary（含待同步），不是只统计活跃项。

## 使用建议

- 健康探针：`GET /v1/mems/status` 适合接入前检查与人工排障；
- 调度器不可用时归档不会自动触发；请检查 `SCHEDULER_ENABLED` 与进程状态；
- `pending_distill` 持续增长说明 Outbox worker 未消费或 LLM 未配置；
- 该端点通过依赖注入聚合各健康检查，便于测试替换。

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| 服务不可达 | 连接失败 | 检查进程与端口 |
| 内部异常 | `500` | 聚合状态时出现未捕获异常 |

组件级异常不会让整个端点 500：对应检查项会标记为 `unhealthy` 并携带
`detail`，整体 `status` 按规则降级。
