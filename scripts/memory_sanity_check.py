"""
자동 메모리 sanity check — 30일마다 실행.

흐름:
  1) ~/.claude/projects/.../memory/auto_*.md 스캔 (최근 30일에 추가된 것만)
  2) Claude CLI로 각 메모리 평가:
     - 사용자 컨텍스트(ZooMerge, Mystic Tarot, Claude Code, 한국어)와 정말 부합하나?
     - 한 달 사용 후 뒤돌아보니 노이즈로 판명될 가능성?
     - 다른 메모리와 통합 후보?
  3) 의심스러운 항목 / 통합 후보 / 전체 건강도를 보고서로 출력
  4) 보고서를 vault/curation_reports/memory_sanity_YYYY-MM-DD.md에 저장
  5) data/sanity_state.json에 unread 플래그 → 다음 세션에서 알림용

원칙:
  - 자동 폐기는 안 한다 (skill_pruner.py가 90일 미참조로 처리)
  - 사람의 sanity check가 핵심 — 보고서만 만들고 너가 한 번 본다
  - 점검 후 너가 직접 본 적이 있는지 모르므로, 다음 세션 시작 시 한 번만 알림
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SANITY_STATE = DATA_DIR / "sanity_state.json"
REPORTS_DIR = ROOT / "vault" / "curation_reports"

CLAUDE_HOME = Path.home() / ".claude"
MEMORY_DIR = CLAUDE_HOME / "projects" / "C--Users-chodo" / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 600  # 더 길게: 한 번에 여러 메모리 평가


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


SANITY_PROMPT = """다음은 사용자가 지난 30일간 DailyAINews를 통해 자동으로 누적한 auto_ 메모리 목록이다.
각 항목의 **본질적 진위와 LLM 활용 가치**를 평가하라.

[중요 — 평가 철학]
사용자는 "지금 내 워크플로에 직접 적용 가능한가"로 평가하기를 원하지 않는다.
이유: 지금 안 맞아도 언제 필요해질지 모르고, 미리 모아두면 나중에 리서치 비용을 절약한다 (옵션 가치).
대신 **다음 두 가지로만** 평가하라:

1. **진위 (Truth)** — 이게 진짜 쓸모 있는 정보인가?
   - 흥미: 검증된 패턴/아이디어인가, 아니면 트위터 hype/일회성 가십/벤치마크 자랑인가?
   - 깊이: 한 번 읽고 끝나는 뉴스인가, 다시 펼쳐 참고할 만한 구조적 통찰인가?
   - 수명: 6~12개월 후에도 유효할 가능성이 있나, 한 달이면 노후화될 모델/벤치마크 한정 정보인가?

2. **LLM 활용 가치 (LLM Leverage)** — LLM을 더 똑똑하게 쓰는 데 도움 되나?
   - 프롬프트 기법, 에이전트 패턴, 컨텍스트 엔지니어링, 도구 사용, 워크플로 설계 등
   - 단순 "AI 트렌드 뉴스"는 LLM 활용에 직접 기여하지 않으면 가치 낮음
   - 사례/데이터(우버 비용 등)도 LLM 활용 의사결정에 쓸 수 있으면 가치 있음

[중요 — 사용자 컨텍스트는 평가 기준 아님]
ZooMerge/Mystic Tarot/솔로개발과 직접 관련 없어도 "엔터프라이즈라 안 맞다"고 의심으로 분류하지 마라.
사용자 컨텍스트는 변할 수 있고 메모리는 옵션이다. 컨텍스트 무관성을 기준으로 삼지 말 것.

[메모리 목록]
{memory_list}

[분류]
- "유지" — 진위·활용가치 둘 다 합격. 메모리로 둘 가치 명백.
- "의심" — 둘 중 하나라도 의심스러움. 구체적 이유 명시 (hype/노후화 임박/뉴스성 가십/LLM과 무관 등).
- "통합" — 다른 메모리와 주제·결론이 거의 동일하여 한 파일로 합치면 깔끔.

[출력 — 한국어 마크다운, 코드블록 없이]
## 메모리 Sanity 보고서 ({today})

### 전체 건강도
- 총 메모리: {total}개
- 한 줄 평가 (진위/LLM활용 관점에서 — 예: "양호 — 대부분 LLM 활용 패턴/원칙으로 다시 펼쳐 참고할 가치" 또는 "주의 — 40%가 일회성 hype나 노후화 임박 벤치마크")

### 유지 권장
- 파일명 (각 한 줄: 왜 유지 — 진위 또는 LLM활용 측면)

### 의심 (검토 권장)
1. **파일명**: 한 줄 의심 사유 — hype인가/노후화 임박인가/LLM 활용에 무관한가/구체적 이유.
2. ...

### 통합 후보
1. **A.md + B.md**: 같은 결론을 다른 각도로 말함 — 한 파일로 합치면 좋을 형태 한 줄
2. ...

