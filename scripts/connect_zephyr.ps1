param(
    [switch]$CheckOnly, [switch]$ReplaceToken, [switch]$OpenInChrome, [switch]$RepairCache,
    [string]$ProjectPath, [string]$ClaudePath, [string]$ServerName = 'zephyr', [string]$TokenPageUrl
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'zephyr_runtime.ps1')

function Get-TcUserToken { [Environment]::GetEnvironmentVariable('ZEPHYR_API_TOKEN', 'User') }
function Save-TcUserToken([string]$Token) { [Environment]::SetEnvironmentVariable('ZEPHYR_API_TOKEN', $Token, 'User') }
function Test-TcInteractive { -not [Console]::IsInputRedirected }

function Read-TcToken {
    $tcSecret = Read-Host 'Zephyr API 토큰을 입력하세요 (입력 숨김, 빈 입력은 취소)' -AsSecureString
    $tcPointer = [IntPtr]::Zero
    try {
        $tcPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tcSecret)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tcPointer).Trim()
    } finally {
        if ($tcPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tcPointer) }
        $tcSecret.Dispose()
    }
}

function Test-TcToken([string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Token) -or $Token -match '\s' -or $Token.StartsWith('${')) {
        throw '[TOKEN_FORMAT] Bearer·따옴표·공백 없이 API 토큰만 입력하세요.'
    }
    try {
        $tcReply = Invoke-WebRequest -UseBasicParsing -Method Get -TimeoutSec 20 -MaximumRedirection 0 -Uri 'https://api.zephyrscale.smartbear.com/v2/projects?maxResults=1' -Headers @{ Authorization = "Bearer $Token"; Accept = 'application/json' }
        $tcBody = $tcReply.Content | ConvertFrom-Json
        if ($tcReply.StatusCode -ne 200 -or $null -eq $tcBody.values) { throw 'Unexpected API response' }
    } catch {
        $tcCode = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 'unavailable' }
        throw "[API_READ_FAILED] Zephyr API 조회 실패 (HTTP $tcCode). 기존 사용자 변수는 보존했습니다. 네트워크·권한을 확인하고, 토큰 교체가 필요하면 -ReplaceToken을 사용하세요."
    }
}

function Open-TcTokenPage([string]$Url, [bool]$Chrome) {
    if ($Chrome) {
        $tcChrome = Get-Process -Name chrome -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path
        if (-not $tcChrome) { throw 'Chrome을 먼저 열거나 -OpenInChrome 옵션을 빼고 실행하세요.' }
        Start-Process -FilePath $tcChrome -ArgumentList @($Url)
    } else { Start-Process -FilePath $Url }
}

function Write-TcJson([string]$Path, $Value) {
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path)
    $tcTemp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($tcTemp, ($Value | ConvertTo-Json -Depth 40), (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $tcTemp -Destination $Path -Force
    } finally { if (Test-Path -LiteralPath $tcTemp) { Remove-Item -LiteralPath $tcTemp } }
}

function Get-TcMcpDefinition([string]$Project = (Join-Path $PSScriptRoot '..')) {
    [pscustomobject]@{
        type = 'stdio'; command = 'powershell.exe'
        args = @('-NoProfile', '-NonInteractive', '-File', [IO.Path]::GetFullPath((Join-Path $Project 'scripts/start_zephyr_mcp.ps1')))
    }
}

function Test-TcMcpDefinition($Server, [string]$Project = (Join-Path $PSScriptRoot '..')) {
    if ($null -eq $Server) { return $false }
    $tcExpected = Get-TcMcpDefinition $Project
    return ($Server.type -in @($null, 'stdio') -and $Server.command -eq $tcExpected.command -and
        (@($Server.args) -join '|') -ceq ($tcExpected.args -join '|') -and -not $Server.env)
}

