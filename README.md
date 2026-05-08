# Daily AI News

매일 AI 관련 뉴스를 자동 수집해서 Obsidian vault로 정리한다.

## 무엇을 하는가

### 일일 처리 (매일 8시)
1. **수집** — HackerNews / GeekNews / GitHub Trending / Anthropic 블로그에서 AI 관련 글
   - 트렌드 임계값 통과한 것만: HN ≥100점, GitHub ≥50 stars/today, GeekNews ≥5 추천, Anthropic 전부
2. **정제** — Claude Code CLI로 영문 번역 + 한국어 요약(3~4줄) + 활용방안(1~2줄)
3. **품질 필터** — LLM이 매긴 quality 4 이상만 유지 (광고/이미 알려진 내용 1차 차단)
4. **위키화** — 글에서 등장하는 개념·인물·조직·논문을 노드로 추출, `[[위키링크]]`로 연결
5. **저장** — Obsidian vault에 마크다운으로 기록 (그래프뷰로 망 형태 시각화)
6. **자동 변환** — vault 항목 중 행동 가능한 것만 LLM이 판정해서 자동으로 `~/.claude/skills/` 또는
   `~/.claude/projects/C--Users-chodo/memory/`에 적용. 사용자 컨텍스트(ZooMerge, Mystic Tarot, Claude Code,
   한국어)와 직접 관련 + 중복 없음 + 6개월 유효성 모두 통과한 항목만. 의심스러우면 자동 스킵.

### 주간 필터 (매주 월요일 자동)
지난 7일치 항목 LLM 재검토 → 광고/이미알려진 내용을 frontmatter `hidden_indices`에 표시.

### 월간 큐레이션 (매월 1일 자동)
지난 30일치 항목 LLM 검토 → 4가지 작업:
- **중복 묶기** — 같은 주제 여러 글 → 대표 1건만 유지, 나머지 hidden + duplicate_of
- **노후화 판정** (강도 중간) — deprecated/반증된 정보 + 신규 모델로 대체된 가이드 → `outdated_indices` 표시
- **노드 정리 제안** — 동일 개념 다른 표기 통합 후보 + 약한 노드 리스트 (자동 적용 X, 보고서로 출력)
- **충돌/반증 연결** — 같은 주제 반대 결론 항목들 쌍으로 묶어 표시

월간 보고서: `vault/curation_reports/{날짜}.md`

**원칙: 절대 항목/노드를 삭제하지 않는다.** frontmatter 표시만 하고, Obsidian에서 필터로 숨김.

## 폴더 구조

```
DailyAINews/
├── scripts/                # 파이썬 스크립트
│   ├── scraper.py
│   ├── processor.py
│   ├── vault_writer.py
│   ├── filter_weekly.py
│   └── run.py              # 진입점
├── vault/                  # Obsidian vault
│   ├── daily/              # YYYY-MM-DD.md
│   ├── concepts/           # 개념 노드 (RAG, Transformer 등)
│   ├── people/             # 인물 노드
│   ├── orgs/               # 조직 노드
│   └── papers/             # 논문 노드
├── data/                   # 상태 파일 (마지막 실행일, 이미 본 URL)
├── logs/                   # run.bat 실행 로그
├── requirements.txt
├── run.bat                 # 작업 스케줄러용 진입점
└── README.md
```

## 1회 셋업

```powershell
# 의존성 설치
python -m pip install -r requirements.txt

# Claude Code 로그인 확인 (이미 되어 있으면 스킵)
claude --version
```

## 수동 실행

```powershell
# 누락된 날짜 자동 처리 (기본)
python scripts\run.py

# 특정 날짜만
python scripts\run.py --date 2026-05-05

# 주간 필터만
python scripts\run.py --weekly

# 월간 큐레이션만
python scripts\run.py --monthly

# vault → ~/.claude/ 변환만 (최근 7일)
python scripts\run.py --extract
```

또는 `run.bat` 더블클릭.

## Windows 작업 스케줄러 등록 (매일 8시 자동 실행)

