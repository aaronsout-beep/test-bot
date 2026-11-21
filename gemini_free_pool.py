"""
Пул бесплатных (Free tier) Gemini-ключей с контролем лимитов.

Каждый ключ — отдельный Google Cloud проект (квоты Google считаются по проекту,
а не по ключу). Запрос уходит на первый проект, у которого есть запас по
лимитам; когда лимит почти выбран — берётся следующий. Если исчерпаны все —
GeminiPoolExhausted, и вызывающий код идёт к другим моделям (Yandex и т.д.).

Контролируются RPM, TPM (только ВХОДНЫЕ токены — так считает Google) и RPD
(сброс в полночь America/Los_Angeles) на каждый проект. Работаем на SAFETY
(по умолчанию 90%) от лимита.

Счётчики живут в сменном бэкенде:
  RemoteQuota — общий для ВСЕХ ботов счётчик в Cloudflare Worker (Durable
                Object, см. gemini_quota_do.js). Основной режим для флота.
                Если координатор недоступен — GeminiPoolExhausted (бот идёт
                к Yandex, а не гоняет ключи вслепую).
  LocalQuota  — счётчики в памяти + файл (годится для одного бота/отладки).

В файл состояния и в Worker уходит только номер слота, не сам ключ.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

_PACIFIC = ZoneInfo("America/Los_Angeles")


class GeminiPoolExhausted(Exception):
    """Все ключи пула исчерпали (или почти исчерпали) лимиты Free tier."""


def load_keys_from_env() -> list[str]:
    """Ключи из GEMINI_FREE_KEYS (через запятую) и/или GEMINI_FREE_KEY_1..GEMINI_FREE_KEY_20.
    Дубликаты отбрасываются, порядок сохраняется (порядок = приоритет)."""
    keys: list[str] = []
    raw = os.environ.get("GEMINI_FREE_KEYS", "")
    keys.extend(k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip())
    for i in range(1, 21):
        k = os.environ.get(f"GEMINI_FREE_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    seen: set[str] = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


class LocalQuota:
    """Локальные счётчики (память + JSON-файл). Для одного процесса/бота."""

    def __init__(self, n_keys, rpm, tpm, rpd, safety=0.9, state_file="gemini_quota_state.json"):
        self.n = n_keys
        self.rpm, self.tpm, self.rpd, self.safety = rpm, tpm, rpd, safety
        self.state_file = state_file
        self._lock = threading.Lock()
        self._window = {i: deque() for i in range(n_keys)}  # [ts, tokens]
        self._dead: set[int] = set()
        self._state = self._load_state()

    @staticmethod
    def _today() -> str:
        return datetime.now(_PACIFIC).strftime("%Y-%m-%d")

    @staticmethod
    def _next_reset_epoch() -> float:
        now = datetime.now(_PACIFIC)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return nxt.timestamp()

    def _fresh(self) -> dict:
        return {"day": self._today(), "keys": {}}

    def _load_state(self) -> dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
            if st.get("day") == self._today() and isinstance(st.get("keys"), dict):
                return st
        except (OSError, ValueError):
            pass
        return self._fresh()

    def _save(self) -> None:
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            print(f"  Gemini Free: не удалось сохранить {self.state_file}: {e}")

    def _ks(self, idx: int) -> dict:
        if self._state.get("day") != self._today():
            self._state = self._fresh()
        return self._state["keys"].setdefault(
            str(idx), {"requests": 0, "tokens": 0, "blocked_until": 0}
        )

    def reserve(self, est_tokens: int, exclude: set[int]):
        with self._lock:
            now = time.time()
            for idx in range(self.n):
                if idx in exclude or idx in self._dead:
                    continue
                st = self._ks(idx)
                if st["blocked_until"] > now:
                    continue
                win = self._window[idx]
                while win and now - win[0][0] > 60:
                    win.popleft()
                if len(win) + 1 > self.rpm * self.safety:
                    continue
                if sum(t for _, t in win) + est_tokens > self.tpm * self.safety:
                    continue
                if st["requests"] + 1 > self.rpd * self.safety:
                    continue
                entry = [now, est_tokens]
                win.append(entry)
                st["requests"] += 1
                st["tokens"] += est_tokens
                self._save()
                return idx, entry
            return None

    def report(self, idx, handle, est, input_tokens, status, retry_after=None):
        with self._lock:
            st = self._ks(idx)
            if status == "ok" and input_tokens:
                handle[1] = input_tokens
                st["tokens"] += input_tokens - est
            elif status == "429_day":
                st["blocked_until"] = self._next_reset_epoch()
                st["requests"] = max(st["requests"], self.rpd)
            elif status == "429_min":
                st["blocked_until"] = time.time() + (retry_after or 63) + 2
            elif status == "auth":
                self._dead.add(idx)
            self._save()


class RemoteQuota:
    """Общий счётчик всех ботов — Cloudflare Worker + Durable Object."""

    def __init__(self, base_url: str, secret: str, n_keys: int, timeout: int = 6):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
        self.n = n_keys
        self.timeout = timeout

    def reserve(self, est_tokens: int, exclude: set[int]):
        try:
            r = requests.post(
                f"{self.base}/gemini/reserve",
                headers=self.headers,
                json={"est_tokens": est_tokens, "n": self.n,
                      "exclude": sorted(i + 1 for i in exclude)},
                timeout=self.timeout,
            )
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            raise GeminiPoolExhausted(f"координатор лимитов недоступен: {e}")
        if d.get("slot") is None:
            return None
        return int(d["slot"]) - 1, d["id"]

    def report(self, idx, handle, est, input_tokens, status, retry_after=None):
        try:  # best-effort: потеря отчёта лишь делает счётчик консервативнее
            requests.post(
                f"{self.base}/gemini/report",
                headers=self.headers,
                json={"slot": idx + 1, "id": handle, "input_tokens": input_tokens,
                      "status": status, "retry_after": retry_after},
                timeout=self.timeout,
            )
        except Exception as e:
            print(f"  Gemini Free: отчёт координатору не отправлен: {e}")


class GeminiFreePool:
    def __init__(
        self,
        keys: list[str],
        model: str,
        rpm: int = 15,
        tpm: int = 250000,
        rpd: int = 500,
        safety: float = 0.9,
        state_file: str = "gemini_quota_state.json",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        quota=None,
    ):
        self.keys = list(keys)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.quota = quota or LocalQuota(len(self.keys), rpm, tpm, rpd, safety, state_file)

    def _post(self, key: str, payload: dict, timeout: int):
        # Ключ — в заголовке, а не в query: иначе он попадает в текст HTTPError/логи.
        return requests.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            json=payload,
            timeout=timeout,
        )

    def generate(self, prompt: str, max_output_tokens: int, timeout: int, json_mode: bool = False):
        """Возвращает (текст, номер_ключа_с_1, usageMetadata).

        GeminiPoolExhausted — нет ни одного проекта с запасом по лимитам
        (или недоступен координатор). requests.* — прочие ошибки (не квота).
        """
        # TPM у Google — по ВХОДНЫМ токенам. Оценка ~2.5 символа/токен
        # (ru/en), затем заменяется реальным promptTokenCount.
        est = int(len(prompt) / 2.5)
        tried: set[int] = set()
        server_errors = 0

        while True:
            slot = self.quota.reserve(est, tried)
            if slot is None:
                raise GeminiPoolExhausted(
                    f"лимиты Free tier исчерпаны на всех проектах ({len(self.keys)})"
                )
            idx, handle = slot
            if idx >= len(self.keys):
                raise GeminiPoolExhausted("в координаторе больше слотов, чем ключей у бота")
            tried.add(idx)
            cfg = {"temperature": 0, "maxOutputTokens": max_output_tokens, "candidateCount": 1}
            if json_mode:
                cfg["responseMimeType"] = "application/json"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg}
            try:
                r = self._post(self.keys[idx], payload, timeout)
            except requests.RequestException:
                self.quota.report(idx, handle, est, None, "error")
                raise

            if r.status_code == 429:
                body = r.text or ""
                if re.search(r"PerDay|per day|daily", body, re.I):
                    print(f"  Gemini Free #{idx + 1}: дневной лимит (429) — проект отключён до конца суток")
                    self.quota.report(idx, handle, est, None, "429_day")
                else:
                    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
                    delay = float(m.group(1)) if m else 63
                    print(f"  Gemini Free #{idx + 1}: лимит в минуту (429) — пауза ~{int(delay)} с")
                    self.quota.report(idx, handle, est, None, "429_min", delay)
                continue
            if r.status_code in (401, 403):
                print(f"  Gemini Free #{idx + 1}: ключ отклонён (HTTP {r.status_code})")
                self.quota.report(idx, handle, est, None, "auth")
                continue
            if r.status_code in (500, 502, 503, 504):
                self.quota.report(idx, handle, est, None, "error")
                server_errors += 1
                print(f"  Gemini Free #{idx + 1}: HTTP {r.status_code}")
                if server_errors >= 2:
                    r.raise_for_status()
                continue
            if r.status_code >= 400:
                self.quota.report(idx, handle, est, None, "error")
            r.raise_for_status()

            data = r.json()
            usage = data.get("usageMetadata") or {}
            self.quota.report(idx, handle, est, usage.get("promptTokenCount"), "ok")
            parts = data["candidates"][0]["content"].get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts).strip()
            return text, idx + 1, usage
