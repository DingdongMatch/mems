# Health — 健康检查

## Endpoint

| 项 | 值 |
| --- | --- |
| URI | `/v1/mems/health` |
| 方法 | `GET` |
| 默认 Base URL | `http://localhost:8210` |

轻量存活（liveness）探针，适合：

- 本地启动后的 smoke test；
- 容器健康检查；
- 反向代理 / 编排系统的存活探针。

该端点**只返回进程是否存活**，不探测数据库、Redis、Qdrant 或调度器；完整
依赖健康请使用 [`/v1/mems/status`](/zh/api/status)。

## 请求参数

无。`GET` 请求，不需要请求体。

```bash
curl http://localhost:8210/v1/mems/health
```

## 响应

### HTTP 200

```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### 字段含义

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | string | 当前恒为 `healthy`（能返回 200 即视为存活） |
| `version` | string | Mems 服务版本 |

## 错误

| 场景 | HTTP | 说明 |
| --- | --- | --- |
| 服务未启动 / 已停止 | 连接失败 | 检查进程与端口 |

即使依赖组件不可用，该端点通常仍返回 200——它不执行组件探测。生产环境如需
「依赖就绪才放流量」，应改用 `/status` 判断各组件健康。
