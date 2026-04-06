# 架构总览

```text
Third-party Agent
  |-- GET /v1/mems/context --> L0 Redis (live session memory)
  |-- POST /v1/mems/query --> L1 + L2 recall
  `-- POST /v1/mems/write --> L1 SQL + Qdrant (episodic memory)
                                  |-- L2 distilled knowledge
                                  `-- L3 JSONL archive
```

## 四层职责

| 层级 | 作用 | 存储 |
| --- | --- | --- |
| `L0` | 当前会话工作记忆、最近消息窗口、临时状态 | Redis |
| `L1` | 原始情景历史、回放、在线检索证据 | SQL + Qdrant |
| `L2` | 画像、事实、事件、摘要、冲突日志 | SQL + 部分向量索引 |
| `L3` | 长期冷归档 | JSONL |

## 后台流水线

- `L0 -> L1`：写入时实时持久化
- `L1 -> L2`：过滤、提取、对账、提交
- `L1 -> L3`：按计划归档历史记录

## 当前实现要点

- 默认公开 API 统一挂在 `/v1/mems/*`
- 调度器在应用生命周期内启动
- 当前蒸馏依赖 OpenAI-compatible LLM 配置
- 开发环境默认使用 `Qdrant 1.15.3`
