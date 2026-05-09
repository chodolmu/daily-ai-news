"""
승격 자동 제안 — claude-mem 옵저베이션 중 MEMORY.md급 후보 골라내기.

흐름:
  1) ~/.claude-mem/claude-mem.db 에서 최근 30일 옵저베이션 읽기
  2) project별로 그룹핑 — 각 project = ~/.claude/projects/<...>/memory/MEMORY.md 대응
  3) Claude CLI(Sonnet)로 각 project 옵저베이션 묶음 평가:
     - 이 중 MEMORY.md에 승격할 가치 있는 것?
     - 이미 MEMORY.md에 있는 것과 중복되는지 확인
     - 정제된 메모리 파일 frontmatter+본문 초안 제시
  4) 보고서를 vault/curation_reports/promotion_YYYY-MM-DD.md 저장
  5) data/promotion_state.json 에 unread 플래그 → 다음 세션에서 알림

원칙:
  - 자동 승격 X. 보고서만 생성 → 사용자가 검토 후 결정.
  - claude-mem DB는 잡식 캐시, MEMORY.md는 정제 저장소 — 그 사이의 다리.
  - 30일 주기 (memory_sanity_check와 같은 사이클).
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROMOTION_STATE = DATA_DIR / "promotion_state.json"
REPORTS_DIR = ROOT / "vault" / "curation_reports"

CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_MEM_DB = Path.home() / ".claude-mem" / "claude-mem.db"
PROJECTS_DIR = CLAUDE_HOME / "projects"

CLAUDE_CMD = "claude"
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_TIMEOUT = 600

CHECK_INTERVAL_DAYS = 30
LOOKBACK_DAYS = 30
MAX_OBSERVATIONS_PER_PROJECT = 60  # 너무 많으면 잘라냄


def _ensure_git_bash() -> None:
    if os.name != "nt" or os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return
    candidates = [
        r"D:\Git\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = c
            return


_ensure_git_bash()


PROMOTION_PROMPT = """다음은 사용자 한 명이 지난 30일간 Claude Code 작업 중 자동 캡처된 옵저베이션들이다.
프로젝트: {project}
이미 정제된 MEMORY.md 인덱스 (현재 상태):
---
{existing_memory_index}
---

각 옵저베이션의 **MEMORY.md 승격 가치**를 평가하라.

[승격 기준 — 반드시 다음 중 하나에 해당해야 한다]
1. **회피해야 할 함정** — 환경 특이사항, 작동 안 하는 패턴, 위험한 명령. 다음에 같은 함정 안 빠지도록.
2. **사용자의 비명시적 결정/취향** — 코드만 봐선 모르는 것 ("이 프로젝트는 X 안 쓴다", "Y 방식 선호" 등).
3. **재사용 가능한 작업 흐름** — 사용자가 반복할 패턴 (예: Task Scheduler로 테스트, 특정 디버깅 절차).
4. **프로젝트의 비밀 제약** — 외부 API 한도, 라이선스 제약, 비공개 종속성 등.

[승격 안 함]
- 코드만 보면 알 수 있는 것 (구조, 함수, 변수)
- 일회성 task 진행 상황
- 단순 버그픽스 그 자체 (커밋 메시지로 충분)
- 일반적 도구 사용법 (LLM이 이미 안다)
- 이미 MEMORY.md에 같은 취지로 들어간 항목 (중복)

[옵저베이션 목록]
{observations}

[출력 형식 — 반드시 JSON]
{{
  "promotion_candidates": [
    {{
      "obs_ids": [123, 145],
      "reason": "왜 승격감인가 (위 기준 1~4 중 어디에 해당)",
      "proposed_filename": "feedback_xxx.md 또는 project_xxx.md 등",
      "proposed_frontmatter": {{
        "name": "...",
        "description": "한 줄, 검색 키워드 포함",
        "type": "feedback | project | reference | user"
      }},
      "proposed_body": "정제된 본문 (한국어, 200자 이내)"
    }}
  ],
  "duplicates": [
    {{ "obs_ids": [...], "duplicates_with": "기존 MEMORY.md 항목 이름", "note": "..." }}
  ],
  "stats": {{
    "total_observations": <int>,
    "promotion_count": <int>,
    "duplicate_count": <int>,
    "noise_count": <int>
  }}
}}

