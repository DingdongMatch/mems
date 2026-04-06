# 贡献指南

## 本地开发

```bash
uv sync
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

## 质量检查

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
```

## 提交贡献

1. 创建功能分支
2. 保持改动最小且聚焦
3. 补充或更新测试
4. 确保 lint 和测试通过
5. 提交 PR，说明修改背景、行为变化和验证方式

如果改动会影响接入方式、架构含义或公开接口，请同步更新文档站内容。
