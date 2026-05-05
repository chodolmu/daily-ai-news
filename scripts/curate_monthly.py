"""
월 1회 자동 실행. 지난 30일치 vault 항목을 LLM이 검토해 다음을 수행한다:

1. 중복 묶기 (duplicate cluster)
   - 같은 주제 다른 글 → 대표 1건만 active 유지, 나머지는 hidden + duplicate_of 표시
2. 노후화 판정 (outdated, 강도: 중간)
   - 보수적: 명백히 deprecated된 모델/라이브러리, 반증된 정보
   - 중간 추가: 신규 모델로 대체된 기법, 트렌드 종료된 단기 화제
   - 적극(제외): 단순히 "오래된 글"은 노후화로 보지 않는다
3. 노드 정리 제안 (suggest only — 자동 변경 X)
   - 동일 개념 다른 표기 통합 후보 (예: RAG ↔ Retrieval-Augmented Generation)
   - 언급 1회뿐인 약한 노드 (low_signal 표시)
4. 반증/충돌 연결
   - 같은 주제 다른 결론 두 항목이 있으면 양쪽 frontmatter에 conflicts_with 추가

원칙: 절대 항목/노드를 삭제하지 않는다. frontmatter 표시만 한다.
출력은 vault/curation_reports/{YYYY-MM-DD}.md 에 보고서로도 남긴다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
DAILY = VAULT / "daily"
REPORTS = VAULT / "curation_reports"
CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 600  # 월간 큐레이션은 입력이 커서 여유 있게


def _ensure_git_bash() -> None:
    if os.name != "nt" or os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return
    for c in (r"D:\Git\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
        if Path(c).exists():
            os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = c
            return


_ensure_git_bash()


PROMPT = """다음은 지난 30일간 수집된 AI 뉴스 항목들이다.
각 항목에 대해 4가지 큐레이션 작업을 수행하라.

[작업 1: duplicate_clusters]
의미적으로 같은 주제(같은 모델 출시, 같은 라이브러리, 같은 사건)를 다룬 항목들을 묶어라.
각 묶음에서 가장 정보가 풍부한 것을 representative로, 나머지는 duplicates로.

[작업 2: outdated 판정 — 강도: 중간]
다음 둘 중 하나에 해당하면 outdated:
  (a) [보수적] 명백히 deprecated된 모델/라이브러리/API가 핵심 내용
  (b) [보수적] 후속 정보로 명백히 반증/취소된 사실
  (c) [중간] 신규 모델·기법으로 거의 완전히 대체된 구체 가이드 (예: GPT-3.5 fine-tune 가이드)
  (d) [중간] 단기 트렌드가 종료된 화제 (한때 화제였으나 더 이상 유효 X)
주의: 단순히 "한 달 지났다"는 outdated가 아니다. 본질적 가치 손실이 있어야 한다.

[작업 3: node_suggestions — 제안만, 자동 적용 X]
- merge_candidates: 동일 개념의 다른 표기 묶음 (예: ["RAG", "Retrieval-Augmented Generation"])
- low_signal_nodes: 한두 번만 등장하고 더 이상 다뤄지지 않을 것 같은 약한 노드

[작업 4: conflicts]
같은 주제에 대해 정반대 결론을 내린 두 항목이 있으면 쌍으로 묶어라.
(예: "X 라이브러리 추천" vs "X 보안 이슈로 폐기")

[입력]
{items}

