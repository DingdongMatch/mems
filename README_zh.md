# Mems

> 一个让 Agent 不只会聊天、还能持续记住的记忆基础设施。

`Mems` 是面向 AI Agent 的分层记忆后端。
它帮助 Agent 管理当前会话、保存原始对话、沉淀长期知识，并把老数据做成可追溯的长期归档。

你不需要让每个 Agent 都自己重新发明一套 memory 逻辑。Mems 直接提供一组清晰的公开 API，用来：

- 读取当前会话上下文
- 检索长期记忆
- 写入新的 user / assistant turn
- 观察哪些内容被存储、召回、蒸馏和归档

[English](README.md) | [中文](README_zh.md)

## 为什么要做 Mems

大多数 Agent 的记忆问题都很像：

- prompt window 太短，聊几轮就忘
- 原始聊天记录堆了很多，但很难变成稳定知识
- 会话上下文、用户偏好、事实信息、历史归档全混在一起
- 第三方系统要接 memory 时，接口语义往往不清楚

Mems 的思路是：把不同类型的记忆分层，并自动完成层与层之间的流转。

## 它的特点是什么

- **为第三方 Agent 接入而设计**：按 `context -> search -> turns` 就能接入
- **四层记忆清晰分工**：不是一张大表硬塞所有记忆
- **原始记录和结构化知识并存**：既保留对话，也沉淀画像、事实、事件、摘要
- **多 Agent 隔离**：每个 Agent 有自己的记忆空间
- **长期知识可追溯**：L2 可以追到对应的 L1 证据
- **分层召回与冷归档**：默认在线检索聚焦活跃记忆，L3 负责可解释的长周期保留
- **内置 simulator 和 playground**：在接真实外部 Agent 前就能先验收效果

## 一句话理解

你可以把 Mems 理解成四层记忆书架：

- **L0**：Agent 眼前正在聊什么
- **L1**：最近真实发生过什么
- **L2**：哪些内容值得长期记住
- **L3**：哪些历史记录值得长期保存

## 四层记忆架构

| 层级 | 存什么 | 为什么存在 |
|------|--------|-----------|
| `L0` | 当前 session 上下文、最近 turn、临时状态 | 快速还原当前对话 |
| `L1` | 原始情景对话记录 | 保证证据完整、支持回放 |
| `L2` | 画像、事实、事件、摘要、冲突日志 | 形成可复用的长期知识 |
| `L3` | JSONL 历史归档 | 低成本、可长期保存、可人读 |

## 数据是怎么流动的

```text
第三方 Agent
  -> GET /memories/context   -> 当前 live 上下文首页 + 懒加载历史分页
  -> POST /memories/search   -> 默认长期记忆召回（活跃 L1 + L2）
  -> 组装 prompt / 生成回答
  -> POST /memories/turns    -> 把新一轮对话写回系统

后台流水线
  L0 -> L1                   实时持久化
  L1 -> L2                   filter -> extract -> reconcile -> commit
  L1 -> L3                   定时归档
```

## 架构示意图

```text
                        +-------------------------+
                        |   第三方 Agent /        |
                        |   官方 Reference Agent  |
                        +-----------+-------------+
                                    |
                +-------------------+-------------------+
                |                                       |
      GET /memories/context                  POST /memories/search
                |                                       |
                v                                       v
         +------+-------+                       +-------+------+
         |   L0 Redis   |                       |  L1 + L2 检索 |
         | 当前会话记忆 |                       | 长期召回层     |
         +------+-------+                       +-------+------+
                |                                       |
                +-------------------+-------------------+
                                    |
                             Agent 组装 prompt
                                    |
                                    v
                        POST /memories/turns 或 /write
                                    |
                                    v
         +-------------------------------------------------------+
         | L1 情景记忆: SQL + Qdrant + JSONL                     |
         | 原始记录、聊天回放、语义召回、证据层                  |
         +----------------------+--------------------------------+
                                |
                  +-------------+-------------+
                  |                           |
                  v                           v
         +--------+---------+       +---------+--------+
         | L2 语义记忆      |       | L3 长期归档      |
         | 画像/事实/事件/  |       | JSONL 历史记录   |
         | 摘要/冲突日志    |       | 长周期保存       |
         +------------------+       +------------------+
```

