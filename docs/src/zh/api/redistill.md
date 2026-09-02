# `POST /v1/mems/redistill` — 再蒸馏归档记忆

手动用当前 `OPENAI_MODEL` 将归档 L3 记录再蒸馏到 L2。这是「记忆随模型进化」的
闭环：模型升级后，归档 JSONL 重新进入提取与对账流程，通过版本链
（`supersedes` + 冲突日志）升级 L2 知识。

## 请求字段

- `agent_id`：可选，只再蒸馏某个 agent 的归档记录
- `start_date` / `end_date`：可选的创建时间范围（含端点）
- `min_importance`：最低 `importance_score`（0-1），默认 `0.0`
- `batch_size`：本次最多处理的 L3 记录数，默认 `REDISTILL_BATCH_SIZE`

## 行为说明

- 从 `storage/l3_archive/*.jsonl` 与 `mems_l3_archive` 批次元数据读取。
- 按内容指纹 + 模型去重，已用当前模型处理过的记录会被跳过；更换模型后旧数据
  自动重新具备再蒸馏资格。
- 缺失或损坏的行会记录日志并跳过、不标记进度，下次可重试。
- 返回 `processed` / `created` / `updated` / `skipped` 计数。

## CLI 方式

```bash
uv run python -m mems.redistill --agent demo-agent
```
