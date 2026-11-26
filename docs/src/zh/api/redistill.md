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

请求体为 JSON。`tenant_id`、`user_id`、`agent_id` 为必填项；缺少请求体或发送
空对象（`{}`）会返回 HTTP 422。再蒸馏始终限制在所选择的 identity 与分区内。

| 字段 | 类型 | 必填 | 默认 | 约束 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `tenant_id` | string | 是 | — | — | 租户边界 |
| `user_id` | string | 是 | — | — | 用户边界 |
| `agent_id` | string | 是 | — | — | 只处理该 agent 的归档记录 |
| `namespace` | string 数组 | 否 | `[]` | 最多 16 个非空路径段，每段最多 128 字符 | 精确分区路径；省略时仅选择该 identity 的默认分区，不搜索所有子路径 |
| `start_date` | string | 否 | `null` | ISO-8601 | 只处理 `created_at >= start_date` 的记录 |
| `end_date` | string | 否 | `null` | ISO-8601 | 只处理 `created_at <= end_date` 的记录 |
| `min_importance` | number | 否 | `0.0` | 0-1 | 只处理 `importance_score >= min_importance` 的记录；为 0 时不过滤 |
| `batch_size` | number | 否 | `REDISTILL_BATCH_SIZE` | 1-1000 | 最多尝试的有效记录数（含失败）；服务层会将其限制在 `REDISTILL_BATCH_SIZE`（默认 100）以内 |

identity 与 namespace 组合后的规范 JSON 键（包括版本标记和路径分隔符）最多为 **512 个 UTF-8 字节**；超过限制返回 HTTP 422。此限制也适用于省略 namespace 时的默认分区。

> `start_date` / `end_date` 针对归档记录自身的 `created_at`（即原 L1 写入时间），
> 不是文件归档时间。无时区的时间串按 UTC 处理。

### 请求示例

```json
{
  "tenant_id": "acme",
  "user_id": "user-42",
  "agent_id": "demo-agent",
  "namespace": ["projects", "prj_demo"],
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
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "batch_size": 100
  }'
```

## 响应

### HTTP 200

```json
{
  "success": true,
  "attempted": 37,
  "failed": 0,
  "processed": 37,
  "created": 12,
  "updated": 8,
  "skipped": 17,
  "message": "Re-distilled 37 records with gpt-4o-mini (attempted 37, failed 0, created 12, updated 8, skipped 17)"
}
```

### 字段含义

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `success` | boolean | 无处理失败时为 true；单条或运行失败、LLM 禁用时为 false |
| `attempted` | number | 本次尝试处理的有效记录数；失败也占批次额度 |
| `failed` | number | 本次处理失败的记录数；失败不写进度，可重试 |
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
- 画像和单值事实按原始 created_at 对账；旧来源不会覆盖更新的值，等时来源允许模型升级修正。
- L2 新记录保留原 L1 id 到 `source_l1_ids`，冲突经确定性对账写入
  `mems_l2_conflict_log`，版本链向前（`supersedes` + `version`）。

### 容错

- 单条记录在处理阶段失败（如 LLM 错误）会记录日志并跳过，**不标记进度**，
  下次可重试；
- JSON 语法损坏、UTF-8 非法或非对象 JSON 行会记录日志并跳过；
- 归档文件缺失、路径不安全或不可读取时，记录日志并跳过；
- 每条成功记录独立 commit；后续失败保留已提交的进度和计数。
- 完成的运行返回 HTTP 200；处理失败时 success=false，并通过 attempted / failed / processed 报告结果。捕获的运行异常也保留已提交计数，返回通用失败消息；未处理的接口异常仍返回 HTTP 500。

### 前置条件

- 需要 LLM_PROVIDER=openai 和可用的 OpenAI-compatible LLM（`OPENAI_API_KEY` 等）；
- 需要至少一个已归档批次；没有任何归档时返回
  `processed=0` 与提示消息。

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| 缺少请求体、空 `{}` 或缺少 identity | `422` | `tenant_id`、`user_id`、`agent_id` 为必填项 |
| 参数类型/范围非法 | `422` | 校验失败（如 `min_importance` 越界、日期格式错误） |
| 未处理的接口异常 | `500` | 通用内部错误响应，诊断详情记录在服务端日志 |
| 运行内处理失败 | `200`（`success=false`） | 保留已提交计数，失败记录不写进度 |
| LLM 禁用 / key 无效 | `200`（`success=false`） | 禁用时 attempted=0；调用失败计入 failed，并占批次额度 |

> 单条处理失败通过 success=false 和 failed 显式报告；损坏行计入 skipped。

## 注意

- 再蒸馏是手动动作，没有定时任务；模型升级后建议主动执行一次；
- 在线召回**不会**自动触发再蒸馏，也不会自动 fallback 到 L3；
- 需要先有归档（默认每天 3:00 自动归档，也可等待 `ARCHIVE_DAYS` 到期）；
- 再蒸馏会调用 LLM，成本与耗时按记录数线性增长，建议配合 `batch_size` 与
  `min_importance` 使用。
