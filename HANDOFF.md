# Handoff: 증분 빌드 전환

## 한 줄 목표

`scripts/builder.py`를 **매번 전체 빌드**에서 **그날치만 추가**하는 구조로 바꾼다. 어제 페이지·캘린더·prev/next 링크는 새 날짜가 추가될 때 한 번만 업데이트되도록.

## 왜 (사용자 의도)

> "매번 전체 빌드하는건 싫어. 그냥 추가만 하면 되는거라고 나는 생각해"

지금은 vault에 1개 날짜가 추가돼도 365개 페이지 전부 다시 렌더링됨. git diff도 의미 없이 커지고, 단순한 일일 추가 작업에 비해 빌드 행위가 과함.

## 현재 상태 (왜 전체 빌드인가)

`scripts/builder.py:218-258` — 모든 날짜 페이지를 매 빌드 때 재생성. 이유는 페이지마다 **세 가지 동적 요소**가 인라인되어 있기 때문:

1. **캘린더 데이터** (`builder.py:252` → `daily.html.j2:115`)
   - `calendar_json`을 `<script>` 안에 인라인 — 새 날짜 추가 시 모든 옛 페이지의 캘린더 점도 갱신되어야 함
2. **prev/next 네비게이션** (`daily.html.j2:30-44`)
   - 새 어제 글이 추가되면 그제 글 페이지의 `next_date`가 바뀜 → 그제 페이지 재렌더 필요
3. **`latest_date`** (`daily.html.j2:28, 113`)
   - 모든 페이지가 "최신 페이지로 가기" 링크에 사용 → 최신이 바뀌면 모든 페이지 갱신 필요
4. (사소) `last_updated` 시각 — 푸터에 표시 (`daily.html.j2:18`)

`templates/assets/calendar.js`가 이미 있어 클라이언트 fetch 인프라는 절반 마련된 상태. `docs/data/calendar.json`도 이미 별도 파일로 쓰지만, 동시에 페이지에 인라인하기도 해서 **절반 섞인 어정쩡한 상태**.

## 해결 방향

세 가지 동적 요소를 **모두 클라이언트 사이드 fetch**로 빼면 페이지 HTML 자체는 정적이 됨 → 새 날짜 페이지 한 개만 쓰면 끝.

### 구체적 변경 계획

**1. `docs/data/site_meta.json` 신설** (또는 `calendar.json`을 확장):
```json
{
  "days": {"2026-05-06": 6, "2026-05-05": 18, ...},
  "latest": "2026-05-06",
  "earliest": "2026-05-01",
  "last_updated": "2026-05-06 23:30"
}
```

**2. `templates/daily.html.j2` 수정**:
- `{{ calendar_json | safe }}` 인라인 → `fetch('../data/site_meta.json')`로 변경
- `{{ latest_date }}` 하드코딩 → JS가 같은 fetch 결과로 채움 (`href`/`data-latest` 등)
- prev/next 링크는 fetch한 `days` 키 정렬해서 현재 날짜 위치에서 ±1 계산
- `{{ last_updated }}` 푸터도 fetch로 채움
- `data-current="{{ date_str }}"` 만 서버사이드로 남김 (이건 페이지 자체의 정체성)

**3. `templates/assets/calendar.js` 수정**:
- 현재는 `window.SITE.calendar`를 인라인 데이터로 받음 (`daily.html.j2:115`)
- fetch로 받는 흐름으로 통일. 캘린더 렌더, prev/next 링크 주입, latest 링크 주입까지 모두 처리.

**4. `scripts/builder.py` 핵심 변경**:
- 새 함수 `build_one_date(date_str)`: 그 날짜 페이지 한 개만 렌더 + `site_meta.json` 갱신.
- `build_site()` (전체 재빌드)는 보존 — 에셋 변경/템플릿 변경 시 수동 호출용 (`python scripts/run.py --rebuild-all` 같은 옵션).
- `run.py`는 일상 자동 실행에서 `build_one_date(target)`만 호출.

**5. `scripts/run.py` 수정**:
- `build_and_push()`가 `build_site()` 부르던 곳을 `build_one_date(processed_date)`로 변경.
- 처리한 날짜 인자 전달 필요.
- 전체 재빌드용 CLI 옵션 추가 (예: `--rebuild-all`)

## 구현 순서 추천

1. **`docs/data/site_meta.json` 스키마 결정 + 한 번 수동 생성**해서 동작 확인
2. **`calendar.js`를 fetch 기반으로 리팩토링** — 기존 `window.SITE` 코드 경로는 일단 유지하되 fallback으로
3. **`daily.html.j2`에서 인라인 데이터 제거** — JS가 다 채우게
4. 기존 6개 페이지로 한 번 전체 빌드 → 브라우저에서 캘린더·prev/next·latest 링크 동작 확인
5. **`builder.build_one_date()` 추가**, `build_site()`는 그대로 유지
6. **`run.py`가 일상 실행에서 `build_one_date()`만 부르도록 변경**
7. 사이트 (`https://chodolmu.github.io/daily-ai-news`)에서 동작 검증

## 주의할 함정

- **GitHub Pages는 정적 파일 서빙**이라 `fetch('../data/site_meta.json')`은 같은 origin이면 OK. CORS 문제 없음.
- **첫 페이지 진입 시 캘린더가 비어 보일 수 있음** — fetch 동안 스켈레톤 노출. 인라인이었던 이유 중 하나.
  - 대안: 페이지 HTML에 `<noscript>` fallback 또는 SSR로 최소 정보(현재/이전/다음 날짜만)는 인라인 유지하고 캘린더 전체만 fetch.
- **`docs/index.html`(루트 redirect)** 은 그대로 두면 됨 — `latest`로 redirect하는 단순 메타. `build_one_date`가 latest 갱신 시 이것만 다시 쓰면 됨.
- **prev/next 계산을 클라이언트로 옮기면**, 사용자가 캘린더 fetch 전에 화살표 누를 수 있음 → fetch 완료 전엔 비활성화해야 함.

## 함께 정리하면 좋은 것 (선택)

- `docs/data/calendar.json`은 신설 `site_meta.json`과 통합하거나 deprecated로 두기 (둘 다 같은 정보라 헷갈림)
- `scripts/builder.py`의 `_write_redirect_index()`는 그대로 — `latest` 바뀔 때만 호출되면 됨

## 오늘 끝낸 것 (참고)

오늘 별도 사고 복구·구조 변경이 있었음. 새 자동화 정책:
- **매일 8시 실행 = 어제 하루치만 처리** (today 글 안 다룸)
- `data/seen_urls.json` 폐기 (`.deprecated`로 이름 변경)
- `data/last_run.json` 키: `last_processed_date` (이전 `last_run_date`에서 변경)
- 관련 커밋: `eb737ab simplify: process yesterday's news only, drop seen_urls and idempotency guards`

증분 빌드 작업은 위 변경과 **독립적**으로 진행 가능.

## 작업 시작 전 체크

```powershell
cd C:\AI\DailyAINews
git pull
git log --oneline -3   # eb737ab가 최신인지 확인
python scripts\run.py --build   # 전체 빌드가 현재처럼 동작하는지 한 번 확인
```

## 핵심 파일

- `scripts/builder.py:185-271` — `build_site()` 본체
- `scripts/builder.py:218-258` — 전체 페이지 루프 (잘라낼 부분)
- `templates/daily.html.j2:18, 28, 30-44, 113-115` — 인라인 동적 요소들
- `templates/assets/calendar.js` — 이미 있는 클라이언트 인프라
- `scripts/run.py:144-178` — `build_and_push()`, 호출 지점
