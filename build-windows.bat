@echo off
rem Build PuTTY Directory on Windows. Double-click it, or from cmd/PowerShell:
rem
rem   build-windows.bat            dist\PuttyDirectory.exe  (single file)
rem   build-windows.bat --onedir   dist\PuttyDirectory\     (folder, faster start)
rem   build-windows.bat --clean    discard cached analysis first
rem
rem Same job as build-windows.sh, for people without Git Bash. build.py runs the
rem binary's own --selftest afterwards and fails the build if the icon or tray
rem pipeline is broken, so a green run means a genuinely working executable.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VENV=%PUTTYDIR_VENV%"
if "%VENV%"=="" set "VENV=.venv"

rem --- python ---------------------------------------------------------------
rem Resolve a real python.exe rather than invoking whatever "py"/"python" is on
rem PATH. Those are often batch shims (pyenv-win uses .bat), and running a batch
rem file from a batch file without CALL transfers control and never returns -
rem the script would just stop dead here with no error. Asking for
rem sys.executable inside FOR /F is safe: that runs in a child cmd.

set "PYEXE="
for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
if not defined PYEXE for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
if not defined PYEXE goto :no_python
if not exist "%PYEXE%" goto :no_python

"%PYEXE%" -c "import tkinter" >nul 2>&1 || goto :no_tkinter
"%PYEXE%" -c "import ensurepip" >nul 2>&1 || goto :no_venv_module

rem "%PYEXE%" -V rather than -c "...": FOR /F cannot parse a command that
rem has both a quoted exe path and a quoted argument - it silently yields
rem nothing. "python -V" prints "Python 3.11.12", so take the second token.
for /f "tokens=2 delims= " %%v in ('"%PYEXE%" -V') do set "PYVER=%%v"
echo ==^> Python !PYVER!, tkinter ok
echo     !PYEXE!

rem --- dependencies ---------------------------------------------------------
rem This project is uv-managed. uv creates virtualenvs WITHOUT pip in them, so
rem "can I run pip in there?" is NOT a valid health check - it reports a
rem perfectly good uv venv as broken. Nothing here deletes an environment
rem automatically: a wrong guess costs real work, so we report and stop.

set "VPY=%VENV%\Scripts\python.exe"

set "UVEXE="
for /f "delims=" %%u in ('where uv 2^>nul') do if not defined UVEXE set "UVEXE=%%u"

if defined UVEXE if exist "uv.lock" goto :use_uv
goto :use_pip

:use_uv
echo ==^> installing dependencies with uv (uv.lock present)
set "UV_PROJECT_ENVIRONMENT=%VENV%"
"%UVEXE%" sync --dev
if errorlevel 1 goto :deps_failed
goto :have_env

:use_pip
if not exist "%VENV%\" (
    echo ==^> creating %VENV%
    "%PYEXE%" -m venv "%VENV%"
    if errorlevel 1 goto :venv_failed
)
if not exist "%VPY%" goto :venv_unusable
"%VPY%" -m pip --version >nul 2>&1
if errorlevel 1 goto :venv_no_pip

echo ==^> installing dependencies with pip
rem python-xlib is Linux-only; on Windows pystray uses its win32 backend.
"%VPY%" -m pip install --quiet --upgrade pip
"%VPY%" -m pip install --quiet pillow pystray pyinstaller
if errorlevel 1 goto :deps_failed

:have_env
if not exist "%VPY%" goto :venv_unusable
"%VPY%" -c "import PIL, pystray, PyInstaller" >nul 2>&1
if errorlevel 1 goto :deps_failed

echo ==^> building
"%VPY%" build.py %*
if errorlevel 1 goto :build_failed

echo.
echo Next steps
echo ----------
echo     dist\PuttyDirectory.exe --tray       start it in the notification area
echo     dist\PuttyDirectory.exe --selftest   re-check the build at any time
echo.
echo To start it at login, press Win+R, run  shell:startup , and put a shortcut
echo to dist\PuttyDirectory.exe with the --tray argument in the folder that opens.
echo.
echo A new tray icon usually lands in the hidden-icons overflow (the ^^ chevron);
echo drag it onto the taskbar to keep it visible.
goto :done

rem --- failures --------------------------------------------------------------

:no_python
echo.
echo error: no working Python found.
echo Install Python 3.11+ from python.org and tick "Add python.exe to PATH",
echo or install it from the Microsoft Store.
set "RC=1"
goto :done

:no_tkinter
echo.
echo error: tkinter is missing from this Python.
echo Re-run the python.org installer and enable "tcl/tk and IDLE".
set "RC=1"
goto :done

:no_venv_module
echo.
echo error: this Python cannot create virtual environments (no ensurepip).
set "RC=1"
goto :done

:venv_failed
echo.
echo error: could not create a venv in %VENV%.
set "RC=1"
goto :done

:venv_unusable
echo.
echo error: %VENV% has no Scripts\python.exe.
echo If it is a Linux venv copied across, or a failed creation, remove it:
echo     rmdir /s /q %VENV%
set "RC=1"
goto :done

:venv_no_pip
echo.
echo error: %VENV% has no pip, and uv is not available to manage it.
echo Install uv (https://docs.astral.sh/uv/), or recreate the venv:
echo     rmdir /s /q %VENV%
set "RC=1"
goto :done

:deps_failed
echo.
echo error: installing the build dependencies failed.
set "RC=1"
goto :done

:build_failed
echo.
echo error: the build failed. If the self-test reported failures, an exclude in
echo PuttyDirectory.spec is too aggressive - see README-BUILD.md.
set "RC=1"
goto :done

:done
if not defined RC set "RC=0"
rem Keep the window open when launched by double-click, but not when run from
rem an existing console. Done with string substitution rather than piping to
rem FIND: with Git on PATH, "find" is Git's Unix find, which rejects /i - the
rem window would then close instantly and hide whatever went wrong.
set "CMDLINE=!cmdcmdline!"
if defined PUTTYDIR_NOPAUSE goto :no_pause
if not "!CMDLINE!"=="!CMDLINE:%~nx0=!" pause
:no_pause
exit /b %RC%
