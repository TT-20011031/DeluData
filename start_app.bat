@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

echo ===================================================
echo   DeluData Startup Script
echo ===================================================
echo.

cd /d %~dp0

set ADMIN_PORT=8000
set EXPERIENCE_PORT=8001
if not defined RAG_OCR_DEVICE set RAG_OCR_DEVICE=gpu
if not defined RAG_OCR_GPU_CONSERVATIVE_MODE set RAG_OCR_GPU_CONSERVATIVE_MODE=true
if not defined RAG_OCR_PARALLEL_PAGES (
    if /I "%RAG_OCR_DEVICE%"=="gpu" (
        set RAG_OCR_PARALLEL_PAGES=1
    ) else (
        set RAG_OCR_PARALLEL_PAGES=2
    )
)
if not defined RAG_OCR_WORKER_PROCESSES (
    if /I "%RAG_OCR_DEVICE%"=="gpu" (
        set RAG_OCR_WORKER_PROCESSES=1
    ) else (
        set RAG_OCR_WORKER_PROCESSES=2
    )
)
if not defined RAG_OCR_MAX_INFLIGHT_PAGES (
    if /I "%RAG_OCR_DEVICE%"=="gpu" (
        set RAG_OCR_MAX_INFLIGHT_PAGES=1
    ) else (
        set RAG_OCR_MAX_INFLIGHT_PAGES=4
    )
)
if not defined RAG_OCR_BATCH_SUBMIT_SIZE (
    if /I "%RAG_OCR_DEVICE%"=="gpu" (
        set RAG_OCR_BATCH_SUBMIT_SIZE=1
    ) else (
        set RAG_OCR_BATCH_SUBMIT_SIZE=4
    )
)
if not defined RAG_OCR_DPI set RAG_OCR_DPI=175
if not defined RAG_OCR_DET_DB_BOX_THRESH set RAG_OCR_DET_DB_BOX_THRESH=0.45
if not defined RAG_OCR_DET_DB_UNCLIP_RATIO set RAG_OCR_DET_DB_UNCLIP_RATIO=1.3
set RAG_OCR_WORKER_MAX_OCR_IMAGE_HEIGHT_PX=3800
set RAG_OCR_WORKER_MAX_RENDER_PIXELS=18000000

echo [INFO] Releasing ports 8000/8001/3000/5173/5174 if occupied...
for %%P in (8000 8001 3000 5173 5174) do (
    for /f "tokens=5" %%I in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
        if not "%%I"=="0" (
            echo [WARN] Port %%P occupied by PID %%I, stopping...
            taskkill /PID %%I /F >nul 2>&1
        )
    )
)

timeout /t 1 >nul

for /f %%A in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set ADMIN_PORT=18000
for /f %%A in ('netstat -ano ^| findstr /R /C:":8001 .*LISTENING"') do set EXPERIENCE_PORT=18001

if not "%ADMIN_PORT%"=="8000" (
    echo [WARN] Port 8000 is still occupied, fallback admin-backend to %ADMIN_PORT%.
)
if not "%EXPERIENCE_PORT%"=="8001" (
    echo [WARN] Port 8001 is still occupied, fallback experience-backend to %EXPERIENCE_PORT%.
)

set ADMIN_API_URL=http://localhost:%ADMIN_PORT%/api
set EXPERIENCE_API_URL=http://localhost:%EXPERIENCE_PORT%/api/experience
set ADMIN_PROXY_TARGET=http://localhost:%ADMIN_PORT%
set EXPERIENCE_PROXY_TARGET=http://localhost:%EXPERIENCE_PORT%

echo [1/7] Starting admin-backend on port %ADMIN_PORT%...
cd backend
set NEED_INSTALL=0
if not exist "venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set NEED_INSTALL=1
)
set PY_EXE=venv\Scripts\python.exe

if not exist "venv\.deps_ready" (
    set NEED_INSTALL=1
)