### 너에게 묻고 싶은 결정
- 1~3개 구체적 질문 (예: "X와 Y는 결론이 사실상 같은데 통합할까?", "Z 사례는 1년 내 노후화 가능성 — 폐기 vs 유지?")

마지막 줄에 다음 정확한 텍스트:
**다음 점검: 30일 후**
"""


def _call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            timeout=CLAUDE_TIMEOUT, env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found in PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timeout")
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:500]}")
    return result.stdout.strip()


def _load_state() -> dict:
    if SANITY_STATE.exists():
        return json.loads(SANITY_STATE.read_text(encoding="utf-8"))
    return {"last_check_date": None, "unread_report": None}


def _save_state(state: dict) -> None:
    SANITY_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_recent_auto_memories(within_days: int = 30) -> list[dict]:
    """최근 N일에 추가된 auto 메모리 메타데이터 추출."""
    if not MEMORY_DIR.exists():
        return []
    cutoff = datetime.now() - timedelta(days=within_days)
    out = []
    for p in sorted(MEMORY_DIR.iterdir()):
        if not (p.is_file() and p.name.startswith("auto_") and p.suffix == ".md"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Auto-extracted from DailyAINews on (\d{4}-\d{2}-\d{2})", text)
        if m:
            created = datetime.strptime(m.group(1), "%Y-%m-%d")
        else:
            created = datetime.fromtimestamp(p.stat().st_mtime)
        if created < cutoff:
            continue
        # 본문 첫 200자 미리보기 (frontmatter 제외)
        body = re.sub(r"^---.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
        preview = body[:200].replace("\n", " ")
        out.append({
            "filename": p.name,
            "created": created.strftime("%Y-%m-%d"),
            "preview": preview,
        })
    return out


def _format_memory_list(memories: list[dict]) -> str:
    lines = []
    for m in memories:
        lines.append(f"- **{m['filename']}** (created {m['created']}): {m['preview']}")
    return "\n".join(lines)


def run_sanity_check(force: bool = False) -> dict:
    """30일마다 호출. force=True면 주기 무시."""
    today = datetime.now()
    state = _load_state()

    if not force and state.get("last_check_date"):
        last_dt = datetime.strptime(state["last_check_date"], "%Y-%m-%d")
        if (today - last_dt).days < 30:
            print(f"[sanity] 마지막 점검 {state['last_check_date']}, 30일 미경과 — 스킵")
            return {"skipped": True}

    memories = _list_recent_auto_memories(within_days=30)
    if not memories:
        print("[sanity] 최근 30일 auto 메모리 없음 — 스킵")
        state["last_check_date"] = today.strftime("%Y-%m-%d")
        _save_state(state)
        return {"skipped": True, "reason": "no_memories"}

    print(f"[sanity] 메모리 {len(memories)}개 점검 중...")
    prompt = SANITY_PROMPT.format(
        memory_list=_format_memory_list(memories),
        today=today.strftime("%Y-%m-%d"),
        total=len(memories),
        유지_개수="?",  # LLM이 채움
    )
    try:
        report = _call_claude(prompt)
    except Exception as e:
        print(f"[sanity] LLM 호출 실패: {e}")
        return {"error": str(e)}

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"memory_sanity_{today:%Y-%m-%d}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[sanity] 보고서 저장: {report_path}")

    state["last_check_date"] = today.strftime("%Y-%m-%d")
    state["unread_report"] = str(report_path)
    state["unread_since"] = today.strftime("%Y-%m-%d")
    _save_state(state)

    return {
        "report_path": str(report_path),
        "memories_checked": len(memories),
    }


def get_unread_report() -> dict | None:
    """세션 시작 시 호출용. 미확인 보고서 정보 반환."""
    state = _load_state()
    unread = state.get("unread_report")
    if not unread:
        return None
    p = Path(unread)
    if not p.exists():
        return None
    return {
        "path": str(p),
        "since": state.get("unread_since", ""),
        "preview": p.read_text(encoding="utf-8", errors="replace")[:500],
    }


def mark_report_read() -> None:
    """사용자가 보고서를 봤을 때 호출."""
    state = _load_state()
    state["unread_report"] = None
    state["unread_since"] = None
    state["last_read_date"] = datetime.now().strftime("%Y-%m-%d")
    _save_state(state)


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="주기 무시하고 강제 실행")
    ap.add_argument("--mark-read", action="store_true", help="현재 보고서를 읽음 처리")
    ap.add_argument("--show-unread", action="store_true", help="미확인 보고서 정보 출력")
    args = ap.parse_args()

    if args.mark_read:
        mark_report_read()
        print("[sanity] 보고서 읽음 처리 완료")
        return 0
    if args.show_unread:
        unread = get_unread_report()
        if unread:
            print(json.dumps(unread, ensure_ascii=False, indent=2))
        else:
            print("(미확인 보고서 없음)")
        return 0

    result = run_sanity_check(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
