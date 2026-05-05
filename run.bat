@echo off
REM Daily AI News - 작업 스케줄러가 호출하는 진입점
REM 콘솔 인코딩 UTF-8 (한글 깨짐 방지)
chcp 65001 > nul

cd /d "%~dp0"

REM Claude Code CLI는 Windows에서 git-bash 필요
if not defined CLAUDE_CODE_GIT_BASH_PATH (
    if exist "D:\Git\bin\bash.exe" (
        set "CLAUDE_CODE_GIT_BASH_PATH=D:\Git\bin\bash.exe"
    ) else if exist "C:\Program Files\Git\bin\bash.exe" (
        set "CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe"
    )
)

REM 가상환경 사용시 활성화 (없으면 시스템 python)
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM 로그 파일에 날짜별로 기록
if not exist logs mkdir logs
set "LOGFILE=logs\run-%date:~0,4%-%date:~5,2%-%date:~8,2%.log"

python scripts\run.py >> "%LOGFILE%" 2>&1
exit /b %errorlevel%
