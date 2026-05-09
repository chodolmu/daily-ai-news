"""
PreToolUse:Read hook이 호출 — 메모리 파일이 Read되면 access log에 기록.

stdin으로 JSON 받음 (Claude Code hook 규약):
  { "tool_name": "Read", "tool_input": { "file_path": "..." } }

기록 대상:
  - ~/.claude/projects/<...>/memory/auto_*.md
  - C:/AI/DailyAINews/vault/daily/*.md (외부 지식 vault 노트)
  - C:/AI/DailyAINews/vault/concepts/*.md, people/, orgs/, papers/

기록 위치:
  C:/AI/DailyAINews/data/memory_access_log.jsonl
  한 줄당 {"ts": ..., "path": ..., "category": "auto|vault|concept|..."}

규칙:
  - 빠르게 끝나야 함 (Read hook 매번 발동) — sub-50ms 목표
  - 실패해도 silent — Read 자체를 막으면 안 됨
  - 같은 파일 1분 이내 중복 기록 안 함 (debounce)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOG_PATH = Path(r"C:\AI\DailyAINews\data\memory_access_log.jsonl")
DEBOUNCE_SECONDS = 60


def _categorize(path: Path) -> str | None:
    """관심 있는 파일만 분류 — None이면 기록 안 함."""
    s = str(path).replace("\\", "/").lower()
    if "/memory/auto_" in s and s.endswith(".md"):
        return "auto"
    if "/memory/" in s and s.endswith("memory.md"):
        return "memory_index"
    if "/dailyainews/vault/daily/" in s and s.endswith(".md"):
        return "vault_daily"
    if "/dailyainews/vault/concepts/" in s and s.endswith(".md"):
        return "vault_concept"
    if "/dailyainews/vault/people/" in s and s.endswith(".md"):
        return "vault_person"
    if "/dailyainews/vault/orgs/" in s and s.endswith(".md"):
        return "vault_org"
    if "/dailyainews/vault/papers/" in s and s.endswith(".md"):
        return "vault_paper"
    return None


def _was_logged_recently(path_str: str) -> bool:
    """같은 path가 DEBOUNCE_SECONDS 내에 기록되었는지 — 마지막 N줄만 본다."""
    if not LOG_PATH.exists():
        return False
    try:
        # 마지막 50줄만 효율적으로 읽기
        with LOG_PATH.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")
        cutoff = datetime.now() - timedelta(seconds=DEBOUNCE_SECONDS)
        for line in tail.splitlines()[-50:]:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if e.get("path") == path_str:
                    ts = datetime.fromisoformat(e.get("ts", ""))
                    if ts > cutoff:
                        return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # 파싱 실패 — silent

    tool_name = payload.get("tool_name")
    if tool_name != "Read":
        return 0

    file_path = payload.get("tool_input", {}).get("file_path")
    if not file_path:
        return 0

    try:
        p = Path(file_path)
    except Exception:
        return 0

    category = _categorize(p)
    if not category:
        return 0

    path_str = str(p)
    if _was_logged_recently(path_str):
        return 0

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "path": path_str,
        "category": category,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