function Test-TcLegacyMcpDefinition($Server) {
    if ($null -eq $Server) { return $false }
    $tcArgs = @($Server.args) -join '|'
    return ($Server.type -in @($null, 'stdio') -and
        (($Server.command -in @('npx', 'npx.cmd') -and $tcArgs -eq '-y|@smartbear/mcp@0.41.0') -or
         ($Server.command -in @('cmd', 'cmd.exe') -and $tcArgs -eq '/d|/c|npx|-y|@smartbear/mcp@0.41.0')) -and
        $Server.env.ZEPHYR_API_TOKEN -ceq '${ZEPHYR_API_TOKEN}' -and
        (-not $Server.env.ZEPHYR_BASE_URL -or $Server.env.ZEPHYR_BASE_URL -eq 'https://api.zephyrscale.smartbear.com/v2'))
}

function Get-TcMcpStatus([string]$Claude, [string]$Name) {
    $tcPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $tcOutput = (& $Claude mcp get $Name 2>&1 | Out-String)
        $tcExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $tcPreference }
    # Do not print CLI output: it may contain expanded environment values.
    if ($tcExit -eq 0 -and $tcOutput -match '(?im)^\s*Status:\s*[^\w\r\n]*Connected\s*$') { return 'Connected' }
    if ($tcOutput -match '(?i)Pending approval') { return 'Pending approval' }
    return 'Not connected'
}

function Get-TcClaudeConfigPath {
    if ($env:CLAUDE_CONFIG_DIR) { throw '별도 CLAUDE_CONFIG_DIR가 설정돼 있습니다. 해당 프로필에서 폴더 신뢰를 확인하세요. 설정은 변경하지 않았습니다.' }
    return (Join-Path $env:USERPROFILE '.claude.json')
}

