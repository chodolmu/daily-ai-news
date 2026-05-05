"""
주 1회 (월요일) 호출. 지난 7일치 daily 노트를 읽어 LLM에 일괄 검토 의뢰:
  - 광고/홍보성
  - "이미 Claude/주류 LLM에 적용된 잘 알려진 내용"
이런 항목을 hidden 처리한다.

처리 방식: 각 daily 노트의 frontmatter에 hidden_indices 필드를 추가.
원본은 그대로 두고 별도 파일 `vault/daily/{date}.hidden.md`도 만들지 않음 — Obsidian이 frontmatter
필드를 보고 표시 여부를 결정할 수 있도록 메타만 갱신한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "vault" / "daily"
CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 300


def _ensure_git_bash() -> None:
    if os.name != "nt" or os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return
    for c in (r"D:\Git\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
        if Path(c).exists():
            os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = c
            return


_ensure_git_bash()


PROMPT = """다음은 지난 일주일간 수집한 AI 뉴스 항목 목록이다.
각 항목에 대해 hide 여부를 판단하라:

[hide=true 기준]
- 단순 광고/홍보 (제품 홍보, 컨퍼런스 모집 등)
- 이미 Claude나 주류 LLM에 통합되어 새로움이 거의 없는 내용
- 클릭베이트성, 본문보다 제목이 과장된 항목
- 같은 주제의 반복 (이미 더 깊은 글이 있는 경우)

[hide=false 기준]
- 새로운 모델/논문/연구
- 실제로 활용 가능한 새 도구·라이브러리·기법
- 의미 있는 산업 동향

[입력]
{items}

[출력 — JSON 한 덩어리만]
{{
  "decisions": [
    {{"id": "<항목ID>", "hide": true|false, "reason": "한 줄 사유"}}
  ]
}}
"""


def _read_daily(path: Path) -> tuple[dict, str, list[dict]]:
    """일일 노트 → (frontmatter dict, body, items 추출 리스트)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text, []
    fm_raw, body = m.group(1), m.group(2)
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()

    items = []
    for sec in re.finditer(r"^## (\d+)\. (.+?)$\n(.*?)(?=^## \d+\.|\Z)", body, re.DOTALL | re.MULTILINE):
        idx = int(sec.group(1))
        title = sec.group(2).strip()
        block = sec.group(3)
        url_m = re.search(r"\*\*원문\*\*: \[(.+?)\]", block)
        url = url_m.group(1) if url_m else ""
        items.append({
            "id": f"{path.stem}#{idx}",
            "title": title,
            "url": url,
            "block": block.strip()[:600],
        })
    return fm, body, items


def _write_daily(path: Path, fm: dict, body: str) -> None:
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path.write_text(f"---\n{fm_lines}\n---\n{body}", encoding="utf-8")


def _call_claude(prompt: str) -> str:
    result = subprocess.run(
        [CLAUDE_CMD, "-p", prompt],
        capture_output=True, text=True, encoding="utf-8", timeout=CLAUDE_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:300]}")
    return result.stdout.strip()


def _extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


def run_weekly_filter(now: datetime | None = None) -> int:
    """지난 7일치 항목 검토. 처리한 항목 수 반환."""
    now = now or datetime.now()
    week_ago = now - timedelta(days=7)

    targets: list[tuple[Path, dict, str, list[dict]]] = []
    for path in sorted(DAILY.glob("*.md")):
        try:
            d = datetime.strptime(path.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if not (week_ago.date() <= d.date() <= now.date()):
            continue
        fm, body, items = _read_daily(path)
        if items:
            targets.append((path, fm, body, items))

    if not targets:
        print("[weekly] no daily notes in past 7 days")
        return 0

    # 모든 항목을 하나의 프롬프트로 (수십 개라 토큰 부담 작음)
    flat: list[dict] = []
    for _, _, _, items in targets:
        flat.extend(items)

    items_blob = "\n".join(
        f"- id: {it['id']}\n  title: {it['title']}\n  url: {it['url']}\n  excerpt: {it['block'][:300]}"
        for it in flat
    )

    try:
        raw = _call_claude(PROMPT.format(items=items_blob))
        decisions = _extract_json(raw).get("decisions", [])
    except Exception as e:
        print(f"[weekly] claude call failed: {e}")
        return 0

    decisions_by_id = {d["id"]: d for d in decisions}

    # 각 daily 파일별로 frontmatter 갱신
    updated = 0
    for path, fm, body, items in targets:
        hidden = [
            it["id"].split("#", 1)[1]
            for it in items
            if decisions_by_id.get(it["id"], {}).get("hide") is True
        ]
        if hidden:
            fm["hidden_indices"] = "[" + ", ".join(hidden) + "]"
            _write_daily(path, fm, body)
            updated += len(hidden)
            print(f"[weekly] {path.stem}: hide {hidden}")

    print(f"[weekly] hidden {updated} items across {len(targets)} days")
    return updated


if __name__ == "__main__":
    run_weekly_filter()
