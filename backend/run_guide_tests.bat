@echo off
set "PYTHONPATH=%~dp0"
echo Running tests with PYTHONPATH set to %PYTHONPATH%
python "%~dp0tests\museum\test_guide_modular.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Tests failed with error code %ERRORLEVEL%
) else (
    echo.
    echo Tests passed successfully!
)
