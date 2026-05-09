"""
Vault → ~/.claude/ 자동 변환기.

흐름:
  1) 최근 N일치 daily 노트를 읽어 quality≥7 항목만 후보로 추출
  2) 각 항목에 대해 Claude CLI로 판정:
     - "이게 ~/.claude/skills/ 또는 memory/ 에 들어갈 가치 있나?"
     - 있다면 어떤 형식(skill/memory)으로, 어떤 내용으로?
  3) 합리적 판정이 나오면 자동 적용:
     - skill 후보 → ~/.claude/skills/<name>/SKILL.md 작성
     - memory 후보 → ~/.claude/projects/C--Users-chodo/memory/<name>.md + MEMORY.md 인덱스 갱신
  4) 중복 회피: 이미 비슷한 스킬/메모리 있으면 스킵 또는 통합
  5) data/skill_extraction_state.json 으로 처리 이력 추적

원칙:
  - 100% 자율: 너의 검토 없이 바로 적용
  - 보수적 판정: 의심스러우면 스킵 (false negative > false positive)
  - 출처 보존: 모든 생성 파일에 vault 원문 링크 기록
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
DAILY_DIR = VAULT / "daily"
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "skill_extraction_state.json"

CLAUDE_HOME = Path.home() / ".claude"
SKILLS_DIR = CLAUDE_HOME / "skills"
MEMORY_DIR = CLAUDE_HOME / "projects" / "C--Users-chodo" / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 300
CLAUDE_MODEL = "claude-sonnet-4-6"

# 윈도우 git-bash 경로 (processor.py와 동일 패턴)
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


JUDGE_PROMPT = """다음은 어제 vault에 누적된 AI/개발 관련 정보 항목이다.
이 항목이 ~/.claude/ 환경(개인 AI 코딩 어시스턴트 설정)에 영구 저장할 가치가 있는지 판정하라.

[입력 항목]
제목: {title}
요약: {summary}
활용방안: {application}
태그: {tags}
출처: {source}
원문: {url}

[현재 ~/.claude/ 상태]
기존 스킬: {existing_skills}
기존 메모리: {existing_memories}

[사용자 컨텍스트]
- 프로젝트: ZooMerge (Godot 머지+드래곤 사육 게임), Mystic Tarot (타로 앱, 2026-04 출시 예정)
- 도구: Claude Code, Godot, Python, 한국어 우선
- 워크플로: 자율 개발 루프(zoodev-loop), TaskForge Pro, 솔로 개발

[판정 기준]
3가지 결정이 가능하다. 보수적 0/1/2/3 단계로 생각하라:

* "skill" (가장 엄격): 다음을 모두 만족해야 함
  1) 사용자 컨텍스트와 직접 관련
  2) 구체적 행동/명령어/워크플로/설정이 명확
  3) 기존 스킬과 중복 없음
  4) 6개월 이상 유효
  → SKILL.md 폴더로 생성. 의심스러우면 memory로 강등하라.

* "memory" (적당히 후하게): 다음 중 하나면 충분
  1) 사용자 워크플로/프로젝트(Claude Code, Godot, 게임, 타로, AI 자율 루프, 한국어)와 관련 가능성 있음
  2) 6개월 내 참고할 가치 있는 도구/프로젝트/기법/원칙 (직접 적용 아니어도 OK)
  3) 기존 메모리와 명백히 중복은 아님 (살짝 겹쳐도 OK — 새로운 각도면 추가)
  → memory 파일 생성. type=reference가 기본. 사용자 선호/원칙이면 user/feedback.

* "skip" (정말 무관할 때만):
  - 금융 전용/특정 산업 한정/탈옥/대기업 일화처럼 사용자와 무관
  - 한 달 안에 노후화될 단순 벤치마크/모델 출시 뉴스
  - 이미 너무 잘 알려진 일반 상식

원칙: 중간이면 skip 말고 memory로. 들어오는 게 없으면 시스템이 없는 거랑 같다.
단, 의심스러운 skill은 안 만든다 (skill은 영구 영향). memory는 90일 미참조 시 자동 폐기되니 후하게.

