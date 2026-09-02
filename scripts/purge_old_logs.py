#!/usr/bin/env python3
"""
Purge JSONL log entries older than RETENTION_DAYS from telephony''s orders and cost logs.

Design decisions:
  - Purge field: emitted_at (when the event was recorded, always UTC ISO-8601).
  - Entire lines are deleted: no in-place anonymisation.  The full record including
    the structured order object is removed after 30 days.
  - Atomic write: records are written to a temp file next to the target, then renamed
    over it.  A crash mid-write never leaves a truncated or corrupt log.
  - Malformed lines (no emitted_at, bad JSON) are silently KEPT to avoid data loss.
  - Idempotent: safe to re-run.

Usage:
    python scripts/purge_old_logs.py [--dry-run] [--days N]

Cron example (2 AM UTC daily):
    0 2 * * * cd /app && python scripts/purge_old_logs.py >> /data/logs/purge.log 2>&1
"""
import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

RETENTION_DAYS: int = int(os.getenv("RETENTION_DAYS", "30"))


def _purge_file(path: str, cutoff: datetime, dry_run: bool) -> tuple[int, int]:
    """Purge one JSONL file.  Returns (total_lines, kept_lines)."""
    if not os.path.exists(path):
        log.info("File not found, skipping: %s", path)
        return 0, 0

    total = 0
    kept_lines: list[bytes] = []

    with open(path, "rb") as fh:
        for raw in fh:
            raw_stripped = raw.rstrip(b"\r\n")
            if not raw_stripped:
                continue
            total += 1
            try:
                record = json.loads(raw_stripped)
                emitted_str = record["emitted_at"]
                emitted = datetime.fromisoformat(emitted_str)
                if emitted.tzinfo is None:
                    emitted = emitted.replace(tzinfo=timezone.utc)
                if emitted >= cutoff:
                    kept_lines.append(raw_stripped)
                else:
                    log.debug(
                        "Purging %s record emitted_at=%s",
                        os.path.basename(path), emitted_str,
                    )
            except (KeyError, ValueError, json.JSONDecodeError):
                # Keep malformed / unknown lines — do not silently destroy data.
                kept_lines.append(raw_stripped)
                log.warning("Kept unparseable line in %s", os.path.basename(path))

    kept = len(kept_lines)
    log.info(
        "%s | total=%d kept=%d purged=%d | dry_run=%s",
        os.path.basename(path), total, kept, total - kept, dry_run,
    )

    if not dry_run and kept < total:
        # Atomic overwrite via temp file + rename.
        target_dir = os.path.dirname(os.path.abspath(path))
        with tempfile.NamedTemporaryFile(
            dir=target_dir, delete=False, suffix=".tmp"
        ) as tmp:
            for line in kept_lines:
                tmp.write(line + b"\n")
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        log.info("Rewrote %s (%d lines remaining)", path, kept)

    return total, kept


def purge(dry_run: bool = False, days: int = RETENTION_DAYS) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    log.info(
        "Purge run started | cutoff=%s | dry_run=%s",
        cutoff.isoformat(), dry_run,
    )

    for log_path in [config.ORDERS_LOG_PATH, config.COST_LOG_PATH]:
        _purge_file(log_path, cutoff, dry_run)

    log.info("Purge run complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge old telephony JSONL log entries.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without making changes.")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS,
                        help=f"Retention window in days (default: {RETENTION_DAYS}).")
    args = parser.parse_args()
    purge(dry_run=args.dry_run, days=args.days)
