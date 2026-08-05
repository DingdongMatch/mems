# 架构总览

![Mems 架构图](/architecture.svg)

*L0 热缓存 → L1 情景账本 → L2 蒸馏知识 → L3 冷归档；另有一条默认关闭的 L0
输出流，供自带代码的外部消费者使用。*

## 四层设计

| 层级 | 职责 | 存储 | 关键实体 |
| --- | --- | --- | --- |
| `L0` | 近期热记忆缓存、快速召回窗口；不管理运行时状态 | Redis | `mems:l0:*` 快照（TTL）、`short_term_buffer`、可选流 `mems:l0:events` |
| `L1` | 情景记忆账本：在线事实来源，支持回放与审计 | SQL + Qdrant | `mems_l1_episodic`；Qdrant 集合 `agent_{agent_id}`（派生向量副本） |
| `L2` | 带来源链路与版本管理的蒸馏长期知识 | SQL（+ 摘要向量索引） | `mems_l2_profile_item`、`mems_l2_fact`、`mems_l2_event`、`mems_l2_summary`、`mems_l2_conflict_log` |
| `L3` | 跨世纪冷归档，格式可移植 | JSONL 文件 | `storage/l3_archive/*.jsonl`、`mems_l3_archive` 批次元数据 |

## 数据流

- **写入（Remember）**（`POST /v1/mems/write`）：写入 L0 快照（TTL）→
  `sync_l0_to_l1` 落库 `mems_l1_episodic`，并尽力同步 Qdrant 向量副本；
  当 `L0_PIPELINE_ENABLED=true` 时还会向 Redis Stream 发布 `write`/`append` 事件。
- **召回（Recall）**（`POST /v1/mems/query`）：读取 L0 跨会话活跃快照，汇总
  L1/L2 SQL 候选，结合 Qdrant 向量检索，再按归一化 base（0-1）+ 意图权重 +
  新鲜度排序；词法重叠使用 CJK bigram + ASCII 整词。默认排除已归档 L1。
- **蒸馏（L1 → L2）**：阈值触发。每次成功写入后，后台任务检查未蒸馏记录；
  达到 `DISTILL_THRESHOLD`（默认 100）即执行 LLM 提取与对账，创建/更新画像、
  事实、事件、摘要与冲突日志，并同步摘要向量。`importance_score >=
  DISTILL_HIGH_IMPORTANCE_THRESHOLD`（默认 0.8）的记录写入后立即蒸馏
  （batch=1），不等待全局阈值；批内按重要度降序、创建时间升序取数。
  无定时蒸馏任务。
- **归档（L1 → L3）**：APScheduler 每日 03:00 将早于 `ARCHIVE_DAYS` 的记录
  原子写入 JSONL 批次、登记元数据，并标记 L1 已归档。
- **L0 输出管道**（默认关闭）：消费者用 `XREAD` / `XREADGROUP` 配合自己的代码
  消费事件；发布为尽力而为，绝不阻断 `/write`。详见
  [L0 输出管道](../integration/l0-pipeline)。

## 组件

- **API 层**：FastAPI 路由统一挂在 `/v1/mems/*`；依赖注入
  `Session`、`RedisService`、`VectorService`、`EmbeddingService`。
- **服务层**：`redis_service`（L0）、`l0_sync`（L0→L1）、`vector_service`
  （Qdrant 异步 SDK）、`embedding`（sentence-transformers 或 openai）、
  `llm_client`（OpenAI-compatible）、`distill`、`archive`、`scheduler`
  （APScheduler 单例）、`jsonl_utils`。
- **隔离模型**：硬边界 `tenant_id` / `user_id` / `agent_id`；软标签 `scope`；
  `session_id` 为可选溯源。多用户隔离不能只依赖 `agent_id`。
- **失败容忍**：向量副本同步与流发布均为尽力而为；蒸馏、归档在后台执行，
  以状态列跟踪（`vector_status`、`archive_status`、`is_distilled`、`is_archived`）。

## 部署说明

- 端口：Mems API `8210`、Qdrant `6333`、Redis `6379`。
- 测试全部使用 Fake 注入（`FakeRedisService`、`FakeVectorService`、
  `FakeEmbeddingService`），无需外部服务。
- 蒸馏依赖 OpenAI-compatible LLM 配置；无凭据时自动跳过。