## 典型场景

- 给聊天 Agent 提供稳定的会话记忆和长期记忆
- 沉淀用户偏好、身份信息、关系事实
- 为多 Agent 系统提供统一 memory backend
- 在接真实第三方 Agent 前，用 simulator 先做接入验证
- 用纯文本归档长期保存历史记录，而不是留下一堆难读的冷数据

## 对第三方 Agent 来说，接入非常简单

推荐的公开接口时序只有四步：

1. `GET /memories/context` 取当前 live 上下文首页，并按需懒加载更早历史
2. `POST /memories/search` 取长期记忆
3. 由 Agent 自己组装 prompt 并生成回答
4. `POST /memories/turns` 把这轮 user / assistant 写回

内置的 simulator 在正常 chat 模式下走的就是这套流程。

## Memory Identity Model

Mems 现在通过统一的 identity model 同时支持单用户和多用户 Agent 场景。

- `tenant_id`：可选的租户 / 组织边界
- `user_id`：多用户 Agent 场景下强烈建议传入的用户边界
- `agent_id`：使用 Mems 的 Agent 身份
- `session_id`：当前会话或线程
- `scope`：由上层业务定义的可见性标签，例如 `private`、`shared`、`team:sales`

推荐理解方式：

- `tenant_id / user_id / agent_id / session_id` 是硬边界
- `scope` 是上层业务定义的软可见性标签

如果是单用户应用，也可以只保留一个 `user_id`，把 `tenant_id` 留空或使用默认值。

## 核心接口

| 接口 | 方法 | 作用 |
|------|------|------|
| `/memories/write` | `POST` | 写入工作记忆快照，并自动落到 L0/L1 |
| `/memories/turns` | `POST` | 像真实第三方 Agent 一样按 turn 追加消息 |
| `/memories/context` | `GET` | 读取某个 `session_id` 的 live 首页与分页历史 |
| `/memories/search` | `POST` | 做默认在线长期记忆检索（活跃 L1 + L2） |
| `/simulator/chat` | `POST` | 运行官方参考 Agent |
| `/simulator/chat/stream` | `POST` | 以流式方式返回 simulator 输出 |
| `/simulator/playground` | `GET` | 打开可视化调试页，查看 chat / prompt / context / search |
| `/monitor/status` | `GET` | 查看依赖健康和流水线状态 |
| `/health` | `GET` | 轻量健康检查 |

### `/memories/write` 和 `/memories/turns` 的区别

- `/memories/write` 更适合提交一份工作记忆快照
- `/memories/turns` 更适合像第三方 Agent 一样按 turn 增量写入
- `/memories/turns` 还支持 `persist_to_l1`、`ttl_seconds`、`active_plan`、`temp_variables`
- L0 会话上下文会保留一个有限的最近窗口，更像滚动聊天缓冲区，而不是无限增长的全文转录

### `/memories/context` 分页规则

- 不传 `before_id` 时，返回某个 session 的 live 首页
- live 首页优先使用 Redis L0；如果 Redis 不能完整覆盖最近已持久化内容，会补充合并最新 L1 记录
- 前端把上一次响应中的 `next_before_id` 传回 `before_id`，即可继续向前懒加载更早的 L1 历史
- `limit` 表示每页读取的 L1 record 数量，不是最终 message 数量
- 响应中会返回 `page_type`、`has_more`、`next_before_id`，供前端分页使用

### 多用户查询默认规则

- 如果请求中带了 `tenant_id`、`user_id` 或 `scope`，Mems 会把它们作为检索和写入边界
- 因此同一个 `agent_id` 可以服务多个用户，而不会默认共享上下文
- simulator 和 playground 现在也支持这些字段，便于调试真实多用户接入效果

