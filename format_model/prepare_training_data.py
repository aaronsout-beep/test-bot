#!/usr/bin/env python3
"""
Prepare and validate formatting examples for RAG checks.

Usage:
  python format_model/prepare_training_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_PATH = ROOT / "examples.jsonl"
OUT_DIR = ROOT / "build"
TRAIN_PATH = OUT_DIR / "train.jsonl"
VALID_PATH = OUT_DIR / "valid.jsonl"


def load_rows() -> list[dict]:
    rows: list[dict] = []
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"File not found: {INPUT_PATH}")
    with open(INPUT_PATH, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line)
            bad = str(obj.get("bad") or "").strip()
            good = str(obj.get("good") or "").strip()
            if not bad or not good:
                raise ValueError(f"Line {line_no}: fields 'bad' and 'good' are required")
            rows.append(
                {
                    "bad": bad,
                    "good": good,
                    "tag": str(obj.get("tag") or "").strip(),
                    "notes": str(obj.get("notes") or "").strip(),
                }
            )
    return rows


def split_rows(rows: list[dict], valid_ratio: float = 0.15) -> tuple[list[dict], list[dict]]:
    if len(rows) < 2:
        return rows, []
    valid_size = max(1, int(len(rows) * valid_ratio))
    train = rows[:-valid_size]
    valid = rows[-valid_size:]
    return train, valid


def save_jsonl(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    rows = load_rows()
    train_rows, valid_rows = split_rows(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    save_jsonl(TRAIN_PATH, train_rows)
    save_jsonl(VALID_PATH, valid_rows)
    print(f"Loaded examples: {len(rows)}")
    print(f"Train examples: {len(train_rows)} -> {TRAIN_PATH}")
    print(f"Valid examples: {len(valid_rows)} -> {VALID_PATH}")


if __name__ == "__main__":
    main()
