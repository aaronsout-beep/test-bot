#!/usr/bin/env python3
"""
Automatic collection of "hard" formatting cases for later training.

Writes JSONL records with original text, candidate text, stage and reason.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HARD_CASES_PATH = Path(
    os.environ.get("FORMAT_HARD_CASES_PATH", "format_model/hard_cases.jsonl")
).resolve()


@dataclass
class HardCase:
    stage: str
    reason: str
    original: str
    candidate: str = ""
    meta: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def save_hard_case(
    stage: str,
    reason: str,
    original: str,
    candidate: str = "",
    meta: dict | None = None,
) -> None:
    """Append a hard case record to JSONL file."""
    try:
        case = HardCase(
            stage=str(stage),
            reason=str(reason)[:200],
            original=str(original or ""),
            candidate=str(candidate or ""),
            meta=meta or {},
        )
        HARD_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HARD_CASES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
    except Exception:
        # Логирование не должно ломать основной поток.
        return

