# L0 记忆输出管道

Mems 可以把每一次 L0 记忆写入作为一条事件发布到 Redis Stream，这样你就可以用
自己的代码消费记忆：镜像到另一个存储、驱动实时看板，或者触发下游工作流。

管道**默认关闭**。在启用之前，它不会发布任何内容、也不会创建流，现有部署不受影响。

## 启用

在 `.env` 中设置：

```bash
L0_PIPELINE_ENABLED=true
L0_PIPELINE_STREAM=mems:l0:events
L0_PIPELINE_MAXLEN=10000
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `L0_PIPELINE_ENABLED` | `false` | 输出管道总开关 |
| `L0_PIPELINE_STREAM` | `mems:l0:events` | 记忆事件使用的 Redis Stream 键 |
| `L0_PIPELINE_MAXLEN` | `10000` | 流的近似最大长度（旧事件会被裁剪） |

无需新增基础设施：事件写入与 L0 相同的 Redis 实例。

## 事件格式

每条事件是流中的一组扁平字段：

| 字段 | 说明 |
| --- | --- |
| `event_id` | Mems 生成的稳定 id，可用于幂等 |
| `event_type` | `write`（新建 L0 快照）或 `append`（在缓冲区上追加） |
| `timestamp` | 发布时间（UTC ISO-8601） |
| `tenant_id` / `user_id` / `agent_id` / `session_id` | 身份上下文；未设置的字段为空字符串 |
| `content` | 本次事件后的 L0 记忆文本快照 |
| `messages` | JSON 编码的短期缓冲区 |
| `expires_at` | L0 快照的过期时间（TTL 结束时刻） |

## 用自己的代码消费

任何 Redis 客户端都可以。从头开始读取：

```bash
redis-cli XREAD COUNT 10 STREAMS mems:l0:events 0
```

阻塞并尾随新事件：

```bash
redis-cli XREAD BLOCK 5000 STREAMS mems:l0:events $
```

需要并行消费者并确认消费时，先创建消费组，再用 `XREADGROUP` 读取：

```bash
redis-cli XGROUP CREATE mems:l0:events consumers 0 MKSTREAM
redis-cli XREADGROUP GROUP consumers worker1 COUNT 10 BLOCK 5000 \
  STREAMS mems:l0:events >
```

Python 消费示例：

```python
import asyncio, json
import redis.asyncio as redis

async def main():
    client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    stream = "mems:l0:events"
    group = "consumers"
    try:
        await client.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError:
        pass  # 消费组已存在

    while True:
        entries = await client.xreadgroup(group, "worker1", {stream: ">"}, count=10)
        for _, events in entries:
            for event_id, fields in events:
                print(event_id, fields["event_type"], fields["agent_id"],
                      fields["content"], json.loads(fields["messages"]))
                await client.xack(stream, group, event_id)

asyncio.run(main())
```

## 保证

- **尽力而为发布**：如果流写入失败，Mems 只记录警告，记忆写入本身仍然成功。
  管道永远不会阻塞 `POST /v1/mems/write`。
- **有界流**：事件按 `L0_PIPELINE_MAXLEN` 近似裁剪。消费速度跟不上的场景建议
  使用消费组，或从较早的偏移量追赶。
- **快照语义**：每条事件携带写入/追加完成后的完整 L0 快照，消费者可以把每条
  事件视为该 agent 会话热记忆的当前状态。
- **与 L0 同一信任域**：事件包含完整记忆内容，并与 L0 快照存放在同一个 Redis
  实例/数据库，访问控制与加密级别与 L0 其余部分一致。
- **没有删除事件**：v1 不发布删除事件。L0 的自然生命周期是快照按 TTL 过期，
  流中不存在 tombstone 条目。
