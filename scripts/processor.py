"""
Claude Code CLI(`claude -p`)를 호출해서 각 뉴스 항목을 처리한다:
  - 한국어 제목 (영문 원문일 경우 번역)
  - 3~4줄 요약 (한국어)
  - 활용방안 1~2줄 (한국어)
  - 위키 노드 추출: concepts / people / orgs / papers

응답은 JSON 한 덩어리로 받아 파싱한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT = 300
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Windows에서 Claude Code CLI는 git-bash가 필요. 자동 탐색.
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

PROMPT_TEMPLATE = """다음은 AI 관련 뉴스 항목이다. 정해진 JSON 스키마에 맞춰 한국어로 응답하라.

[입력]
source: {source}
title: {title}
url: {url}
published: {published}
raw_summary: {raw_summary}

[지시]
1. title_ko: 제목을 자연스러운 한국어로 (이미 한국어면 그대로).
2. summary_ko: 본문 핵심을 2~3줄(한 줄당 40~80자)로 한국어 요약. 광고성/홍보 표현은 제거.
3. application_ko: 이 정보를 실제로 어떻게 활용할 수 있는지 1줄(한국어). 추상적 미사여구 금지, 구체적인 사용 시나리오.
4. tags: 관련 태그 3~6개 (영문 lower-case, 예: "rag", "agent", "fine-tuning").
5. nodes: 본문에서 등장하는 위키 노드를 카테고리별로 추출.
   - concepts: 기술/개념/방법론 (예: "RAG", "Mixture of Experts", "Constitutional AI")
   - people: 인물 풀네임 (예: "Andrej Karpathy")
   - orgs: 조직/회사 (예: "Anthropic", "OpenAI")
   - papers: 논문 제목 (확실히 논문일 때만)
   각 카테고리는 0~5개. 확실하지 않으면 비워라. 약어보다 정식 명칭 우선.
6. quality: 0~10 정수. 다음 항목이면 0~3 → 가치 낮음:
   - 단순 광고/홍보
   - "이미 Claude/주류 LLM에 적용된 잘 알려진 내용"
   - 클릭베이트성 짧은 글
   가치 있는 신규 정보면 7~10.

[출력 — JSON 한 덩어리만, 코드블록 없이]
{{
  "title_ko": "...",
  "summary_ko": "...",
  "application_ko": "...",
  "tags": ["..."],
  "nodes": {{
    "concepts": ["..."],
    "people": ["..."],
    "orgs": ["..."],
    "papers": ["..."]
  }},
  "quality": 8
}}
"""


def _call_claude(prompt: str) -> str:
    """claude -p 동기 호출. CLAUDECODE 환경변수를 제거해 중첩 세션 차단 우회."""
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
        raise RuntimeError("claude CLI not found in PATH. Claude Code 설치/로그인 확인 필요.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI 호출 타임아웃")
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:500]}")
    return result.stdout.strip()


def _extract_json(text: str) -> dict[str, Any]:
    """응답에서 JSON 본문만 추출. 코드블록/잡설 섞여 있어도 견디게."""
    # ```json ... ``` 블록 우선
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # 첫 { 부터 마지막 } 까지
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"JSON not found in response: {text[:200]}")
    return json.loads(text[start:end + 1])


def process_item(item: dict) -> dict | None:
    """단일 뉴스 항목 → 처리된 메타 dict. 실패 시 None."""
    prompt = PROMPT_TEMPLATE.format(
        source=item.get("source", ""),
        title=(item.get("title") or "")[:300],
        url=item.get("url", ""),
        published=item.get("published", ""),
        raw_summary=(item.get("raw_summary") or "")[:1500],
    )
    try:
        raw = _call_claude(prompt)
        parsed = _extract_json(raw)
    except Exception as e:
        print(f"  ! processor failed for {item.get('url')}: {e}")
        return None

    # 기본 필드 보존 + 처리 결과 병합
    out = {
        **item,
        "title_ko": parsed.get("title_ko") or item.get("title", ""),
        "summary_ko": parsed.get("summary_ko", ""),
        "application_ko": parsed.get("application_ko", ""),
        "tags": parsed.get("tags") or [],
        "nodes": {
            "concepts": (parsed.get("nodes") or {}).get("concepts") or [],
            "people":   (parsed.get("nodes") or {}).get("people") or [],
            "orgs":     (parsed.get("nodes") or {}).get("orgs") or [],
            "papers":   (parsed.get("nodes") or {}).get("papers") or [],
        },
        "quality": int(parsed.get("quality", 5)),
    }
    return out


def process_items(items: list[dict], min_quality: int = 4) -> list[dict]:
    """모든 항목 처리. quality 임계값만 적용, 개수 제한 없음.
    수집 단계에서 이미 점수/별로 1차 필터된 항목들이 들어온다."""
    kept: list[dict] = []
    for i, it in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] processing: {it['title'][:60]}")
        result = process_item(it)
        if result and result["quality"] >= min_quality:
            kept.append(result)
    kept.sort(key=lambda x: x["quality"], reverse=True)
    return kept


if __name__ == "__main__":
    sample = {
        "source": "Anthropic",
        "title": "Introducing Claude Sonnet 4.6",
        "url": "https://www.anthropic.com/news/claude-sonnet-4-6",
        "published": "2026-05-04T12:00:00Z",
        "raw_summary": "Claude Sonnet 4.6 advances coding and agent reasoning...",
        "lang": "en",
    }
    out = process_item(sample)
    print(json.dumps(out, ensure_ascii=False, indent=2))
