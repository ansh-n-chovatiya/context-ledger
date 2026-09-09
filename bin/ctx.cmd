@echo off
REM Windows wrapper. Delegates to bin\ctx.py, the platform-neutral entry point.
REM
REM `exit /b %ERRORLEVEL%` must not sit inside a parenthesised block: cmd.exe
REM expands the whole block when it parses it, so the previous
REM `where python3 && ( ... exit /b %ERRORLEVEL% )` form substituted the
REM errorlevel of `where` — always 0, because `&&` only runs the block on
REM success. Every failing `ctx ci`, `ctx verify` and `ctx doctor` therefore
REM reported success on Windows while the POSIX `bin/ctx` propagated correctly.
REM Branching with goto keeps each `exit /b` at top level, where it expands when
REM it runs.
setlocal
where python3 >nul 2>nul
if errorlevel 1 goto :fallback
python3 "%~dp0ctx.py" %*
exit /b %ERRORLEVEL%

:fallback
python "%~dp0ctx.py" %*
exit /b %ERRORLEVEL%
