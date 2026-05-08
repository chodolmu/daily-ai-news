"""
~/.claude/projects/.../memory/auto_*.md 자동 메모리 가지치기.

원칙:
  - auto_ 접두사 메모리만 건드린다 (사용자가 직접 만든 건 절대 X)
  - 90일간 한 번도 참조 안 된 메모리 폐기
  - 같은 주제 3개 이상이면 LLM이 1개로 통합
  - 총량 100 초과 시 가장 오래된 것부터 폐기
  - dead URL(404)인 출처 메모리 폐기 (선택, 비용 큼 → 옵션)

호출:
  - run_pruning() — 월간 큐레이션 또는 수동 실행
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PRUNE_LOG = DATA_DIR / "prune_log.jsonl"

CLAUDE_HOME = Path.home() / ".claude"
MEMORY_DIR = CLAUDE_HOME / "projects" / "C--Users-chodo" / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

MAX_AUTO_MEMORIES = 500
STALE_DAYS = 90


def _list_auto_memories() -> list[Path]:
    if not MEMORY_DIR.exists():
        return []
    return sorted([p for p in MEMORY_DIR.iterdir()
                   if p.is_file() and p.name.startswith("auto_") and p.suffix == ".md"])


def _extract_creation_date(path: Path) -> datetime | None:
    """Auto-extracted from DailyAINews on YYYY-MM-DD 패턴 파싱."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Auto-extracted from DailyAINews on (\d{4}-\d{2}-\d{2})", text)
        if m:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
    except Exception:
        pass
    # fallback: 파일 mtime
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None


def _file_referenced_recently(path: Path, since_days: int) -> bool:
    """파일 atime(접근 시간)으로 최근 참조 여부 추정.
    Windows에서 atime은 기본 비활성일 수 있어 mtime 폴백."""
    try:
        st = path.stat()
        atime = datetime.fromtimestamp(st.st_atime)
        mtime = datetime.fromtimestamp(st.st_mtime)
        most_recent = max(atime, mtime)
        return (datetime.now() - most_recent).days < since_days
    except Exception:
        return True  # 알 수 없으면 보존


def _log(action: str, path: Path, reason: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "file": path.name,
        "reason": reason,
    }
    with PRUNE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _remove_from_index(filenames: set[str]) -> int:
    if not MEMORY_INDEX.exists():
        return 0
    text = MEMORY_INDEX.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    kept = []
    removed = 0
    for line in lines:
        if any(fn in line for fn in filenames):
            removed += 1
            continue
        kept.append(line)
    if removed:
        MEMORY_INDEX.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    return removed


def prune_stale(stale_days: int = STALE_DAYS) -> int:
    """오래된 미참조 auto 메모리 폐기. 폐기된 개수 반환."""
    files = _list_auto_memories()
    to_remove: list[Path] = []
    for p in files:
        created = _extract_creation_date(p)
        if not created or (datetime.now() - created).days < stale_days:
            continue  # 아직 어림
        if _file_referenced_recently(p, since_days=stale_days):
            continue  # 최근 참조됨
        to_remove.append(p)

    for p in to_remove:
        _log("prune_stale", p, f"unreferenced for {stale_days}+ days")
        p.unlink()

    if to_remove:
        _remove_from_index({p.name for p in to_remove})
    return len(to_remove)


def prune_overflow(max_total: int = MAX_AUTO_MEMORIES) -> int:
    """auto 메모리 총량 초과 시 가장 오래된 것부터 폐기."""
    files = _list_auto_memories()
    if len(files) <= max_total:
        return 0
    files_with_date = []
    for p in files:
        d = _extract_creation_date(p) or datetime.fromtimestamp(p.stat().st_mtime)
        files_with_date.append((d, p))
    files_with_date.sort(key=lambda x: x[0])  # 오래된 것 먼저
    overflow = len(files_with_date) - max_total
    to_remove = [p for _, p in files_with_date[:overflow]]

    for p in to_remove:
        _log("prune_overflow", p, f"total {len(files)} > {max_total}, oldest first")
        p.unlink()

    if to_remove:
        _remove_from_index({p.name for p in to_remove})
    return len(to_remove)


def run_pruning() -> dict:
    """월간 큐레이션에서 호출. 모든 가지치기 단계 실행."""
    print("\n=== auto memory pruning ===")
    stale = prune_stale()
    overflow = prune_overflow()
    total_remaining = len(_list_auto_memories())
    print(f"  stale removed: {stale}")
    print(f"  overflow removed: {overflow}")
    print(f"  remaining auto memories: {total_remaining}")
    return {
        "stale_removed": stale,
        "overflow_removed": overflow,
        "remaining": total_remaining,
    }


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    run_pruning()