PowerShell을 **관리자 권한**으로 열고 아래 한 덩어리를 그대로 실행:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\AI\DailyAINews\run.bat" `
    -WorkingDirectory "C:\AI\DailyAINews"

$trigger = New-ScheduledTaskTrigger -Daily -At 8:00am

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "DailyAINews" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "매일 8시 AI 뉴스 자동 수집"
```

옵션 설명:
- `-StartWhenAvailable`: 8시에 PC가 꺼져 있으면 부팅 후 가장 가까운 시점에 실행
- 누락된 날짜는 `run.py`가 `data/last_run.json`을 보고 자동으로 따라잡음 (최대 14일)

### 등록 확인 / 즉시 실행 / 삭제

```powershell
# 등록된 작업 확인
Get-ScheduledTask -TaskName "DailyAINews"

# 즉시 한 번 실행 (테스트)
Start-ScheduledTask -TaskName "DailyAINews"

# 작업 삭제
Unregister-ScheduledTask -TaskName "DailyAINews" -Confirm:$false
```

## Obsidian vault로 열기

Obsidian 실행 → `Open folder as vault` → `C:\AI\DailyAINews\vault` 선택.

그래프 뷰(`Cmd/Ctrl + G` 또는 좌측 사이드바)에서 일일 노트와 개념·인물·조직·논문 노드들이
`[[링크]]`로 연결된 망 형태가 보인다. 같은 개념이 여러 날짜에 등장할수록 해당 노드가 허브화된다.

## 동작 원리 메모

- **중복 방지(URL 단위)**: `data/seen_urls.json`이 처리한 URL을 모두 기록. 같은 글이 여러 소스에 올라와도 한 번만.
- **트렌드 임계값**: 수집 단계에서 점수 미달은 버려서 LLM 호출 비용을 줄임.
- **품질 필터**: LLM이 매기는 `quality` 점수가 4 미만이면 일일 노트에서 제외 (개수 제한 없음).
- **누락 일자 처리**: `run.py`가 `last_run_date` 다음날부터 오늘까지 빠진 날짜를 순차 처리 (최대 14일).
- **3단계 큐레이션**: 일일(quality 필터) → 주간(광고/홍보 hide) → 월간(중복묶기/노후화/충돌).
- **frontmatter 상태**: `hidden_indices`(광고/중복) / `outdated_indices`(노후화). 원본은 유지, Obsidian에서 dataview로 필터링 가능.

## 사이트 (GitHub Pages)

빌드된 정적 사이트는 `docs/` 폴더에 생성되며, GitHub로 push되면 자동 배포된다. (GitHub Pages는 `/docs` 또는 root만 source로 지원)

### 1회 셋업 (PowerShell, 작업 디렉토리에서)

```powershell
# 1. git 저장소 초기화 + 첫 커밋
git init
git branch -M main
git add -A
git commit -m "initial commit"

# 2. GitHub 저장소 연결 (이미 chodolmu/daily-ai-news 만들어둔 상태)
git remote add origin https://github.com/chodolmu/daily-ai-news.git
git push -u origin main

# 3. GitHub Pages 활성화 (브라우저)
#    → 저장소 Settings → Pages
#    → Source: "Deploy from a branch"
#    → Branch: main / folder: /docs
#    → Save
```

이후 매일 `python scripts/run.py`가 끝나면 자동으로:
1. `python scripts/builder.py`가 호출되어 `docs/` 갱신
2. `git add -A && git commit -m "daily update YYYY-MM-DD" && git push` 자동 수행
3. GitHub Pages가 1~2분 뒤 새 내용으로 갱신

사이트 URL: **https://chodolmu.github.io/daily-ai-news**

### 빌드만 다시 하고 싶을 때

```powershell
python scripts\run.py --build
```

### 빌드 산출물 구조

```
docs/
├── index.html              # 최신 날짜로 redirect
├── assets/
│   ├── style.css
│   └── calendar.js
├── data/
│   └── calendar.json       # 어느 날짜에 데이터 있는지 (캘린더 점 표시용)
├── 2026-05-05/
│   └── index.html
└── 2026-05-06/
    └── index.html
```