function Get-TcWorkspaceTrustKey([string]$Project) {
    $tcDirectory = (Resolve-Path -LiteralPath $Project).Path.TrimEnd('\', '/')
    $tcGit = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($tcGit) {
        $tcPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $tcGitRoot = & $tcGit.Source -C $tcDirectory rev-parse --show-toplevel 2>$null
            $tcGitExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $tcPreference }
        if ($tcGitExit -eq 0 -and $tcGitRoot) {
            $tcRepository = (Resolve-Path -LiteralPath ($tcGitRoot | Select-Object -First 1)).Path.TrimEnd('\', '/')
            if (-not $tcRepository.Equals($tcDirectory, [StringComparison]::OrdinalIgnoreCase)) {
                throw '더 큰 Git 저장소 안에 있습니다. 독립된 폴더에 설치하거나 Claude에서 저장소 신뢰 범위를 직접 확인하세요. 신뢰 설정은 변경하지 않았습니다.'
            }
        }
    }
    return $tcDirectory.Replace('\', '/')
}

function Confirm-TcWorkspaceTrust([string]$Project) {
    $tcKey = Get-TcWorkspaceTrustKey $Project
    $tcConfigPath = Get-TcClaudeConfigPath
    $tcConfig = if (Test-Path -LiteralPath $tcConfigPath) { Get-Content -LiteralPath $tcConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    if ($tcConfig.projects -and $tcConfig.projects.$tcKey.hasTrustDialogAccepted -eq $true) { return }
    if (-not (Test-TcInteractive)) { throw '폴더 신뢰 승인이 필요합니다. Connect-Zephyr.cmd를 더블클릭하세요. 신뢰 설정은 변경하지 않았습니다.' }
    Write-Host 'Claude에 이 작업 폴더의 신뢰 승인이 저장되지 않았습니다.' -ForegroundColor Yellow
    Write-Host ('작업 폴더: ' + $tcKey)
    Write-Host '승인하면 Claude가 이 폴더의 설정·플러그인·명령을 불러옵니다. 다른 폴더와 도구 권한은 변경하지 않습니다.'
    $tcAnswer = Read-Host '이 폴더를 신뢰하면 TRUST를 입력하세요. Enter만 누르면 취소합니다'
    if ($tcAnswer -cne 'TRUST') { throw '폴더 신뢰를 승인하지 않아 중단했습니다. API 토큰과 연결 설정은 보존했습니다.' }
    # Persist only the exact workspace after explicit human consent, as documented by Claude.
    $tcExists = Test-Path -LiteralPath $tcConfigPath
    $tcSnapshot = if ($tcExists) { [Convert]::ToBase64String([IO.File]::ReadAllBytes($tcConfigPath)) } else { $null }
    $tcConfig = if ($tcExists) { Get-Content -LiteralPath $tcConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    if (-not $tcConfig.projects) { $tcConfig | Add-Member -NotePropertyName projects -NotePropertyValue ([pscustomobject]@{}) -Force }
    if (-not $tcConfig.projects.$tcKey) { $tcConfig.projects | Add-Member -NotePropertyName $tcKey -NotePropertyValue ([pscustomobject]@{}) }
    $tcConfig.projects.$tcKey | Add-Member -NotePropertyName hasTrustDialogAccepted -NotePropertyValue $true -Force
    if ($tcExists) {
        $tcBackup = $tcConfigPath + '.before-tc-workspace-trust-' + [guid]::NewGuid().ToString('N') + '.bak'
        Copy-Item -LiteralPath $tcConfigPath -Destination $tcBackup -ErrorAction Stop
        if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($tcConfigPath)) -cne $tcSnapshot) { throw 'Claude 설정이 동시에 변경됐습니다. 다른 세션을 닫고 다시 실행하세요. 신뢰 설정은 저장하지 않았습니다.' }
    } elseif (Test-Path -LiteralPath $tcConfigPath) { throw 'Claude 설정이 동시에 생성됐습니다. 다시 실행하세요. 신뢰 설정은 저장하지 않았습니다.' }
    Write-TcJson $tcConfigPath $tcConfig
    Write-Host '폴더 신뢰 승인을 저장했습니다. 이어서 Zephyr MCP 승인을 확인합니다.' -ForegroundColor Green
}

function Invoke-TcMcpApproval([string]$Claude, [string]$Name) {
    Write-Host "이 폴더에서 Claude Code를 엽니다. 회사 계정 로그인·폴더 신뢰를 완료하고 /mcp에서 $Name 사용을 승인하세요." -ForegroundColor Yellow
    Write-Host '승인 후 /exit를 입력하면 이 창으로 돌아와 연결을 다시 확인합니다.'
    Write-Host 'TC 작업은 요청하지 않습니다. Zephyr 토큰을 Claude 채팅에 입력하지 마세요.'
    & $Claude
    if ($LASTEXITCODE -ne 0) {
        throw '[CLAUDE_CANCELLED] Claude Code가 취소되거나 시작하지 못했습니다. Connect-Zephyr.cmd를 다시 실행하세요. 설정은 보존했습니다.'
    }
}

function Resolve-TcClaude([string]$Requested) {
    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) { throw '지정한 Claude 실행 파일이 없습니다.' }
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    $tcStandalone = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path -LiteralPath $tcStandalone) { return $tcStandalone }
    $tcInstalled = Get-Command claude.exe -ErrorAction SilentlyContinue
    if ($tcInstalled) { return $tcInstalled.Source }
    throw '[CLAUDE_MISSING] Claude Code를 찾지 못했습니다. Install-TC-Manager.cmd로 설치하거나 -ClaudePath를 지정하세요.'
}

function Test-TcNode {
    $tcNode = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $tcNode -or -not (Get-Command npx.cmd -ErrorAction SilentlyContinue)) { throw '[NODE_MISSING] npm/npx를 포함한 Node.js 22 이상이 필요합니다. Install-TC-Manager.cmd를 실행하세요.' }
    $tcVersion = & $tcNode.Source --version
    if ($LASTEXITCODE -ne 0 -or $tcVersion -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 22) { throw '[NODE_VERSION] Node.js 22 이상이 필요합니다.' }
}

