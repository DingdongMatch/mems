# Contributing

## Local Development

```bash
uv sync
docker compose up -d
python scripts/init_db.py
uv run python -m mems.main
```

## Quality Checks

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
```

## Contribution Flow

1. Create a feature branch
2. Keep changes focused and minimal
3. Add or update tests where needed
4. Ensure lint and tests pass
5. Open a PR that explains the motivation, behavior change, and verification steps

If your change affects integration flow, architecture meaning, or public APIs, update the docs site in the same PR.
