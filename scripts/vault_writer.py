"""
처리된 뉴스 항목들을 Obsidian vault에 마크다운으로 저장한다.
구조:
  vault/daily/{YYYY-MM-DD}.md          하루치 모음
  vault/concepts/{Name}.md             개념 노드
  vault/people/{Name}.md               인물 노드
  vault/orgs/{Name}.md                 조직 노드
  vault/papers/{Name}.md               논문 노드

위키링크는 [[Name]] 형태로 일일 노트와 노드 양쪽에서 상호 참조된다.
노드 파일은 누적(append)된다 — 새 뉴스가 같은 노드를 언급하면 references 섹션에 추가만 함.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
DAILY = VAULT / "daily"
CONCEPTS = VAULT / "concepts"
PEOPLE = VAULT / "people"
ORGS = VAULT / "orgs"
PAPERS = VAULT / "papers"

CAT_DIRS = {
    "concepts": CONCEPTS,
    "people": PEOPLE,
    "orgs": ORGS,
    "papers": PAPERS,
}

INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """파일명으로 못 쓰는 문자 제거. 공백은 유지(Obsidian이 처리)."""
    cleaned = INVALID_FS_CHARS.sub("", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:100] or "unnamed"


def _format_daily_note(publish_date: datetime, content_date: datetime, items: list[dict]) -> str:
    pub_str = publish_date.strftime("%Y-%m-%d")
    content_str = content_date.strftime("%Y-%m-%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][publish_date.weekday()]

    lines: list[str] = []
    lines.append("---")
    lines.append(f"date: {pub_str}")
    lines.append(f"content_date: {content_str}")
    lines.append(f"count: {len(items)}")
    sources = sorted({it["source"] for it in items})
    lines.append(f"sources: [{', '.join(sources)}]")
    lines.append("type: daily")
    lines.append("---")
    lines.append("")
    lines.append(f"# {pub_str} ({weekday}) AI 뉴스")
    lines.append("")
    if not items:
        lines.append("> 오늘 수집된 항목이 없습니다.")
        return "\n".join(lines) + "\n"

    for idx, it in enumerate(items, 1):
        lines.append(f"## {idx}. {it.get('title_ko') or it.get('title', '')}")
        lines.append("")
        score_label = {
            "points": "HN 점수",
            "stars_today": "오늘 별",
            "votes": "GN 추천",
            "official": "공식발표",
        }.get(it.get("score_kind"), "점수")
        score_val = it.get("score")
        if score_val is not None and it.get("score_kind") != "official":
            lines.append(f"- **출처**: {it['source']} ({score_label} {score_val})")
        else:
            lines.append(f"- **출처**: {it['source']}")
        lines.append(f"- **원문**: [{it['url']}]({it['url']})")
        if it.get("published"):
            lines.append(f"- **게시**: {it['published']}")
        if it.get("tags"):
            tag_line = " ".join(f"#{t.replace(' ', '-')}" for t in it["tags"])
            lines.append(f"- **태그**: {tag_line}")
        lines.append("")
        lines.append("**요약**")
        lines.append("")
        lines.append(it.get("summary_ko", "").strip() or "_요약 없음_")
        lines.append("")
        if it.get("application_ko"):
            lines.append("**활용방안**")
            lines.append("")
            lines.append(it["application_ko"].strip())
            lines.append("")

        # 위키 링크 (관련 노드)
        nodes = it.get("nodes", {})
        link_chunks = []
        for cat in ("concepts", "people", "orgs", "papers"):
            for n in nodes.get(cat, []):
                link_chunks.append(f"[[{_safe_filename(n)}]]")
        if link_chunks:
            lines.append("**관련 노드**: " + " · ".join(link_chunks))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines) + "\n"


def _node_path(category: str, name: str) -> Path:
    return CAT_DIRS[category] / f"{_safe_filename(name)}.md"


def _ensure_node_file(category: str, name: str) -> Path:
    """노드 파일이 없으면 frontmatter 헤더로 새로 만든다."""
    path = _node_path(category, name)
    if path.exists():
        return path
    cat_label = {
        "concepts": "concept",
        "people": "person",
        "orgs": "org",
        "papers": "paper",
    }[category]
    header = (
        f"---\n"
        f"name: {name}\n"
        f"type: {cat_label}\n"
        f"category: {category}\n"
        f"---\n\n"
        f"# {name}\n\n"
        f"## 설명\n\n"
        f"_여기에 자유롭게 메모 추가_\n\n"
        f"## 언급된 뉴스\n\n"
    )
    path.write_text(header, encoding="utf-8")
    return path


def _append_reference(category: str, name: str, publish_date: datetime, item: dict) -> None:
    """노드 파일의 '언급된 뉴스' 섹션에 항목을 추가한다 (중복 제거).

    날짜 라벨/백링크는 publish_date 기준 — 일일 노트가 publish_date.md에 저장되므로 [[YYYY-MM-DD]] 링크가 살아있게 됨.
    """
    path = _ensure_node_file(category, name)
    content = path.read_text(encoding="utf-8")
    date_str = publish_date.strftime("%Y-%m-%d")
    title = item.get("title_ko") or item.get("title", "")
    ref_line = f"- {date_str} — [[{date_str}|{title}]] _(출처: {item['source']})_"
    if ref_line in content:
        return
    if "## 언급된 뉴스" not in content:
        content += "\n## 언급된 뉴스\n\n"
    content = content.rstrip() + "\n" + ref_line + "\n"
    path.write_text(content, encoding="utf-8")


def write_daily(publish_date: datetime, content_date: datetime, items: list[dict]) -> Path:
    """일일 노트 + 위키 노드 모두 저장. 일일 노트 경로 반환.

    publish_date: 발행 날짜 (=파일명, 폴더명, 헤더 표기)
    content_date: 기사 수집 대상 날짜 (예: 발행 전날)
    """
    DAILY.mkdir(parents=True, exist_ok=True)
    for d in CAT_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)

    daily_path = DAILY / f"{publish_date.strftime('%Y-%m-%d')}.md"
    daily_path.write_text(_format_daily_note(publish_date, content_date, items), encoding="utf-8")

    # 위키 노드 누적 갱신 — 백링크는 publish_date 기준
    for it in items:
        nodes = it.get("nodes", {})
        for cat in CAT_DIRS:
            for name in nodes.get(cat, []):
                if name and name.strip():
                    _append_reference(cat, name.strip(), publish_date, it)

    print(f"[vault] wrote {daily_path.name} (content={content_date.strftime('%Y-%m-%d')}) + node refs")
    return daily_path


if __name__ == "__main__":
    sample_items = [{
        "source": "Anthropic",
        "title": "Test post",
        "title_ko": "테스트 포스트",
        "url": "https://example.com",
        "published": "2026-05-05",
        "summary_ko": "샘플 요약입니다.\n두 번째 줄.",
        "application_ko": "활용 방안 예시.",
        "tags": ["test", "sample"],
        "nodes": {
            "concepts": ["RAG"],
            "people": ["Andrej Karpathy"],
            "orgs": ["Anthropic"],
            "papers": [],
        },
    }]
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    write_daily(today, yesterday, sample_items)
