"""CLI entrypoint for manually re-distilling L3 archives into L2.

手动再蒸馏的命令行入口：python -m mems.redistill
"""

import argparse
import asyncio
from datetime import datetime, timezone

from mems.interfaces.bootstrap import trigger_redistill


def _parse_date(value: str) -> datetime:
    """Parse an ISO date, treating naive values as local time."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually re-distill L3 archived memories into L2 "
        "using the current OPENAI_MODEL."
    )
    parser.add_argument("--agent", dest="agent_id", default=None)
    parser.add_argument("--start", dest="start_date", default=None)
    parser.add_argument("--end", dest="end_date", default=None)
    parser.add_argument("--min-importance", type=float, default=0.0)
    parser.add_argument("--batch", type=int, default=None)
    args = parser.parse_args()

    result = asyncio.run(
        trigger_redistill(
            agent_id=args.agent_id,
            start_date=_parse_date(args.start_date) if args.start_date else None,
            end_date=_parse_date(args.end_date) if args.end_date else None,
            min_importance=args.min_importance,
            batch_size=args.batch,
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
