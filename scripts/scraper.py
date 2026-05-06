"""
4개 소스에서 AI 관련 글 수집:
  - HackerNews (Algolia API, AI 키워드 필터)
  - GeekNews (RSS, AI 카테고리 + 키워드)
  - GitHub Trending (HTML 파싱, AI 관련 저장소)
  - Anthropic 블로그 (RSS)

수집 결과는 표준화된 dict 리스트로 반환:
  {source, title, url, published, raw_summary, lang}
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEEN_FILE = DATA_DIR / "seen_urls.json"

UA = "Mozilla/5.0 (DailyAINews scraper)"
TIMEOUT = 20

# 트렌드 임계값 — 이 미만이면 수집 단계에서 버린다
# (빡빡 모드 — 토큰 절약 + 진짜 핵심만 남기기)
MIN_HN_POINTS = 200         # HackerNews 점수
MIN_GH_STARS_TODAY = 200    # GitHub Trending 오늘 받은 별
MIN_GEEKNEWS_VOTES = 10     # GeekNews 추천 수
# Anthropic 공식 발표는 임계값 없이 모두 통과

# 제외 키워드 — 제목에 포함되면 AI 키워드 통과해도 버린다 (펀딩/채용/주가 등 노이즈 컷)
EXCLUDE_KEYWORDS = [
    "hiring", "we're hiring", "we are hiring", "careers at",
    "raises", "raised", "series a", "series b", "series c",
    "valuation", "funding round", "ipo",
    "stock price", "market cap", "share price",
    "채용", "공고",
]
EXCLUDE_PATTERN = re.compile("|".join(re.escape(k) for k in EXCLUDE_KEYWORDS), re.IGNORECASE)

AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "anthropic", "openai", "gemini", "deepmind",
    "transformer", "diffusion", "stable diffusion", "midjourney", "sora",
    "agent", "rag", "embedding", "vector", "fine-tun", "lora", "rlhf", "dpo",
    "neural", "deep learning", "machine learning", "ml ", "mlops",
    "huggingface", "hugging face", "langchain", "llamaindex", "vllm", "ollama",
    "인공지능", "딥러닝", "머신러닝", "거대언어모델", "에이전트", "프롬프트",
    "생성형", "파인튜닝", "임베딩", "벡터",
]

AI_PATTERN = re.compile("|".join(re.escape(k) for k in AI_KEYWORDS), re.IGNORECASE)


def is_ai_related(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t)
    return bool(AI_PATTERN.search(blob))


def is_excluded(*texts: str) -> bool:
    """제외 키워드(펀딩/채용/주가 등)가 포함되면 True."""
    blob = " ".join(t for t in texts if t)
    return bool(EXCLUDE_PATTERN.search(blob))


def load_seen() -> dict:
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def _today_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


# --- HackerNews ---------------------------------------------------------------
def fetch_hackernews(target_date: datetime, limit: int = 100) -> list[dict]:
    """Algolia HN API로 target_date 하루치 인기글 조회. 점수 임계값 + AI 키워드 필터."""
    start = int(target_date.replace(hour=0, minute=0, second=0).timestamp())
    end = start + 86400
    url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        f"?tags=story&numericFilters=created_at_i>={start},created_at_i<{end},"
        f"points>={MIN_HN_POINTS}&hitsPerPage={limit}"
    )
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"[HN] fetch failed: {e}")
        return []
    out = []
    for hit in r.json().get("hits", []):
        title = hit.get("title") or ""
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not is_ai_related(title):
            continue
        if is_excluded(title):
            continue
        points = hit.get("points", 0)
        out.append({
            "source": "HackerNews",
            "title": title,
            "url": link,
            "published": hit.get("created_at", ""),
            "raw_summary": "",
            "lang": "en",
            "score": points,
            "score_kind": "points",
        })
    return out


# --- GeekNews -----------------------------------------------------------------
def _fetch_geeknews_votes(topic_url: str) -> int:
    """GeekNews 글 페이지에서 추천수(P) 파싱. 실패 시 0."""
    m = re.search(r"id=(\d+)", topic_url)
    if not m:
        return 0
    topic_id = m.group(1)
    try:
        r = requests.get(topic_url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception:
        return 0
    vm = re.search(rf"<span id=['\"]tp{topic_id}['\"]>(\d+)</span>", r.text)
    return int(vm.group(1)) if vm else 0


def fetch_geeknews(target_date: datetime) -> list[dict]:
    """GeekNews RSS → 후보 추림 → 각 글 페이지에서 추천수 확인 → 임계값 통과만 반환."""
    feed_url = "https://feeds.feedburner.com/geeknews-feed"
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        print(f"[GeekNews] fetch failed: {e}")
        return []
    out = []
    target_str = target_date.strftime("%Y-%m-%d")
    for entry in feed.entries:
        pub = ""
        if getattr(entry, "published_parsed", None):
            pub = time.strftime("%Y-%m-%dT%H:%M:%SZ", entry.published_parsed)
        if pub and not pub.startswith(target_str):
            continue
        title = getattr(entry, "title", "")
        summary = BeautifulSoup(getattr(entry, "summary", ""), "lxml").get_text(" ", strip=True)
        if not is_ai_related(title, summary):
            continue
        if is_excluded(title, summary):
            continue
        link = getattr(entry, "link", "")
        votes = _fetch_geeknews_votes(link)
        if votes < MIN_GEEKNEWS_VOTES:
            continue
        out.append({
            "source": "GeekNews",
            "title": title,
            "url": link,
            "published": pub,
            "raw_summary": summary[:1000],
            "lang": "ko",
            "score": votes,
            "score_kind": "votes",
        })
    return out


# --- GitHub Trending ----------------------------------------------------------
def _parse_today_stars(repo_el) -> int:
    """Trending 페이지의 'N stars today' 숫자 추출."""
    span = repo_el.select_one("span.d-inline-block.float-sm-right")
    if not span:
        return 0
    txt = span.get_text(" ", strip=True)
    m = re.search(r"([\d,]+)\s+stars\s+today", txt)
    if not m:
        return 0
    return int(m.group(1).replace(",", ""))


def fetch_github_trending(target_date: datetime) -> list[dict]:
    """GitHub Trending(daily) 파싱. today_stars 임계값 + AI 키워드 필터."""
    url = "https://github.com/trending?since=daily"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"[GH-Trending] fetch failed: {e}")
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for repo in soup.select("article.Box-row"):
        link_el = repo.select_one("h2 a")
        if not link_el:
            continue
        repo_path = link_el.get("href", "").strip("/")
        title = repo_path
        desc_el = repo.select_one("p")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        if not is_ai_related(title, desc):
            continue
        if is_excluded(title, desc):
            continue
        today_stars = _parse_today_stars(repo)
        if today_stars < MIN_GH_STARS_TODAY:
            continue
        out.append({
            "source": "GitHub Trending",
            "title": title,
            "url": f"https://github.com/{repo_path}",
            "published": target_date.strftime("%Y-%m-%dT00:00:00Z"),
            "raw_summary": desc[:500],
            "lang": "en",
            "score": today_stars,
            "score_kind": "stars_today",
        })
    return out


# --- Anthropic blog -----------------------------------------------------------
def fetch_anthropic(target_date: datetime) -> list[dict]:
    """Anthropic 뉴스 RSS. target_date 하루치만."""
    feed_url = "https://www.anthropic.com/news/rss.xml"
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        print(f"[Anthropic] fetch failed: {e}")
        return []
    out = []
    target_str = target_date.strftime("%Y-%m-%d")
    for entry in feed.entries:
        pub = ""
        if getattr(entry, "published_parsed", None):
            pub = time.strftime("%Y-%m-%dT%H:%M:%SZ", entry.published_parsed)
        if pub and not pub.startswith(target_str):
            continue
        title = getattr(entry, "title", "")
        summary = BeautifulSoup(getattr(entry, "summary", ""), "lxml").get_text(" ", strip=True)
        out.append({
            "source": "Anthropic",
            "title": title,
            "url": getattr(entry, "link", ""),
            "published": pub,
            "raw_summary": summary[:1500],
            "lang": "en",
            "score": 999,
            "score_kind": "official",
        })
    return out


# --- aggregate ----------------------------------------------------------------
def scrape_for_date(target_date: datetime) -> list[dict]:
    """target_date 하루치를 전 소스에서 모아 중복 제거.

    중요: seen_urls.json 저장은 여기서 하지 않는다. 후속 단계(processor → vault writer)가
    성공한 항목만 mark_seen()으로 등록해야 실패 시 영영 차단되는 사고를 막을 수 있다.
    """
    seen = load_seen()
    items: list[dict] = []
    items.extend(fetch_anthropic(target_date))
    items.extend(fetch_geeknews(target_date))
    items.extend(fetch_hackernews(target_date))
    items.extend(fetch_github_trending(target_date))

    deduped = []
    for item in items:
        url = item.get("url")
        if not url or url in seen:
            continue
        deduped.append(item)
    print(f"[scraper] {target_date.date()}: {len(deduped)} new items "
          f"(anthropic+geeknews+hn+gh-trending)")
    return deduped


def mark_seen(items: list[dict], target_date: datetime) -> None:
    """vault 기록까지 성공한 항목을 seen_urls.json에 등록."""
    if not items:
        return
    seen = load_seen()
    date_str = target_date.strftime("%Y-%m-%d")
    for it in items:
        url = it.get("url")
        if not url:
            continue
        seen[url] = {"date": date_str, "source": it.get("source", "?")}
    save_seen(seen)


if __name__ == "__main__":
    today = datetime.now()
    items = scrape_for_date(today)
    for it in items[:5]:
        print(f"  - [{it['source']}] {it['title'][:80]}")
