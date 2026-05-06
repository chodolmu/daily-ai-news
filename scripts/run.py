"""
메인 진입점.

흐름:
  1) 실행 시점의 "어제" 하루치만 처리 (어제는 이미 종료된 시점이라 누락·중복 없음)
  2) data/last_run.json의 last_processed_date 다음날부터 어제까지 빠진 날짜 모두 백필
  3) 각 날짜별로 scrape → process → vault에 기록
  4) 월요일이면 주간 필터, 매월 1일이면 월간 큐레이션
  5) last_run.json 갱신

수동 실행:
  python scripts/run.py             # 누락분 자동 처리 (기본: 어제까지)
  python scripts/run.py --date 2026-05-04   # 특정 날짜만 (last_run 안 건드림)
  python scripts/run.py --weekly    # 주간 필터만
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# Windows 콘솔 한글 깨짐 방지
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scraper import scrape_for_date  # noqa: E402
from processor import process_items  # noqa: E402
from vault_writer import write_daily  # noqa: E402
from filter_weekly import run_weekly_filter  # noqa: E402
from curate_monthly import run_monthly_curation  # noqa: E402
from builder import build_site  # noqa: E402

DATA_DIR = ROOT / "data"
LAST_RUN = DATA_DIR / "last_run.json"
LOGS_DIR = ROOT / "logs"

MAX_BACKFILL_DAYS = 14


def _load_state() -> dict:
    if LAST_RUN.exists():
        return json.loads(LAST_RUN.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    LAST_RUN.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _dates_to_process(state: dict, today: datetime) -> list[datetime]:
    """처리할 날짜 = (last_processed_date + 1) ~ 어제. 최대 14일."""
    yesterday = today - timedelta(days=1)
    last = state.get("last_processed_date")
    if not last:
        return [yesterday]
    last_dt = datetime.strptime(last, "%Y-%m-%d")
    dates = []
    cur = last_dt + timedelta(days=1)
    while cur.date() <= yesterday.date():
        dates.append(cur)
        cur += timedelta(days=1)
    if len(dates) > MAX_BACKFILL_DAYS:
        print(f"[run] 누락 일수 {len(dates)}일 — 최근 {MAX_BACKFILL_DAYS}일만 처리")
        dates = dates[-MAX_BACKFILL_DAYS:]
    return dates


def process_date(target: datetime) -> int:
    """target 날짜(어제 또는 그 이전 백필일)의 글을 수집·처리해 vault에 기록."""
    print(f"\n=== {target.strftime('%Y-%m-%d')} 처리 시작 ===")
    items = scrape_for_date(target)
    if not items:
        write_daily(target, [])
        return 0
    processed = process_items(items, min_quality=4)
    write_daily(target, processed)
    return len(processed)


def maybe_run_weekly(state: dict, today: datetime) -> None:
    """월요일이고 마지막 주간 필터로부터 6일 이상 지났으면 실행."""
    if today.weekday() != 0:
        return
    last = state.get("last_weekly_filter_date")
    if last:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
        if (today - last_dt).days < 6:
            return
    print("\n=== 주간 필터 실행 ===")
    try:
        run_weekly_filter(today)
        state["last_weekly_filter_date"] = today.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[weekly] 실패: {e}")
        traceback.print_exc()


def maybe_run_monthly(state: dict, today: datetime) -> None:
    """매월 1일이고 마지막 큐레이션으로부터 25일 이상 지났으면 실행."""
    if today.day != 1:
        return
    last = state.get("last_monthly_curation_date")
    if last:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
        if (today - last_dt).days < 25:
            return
    print("\n=== 월간 큐레이션 실행 ===")
    try:
        run_monthly_curation(today)
        state["last_monthly_curation_date"] = today.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[monthly] 실패: {e}")
        traceback.print_exc()


def build_and_push(today: datetime) -> None:
    """사이트 빌드 후 git push."""
    print("\n=== 사이트 빌드 ===")
    try:
        build_site()
    except Exception as e:
        print(f"[build] 실패: {e}")
        traceback.print_exc()
        return

    git_dir = ROOT / ".git"
    if not git_dir.exists():
        print("[push] .git 없음 — push 스킵.")
        return

    try:
        diff = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        )
        if not diff.stdout.strip():
            print("[push] 변경 없음")
            return
        msg = f"daily update {today.strftime('%Y-%m-%d')}"
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True)
        print(f"[push] {msg}")
    except subprocess.CalledProcessError as e:
        print(f"[push] git 명령 실패 (rc={e.returncode})")
    except Exception as e:
        print(f"[push] 실패: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD, 특정 날짜만 처리 (last_run 안 건드림)")
    ap.add_argument("--weekly", action="store_true", help="주간 필터만 실행")
    ap.add_argument("--monthly", action="store_true", help="월간 큐레이션만 실행")
    ap.add_argument("--build", action="store_true", help="사이트 빌드 + push만 실행")
    args = ap.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now()
    state = _load_state()

    if args.build:
        build_and_push(today)
        return 0

    if args.weekly:
        run_weekly_filter(today)
        state["last_weekly_filter_date"] = today.strftime("%Y-%m-%d")
        _save_state(state)
        build_and_push(today)
        return 0

    if args.monthly:
        run_monthly_curation(today)
        state["last_monthly_curation_date"] = today.strftime("%Y-%m-%d")
        _save_state(state)
        build_and_push(today)
        return 0

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d")
        process_date(target)
        build_and_push(today)
        return 0

    dates = _dates_to_process(state, today)
    if not dates:
        print(f"[run] 처리할 날짜 없음 (마지막: {state.get('last_processed_date')})")
        build_and_push(today)
        return 0

    print(f"[run] 처리 대상: {[d.strftime('%Y-%m-%d') for d in dates]}")
    total = 0
    for d in dates:
        try:
            total += process_date(d)
            state["last_processed_date"] = d.strftime("%Y-%m-%d")
            _save_state(state)  # 한 날짜 끝날 때마다 저장 — 중간 실패해도 진행분 보존
        except Exception as e:
            print(f"[run] {d.date()} 실패: {e}")
            traceback.print_exc()
            continue

    maybe_run_weekly(state, today)
    maybe_run_monthly(state, today)
    _save_state(state)
    build_and_push(today)
    print(f"\n=== 완료. 총 {total}건 vault 기록 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
