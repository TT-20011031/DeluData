<#
.SYNOPSIS
    DeluData 本地到云端同步脚本
.DESCRIPTION
    用法:
      .\sync-to-cloud.ps1                 完整同步 + 询问构建
      .\sync-to-cloud.ps1 -SyncOnly       仅同步代码
      .\sync-to-cloud.ps1 -RebuildOnly    仅构建重启
      .\sync-to-cloud.ps1 -SetupKeys      设置SSH免密
#>

param(
    [switch]$SyncOnly,
    [switch]$RebuildOnly,
    [switch]$SetupKeys,
    [ValidateSet('pro', 'legacy')]
    [string]$Target = 'pro',
    [string]$LocalPath = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

# --- 配置 ---
$SERVER_IP   = '115.120.248.123'
$SERVER_USER = 'root'
$SERVER_PORT = '22'
$SSH_TARGET  = ($SERVER_USER + '@' + $SERVER_IP)

$TARGETS = @{
    pro = @{
        RemotePath = '/root/AiData/DeluData_pro'
        MainUrl    = 'http://agent.pro.deluagent.com'
        PlatformUrl = 'http://platform.agent.pro.deluagent.com'
    }
    legacy = @{
        RemotePath = '/root/AiData/DeluData'
        MainUrl    = 'http://agent.deluagent.com'
        PlatformUrl = 'http://platform.agent.deluagent.com'
    }
}

$REMOTE_PATH = $TARGETS[$Target].RemotePath
$LOCAL_PATH  = (Resolve-Path -LiteralPath $LocalPath).Path

# 排除目录
$EXCLUDE_DIRS = @(
    '.git', '.gitignore', 'node_modules', '__pycache__',
    '.pytest_cache', 'dist', 'venv', '.tmp', 'logs',
    'static', '.gemini', '.agent', '.agents'
)

# 排除文件名
$EXCLUDE_FILES = @(
    'README.md',
    '.env', '.env.development', '.env.local',
    '*.pyc', '*.pyo', '*.log', 'diff_head.txt',
    'CLAUDE.md', 'start_app.bat',
    'sync-to-cloud.sh', 'sync-to-cloud.ps1', 'sync.bat',
    'test_volcengine_bidirection_tts.py'
)

# 排除路径前缀
$EXCLUDE_PATHS = @(
    'backend\data',
    'docs',
    'backend\tests\output',
    'deploy\nginx'
)

# --- 工具函数 ---
function Write-Info  { param($m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "[!!] $m" -ForegroundColor Yellow }
function Write-Err   { param($m) Write-Host "[XX] $m" -ForegroundColor Red }
function Write-Step  { param($m) Write-Host "[->] $m" -ForegroundColor Cyan }

function Assert-DeployTarget {
    if (-not (Test-Path -LiteralPath (Join-Path $LOCAL_PATH 'docker-compose.yml'))) {
        throw "LocalPath does not look like a DeluData repo root: $LOCAL_PATH"
    }

    $localName = Split-Path -Leaf $LOCAL_PATH
    if ($Target -eq 'pro' -and $localName -notmatch 'DeluData_pro$') {
        throw "Refusing pro deploy from '$localName'. Pass -LocalPath explicitly only after confirming the repo root."
    }

    $remoteCheck = & ssh -p $SERVER_PORT -o 'ConnectTimeout=8' $SSH_TARGET "test -f '$REMOTE_PATH/docker-compose.yml' && basename '$REMOTE_PATH'" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Remote target is invalid or unreachable: ${SSH_TARGET}:${REMOTE_PATH}`n$remoteCheck"
    }
    if ($Target -eq 'pro' -and "$remoteCheck".Trim() -ne 'DeluData_pro') {
        throw "Refusing pro deploy to unexpected remote directory: $remoteCheck"
    }
}

function Invoke-RemoteSSH {
    param([string]$Cmd)
    $allArgs = @('-p', $SERVER_PORT, $SSH_TARGET, $Cmd)
    $proc = Start-Process -FilePath 'ssh' -ArgumentList $allArgs -NoNewWindow -Wait -PassThru
    return $proc.ExitCode
}

function Invoke-RemoteSSHOutput {
    param([string]$Cmd)
    $output = & ssh -p $SERVER_PORT $SSH_TARGET $Cmd
    return $output
}

# --- 设置 SSH 免密登录 ---
function Initialize-SSHKeys {
    Write-Step 'Setting up SSH key auth...'

    $sshDir  = Join-Path $env:USERPROFILE '.ssh'
    $keyFile = Join-Path $sshDir 'id_rsa'

    if (-not (Test-Path $sshDir)) {
        New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
    }

    if (-not (Test-Path $keyFile)) {
        Write-Step 'Generating SSH key...'
        $genArgs = @('-t', 'rsa', '-b', '4096', '-f', $keyFile, '-N', '""', '-q')
        Start-Process -FilePath 'ssh-keygen' -ArgumentList $genArgs -NoNewWindow -Wait
        Write-Info 'SSH key generated'
    }

    $pubKeyFile = $keyFile + '.pub'
    $pubKey = (Get-Content $pubKeyFile | Out-String).Trim()

    Write-Step 'Copying public key to server (password required)...'
    $setupCmd = 'mkdir -p ~/.ssh; chmod 700 ~/.ssh; cat >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys'
    $pubKey | & ssh -p $SERVER_PORT $SSH_TARGET $setupCmd

    Write-Info 'SSH key auth setup complete!'
}

# --- 测试连接 ---
function Test-SSHConn {
    Write-Step 'Testing SSH connection...'
    try {
        $r = & ssh -p $SERVER_PORT -o 'ConnectTimeout=5' -o 'BatchMode=yes' $SSH_TARGET 'echo ok' 2>&1
        if ("$r" -eq 'ok') {
            Write-Info 'SSH connection OK (key auth)'
        } else {
            Write-Warn 'Key auth not configured, will prompt for password'
        }
    } catch {
        Write-Warn 'Key auth not configured, will prompt for password'
    }
}

# --- 备份云端 .env ---
function Backup-Env {
    Write-Step 'Backing up remote .env...'
    $cmd = 'cp ' + $REMOTE_PATH + '/backend/.env /root/.env.production.backup 2>/dev/null; ' +
           'cp ' + $REMOTE_PATH + '/kiosk-frontend/.env.development /root/.env.kiosk.backup 2>/dev/null; ' +
           'echo done'
    Invoke-RemoteSSH $cmd | Out-Null
    Write-Info 'Remote .env backed up'
}

# --- 收集需要同步的文件 ---
function Get-FilesToSync {
    Write-Step 'Scanning local files...'

    $allFiles = Get-ChildItem -Path $LOCAL_PATH -Recurse -File -Force -ErrorAction SilentlyContinue
    $result = [System.Collections.ArrayList]::new()

    foreach ($f in $allFiles) {
        $rel = $f.FullName.Substring($LOCAL_PATH.Length + 1)
        $skip = $false

        # Check excluded dirs
        foreach ($d in $EXCLUDE_DIRS) {
            if ($rel -like ("*\" + $d + "\*") -or $rel -like ($d + "\*")) {
                $skip = $true; break
            }
        }

        # Check excluded path prefixes
        if (-not $skip) {
            foreach ($ep in $EXCLUDE_PATHS) {
                if ($rel.StartsWith($ep)) {
                    $skip = $true; break
                }
            }
        }

        # Check excluded file patterns
        if (-not $skip) {
            foreach ($pat in $EXCLUDE_FILES) {
                if ($f.Name -like $pat) {
                    $skip = $true; break
                }
            }
        }

        if (-not $skip) {
            $remotePath = $rel -replace '\\', '/'
            $null = $result.Add([PSCustomObject]@{
                Full   = $f.FullName
                Rel    = $rel
                Remote = $remotePath
            })
        }
    }

    return $result
}

# --- 同步代码 ---
function Invoke-Sync {
    Write-Step 'Starting code sync...'
    Write-Host "  Source: $LOCAL_PATH"
    Write-Host "  Target: ${SSH_TARGET}:${REMOTE_PATH}"
    Write-Host ''

    $files = Get-FilesToSync
    $total = $files.Count
    Write-Info "$total files to sync"

    # Create remote directory structure
    Write-Step 'Creating remote directories...'
    $dirs = $files | ForEach-Object {
        $d = Split-Path $_.Remote -Parent
        $d = $d -replace '\\', '/'
        if ($d) { $REMOTE_PATH + '/' + $d }
    } | Sort-Object -Unique

    $mkdirCmd = ($dirs | ForEach-Object { "mkdir -p '" + $_ + "'" }) -join '; '
    Invoke-RemoteSSH $mkdirCmd | Out-Null

    # Upload files by directory group
    $groups = $files | Group-Object { Split-Path $_.Rel -Parent }
    $done = 0

    foreach ($g in $groups) {
        if ($g.Name) {
            $targetDir = $REMOTE_PATH + '/' + ($g.Name -replace '\\', '/') + '/'
        } else {
            $targetDir = $REMOTE_PATH + '/'
        }

        $paths = @($g.Group | ForEach-Object { $_.Full })
        $dest  = $SSH_TARGET + ':' + $targetDir
        $scpArgs = @('-P', $SERVER_PORT) + $paths + @($dest)

        & scp @scpArgs 2>&1 | Out-Null

        $done += $g.Group.Count
        $pct = [math]::Round(($done / $total) * 100)
        Write-Host ("`r  Progress: $done / $total ($pct%)") -NoNewline
    }

    Write-Host ''
    Write-Info 'Code sync complete'
}

# --- 恢复 .env ---
function Restore-Env {
    Write-Step 'Restoring remote .env...'
    $cmd = 'cp /root/.env.production.backup ' + $REMOTE_PATH + '/backend/.env 2>/dev/null; ' +
           'cp /root/.env.kiosk.backup ' + $REMOTE_PATH + '/kiosk-frontend/.env.development 2>/dev/null; ' +
           'echo done'
    Invoke-RemoteSSH $cmd | Out-Null
    Write-Info 'Remote .env restored'
}

# --- 构建并重启 ---
function Invoke-Rebuild {
    Write-Step 'Building Docker images...'
    Invoke-RemoteSSH ('cd ' + $REMOTE_PATH + '; docker-compose build')

    Write-Step 'Restarting services...'
    Invoke-RemoteSSH ('cd ' + $REMOTE_PATH + '; docker-compose up -d')

    Write-Step 'Restarting gateway nginx to refresh upstream DNS...'
    Invoke-RemoteSSH ('cd ' + $REMOTE_PATH + '; docker-compose restart nginx')

    Write-Info 'Services restarted'
}

# --- 检查状态 ---
function Show-Status {
    Write-Step 'Checking service status...'
    Write-Host ''
    $output = Invoke-RemoteSSHOutput ('cd ' + $REMOTE_PATH + '; docker-compose ps')
    $output | ForEach-Object { Write-Host $_ }
    Write-Host ''
    Write-Info 'Deploy complete!'
    Write-Host ''
    Write-Host ('  Main:     ' + $TARGETS[$Target].MainUrl) -ForegroundColor Cyan
    Write-Host ('  Platform: ' + $TARGETS[$Target].PlatformUrl) -ForegroundColor Cyan
    Write-Host ''
}

# ============================================
# Main
# ============================================

Write-Host ''
Write-Host '=========================================' -ForegroundColor Cyan
Write-Host '  DeluData Code Sync Tool' -ForegroundColor Cyan
Write-Host ('  ' + $SSH_TARGET + ' -> ' + $REMOTE_PATH) -ForegroundColor Cyan
Write-Host '=========================================' -ForegroundColor Cyan
Write-Host ''

if ($SetupKeys) {
    Initialize-SSHKeys
    exit 0
}

Test-SSHConn
Assert-DeployTarget

if ($RebuildOnly) {
    Invoke-Rebuild
    Show-Status
    exit 0
}

Backup-Env
Invoke-Sync
Restore-Env

if ($SyncOnly) {
    Write-Info 'Sync only complete (no rebuild)'
    exit 0
}

Write-Host ''
$choice = Read-Host 'Rebuild Docker and restart? [Y/n]'
if (($choice -eq '') -or ($choice -match '^[Yy]')) {
    Invoke-Rebuild
    Show-Status
} else {
    Write-Info 'Skipped rebuild. Run later: .\sync-to-cloud.ps1 -RebuildOnly'
}