function Resolve-TcTokenPage([string]$Url, [string]$Project) {
    if (-not $Url) {
        $tcTargetPath = Join-Path $Project '.tc-manager/target.json'
        if (Test-Path -LiteralPath $tcTargetPath) {
            $tcTarget = Get-Content -LiteralPath $tcTargetPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $tcTokenProject = $tcTarget.token_project_key
            if (-not $tcTokenProject) { $tcTokenProject = $tcTarget.project_key }
            if ($tcTarget.site -and $tcTokenProject) {
                $Url = $tcTarget.site.TrimEnd('/') + '/plugins/servlet/ac/com.kanoah.test-manager/api-access-tokens?project.key=' + [uri]::EscapeDataString($tcTokenProject)
            }
        }
    }
    $tcUri = $null
    if (-not [uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$tcUri) -or $tcUri.Scheme -ne 'https' -or
        $tcUri.UserInfo -or -not $tcUri.IsDefaultPort -or $tcUri.Host -notmatch '^[a-z0-9-]+\.atlassian\.net$' -or
        $tcUri.AbsolutePath -ne '/plugins/servlet/ac/com.kanoah.test-manager/api-access-tokens' -or $Url -match '[\s"<>]') {
        throw '토큰 발급 주소를 -TokenPageUrl로 지정하거나 .tc-manager/target.json의 site/token_project_key를 확인하세요.'
    }
    return $tcUri.AbsoluteUri
}