## 为什么底层是这种组合

Mems 刻意没有用“一个数据库包打天下”的方案，而是做了分工：

- **Redis 放 L0**：适合 live 首页上下文，快、便宜、适合 TTL
- **SQL 放 L1 / L2 元数据**：适合结构化查询、状态标记、溯源和对账
- **Qdrant 放向量**：负责语义召回
- **JSONL 放归档**：长期可读、低耦合、容易检查

## 一致性模型

Mems 当前把 SQL 定义为在线记忆状态的真相源。

- L1 / L2 业务记录先提交到 SQL
- Qdrant 向量和 JSONL 文件视为派生副本
- 副本同步失败不会抹掉主 SQL 记录，而是写入副本状态，等待后续修复
- 默认 `/memories/search` 只返回活跃 L1 和 L2 结果
- L1 完成归档后会退出默认在线检索路径，历史内容转入 L3

当前已跟踪的副本状态字段包括：

- `vector_status`
- `jsonl_status`
- `archive_status`
- `last_sync_error`
- `last_sync_at`

运维说明：

- 当前官方异步 Qdrant SDK 路径建议搭配较新的 Qdrant 服务端版本；开发环境现在使用 `Qdrant 1.15.3`

## 蒸馏到底在做什么

Mems 不会把每一句话都当作长期记忆。

蒸馏流程是：

1. **Filter**：过滤寒暄、低价值片段、短期噪声
2. **Extract**：提取画像、事实、事件和摘要候选
3. **Reconcile**：和已有长期记忆对账，识别重复、增强、更新、冲突
4. **Commit**：把稳定结果写入 L2，并保留来源证据

当前 L2 记忆类型包括：

- 画像项
- 事实项
- 事件项
- 滚动摘要
- 冲突日志

当前运行时还需要注意：

- 蒸馏目前依赖可用的 LLM 配置
- 如果 LLM provider 不可用，蒸馏可能会跳过记录，最终不产生新的 L2 输出
- 当前“定时蒸馏”在实现上仍然是阈值门控：定时任务会在触发时检查是否达到阈值，而不是无条件每天强制蒸馏
- 自动模式下，只会处理满足条件的、未蒸馏且重要性足够高的 L1 记录

## 搜索不是简单的向量搜索

Mems 内部会组合多种信号：

- L1 情景召回
- Qdrant 语义召回
- L2 摘要召回
- query intent routing

目前真正做向量召回的重点是活跃 L1 情景记忆和 L2 摘要；画像、事实、事件更多还是走结构化查询和排序逻辑。

所以：

- 偏好类问题会更偏向画像记忆
- 过程类问题会更偏向情景 / 事件记忆
- 概括类问题会更偏向摘要记忆

## Simulator 和 Playground

在接真实外部 Agent 之前，你可以先直接验证整条公开链路。

- `POST /simulator/chat`：官方参考 Agent
- `POST /simulator/chat/stream`：支持流式聊天输出
- simulator 还内置了 system overview 与 health/status 这类轻量模式
- 如果 LLM 调用失败，simulator 会退回模板回答，并在 `debug.fallback_used` 中体现
- `GET /simulator/playground`：浏览器可视化调试页，支持：
  - 中英文切换
  - 聊天视图
  - prompt trace
  - context 窗口
  - search 命中
  - raw response

### 流式返回事件

`POST /simulator/chat/stream` 会按下面这些事件逐步返回：

- `meta`：context 来源、检索结果、基础 debug 信息
- `prompt`：实际构建的 prompt
- `token`：逐段输出的回答内容
- `done`：最终完整响应

## 快速开始

### 1. 启动依赖

```bash
uv sync
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

### 2. 试用公开 API

```bash
# 健康检查
curl http://localhost:8000/health