[출력 — JSON 한 덩어리만, 코드블록 없이]
{{
  "decision": "skill|memory|skip",
  "reason": "한 줄 한국어 판정 근거",
  "name": "skill 또는 memory 파일명용 (영문 lower-snake_case, skip이면 빈 문자열)",
  "memory_type": "user|feedback|project|reference (memory일 때만, 그 외 빈 문자열)",
  "title_short": "한국어 한 줄 제목",
  "content": "skill이면 SKILL.md 본문 전체(frontmatter 포함). memory면 메모리 본문(frontmatter 포함). skip이면 빈 문자열.",
  "index_line": "memory일 때 MEMORY.md에 추가될 한 줄 (예: '- [제목](파일명.md) — 짧은 후크'). 그 외 빈 문자열."
}}
"""


def _call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt, "--model", CLAUDE_MODEL],
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


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"JSON not found: {text[:200]}")
    return json.loads(text[start:end + 1])


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_dates": [], "last_run": None, "applied": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_existing_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    return sorted([p.name for p in SKILLS_DIR.iterdir() if p.is_dir()])


def _list_existing_memories() -> list[str]:
    if not MEMORY_INDEX.exists():
        return []
    text = MEMORY_INDEX.read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip().startswith("-")]


def _parse_daily_note(path: Path) -> list[dict]:
    """일일 노트에서 quality 7+ 항목만 파싱. 헤더의 ## 1. 제목 으로 시작하는 블록 단위."""
    text = path.read_text(encoding="utf-8", errors="replace")
    items = []
    blocks = re.split(r"\n## \d+\. ", text)
    if len(blocks) < 2:
        return []
    for block in blocks[1:]:
        title_line, _, body = block.partition("\n")
        title = title_line.strip()
        url_match = re.search(r"\[https?://[^\]]+\]\((https?://[^)]+)\)", body)
        url = url_match.group(1) if url_match else ""
        source_match = re.search(r"\*\*출처\*\*:\s*([^\n]+)", body)
        source = source_match.group(1).strip() if source_match else ""
        tags_match = re.search(r"\*\*태그\*\*:\s*([^\n]+)", body)
        tags = tags_match.group(1).strip() if tags_match else ""
        summary_match = re.search(r"\*\*요약\*\*\s*\n+(.+?)(?=\n\*\*|\n---)", body, re.DOTALL)
        summary = (summary_match.group(1).strip() if summary_match else "")[:800]
        app_match = re.search(r"\*\*활용방안\*\*\s*\n+(.+?)(?=\n\*\*|\n---)", body, re.DOTALL)
        application = (app_match.group(1).strip() if app_match else "")[:500]
        items.append({
            "title": title, "url": url, "source": source,
            "tags": tags, "summary": summary, "application": application,
        })
    return items


def _judge_item(item: dict, existing_skills: list[str], existing_memories: list[str]) -> dict | None:
    prompt = JUDGE_PROMPT.format(
        title=item["title"][:200],
        summary=item["summary"][:600],
        application=item["application"][:300],
        tags=item["tags"][:200],
        source=item["source"][:100],
        url=item["url"][:200],
        existing_skills=", ".join(existing_skills[:30]) or "(없음)",
        existing_memories="\n".join(existing_memories[:30]) or "(없음)",
    )
    try:
        raw = _call_claude(prompt)
        return _extract_json(raw)
    except Exception as e:
        print(f"  ! judge failed for {item['title'][:60]}: {e}")
        return None


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^a-z0-9_-]", "_", name.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    # _apply_memory가 'auto_<type>_'를 다시 prepend하므로 LLM이 이미 붙인 prefix 제거.
    # 재귀 제거 — 'auto_reference_auto_reference_xxx' 같은 케이스도 잡음.
    while True:
        m = re.match(r"^auto_(user|feedback|project|reference)_(.+)$", name)
        if m:
            name = m.group(2)
            continue
        if name.startswith("auto_"):
            name = name[len("auto_"):]
            continue
        break
    return name[:60] or "untitled"


def _apply_skill(verdict: dict, source_url: str) -> bool:
    name = _safe_filename(verdict.get("name", ""))
    if not name:
        return False
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        print(f"  ~ skill already exists: {name} (skip)")
        return False
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = verdict.get("content", "").strip()
    footer = f"\n\n---\n*Auto-extracted from DailyAINews on {datetime.now():%Y-%m-%d}. Source: {source_url}*\n"
    (skill_dir / "SKILL.md").write_text(content + footer, encoding="utf-8")
    print(f"  + skill created: ~/.claude/skills/{name}/")
    return True