if "%NEED_INSTALL%"=="1" (
    echo [INFO] Installing backend dependencies - first run or dependency refresh...
    "%PY_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install backend dependencies.
        pause
        exit /b 1
    )

    if /I "%RAG_OCR_DEVICE%"=="gpu" (
        echo [INFO] Enforcing GPU OCR runtime - remove CPU onnxruntime and keep onnxruntime-gpu...
        "%PY_EXE%" -m pip uninstall -y onnxruntime >nul 2>&1
        "%PY_EXE%" -m pip install onnxruntime-gpu==1.23.2 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
        "%PY_EXE%" -m pip install nvidia-cuda-runtime-cu12 nvidia-cufft-cu12 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
    )

    echo ok>venv\.deps_ready
)

start "DeluData Admin-Backend (%ADMIN_PORT%)" cmd /k "set APP_ADMIN_PORT=%ADMIN_PORT%&& echo [INFO] Using venv python: %CD%\%PY_EXE% && %PY_EXE% -m app.entrypoints.admin_main"

echo [2/7] Starting experience-backend on port %EXPERIENCE_PORT%...
start "DeluData Experience-Backend (%EXPERIENCE_PORT%)" cmd /k "set APP_EXPERIENCE_PORT=%EXPERIENCE_PORT%&& echo [INFO] Using venv python: %CD%\%PY_EXE% && %PY_EXE% -m app.entrypoints.experience_main"

cd ..

if /I "%RAG_INGEST_USE_DB_QUEUE%"=="false" (
    echo [3/7] Skip ingestion-worker because RAG_INGEST_USE_DB_QUEUE=false
) else (
    echo [3/7] Starting ingestion-worker...
    start "DeluData Ingestion-Worker" cmd /k "cd backend && set RAG_INGEST_USE_DB_QUEUE=true&& echo [INFO] Using venv python: %CD%\venv\Scripts\python.exe && venv\Scripts\python.exe -m app.entrypoints.ingestion_worker"
)

echo [4/7] Starting semantic access bootstrap worker...
start "DeluData Semantic-Access-Bootstrap-Worker" cmd /k "cd backend && echo [INFO] Using venv python: %CD%\venv\Scripts\python.exe && venv\Scripts\python.exe -m app.entrypoints.semantic_access_bootstrap_worker"

echo [4/7] Starting permission evidence worker...
start "DeluData Permission-Evidence-Worker" cmd /k "cd backend && echo [INFO] Using venv python: %CD%\venv\Scripts\python.exe && venv\Scripts\python.exe -m app.entrypoints.permission_evidence_worker"

echo [5/7] Starting frontend (tenant/admin UI)...
start "DeluData Frontend" cmd /k "cd frontend && set VITE_API_BASE_URL=%ADMIN_API_URL%&& set VITE_DEV_ADMIN_PROXY_TARGET=%ADMIN_PROXY_TARGET%&& npm run dev"

echo [6/7] Starting frontend-platform...
start "DeluData Platform" cmd /k "cd frontend-platform && npm run dev"

echo [7/7] Starting kiosk-frontend (science experience)...
start "DeluData Kiosk Experience" cmd /k "cd kiosk-frontend && set VITE_API_BASE_URL=%EXPERIENCE_API_URL%&& set VITE_DEV_EXPERIENCE_PROXY_TARGET=%EXPERIENCE_PROXY_TARGET%&& set VITE_DEVICE_TOKEN=dev_device_token_001&& npm run dev"

echo.
echo ===================================================
echo Service URLs
echo ---------------------------------------------------
echo Admin backend docs:        http://localhost:%ADMIN_PORT%/docs
echo Experience backend docs:   http://localhost:%EXPERIENCE_PORT%/docs
echo Frontend (tenant/admin):   http://localhost:3000
echo Frontend platform:         http://localhost:5173/login
echo Kiosk frontend:            http://localhost:5174
echo.
echo Frontend API base:         %ADMIN_API_URL%
echo Kiosk API base:            %EXPERIENCE_API_URL%
echo Kiosk device token:        dev_device_token_001
echo ===================================================
echo.
echo Default admin account: admin / admin123
echo Keep the spawned terminal windows open.
echo ===================================================