function Invoke-TcConnect {
    param([switch]$CheckOnly, [switch]$ReplaceToken, [switch]$OpenInChrome, [switch]$RepairCache,
          [string]$ProjectPath, [string]$ClaudePath, [string]$ServerName = 'zephyr', [string]$TokenPageUrl)
    if ($CheckOnly -and $ReplaceToken) { throw '[OPTIONS] -CheckOnly와 -ReplaceToken은 함께 사용할 수 없습니다.' }
    if ($CheckOnly -and $RepairCache) { throw '[OPTIONS] -CheckOnly와 -RepairCache는 함께 사용할 수 없습니다.' }
    if (-not $ProjectPath) { $ProjectPath = Join-Path $PSScriptRoot '..' }
    $tcProject = (Resolve-Path -LiteralPath $ProjectPath).Path
    $tcSettingsPath = Join-Path $tcProject '.tc-manager/zephyr-connection.json'
    $tcSettings = if (Test-Path -LiteralPath $tcSettingsPath) { Get-Content -LiteralPath $tcSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    $tcSettingsBefore = $tcSettings | ConvertTo-Json -Depth 40
    if (-not $PSBoundParameters.ContainsKey('ServerName') -and $tcSettings.server_name) { $ServerName = $tcSettings.server_name }
    if ($ServerName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw 'MCP 서버 이름 형식이 잘못되었습니다.' }
    if (-not $TokenPageUrl) { $TokenPageUrl = $tcSettings.token_page_url }
    if (-not $ClaudePath) { $ClaudePath = $tcSettings.claude_path }
    if (-not $PSBoundParameters.ContainsKey('OpenInChrome')) { $OpenInChrome = $tcSettings.open_in_chrome -eq $true }
    $tcClaude = Resolve-TcClaude $ClaudePath
    Test-TcNode
    $tcConfigPath = Join-Path $tcProject '.mcp.json'
    $tcConfig = if (Test-Path -LiteralPath $tcConfigPath) { Get-Content -LiteralPath $tcConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{ mcpServers = [pscustomobject]@{} } }
    if ($null -eq $tcConfig.mcpServers -or $tcConfig.mcpServers -isnot [pscustomobject]) { throw 'mcpServers 설정 형식이 잘못되었습니다.' }
    $tcServer = $tcConfig.mcpServers.$ServerName
    $tcCurrent = Test-TcMcpDefinition $tcServer $tcProject
    $tcLocal = $tcCurrent -or (Test-TcLegacyMcpDefinition $tcServer)
    if ($CheckOnly -and -not $tcLocal) { throw 'API 토큰 MCP 설정이 없습니다. 먼저 -CheckOnly 없이 Connect-Zephyr.cmd를 실행하세요.' }
    if ($tcServer -and -not $tcLocal -and -not ($tcServer.type -eq 'http' -and $tcServer.url -eq 'https://zephyr.mcp.smartbear.com/mcp')) {
        throw '사용자 지정 MCP 설정은 덮어쓰지 않았습니다. 별도 -ServerName을 지정하세요.'
    }
    $tcToken = Get-TcUserToken
    $tcNew = $ReplaceToken -or [string]::IsNullOrWhiteSpace($tcToken)
    if ($tcNew) {
        if ($CheckOnly) { throw 'Windows 사용자 변수 ZEPHYR_API_TOKEN이 없습니다. 먼저 Connect-Zephyr.cmd를 실행하세요.' }
        if (-not (Test-TcInteractive)) { throw 'Connect-Zephyr.cmd를 더블클릭하여 토큰을 입력하세요. 설정은 변경하지 않았습니다.' }
        $TokenPageUrl = Resolve-TcTokenPage $TokenPageUrl $tcProject
        Write-Host '열리는 브라우저에서 Zephyr API 토큰을 발급받아 이 창에만 입력하세요. 채팅에 붙여 넣지 마세요.'
        Open-TcTokenPage $TokenPageUrl ([bool]$OpenInChrome)
        $tcToken = Read-TcToken
        if ([string]::IsNullOrWhiteSpace($tcToken)) { throw '취소했습니다. 토큰과 설정은 저장하지 않았습니다.' }
    }
    Test-TcToken $tcToken
    Write-Host 'Zephyr API 조회 검증 통과. TC는 변경하지 않았습니다.' -ForegroundColor Green
    if (-not $CheckOnly) {
        if ($tcNew) {
            Save-TcUserToken $tcToken
            Write-Host 'Windows 사용자 환경 변수에 ZEPHYR_API_TOKEN을 저장했습니다. 시스템 변수는 변경하지 않았습니다.'
        } else { Write-Host '기존 Windows 사용자 변수 ZEPHYR_API_TOKEN을 재사용합니다.' }
        if (-not $tcCurrent) {
            $tcRuntime = Join-Path $tcProject 'scripts/start_zephyr_mcp.ps1'
            if (-not (Test-Path -LiteralPath $tcRuntime)) { throw 'MCP 실행 스크립트가 없습니다. 최신 ZIP 전체를 다시 압축 해제하세요.' }
            if (Test-Path -LiteralPath $tcConfigPath) { Copy-Item -LiteralPath $tcConfigPath -Destination ($tcConfigPath + '.before-token-launcher-' + [guid]::NewGuid().ToString('N') + '.bak') }
            $tcConfig.mcpServers | Add-Member -NotePropertyName $ServerName -NotePropertyValue (Get-TcMcpDefinition $tcProject) -Force
            Write-TcJson $tcConfigPath $tcConfig
        }
        foreach ($tcPair in @{ server_name=$ServerName; auth_mode='api_token'; open_in_chrome=[bool]$OpenInChrome }.GetEnumerator()) {
            $tcSettings | Add-Member -NotePropertyName $tcPair.Key -NotePropertyValue $tcPair.Value -Force
        }
        if ($ClaudePath) { $tcSettings | Add-Member -NotePropertyName claude_path -NotePropertyValue $tcClaude -Force }
        if ($TokenPageUrl) { $tcSettings | Add-Member -NotePropertyName token_page_url -NotePropertyValue $TokenPageUrl -Force }
        if (-not (Test-Path -LiteralPath $tcSettingsPath) -or ($tcSettings | ConvertTo-Json -Depth 40) -cne $tcSettingsBefore) {
            Write-TcJson $tcSettingsPath $tcSettings
        }
        Write-Host 'MCP 승인과 연결을 확인합니다. 승인이 필요하면 이 창에서 Claude Code를 엽니다.'
    }
    $tcPrevious = $env:ZEPHYR_API_TOKEN
    Push-Location -LiteralPath $tcProject
    try {
        $env:ZEPHYR_API_TOKEN = $tcToken
        if (-not $CheckOnly) {
            try { Initialize-TcRuntime -RepairCache:$RepairCache }
            catch {
                $tcRuntimeError = $_
                if ($tcCurrent -and -not $tcNew) {
                    Write-Host '기존 토큰과 MCP 서버 설정은 유지했습니다. 별도로 실행한 MCP 시작 검사에서 실패했습니다.' -ForegroundColor Yellow
                } else {
                    Write-Host '토큰과 MCP 서버 설정은 저장된 상태입니다. MCP 시작 검사가 실패해 연결 확인은 미완료입니다.' -ForegroundColor Yellow
                }
                Write-Host 'Claude 앱의 현재 연결 상태는 확인하지 못했습니다. 이 결과만으로 기존 연결이 끊겼다고 판단하지 않습니다.'
                Write-Host '기존 연결 확인은 Claude에서 Zephyr 조회로 진행하세요. 스킬 자동 업데이트 시험만 하는 경우 Connect-Zephyr.cmd 재실행은 필요하지 않습니다.'
                if (-not $RepairCache -and $_.Exception.Message -match '^\[(EINTEGRITY|ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|ENOENT)\]' -and (Test-TcInteractive)) {
                    Write-Host $_.Exception.Message -ForegroundColor Yellow
                    $tcRepair = Read-Host '전용 캐시를 새로 준비하고 한 번 재검사할까요? 기존 캐시는 삭제하지 않습니다. [y/N]'
                    if ($tcRepair -match '^(?i:y|yes)$') { Initialize-TcRuntime -RepairCache }
                    else { throw $tcRuntimeError }
                } else { throw }
            }
        }
        if (-not $CheckOnly) { Confirm-TcWorkspaceTrust $tcProject }
        $tcStatus = Get-TcMcpStatus $tcClaude $ServerName
        Write-Host "MCP 서버: $ServerName / 상태: $tcStatus"
        if ($tcStatus -eq 'Pending approval') {
            Write-Host 'API 토큰은 유효합니다. 이 작업 폴더에서 MCP 사용 승인이 필요합니다.' -ForegroundColor Yellow
            Write-Host ("작업 폴더: {0}" -f $tcProject)
            if ($CheckOnly) { throw '[MCP_APPROVAL_PENDING] API 토큰 검증 통과. 이 폴더의 MCP 사용 승인 대기 중이며 연결 검증은 미완료입니다.' }
            if (-not (Test-TcInteractive)) {
                throw '[INTERACTIVE_REQUIRED] 대화형 창에서 MCP 승인이 필요합니다. Connect-Zephyr.cmd를 더블클릭하세요. 토큰과 설정은 저장했으며 토큰 재발급은 필요하지 않습니다.'
            }
            Invoke-TcMcpApproval $tcClaude $ServerName
            Write-Host 'Claude Code에서 돌아왔습니다. MCP 연결을 다시 확인합니다...'
            $tcStatus = Get-TcMcpStatus $tcClaude $ServerName
            Write-Host "MCP 서버: $ServerName / 상태: $tcStatus"
            if ($tcStatus -eq 'Pending approval') {
                throw '[MCP_APPROVAL_PENDING] 승인이 아직 대기 중입니다. Connect-Zephyr.cmd를 다시 실행하고 열린 Claude Code에서 서버를 승인하세요. 토큰 재발급은 필요하지 않습니다.'
            }
        }
        if ($tcStatus -ne 'Connected') { throw '[MCP_NOT_CONNECTED] API 토큰 검증은 통과했지만 Claude의 MCP 연결은 실패했습니다. /mcp에서 실행 오류·프로젝트 승인과 이전 MCP 프로세스를 확인하세요. 캐시 복구가 필요하면 Connect-Zephyr.cmd -RepairCache를 실행하세요. 토큰 재발급은 필요하지 않습니다.' }
        Write-Host 'CLI의 MCP 연결을 확인했습니다. Claude 앱에서 프로젝트/TC 조회를 요청해 실제 API 도구 호출까지 확인하세요.' -ForegroundColor Green
        if (-not $CheckOnly) {
            Write-Host '설정 완료. Claude 앱을 완전히 종료 후 다시 열고 Code에서 아래 폴더로 새 세션을 시작하세요:'
            Write-Host $tcProject
        }
    } finally { $env:ZEPHYR_API_TOKEN = $tcPrevious; $tcToken = $null; Pop-Location }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-TcConnect @PSBoundParameters }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
