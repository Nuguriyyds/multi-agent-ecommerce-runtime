from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.v2.api.session_service import V2SessionService

SIGINT_EXIT_CODE = 130


@dataclass(frozen=True, slots=True)
class BackgroundWorkerRunResult:
    processed_event_ids: tuple[str, ...]

    @property
    def idle(self) -> bool:
        return not self.processed_event_ids


class V2BackgroundWorker:
    def __init__(
        self,
        service: V2SessionService,
        *,
        limit: int = 100,
        poll_interval: float = 1.0,
    ) -> None:
        self.service = service
        self.limit = max(1, int(limit))
        self.poll_interval = max(0.0, float(poll_interval))

    async def run_once(self) -> BackgroundWorkerRunResult:
        processed = await self.service.process_background_events(limit=self.limit)
        return BackgroundWorkerRunResult(
            processed_event_ids=tuple(event.event_id for event in processed),
        )

    async def run_forever(
        self,
        *,
        max_loops: int | None = None,
        max_idle_loops: int | None = None,
    ) -> int:
        loops = 0
        idle_loops = 0
        processed_count = 0

        while True:
            result = await self.run_once()
            loops += 1
            processed_count += len(result.processed_event_ids)

            if result.idle:
                idle_loops += 1
            else:
                idle_loops = 0

            if max_loops is not None and loops >= max_loops:
                return processed_count
            if max_idle_loops is not None and idle_loops >= max_idle_loops:
                return processed_count

            await asyncio.sleep(self.poll_interval)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the V2 background refresh worker.")
    parser.add_argument(
        "--database",
        default=str(Path(".tmp") / "v2_runtime" / "v2.sqlite3"),
        help="SQLite database path shared with the V2 HTTP app.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum pending events to process per loop.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Sleep interval in seconds between polling loops.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch and exit.",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        help="Stop after N polling loops.",
    )
    parser.add_argument(
        "--max-idle-loops",
        type=int,
        default=None,
        help="Stop after N consecutive idle loops.",
    )
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    service = V2SessionService(Path(args.database))
    worker = V2BackgroundWorker(
        service,
        limit=args.limit,
        poll_interval=args.poll_interval,
    )

    if args.once:
        result = await worker.run_once()
        print(f"processed_events={len(result.processed_event_ids)}")
        if result.processed_event_ids:
            print("event_ids=" + ",".join(result.processed_event_ids))
        return 0

    processed_count = await worker.run_forever(
        max_loops=args.max_loops,
        max_idle_loops=args.max_idle_loops,
    )
    print(f"processed_events={processed_count}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        return SIGINT_EXIT_CODE


__all__ = [
    "BackgroundWorkerRunResult",
    "SIGINT_EXIT_CODE",
    "V2BackgroundWorker",
    "async_main",
    "build_arg_parser",
    "main",
]
