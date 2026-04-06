# `GET /v1/mems/status`

查看系统依赖健康和内置流水线状态。

## 返回内容

- 数据库、Redis、Qdrant、调度器健康状态
- pending distill / archive 数量
- 近期失败数
- profile / fact / summary 计数
- stale memory 计数
