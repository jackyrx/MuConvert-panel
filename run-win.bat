@echo off
REM Windows launcher alias; delegates to run.bat.
call "%~dp0run.bat" %*
exit /b %errorlevel%