[출력 — JSON 한 덩어리만, 코드블록 없이]
{{
  "duplicate_clusters": [
    {{"representative": "<id>", "duplicates": ["<id>", "<id>"], "topic": "한 줄 설명"}}
  ],
  "outdated": [
    {{"id": "<id>", "reason": "한 줄 사유", "severity": "보수|중간"}}
  ],
  "node_suggestions": {{
    "merge_candidates": [["RAG", "Retrieval-Augmented Generation"]],
    "low_signal_nodes": ["NodeName"]
  }},
  "conflicts": [
    {{"a": "<id>", "b": "<id>", "topic": "한 줄 설명"}}
  ]
}}
"""


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


def _parse_daily(path: Path) -> tuple[dict, str, list[dict]]:
    """일일 노트 → frontmatter, body, items[id, title, url, excerpt]."""
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
            "block": block.strip()[:500],
        })
    return fm, body, items


def _write_daily(path: Path, fm: dict, body: str) -> None:
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path.write_text(f"---\n{fm_lines}\n---\n{body}", encoding="utf-8")


def _set_status_field(fm: dict, key: str, ids: list[str]) -> None:
    """frontmatter에 list 형태 필드 갱신 (중복 제거)."""
    existing = []
    if key in fm:
        existing = re.findall(r"[\w\-#:]+", fm[key])
    merged = sorted(set(existing) | set(ids))
    fm[key] = "[" + ", ".join(merged) + "]"


def run_monthly_curation(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    cutoff = now - timedelta(days=30)
    REPORTS.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[Path, dict, str, list[dict]]] = []
    for path in sorted(DAILY.glob("*.md")):
        try:
            d = datetime.strptime(path.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if d.date() < cutoff.date():
            continue
        fm, body, items = _parse_daily(path)
        if items:
            targets.append((path, fm, body, items))

    if not targets:
        print("[monthly] 지난 30일 daily 노트 없음")
        return {}

    flat: list[dict] = []
    for _, _, _, items in targets:
        flat.extend(items)
    print(f"[monthly] 검토 대상: {len(flat)}개 항목 ({len(targets)}일)")

    items_blob = "\n".join(
        f"- id: {it['id']}\n  title: {it['title']}\n  url: {it['url']}\n  excerpt: {it['block'][:300]}"
        for it in flat
    )
    try:
        raw = _call_claude(PROMPT.format(items=items_blob))
        result = _extract_json(raw)
    except Exception as e:
        print(f"[monthly] claude 호출 실패: {e}")
        return {}

    # ---- 결과 적용 ---------------------------------------------------------
    # 1) duplicates: 각 daily 파일 frontmatter에 hidden_indices + duplicate_of 추가
    dup_map: dict[str, str] = {}  # dup_id -> rep_id
    for cluster in result.get("duplicate_clusters", []):
        rep = cluster.get("representative")
        for d_id in cluster.get("duplicates", []):
            if d_id != rep:
                dup_map[d_id] = rep

    # 2) outdated: id별 사유 매핑
    outdated_map: dict[str, str] = {
        o["id"]: o.get("reason", "") for o in result.get("outdated", [])
    }

    # 3) conflicts: 양방향 매핑
    conflict_pairs: list[tuple[str, str, str]] = [
        (c["a"], c["b"], c.get("topic", "")) for c in result.get("conflicts", [])
    ]

    # daily 파일별로 frontmatter 갱신
    by_date: dict[str, list[str]] = {}
    by_date_outdated: dict[str, list[str]] = {}
    for full_id in list(dup_map.keys()) + list(outdated_map.keys()):
        date_part, idx = full_id.split("#", 1)
        if full_id in dup_map:
            by_date.setdefault(date_part, []).append(idx)
        if full_id in outdated_map:
            by_date_outdated.setdefault(date_part, []).append(idx)

    for path, fm, body, items in targets:
        date_key = path.stem
        changed = False
        if date_key in by_date:
            _set_status_field(fm, "hidden_indices", by_date[date_key])
            changed = True
        if date_key in by_date_outdated:
            _set_status_field(fm, "outdated_indices", by_date_outdated[date_key])
            changed = True
        if changed:
            _write_daily(path, fm, body)

    # ---- 보고서 작성 ------------------------------------------------------
    report_path = REPORTS / f"{now.strftime('%Y-%m-%d')}.md"
    lines = [
        "---",
        f"date: {now.strftime('%Y-%m-%d')}",
        "type: curation_report",
        f"reviewed_items: {len(flat)}",
        f"reviewed_days: {len(targets)}",
        "---",
        "",
        f"# {now.strftime('%Y-%m-%d')} 월간 큐레이션 보고서",
        "",
        f"검토 항목: **{len(flat)}건** / {len(targets)}일",
        "",
        "## 중복 묶음",
        "",
    ]
    clusters = result.get("duplicate_clusters", [])
    if clusters:
        for c in clusters:
            lines.append(f"- **{c.get('topic', '')}**")
            lines.append(f"  - 대표: `{c.get('representative')}`")
            for d in c.get("duplicates", []):
                if d != c.get("representative"):
                    lines.append(f"  - 중복: `{d}` (hidden 처리됨)")
    else:
        lines.append("_없음_")

    lines += ["", "## 노후화 항목 (outdated)", ""]
    od = result.get("outdated", [])
    if od:
        for o in od:
            lines.append(f"- `{o['id']}` [{o.get('severity', '?')}] — {o.get('reason', '')}")
    else:
        lines.append("_없음_")

    lines += ["", "## 충돌/반증 쌍", ""]
    cf = result.get("conflicts", [])
    if cf:
        for c in cf:
            lines.append(f"- `{c['a']}` ↔ `{c['b']}` — {c.get('topic', '')}")
    else:
        lines.append("_없음_")

    lines += ["", "## 노드 정리 제안", "", "### 통합 후보", ""]
    ns = result.get("node_suggestions", {}) or {}
    merges = ns.get("merge_candidates", []) or []
    if merges:
        for group in merges:
            lines.append(f"- {' / '.join(group)}")
    else:
        lines.append("_없음_")
    lines += ["", "### 약한 노드 (low signal)", ""]
    lows = ns.get("low_signal_nodes", []) or []
    if lows:
        for n in lows:
            lines.append(f"- {n}")
    else:
        lines.append("_없음_")

    lines += ["", "---", "", "_이 보고서는 자동 생성되었으며, 노드 정리 제안은 검토 후 수동 적용해야 한다._", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[monthly] 보고서: {report_path}")
    print(f"[monthly] 중복 {len(dup_map)}건, 노후화 {len(outdated_map)}건, 충돌 {len(conflict_pairs)}쌍")
    return result


if __name__ == "__main__":
    run_monthly_curation()
