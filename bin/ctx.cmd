@echo off
REM Windows wrapper. Delegates to bin\ctx.py, the platform-neutral entry point.
setlocal
where python3 >nul 2>nul && (
  python3 "%~dp0ctx.py" %*
  exit /b %ERRORLEVEL%
)
python "%~dp0ctx.py" %*
exit /b %ERRORLEVEL%