def _apply_memory(verdict: dict, source_url: str) -> bool:
    name = _safe_filename(verdict.get("name", ""))
    if not name:
        return False
    mem_type = verdict.get("memory_type", "reference")
    if mem_type not in {"user", "feedback", "project", "reference"}:
        mem_type = "reference"
    # auto_ 접두사로 자동 가지치기 대상 표시 (사용자 직접 작성 메모리는 안 건드림)
    filename = f"auto_{mem_type}_{name}.md"
    target = MEMORY_DIR / filename
    if target.exists():
        print(f"  ~ memory already exists: {target.name} (skip)")
        return False
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    content = verdict.get("content", "").strip()
    footer = f"\n\n---\n*Auto-extracted from DailyAINews on {datetime.now():%Y-%m-%d}. Source: {source_url}*\n"
    target.write_text(content + footer, encoding="utf-8")
    # MEMORY.md 인덱스 추가 — 파일명은 항상 실제 파일명으로 강제
    if MEMORY_INDEX.exists():
        idx_text = MEMORY_INDEX.read_text(encoding="utf-8", errors="replace").rstrip()
    else:
        idx_text = ""
    title_short = verdict.get("title_short", name)
    hook = verdict.get("index_line", "").split("—", 1)[-1].strip() or "auto-extracted"
    index_line = f"- [{title_short}]({filename}) — {hook}"
    if filename not in idx_text:
        idx_text = (idx_text + "\n" + index_line + "\n").lstrip()
        MEMORY_INDEX.write_text(idx_text, encoding="utf-8")
    print(f"  + memory created: {target.name}")
    return True


def extract_for_dates(content_dates: list[datetime], max_items_per_day: int = 5) -> dict:
    """주어진 publish_date들에 대해 vault → ~/.claude/ 변환 실행."""
    state = _load_state()
    processed_set = set(state.get("processed_dates", []))
    applied_count = {"skill": 0, "memory": 0, "skip": 0}

    existing_skills = _list_existing_skills()
    existing_memories = _list_existing_memories()

    for d in content_dates:
        date_str = d.strftime("%Y-%m-%d")
        if date_str in processed_set:
            print(f"[extract] {date_str} 이미 처리됨, 스킵")
            continue
        note_path = DAILY_DIR / f"{date_str}.md"
        if not note_path.exists():
            print(f"[extract] {note_path} 없음, 스킵")
            continue

        items = _parse_daily_note(note_path)
        # 빠른 1차 필터: tags나 application에 가치 있는 키워드 있는 것만
        priority_keywords = [
            "claude", "agent", "skill", "prompt", "godot", "game", "memory",
            "vault", "obsidian", "rag", "워크플로", "스킬", "에이전트", "프롬프트"
        ]
        scored = []
        for it in items:
            score = sum(1 for kw in priority_keywords if kw in (it["title"] + it["tags"] + it["application"]).lower())
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [it for s, it in scored[:max_items_per_day] if s > 0]

        print(f"\n[extract] {date_str}: 후보 {len(top)}/{len(items)}")
        for i, it in enumerate(top, 1):
            print(f"  [{i}/{len(top)}] judging: {it['title'][:60]}")
            verdict = _judge_item(it, existing_skills, existing_memories)
            if not verdict:
                applied_count["skip"] += 1
                continue
            decision = verdict.get("decision", "skip")
            reason = verdict.get("reason", "")
            print(f"    → {decision}: {reason[:80]}")

            applied = False
            if decision == "skill":
                applied = _apply_skill(verdict, it["url"])
                if applied:
                    existing_skills.append(_safe_filename(verdict.get("name", "")))
            elif decision == "memory":
                applied = _apply_memory(verdict, it["url"])
                if applied:
                    existing_memories.append(verdict.get("index_line", ""))

            applied_count[decision if applied else "skip"] += 1
            if applied:
                state.setdefault("applied", []).append({
                    "date": date_str,
                    "title": it["title"][:100],
                    "decision": decision,
                    "name": verdict.get("name", ""),
                    "url": it["url"],
                    "ts": datetime.now().isoformat(timespec="seconds"),
                })

        processed_set.add(date_str)

    state["processed_dates"] = sorted(processed_set)[-90:]  # 최근 90일치만 보존
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)
    return applied_count


def run_daily_extraction(publish_date: datetime) -> dict:
    """run.py에서 호출: 방금 처리한 publish_date 노트 한 개에 대해 추출 실행."""
    return extract_for_dates([publish_date])


def main() -> int:
    """수동 실행: 최근 7일 vault 노트에 대해 추출 (이미 처리한 건 스킵)."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    today = datetime.now()
    dates = [today - timedelta(days=i) for i in range(7)]
    counts = extract_for_dates(dates)
    print(f"\n=== 추출 완료: skill={counts['skill']}, memory={counts['memory']}, skip={counts['skip']} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