# 以第三方 Agent 的方式追加 turn
curl -X POST http://localhost:8000/memories/turns \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme",
    "user_id": "user-42",
    "agent_id": "demo-agent",
    "session_id": "demo-session",
    "scope": "private",
    "messages": [
      {"role": "user", "content": "记住我喜欢 Rust"},
      {"role": "assistant", "content": "收到。"}
    ]
  }'

# 读取当前 live 上下文首页
curl "http://localhost:8000/memories/context?tenant_id=acme&user_id=user-42&agent_id=demo-agent&session_id=demo-session&scope=private&limit=10"

# 读取更早的一页历史
curl "http://localhost:8000/memories/context?tenant_id=acme&user_id=user-42&agent_id=demo-agent&session_id=demo-session&scope=private&limit=10&before_id=120"

# 检索长期记忆
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme", "user_id": "user-42", "agent_id": "demo-agent", "scope": "private", "query": "我喜欢什么？"}'

# 运行参考 simulator
curl -X POST http://localhost:8000/simulator/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme", "user_id": "user-42", "agent_id": "demo-agent", "session_id": "demo-session", "scope": "private", "message": "我喜欢什么？"}'
```

### 3. 打开 playground

```bash
open http://localhost:8000/simulator/playground
```

## 监控与自动化

系统已经内置自动化流水线：

- `L1 -> L2` 当前仍然是阈值门控；定时任务本质上是在定时点执行阈值检查，而不是无条件每日蒸馏
- `L1 -> L3` 可定时归档
- `/monitor/status` 会显示：
  - 依赖健康状态
  - 调度器状态
  - pending distill / archive 数量
  - stale profile / fact / summary 数量

当前系统行为里还有几个值得注意的点：

- L1 归档成功后，不再参与默认在线检索
- L1 turn 会保留结构化消息元数据，所以从 L1 fallback 时仍然像聊天记录
- L2 摘要已进入向量索引，便于长期召回
- 每个 Agent 的 Qdrant collection 和 payload index 会自动创建

## 技术栈

- Python 3.12+
- FastAPI
- SQLModel
- Redis
- Qdrant
- APScheduler
- SQLite（开发）/ PostgreSQL-ready 设计
- uv

## 项目结构

```text
mems/
├── src/mems/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── services/
│   │   ├── redis_service.py
│   │   ├── l0_sync.py
│   │   ├── vector_service.py
│   │   ├── embedding.py
│   │   ├── distill.py
│   │   └── archive.py
│   ├── routers/
│   │   ├── memories.py
│   │   ├── simulator.py
│   │   └── monitor.py
│   └── static/
│       └── simulator_playground.html
├── scripts/
└── storage/
```

## 当前状态

- 面向第三方 Agent 的公开接入流已可用
- 官方 simulator 和 browser playground 已可用
- Qdrant 已接入官方异步 SDK
- 自动化测试已覆盖 write、search、archive、distill、reconciliation、context fallback 和 simulator 主链路

## 后续规划

- 增加陈旧画像/事实/摘要的自动复核任务
- 为画像与事实补齐向量索引，而不只对摘要做向量召回
- 提升 reconciliation 能力，支持更细的冲突分类与置信度合并
- 增加按周、按月的滚动摘要
- 增加检索质量与蒸馏质量评测工具

## 下一步优化计划

- 增加副本修复任务，自动重试失败的 `vector/jsonl/archive` 同步
- 增加 L3 深度检索，让长周期归档记忆可召回但不污染默认搜索
- 引入 migration 机制，支撑模型和副本状态字段演进
- 在监控中增加副本 backlog 和失败计数可观测性

## 开发

```bash
uv run pytest
uv run ruff check src tests
```

## 更多文档

- `README.md` - 英文主文档
- `docs/TECHNICAL.md` - 进阶技术说明
- `docs/TECHNICAL_en.md` - English advanced technical notes
- `docs/VIBE_CODING.md`
- `docs/VIBE_CODING_en.md`

## 开源协议

Licensed under the Apache License, Version 2.0. See `LICENSE` for details.