JSON 외 다른 텍스트 출력 금지."""


def _load_state() -> dict:
    if PROMOTION_STATE.exists():
        return json.loads(PROMOTION_STATE.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    PROMOTION_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _should_run(state: dict, today: datetime) -> bool:
    last = state.get("last_check_date")
    if not last:
        return True
    last_dt = datetime.strptime(last, "%Y-%m-%d")
    return (today - last_dt).days >= CHECK_INTERVAL_DAYS


def _read_observations_by_project(since: datetime) -> dict[str, list[dict]]:
    """claude-mem DB에서 since 이후 옵저베이션을 project별로 묶어서 반환."""
    if not CLAUDE_MEM_DB.exists():
        print(f"[promotion] DB 없음: {CLAUDE_MEM_DB}")
        return {}

    conn = sqlite3.connect(str(CLAUDE_MEM_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    since_epoch = int(since.timestamp())
    cur.execute(
        """
        SELECT id, project, type, title, subtitle, narrative, concepts, created_at
        FROM observations
        WHERE created_at_epoch >= ?
        ORDER BY project, created_at_epoch DESC
        """,
        (since_epoch,),
    )

    by_project: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        proj = row["project"] or "_unknown"
        by_project.setdefault(proj, []).append(
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"] or "",
                "subtitle": row["subtitle"] or "",
                "narrative": (row["narrative"] or "")[:500],
                "concepts": row["concepts"] or "",
                "created_at": row["created_at"],
            }
        )

    conn.close()
    return by_project


def _project_to_memory_dir(project: str) -> Path | None:
    """claude-mem의 project 이름 → ~/.claude/projects/<...>/memory/ 매핑.

    project는 보통 cwd 디렉토리명(예: 'DailyAINews', 'GameMaking-Tool').
    매핑 규칙: 'C:\\AI\\DailyAINews' → 'C--AI-DailyAINews' 형태로 디렉토리 검색.
    """
    if not PROJECTS_DIR.exists():
        return None
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        # 디렉토리 이름이 project로 끝나면 매칭 (가장 단순한 휴리스틱)
        if d.name.endswith(project) or d.name == f"C--{project}":
            mem = d / "memory"
            if mem.exists():
                return mem
    return None


def _read_memory_index(memory_dir: Path) -> str:
    idx = memory_dir / "MEMORY.md"
    if idx.exists():
        return idx.read_text(encoding="utf-8")[:3000]
    return "(MEMORY.md 비어있음)"


def _call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt, "--model", CLAUDE_MODEL],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT,
            env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found in PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI 호출 타임아웃")
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:500]}")
    return result.stdout.strip()


def _evaluate_project(project: str, obs_list: list[dict], memory_dir: Path | None) -> dict:
    """한 프로젝트의 옵저베이션 묶음을 Claude로 평가."""
    if memory_dir:
        existing = _read_memory_index(memory_dir)
    else:
        existing = "(memory 디렉토리 없음 — 신규 프로젝트)"

    # 옵저베이션 너무 많으면 자르기
    obs_to_send = obs_list[:MAX_OBSERVATIONS_PER_PROJECT]
    obs_text = "\n\n".join(
        f"[obs_id={o['id']}] type={o['type']} title={o['title']}\n"
        f"  subtitle: {o['subtitle']}\n"
        f"  narrative: {o['narrative']}\n"
        f"  concepts: {o['concepts']}\n"
        f"  created: {o['created_at']}"
        for o in obs_to_send
    )

    prompt = PROMOTION_PROMPT.format(
        project=project,
        existing_memory_index=existing,
        observations=obs_text,
    )
    raw = _call_claude(prompt)

    # JSON 파싱 시도 (코드블록 제거)
    text = raw.strip()
    if text.startswith("```"):
        # ```json ... ``` 형태
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {
            "_parse_error": str(e),
            "_raw": raw[:1000],
            "promotion_candidates": [],
            "duplicates": [],
            "stats": {"total_observations": len(obs_to_send), "promotion_count": 0, "duplicate_count": 0, "noise_count": 0},
        }


def _write_report(today: datetime, results: dict[str, dict]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"promotion_{today.strftime('%Y-%m-%d')}.md"

    lines = [
        f"# 메모리 승격 후보 보고서 — {today.strftime('%Y-%m-%d')}",
        "",
        f"지난 {LOOKBACK_DAYS}일간 claude-mem 옵저베이션 중 MEMORY.md 승격 가치 있는 것을 골라낸 보고서.",
        "",
        "## 요약",
        "",
    ]
    total_obs = sum(r.get("stats", {}).get("total_observations", 0) for r in results.values())
    total_promote = sum(len(r.get("promotion_candidates", [])) for r in results.values())
    total_dup = sum(len(r.get("duplicates", [])) for r in results.values())
    lines.append(f"- 검토 옵저베이션: **{total_obs}건**")
    lines.append(f"- 승격 후보: **{total_promote}건**")
    lines.append(f"- 중복 의심: **{total_dup}건**")
    lines.append(f"- 프로젝트 수: **{len(results)}개**")
    lines.append("")

    for project, r in results.items():
        lines.append(f"## {project}")
        lines.append("")
        if r.get("_parse_error"):
            lines.append(f"⚠️ 파싱 실패: {r['_parse_error']}")
            lines.append("```")
            lines.append(r.get("_raw", "")[:500])
            lines.append("```")
            lines.append("")
            continue

        cands = r.get("promotion_candidates", [])
        if cands:
            lines.append(f"### 승격 후보 ({len(cands)}건)")
            lines.append("")
            for c in cands:
                fm = c.get("proposed_frontmatter", {})
                lines.append(f"#### `{c.get('proposed_filename', '?')}`")
                lines.append(f"- **이유**: {c.get('reason', '')}")
                lines.append(f"- **type**: {fm.get('type', '?')}")
                lines.append(f"- **description**: {fm.get('description', '')}")
                lines.append(f"- **obs_ids**: {c.get('obs_ids', [])}")
                lines.append("")
                lines.append("**제안 본문**:")
                lines.append("```")
                lines.append(c.get("proposed_body", ""))
                lines.append("```")
                lines.append("")

        dups = r.get("duplicates", [])
        if dups:
            lines.append(f"### 중복 의심 ({len(dups)}건)")
            lines.append("")
            for d in dups:
                lines.append(f"- obs_ids={d.get('obs_ids', [])} ↔ `{d.get('duplicates_with', '?')}`")
                lines.append(f"  - {d.get('note', '')}")
            lines.append("")

        if not cands and not dups:
            lines.append("승격 후보 없음. 옵저베이션이 모두 노이즈로 판정됨.")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 사용 방법")
    lines.append("")
    lines.append("이 보고서는 **자동 적용되지 않는다**. 사용자가 검토 후 직접 결정.")
    lines.append("")
    lines.append("- 승격 후보 중 동의하는 것: 해당 `proposed_filename`을 적절한 `~/.claude/projects/<...>/memory/`에 생성, MEMORY.md 인덱스에 추가")
    lines.append("- 중복: 무시 또는 기존 메모리 갱신")
    lines.append("- 처리 끝나면: `python scripts/memory_promotion_check.py --mark-read` 실행")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def run_promotion_check(force: bool = False) -> dict:
    """월간 큐레이션에서 호출됨. 30일 주기로 보고서 생성."""
    today = datetime.now()
    state = _load_state()

    if not force and not _should_run(state, today):
        last = state.get("last_check_date", "(없음)")
        print(f"[promotion] 마지막 점검 {last} — 30일 미경과, 스킵")
        return {"skipped": True}

    print(f"\n=== 메모리 승격 점검 시작 ({today.strftime('%Y-%m-%d')}) ===")

    since = today - timedelta(days=LOOKBACK_DAYS)
    by_project = _read_observations_by_project(since)

    if not by_project:
        print("[promotion] 평가할 옵저베이션 없음")
        state["last_check_date"] = today.strftime("%Y-%m-%d")
        _save_state(state)
        return {"empty": True}

    print(f"[promotion] {len(by_project)}개 프로젝트, 총 {sum(len(v) for v in by_project.values())}건 옵저베이션 평가")

    results: dict[str, dict] = {}
    for project, obs_list in by_project.items():
        print(f"  - {project}: {len(obs_list)}건 평가 중...")
        memory_dir = _project_to_memory_dir(project)
        try:
            results[project] = _evaluate_project(project, obs_list, memory_dir)
        except Exception as e:
            print(f"    실패: {e}")
            results[project] = {"_error": str(e), "promotion_candidates": [], "duplicates": [], "stats": {}}

    report_path = _write_report(today, results)

    state["last_check_date"] = today.strftime("%Y-%m-%d")
    state["unread_report"] = str(report_path)
    state["unread_since"] = today.strftime("%Y-%m-%d")
    _save_state(state)

    total_promote = sum(len(r.get("promotion_candidates", [])) for r in results.values())
    print(f"\n=== 승격 점검 완료: {total_promote}건 후보 → {report_path} ===")
    return {"report": str(report_path), "promotion_count": total_promote}


def mark_report_read() -> None:
    state = _load_state()
    if state.get("unread_report"):
        state["last_read_date"] = datetime.now().strftime("%Y-%m-%d")
        state["unread_report"] = None
        state["unread_since"] = None
        _save_state(state)
        print("[promotion] 보고서 읽음 처리 완료")
    else:
        print("[promotion] 미확인 보고서 없음")


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="주기 무시하고 강제 실행")
    ap.add_argument("--mark-read", action="store_true", help="보고서 읽음 처리")
    ap.add_argument("--show-unread", action="store_true", help="미확인 보고서 정보")
    args = ap.parse_args()

    if args.mark_read:
        mark_report_read()
        return 0

    if args.show_unread:
        state = _load_state()
        if state.get("unread_report"):
            print(f"unread_report: {state['unread_report']}")
            print(f"unread_since: {state['unread_since']}")
        else:
            print("미확인 보고서 없음")
        return 0

    run_promotion_check(force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
