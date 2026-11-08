# Redistill — L3 → L2 再蒸馏

## Endpoint

| 项 | 值 |
| --- | --- |
| URI | `/v1/mems/redistill` |
| 方法 | `POST` |
| Content-Type | `application/json` |
| 默认 Base URL | `http://localhost:8210` |

手动触发 L3 归档记录（JSONL）→ L2 的再蒸馏：用当前 `OPENAI_MODEL` 把归档原文
重新走「LLM 结构化提取 + 确定性对账 + L2 版本链升级」，实现记忆随模型进化。

再蒸馏是**手动维护动作**，与在线召回无关；`/v1/mems/query` 不会因为执行过
再蒸馏而改变召回行为，除非 L2 记录本身发生变化。

## 请求参数

请求体为 JSON，所有字段均可选（空请求 = 全量再蒸馏）：

| 字段 | 类型 | 必填 | 默认 | 约束 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | string | 否 | `null` | — | 只处理该 agent 的归档记录 |
| `start_date` | string | 否 | `null` | ISO-8601 | 只处理 `created_at >= start_date` 的记录 |
| `end_date` | string | 否 | `null` | ISO-8601 | 只处理 `created_at <= end_date` 的记录 |
| `min_importance` | number | 否 | `0.0` | 0-1 | 只处理 `importance_score >= min_importance` 的记录；为 0 时不过滤 |
| `batch_size` | number | 否 | `100` | 1-1000 | 本次最多处理多少条 L3 记录（HTTP 层约束）；缺省使用 `REDISTILL_BATCH_SIZE` |

> `start_date` / `end_date` 针对归档记录自身的 `created_at`（即原 L1 写入时间），
> 不是文件归档时间。无时区的时间串按 UTC 处理。

### 请求示例

```json
{
  "agent_id": "demo-agent",
  "start_date": "2026-01-01T00:00:00Z",
  "end_date": "2026-06-30T23:59:59Z",
  "min_importance": 0.3,
  "batch_size": 100
}
```

```bash
curl -X POST http://localhost:8210/v1/mems/redistill \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "demo-agent",
    "batch_size": 100
  }'
```

## 响应

### HTTP 200

```json
{
  "success": true,
  "processed": 37,
  "created": 12,
  "updated": 8,
  "skipped": 17,
  "message": "Re-distilled 37 records with gpt-4o-mini (created 12, updated 8, skipped 17)"
}
```

### 字段含义

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `success` | boolean | 运行是否成功；`false` 时 message 给出原因 |
| `processed` | number | 本次实际进入再蒸馏并成功的记录数（不含 skipped） |
| `created` | number | 新建的 L2 记录数（Profile/Fact/Event/Summary） |
| `updated` | number | 更新/增强的已有 L2 记录数 |
| `skipped` | number | 跳过的记录数（已用当前模型处理过，或记录缺少 `content`） |
| `message` | string | 结果摘要或错误信息 |

## 行为说明

### 数据来源

- 归档 JSONL 位于 `ARCHIVE_STORAGE_PATH`（默认 `storage/l3_archive/`）；
- 批次元数据读取自 `mems_l3_archive` 表；
- 单行记录包含原 L1 的 identity、content、messages、`created_at`、
  `importance_score` 等字段。

### 去重与版本链

- 以「内容指纹 + 模型名」去重，唯一键 `(content_hash, model)`；
- 已用当前 `OPENAI_MODEL` 处理过的记录会跳过；更换模型后，旧记录自动重新具备
  再蒸馏资格；
- 再蒸馏进度写入 `mems_redistill_progress`，同时记录 `l3_path` / `l3_line`
  兜底证据指针；
- L2 新记录保留原 L1 id 到 `source_l1_ids`，冲突经确定性对账写入
  `mems_l2_conflict_log`，版本链向前（`supersedes` + `version`）。

### 容错

- 单条记录在处理阶段失败（如 LLM 错误）会记录日志并跳过，**不标记进度**，
  下次可重试；JSON 语法损坏（某一行无法解析）会让整次运行失败并返回
  `success=false`；
- 运行结束统一 commit；整体异常时返回 `success=false` 与 message。

### 前置条件

- 需要可用的 OpenAI-compatible LLM（`OPENAI_API_KEY` 等）；
- 需要至少一个已归档批次；没有任何归档时返回
  `processed=0` 与提示消息。

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| 参数类型/范围非法 | `422` | 校验失败（如 `min_importance` 越界、日期格式错误） |
| 整体运行失败（归档文件不可读、JSON 损坏等） | `500` | `detail` 为 message |
| LLM 未配置 / key 无效 | `200`（`success=true`、`processed=0`） | 每条记录都会失败并记日志，整体仍返回 200；请先检查 `OPENAI_*` 配置 |

> 单行失败不会导致整个请求 500；只有整体运行失败才返回 500。

## 注意

- 再蒸馏是手动动作，没有定时任务；模型升级后建议主动执行一次；
- 在线召回**不会**自动触发再蒸馏，也不会自动 fallback 到 L3；
- 需要先有归档（默认每天 3:00 自动归档，也可等待 `ARCHIVE_DAYS` 到期）；
- 再蒸馏会调用 LLM，成本与耗时按记录数线性增长，建议配合 `batch_size` 与
  `min_importance` 使用。
