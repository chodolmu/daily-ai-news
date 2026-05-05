"""
메인 진입점.

흐름:
  1) data/last_run.json에서 마지막 실행일 읽음
  2) 마지막 실행일 다음날부터 오늘까지 빠진 날짜 모두 처리
  3) 각 날짜별로 scrape → process → vault에 기록
  4) 월요일이면 주간 필터 실행 (마지막 주간 실행으로부터 7일 이상 지났을 때만)
  5) last_run.json 갱신

수동 실행:
  python scripts/run.py             # 누락분 자동 처리
  python scripts/run.py --date 2026-05-04   # 특정 날짜만
  python scripts/run.py --weekly    # 주간 필터만
"""
from __future__ import annotations

import argparse
import io
import json
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

import subprocess  # noqa: E402

DATA_DIR = ROOT / "data"
LAST_RUN = DATA_DIR / "last_run.json"
LOGS_DIR = ROOT / "logs"

MAX_BACKFILL_DAYS = 14  # 한 번에 최대 처리 일수 (너무 오래 누락 시 부담 방지)


def _load_state() -> dict:
    if LAST_RUN.exists():
        return json.loads(LAST_RUN.read_text(encoding="utf-8"))
    return {"last_run_date": None, "last_weekly_filter_date": None}


def _save_state(state: dict) -> None:
    LAST_RUN.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _missing_dates(last_run_date: str | None, today: datetime) -> list[datetime]:
    """마지막 실행 다음날부터 오늘까지의 날짜 리스트. 한 번에 14일까지만."""
    if not last_run_date:
        return [today]
    last = datetime.strptime(last_run_date, "%Y-%m-%d")
    dates = []
    cur = last + timedelta(days=1)
    while cur.date() <= today.date():
        dates.append(cur)
        cur += timedelta(days=1)
    if len(dates) > MAX_BACKFILL_DAYS:
        print(f"[run] 누락 일수 {len(dates)}일 — 최근 {MAX_BACKFILL_DAYS}일만 처리")
        dates = dates[-MAX_BACKFILL_DAYS:]
    return dates or [today]


def process_date(target: datetime) -> int:
    print(f"\n=== {target.strftime('%Y-%m-%d')} 처리 시작 ===")
    items = scrape_for_date(target)
    if not items:
        # 빈 daily 노트도 만들어서 "확인했음"을 기록
        write_daily(target, [])
        return 0
    processed = process_items(items, min_quality=4)
    write_daily(target, processed)
    return len(processed)


def maybe_run_weekly(state: dict, today: datetime) -> None:
    """월요일이고 마지막 주간 필터로부터 6일 이상 지났으면 실행."""
    if today.weekday() != 0:  # 0 == Monday
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


def build_and_push(today: datetime) -> None:
    """사이트 빌드 후, git 저장소가 셋업되어 있으면 자동 push."""
    print("\n=== 사이트 빌드 ===")
    try:
        build_site()
    except Exception as e:
        print(f"[build] 실패: {e}")
        traceback.print_exc()
        return

    # git 저장소가 아니면 push 스킵 (사용자가 아직 init 안 했을 수 있음)
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        print("[push] .git 없음 — 셋업 후 README의 명령어 실행 필요. push 스킵.")
        return

    try:
        # 변경 여부 확인
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
        print(f"[push] git 명령 실패 (rc={e.returncode}). 수동 확인 필요.")
    except Exception as e:
        print(f"[push] 실패: {e}")


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD, 특정 날짜만 처리")
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
        # 수동 실행은 last_run을 건드리지 않음
        build_and_push(today)
        return 0

    dates = _missing_dates(state.get("last_run_date"), today)
    print(f"[run] 처리 대상: {[d.strftime('%Y-%m-%d') for d in dates]}")
    total = 0
    for d in dates:
        try:
            total += process_date(d)
        except Exception as e:
            print(f"[run] {d.date()} 실패: {e}")
            traceback.print_exc()
            continue

    state["last_run_date"] = today.strftime("%Y-%m-%d")
    maybe_run_weekly(state, today)
    maybe_run_monthly(state, today)
    _save_state(state)
    build_and_push(today)
    print(f"\n=== 완료. 총 {total}건 vault 기록 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
