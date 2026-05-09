"""
메모리 시맨틱 검색 — 임베딩 없이 Claude CLI로.

흐름:
  1) ~/.claude/projects/.../memory/ 의 모든 메모리 파일 (auto_*, project_*, feedback_*, reference_*) 인덱스 로딩
  2) 각 파일의 description (frontmatter) + 첫 200자 본문을 묶어 카탈로그 만들기
  3) "쿼리"를 받으면 카탈로그 + 쿼리 → Claude Haiku에게 "관련 top-K 골라" 요청
  4) 결과를 path + 매칭 이유와 함께 반환

원칙:
  - 임베딩 없음 — 설치 종속성 0
  - 한 번 호출 = 한 번 LLM. 카탈로그가 100개 이하라 5~10초 내
  - 슬래시 커맨드 또는 직접 호출

호출:
  python scripts/memory_search.py "query"
  → JSON 출력 [{path, reason, score}, ...]

  python scripts/memory_search.py --rebuild-catalog
  → 카탈로그를 data/memory_catalog.json에 캐시 (다음 호출 빠름)
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATALOG_PATH = DATA_DIR / "memory_catalog.json"

CLAUDE_HOME = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_HOME / "projects"

CLAUDE_CMD = "claude"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_TIMEOUT = 60

DEFAULT_TOP_K = 5
BODY_PREVIEW_CHARS = 200


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


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """간단 frontmatter 파서. (meta, body) 반환."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta_text = parts[1]
    body = parts[2].lstrip("\n")
    meta = {}
    for line in meta_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def _scan_memories() -> list[dict]:
    """모든 프로젝트 memory/ 디렉토리의 메모리 파일 스캔. 카탈로그 항목 리스트 반환."""
    if not PROJECTS_DIR.exists():
        return []

    items = []
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        mem_dir = proj_dir / "memory"
        if not mem_dir.exists():
            continue
        for f in mem_dir.glob("*.md"):
            if f.name == "MEMORY.md":  # 인덱스 자체는 skip
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            meta, body = _parse_frontmatter(text)
            preview = re.sub(r"\s+", " ", body[:BODY_PREVIEW_CHARS]).strip()
            items.append({
                "path": str(f),
                "project": proj_dir.name,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "type": meta.get("type", "unknown"),
                "preview": preview,
            })
    return items


def _build_catalog() -> list[dict]:
    items = _scan_memories()
    DATA_DIR.mkdir(exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps({"built_at": datetime.now().isoformat(), "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return items


def _load_catalog(rebuild: bool = False) -> list[dict]:
    if rebuild or not CATALOG_PATH.exists():
        return _build_catalog()
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return _build_catalog()


def _search_prompt(query: str, items: list[dict], top_k: int) -> str:
    catalog_lines = []
    for i, it in enumerate(items):
        line = (
            f"[{i}] project={it['project']} type={it['type']} name={it['name']}\n"
            f"    description: {it['description']}\n"
            f"    preview: {it['preview']}"
        )
        catalog_lines.append(line)
    catalog = "\n\n".join(catalog_lines)

    return f"""사용자가 다음 쿼리에 관련된 메모리를 찾고 있다.

쿼리: {query}

아래는 사용자의 모든 영구 메모리 카탈로그다 ({len(items)}개).
description과 preview를 토대로 쿼리와 가장 관련 깊은 항목 최대 {top_k}개를 고르라.

[카탈로그]
{catalog}

[출력 형식 — 반드시 JSON 배열만]
[
  {{ "index": 0, "score": 0.9, "reason": "왜 매칭인지 한 줄" }},
  ...
]

규칙:
- score는 0.0~1.0
- 약간이라도 의심스러우면 제외 (false positive 피하라)
- 결과 없으면 빈 배열 []
- JSON 외 다른 텍스트 출력 금지"""


def _call_claude(prompt: str) -> str:
    """prompt가 길면 Windows ARG_MAX 초과 — stdin으로 전달."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", "--model", CLAUDE_MODEL],
            input=prompt,
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


def _parse_response(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 첫 [ 부터 마지막 ] 까지만 추출 시도
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return []
        return []


def search(query: str, top_k: int = DEFAULT_TOP_K, rebuild: bool = False) -> list[dict]:
    items = _load_catalog(rebuild=rebuild)
    if not items:
        return []
    prompt = _search_prompt(query, items, top_k)
    raw = _call_claude(prompt)
    matches = _parse_response(raw)

    results = []
    for m in matches:
        idx = m.get("index")
        if idx is None or not (0 <= idx < len(items)):
            continue
        it = items[idx]
        results.append({
            "path": it["path"],
            "project": it["project"],
            "name": it["name"],
            "type": it["type"],
            "description": it["description"],
            "score": m.get("score", 0.0),
            "reason": m.get("reason", ""),
        })
    return results


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="검색어")
    ap.add_argument("-k", "--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--rebuild-catalog", action="store_true", help="카탈로그 재생성 후 종료")
    ap.add_argument("--show-catalog", action="store_true", help="카탈로그 항목 수만 표시")
    args = ap.parse_args()

    if args.rebuild_catalog:
        items = _build_catalog()
        print(f"카탈로그 재생성: {len(items)}개 항목 → {CATALOG_PATH}")
        return 0

    if args.show_catalog:
        items = _load_catalog()
        print(f"카탈로그: {len(items)}개 항목")
        for it in items[:20]:
            print(f"  - [{it['type']}] {it['project']}/{it['name']}")
        if len(items) > 20:
            print(f"  ... 외 {len(items) - 20}개")
        return 0

    if not args.query:
        print("Usage: python memory_search.py \"query\" [-k 5]", file=sys.stderr)
        return 1

    results = search(args.query, top_k=args.top_k)
    if not results:
        print(json.dumps({"query": args.query, "results": []}, ensure_ascii=False))
        return 0
    print(json.dumps({"query": args.query, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
