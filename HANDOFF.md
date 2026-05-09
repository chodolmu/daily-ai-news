# Handoff: 개인 AGI 풀-푸시 사이클 1.0

**Goal**: DailyAINews 외부 지식 + claude-mem 내부 지식이 다음 세션의 Claude에게 자동으로 흘러가는 닫힌 시스템 완성

**Done**: 5/8·5/9 발행 복구, stdout closed 버그 수정 (Python 3.14 wrapper 중첩), Haiku/Sonnet 모델 분리, 글로벌 CLAUDE.md에 외부 지식 @import + 푸시 규약 + 스케줄러 stale 알림 추가, claude-mem SessionStart 주입 끔 (푸시만 유지), memory_promotion_check.py 추가 (30일 주기), 이중 prefix 버그 수정 + 깨진 2개 파일 정리, _record_health 스케줄러 헬스체크, record_memory_access.py PreToolUse:Read hook (Windows atime 깨진 가지치기 대체), memory_search.py 시맨틱 검색 (88개 메모 대상, Haiku, 임베딩 없음), 글로벌 플러그인 4개 비활성화 + godot MCP 제거 + claude-mem narrative 끔 (세션 시작 토큰 ~70% 감축)

**Next**: 며칠~몇 주 그냥 굴려보고 실제 사용 데이터 쌓이면 그래프 노드 활성화(C안 — vault/concepts 본문 자동 채우기) 검토

**Watch out**: PowerShell 백그라운드에서 run.py 직접 돌리지 말 것 (subprocess→claude.exe stdin/stdout 파이프 hang). 디버그/테스트는 Task Scheduler 일회성 트리거로. 자세한 건 `~/.claude/projects/C--AI-DailyAINews/memory/debug_via_task_scheduler.md` 참조.
