"""
Vault의 일일 마크다운을 파싱해 정적 사이트(site/)로 빌드한다.

산출물:
  site/index.html              가장 최근 날짜로 redirect (메타 + JS)
  site/{YYYY-MM-DD}/index.html 일일 페이지
  site/data/calendar.json      {"days": {"YYYY-MM-DD": <항목수>}, "latest": "...", "earliest": "..."}
  site/assets/style.css        템플릿에서 복사
  site/assets/calendar.js      템플릿에서 복사

마크다운 파싱:
  - frontmatter에서 hidden_indices, outdated_indices 읽어 해당 항목은 카드에서 제외
  - ## N. 제목  →  하나의 카드
  - 본문에서 출처/점수/요약/활용방안/태그/원문 추출
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
DAILY = VAULT / "daily"
TEMPLATES = ROOT / "templates"
TEMPLATE_ASSETS = TEMPLATES / "assets"
SITE = ROOT / "docs"
SITE_ASSETS = SITE / "assets"
SITE_DATA = SITE / "data"

KOREAN_WEEKDAY = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    fm: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def _parse_indices_field(value: str) -> set[int]:
    """frontmatter의 '[1, 3, 7]' 같은 값을 정수 set으로."""
    if not value:
        return set()
    return {int(x) for x in re.findall(r"\d+", value)}


def _source_code(source: str) -> str:
    s = (source or "").lower()
    if "hacker" in s: return "hn"
    if "github" in s: return "gh"
    if "geek" in s:   return "gn"
    if "anthropic" in s: return "an"
    return "other"


def _parse_card(idx: int, title: str, block: str) -> dict | None:
    """## N. 제목 + 본문 블록 → 카드 dict."""
    src_m = re.search(r"\*\*출처\*\*:\s*([^\n]+)", block)
    url_m = re.search(r"\*\*원문\*\*:\s*\[([^\]]+)\]\(([^)]+)\)", block)
    if not (src_m and url_m):
        return None

    source_full = src_m.group(1).strip()
    # "GitHub Trending (오늘 별 2829)" -> source="GitHub Trending", score_label="오늘 별 2829"
    score_label = ""
    sm = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", source_full)
    if sm:
        source = sm.group(1).strip()
        score_label = sm.group(2).strip()
    else:
        source = source_full

    url = url_m.group(2).strip()

    # 요약: '**요약**' 다음 빈 줄 이후 ~ 다음 굵은제목 전까지
    summary = ""
    sm2 = re.search(r"\*\*요약\*\*\s*\n\n(.*?)(?=\n\*\*|\n---|\Z)", block, re.DOTALL)
    if sm2:
        summary = sm2.group(1).strip()

    # 활용방안
    application = ""
    am = re.search(r"\*\*활용방안\*\*\s*\n\n(.*?)(?=\n\*\*|\n---|\Z)", block, re.DOTALL)
    if am:
        application = am.group(1).strip()

    # 태그
    tags: list[str] = []
    tm = re.search(r"\*\*태그\*\*:\s*([^\n]+)", block)
    if tm:
        tags = [t.lstrip("#") for t in re.findall(r"#[\w\-]+", tm.group(1))]

    return {
        "idx": idx,
        "title": title,
        "source": source,
        "source_code": _source_code(source),
        "score_label": score_label,
        "summary": summary,
        "application": application,
        "tags": tags,
        "url": url,
    }


def _parse_daily(path: Path) -> tuple[dict, list[dict]]:
    """일일 노트 → (frontmatter, [카드, ...])"""
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    hidden = _parse_indices_field(fm.get("hidden_indices", ""))
    outdated = _parse_indices_field(fm.get("outdated_indices", ""))
    cards: list[dict] = []
    for sec in re.finditer(
        r"^## (\d+)\. (.+?)$\n(.*?)(?=^## \d+\.|\Z)",
        body, re.DOTALL | re.MULTILINE,
    ):
        idx = int(sec.group(1))
        if idx in hidden or idx in outdated:
            continue
        title = sec.group(2).strip()
        block = sec.group(3)
        card = _parse_card(idx, title, block)
        if card:
            cards.append(card)
    return fm, cards


def _date_meta(date_str: str) -> dict:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return {
        "date_str": date_str,
        "date_long": f"{dt.year}년 {dt.month}월 {dt.day}일",
        "day_name": KOREAN_WEEKDAY[dt.weekday()],
    }


def _collect_all_dates() -> list[str]:
    dates: list[str] = []
    for p in sorted(DAILY.glob("*.md")):
        try:
            datetime.strptime(p.stem, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(p.stem)
    return dates


def _copy_assets() -> None:
    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE_ASSETS.glob("*"):
        if src.is_file():
            shutil.copy2(src, SITE_ASSETS / src.name)


def _write_redirect_index(latest: str | None) -> None:
    target = f"./{latest}/" if latest else "."
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Daily AI News</title>
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
<style>body{{font-family:sans-serif;padding:40px;text-align:center;color:#666}}</style>
</head>
<body>
<p>최신 글로 이동합니다 — <a href="{target}">{target}</a></p>
<script>location.replace("{target}");</script>
</body>
</html>"""
    (SITE / "index.html").write_text(html, encoding="utf-8")


def build_site() -> int:
    print(f"[builder] 빌드 시작 — vault={DAILY}")
    SITE.mkdir(exist_ok=True)
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    _copy_assets()

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template("daily.html.j2")

    all_dates = _collect_all_dates()
    if not all_dates:
        print("[builder] vault에 일일 노트가 없습니다.")
        _write_redirect_index(None)
        (SITE_DATA / "calendar.json").write_text(
            json.dumps({"days": {}, "latest": None, "earliest": None}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 0

    latest = all_dates[-1]
    earliest = all_dates[0]
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    days_count: dict[str, int] = {}
    written = 0

    # 1차 패스: 카드 카운트만 먼저 모음 (캘린더 인라인용)
    cards_by_date: dict[str, list[dict]] = {}
    for date_str in all_dates:
        path = DAILY / f"{date_str}.md"
        _, cards = _parse_daily(path)
        cards_by_date[date_str] = cards
        days_count[date_str] = len(cards)

    calendar_payload = {
        "days": days_count,
        "latest": latest,
        "earliest": earliest,
    }
    calendar_json = json.dumps(calendar_payload, ensure_ascii=False)

    for i, date_str in enumerate(all_dates):
        cards = cards_by_date[date_str]

        # hero 2개 + 나머지 grid (이미 quality 내림차순 정렬되어 있음)
        hero_items = cards[:2] if len(cards) >= 2 else cards[:1]
        grid_items = cards[len(hero_items):]

        prev_date = all_dates[i - 1] if i > 0 else None
        next_date = all_dates[i + 1] if i < len(all_dates) - 1 else None

        meta = _date_meta(date_str)
        ctx = {
            **meta,
            "items": cards,
            "hero_items": hero_items,
            "grid_items": grid_items,
            "prev_date": prev_date,
            "next_date": next_date,
            "latest_date": latest,
            "asset_prefix": "../",
            "last_updated": last_updated,
            "calendar_json": calendar_json,
        }
        html = tpl.render(**ctx)

        out_dir = SITE / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        written += 1

    # 캘린더 JSON (인라인이지만 외부 파일도 유지)
    (SITE_DATA / "calendar.json").write_text(
        json.dumps(calendar_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 루트 index — 최신으로 redirect
    _write_redirect_index(latest)

    print(f"[builder] 완료 — {written}개 페이지, latest={latest}")
    return written


if __name__ == "__main__":
    import io, sys
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    build_site()
