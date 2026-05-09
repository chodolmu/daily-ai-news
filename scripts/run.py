"""
메인 진입점.

날짜 모델:
  - content_date = 기사가 발생한/수집되는 날짜 (예: 어제)
  - publish_date = 사이트에 발행되는 날짜 (= content_date + 1일)
  방송국 9시 뉴스가 어제 일을 오늘 보도하는 것과 동일. 폴더/헤더는 publish_date,
  본문은 content_date 기사들로 구성.

흐름:
  1) "어제" content_date를 처리해 publish_date(=오늘) 노트로 저장
  2) data/last_run.json의 last_processed_content_date 다음날부터 어제까지 빠진
     content_date를 모두 백필 (각각 +1일이 publish_date)
  3) 월요일이면 주간 필터, 매월 1일이면 월간 큐레이션
  4) last_run.json 갱신

수동 실행:
  python scripts/run.py                       # 누락분 자동 처리
  python scripts/run.py --date 2026-05-04    # 특정 content_date만 (last_run 안 건드림)
  python scripts/run.py --weekly             # 주간 필터만
"""
from __future__ import annotations

import argparse
import io
import json
import os
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
from skill_extractor import run_daily_extraction  # noqa: E402

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
    """처리할 content_date 목록 = (last_processed_content_date + 1) ~ 어제. 최대 14일."""
    yesterday = today - timedelta(days=1)
    last = state.get("last_processed_content_date") or state.get("last_processed_date")
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


def process_date(content_date: datetime) -> int:
    """content_date의 글을 수집·처리해 publish_date(=content_date+1일) 노트로 vault에 기록.
    기록 후 ~/.claude/ 환경으로의 변환도 자동 시도."""
    publish_date = content_date + timedelta(days=1)
    print(f"\n=== content={content_date.strftime('%Y-%m-%d')} → publish={publish_date.strftime('%Y-%m-%d')} 처리 시작 ===")
    items = scrape_for_date(content_date)
    if not items:
        write_daily(publish_date, content_date, [])
        return 0
    processed = process_items(items, min_quality=4)
    write_daily(publish_date, content_date, processed)

    # vault 기록 후 → ~/.claude/skills, memory 자동 변환
    # SKIP_EXTRACT=1 환경변수가 있으면 우회 (Claude CLI 호출이 느릴 때)
    if os.environ.get("SKIP_EXTRACT"):
        print("  [extract] SKIP_EXTRACT=1 — 스킵")
    else:
        try:
            counts = run_daily_extraction(publish_date)
            print(f"  [extract] skill={counts['skill']}, memory={counts['memory']}, skip={counts['skip']}")
        except Exception as e:
            print(f"  [extract] 실패 (무시하고 계속): {e}")
            traceback.print_exc()

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

    # 자동 메모리 가지치기 (월간 큐레이션과 함께 실행)
    try:
        from skill_pruner import run_pruning
        run_pruning()
    except Exception as e:
        print(f"[prune] 실패: {e}")
        traceback.print_exc()

    # 메모리 sanity check (30일 주기)
    try:
        from memory_sanity_check import run_sanity_check
        run_sanity_check()
    except Exception as e:
        print(f"[sanity] 실패: {e}")
        traceback.print_exc()

    # 메모리 승격 점검 (30일 주기, claude-mem DB → MEMORY.md 후보)
    try:
        from memory_promotion_check import run_promotion_check
        run_promotion_check()
    except Exception as e:
        print(f"[promotion] 실패: {e}")
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


def _record_health(success: bool, error: str = "") -> None:
    """run.py 종료 시점에 헬스체크용 상태 기록. 디스크에서 다시 로드해 race 방지."""
    state = _load_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["last_run_at"] = now
    if success:
        state["last_success_at"] = now
        state["last_error"] = None
    else:
        state["last_error"] = error[:500]
    _save_state(state)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD content_date, 특정 날짜만 처리 (last_run 안 건드림). publish_date는 +1일이 됨.")
    ap.add_argument("--weekly", action="store_true", help="주간 필터만 실행")
    ap.add_argument("--monthly", action="store_true", help="월간 큐레이션만 실행")
    ap.add_argument("--build", action="store_true", help="사이트 빌드 + push만 실행")
    ap.add_argument("--extract", action="store_true", help="vault → ~/.claude/ 변환만 실행 (최근 7일)")
    args = ap.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now()
    state = _load_state()

    if args.build:
        build_and_push(today)
        return 0

    if args.extract:
        from skill_extractor import main as extract_main
        return extract_main()

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

    print(f"[run] 처리 대상 content_dates: {[d.strftime('%Y-%m-%d') for d in dates]}")
    total = 0
    for d in dates:
        try:
            total += process_date(d)
            state["last_processed_content_date"] = d.strftime("%Y-%m-%d")
            state.pop("last_processed_date", None)  # 구 키 정리
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


def _wrapped_main() -> int:
    """main을 헬스체크로 감싸기 — 성공/실패 모두 last_run.json에 기록."""
    try:
        rc = main()
        _record_health(success=(rc == 0))
        return rc
    except Exception as e:
        traceback.print_exc()
        _record_health(success=False, error=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_wrapped_main())
