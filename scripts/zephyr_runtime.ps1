$ErrorActionPreference = 'Stop'

function Get-TcRuntimeRoot {
    $tcLocal = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $tcLocal) { throw '[CACHE_PATH] 로컬 앱 데이터 폴더를 찾지 못했습니다.' }
    return (Join-Path $tcLocal 'TCManager/zephyr-mcp/0.41.0')
}

function Get-TcRuntimeCache {
    $tcRoot = Get-TcRuntimeRoot
    $tcState = Join-Path $tcRoot 'cache.json'
    $tcGeneration = 'default'
    if (Test-Path -LiteralPath $tcState) {
        $tcGeneration = (Get-Content -LiteralPath $tcState -Raw -Encoding UTF8 | ConvertFrom-Json).generation
        if ($tcGeneration -notmatch '^(default|[a-f0-9]{32})$') { throw '[CACHE_STATE] 전용 캐시 설정이 잘못되었습니다. Connect-Zephyr.cmd -RepairCache로 복구하세요.' }
    }
    return (Join-Path $tcRoot ('cache-' + $tcGeneration))
}

function Get-TcRuntimeFailure([string]$Output, [bool]$TimedOut) {
    foreach ($tcCode in @('EINTEGRITY', 'ERR_MODULE_NOT_FOUND', 'MODULE_NOT_FOUND', 'ENOENT', 'EPERM', 'EACCES', 'EBADENGINE', 'ENOTFOUND', 'EAI_AGAIN', 'ECONNRESET', 'ETIMEDOUT', 'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'CERT_HAS_EXPIRED', 'E401', 'E403')) {
        if ($Output -match ('\b' + $tcCode + '\b')) { return $tcCode }
    }
    if ($TimedOut) { return 'START_TIMEOUT' }
    return 'MCP_START_FAILED'
}

function Test-TcRuntime([string]$Cache, [ValidateRange(1, 180)][int]$TimeoutSeconds = 90) {
    $tcNpx = (Get-Command npx.cmd -ErrorAction Stop).Source
    if ($tcNpx -match '["\r\n%]' -or $Cache -match '["\r\n%]') { throw '[CACHE_PATH] 지원하지 않는 실행 경로 문자입니다.' }
    $tcInfo = New-Object Diagnostics.ProcessStartInfo
    $tcInfo.FileName = $env:ComSpec
    $tcInfo.Arguments = '/d /s /c ""' + $tcNpx + '" --cache "' + $Cache + '" -y @smartbear/mcp@0.41.0"'
    $tcInfo.UseShellExecute = $false
    $tcInfo.CreateNoWindow = $true
    $tcInfo.RedirectStandardInput = $true
    $tcInfo.RedirectStandardOutput = $true
    $tcInfo.RedirectStandardError = $true
    # Startup diagnostics must never send an API request or expose a saved token.
    $tcInfo.EnvironmentVariables.Remove('ZEPHYR_API_TOKEN')
    $tcInfo.EnvironmentVariables.Remove('MCP_SERVER_BUGSNAG_API_KEY')
    $tcInfo.EnvironmentVariables['MCP_TRANSPORT'] = 'stdio'
    $tcProcess = New-Object Diagnostics.Process
    $tcProcess.StartInfo = $tcInfo
    $tcOutput = ''
    $tcOk = $false
    $tcTimedOut = $false
    $tcStarted = $false
    $tcErrors = $null
    try {
        $null = $tcProcess.Start()
        $tcStarted = $true
        $tcErrors = $tcProcess.StandardError.ReadToEndAsync()
        $tcProcess.StandardInput.WriteLine('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"tc-manager-diagnostics","version":"1"}}}')
        $tcProcess.StandardInput.Flush()
        $tcClock = [Diagnostics.Stopwatch]::StartNew()
        while ($tcClock.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            $tcLineTask = $tcProcess.StandardOutput.ReadLineAsync()
            $tcRemaining = [Math]::Max(1, [int](($TimeoutSeconds - $tcClock.Elapsed.TotalSeconds) * 1000))
            if (-not $tcLineTask.Wait($tcRemaining)) { $tcTimedOut = $true; break }
            $tcLine = $tcLineTask.Result
            if ($null -eq $tcLine) { break }
            $tcOutput += $tcLine + "`n"
            try { $tcReply = $tcLine | ConvertFrom-Json } catch { continue }
            if ($tcReply.id -eq 1) { $tcOk = ($null -ne $tcReply.result.serverInfo -and -not $tcReply.error); break }
        }
        if (-not $tcOk -and $tcClock.Elapsed.TotalSeconds -ge $TimeoutSeconds) { $tcTimedOut = $true }
    } catch {
        $tcOutput += $_.Exception.Message
    } finally {
        if ($tcStarted -and -not $tcProcess.HasExited) {
            $null = & taskkill.exe /PID $tcProcess.Id /T /F 2>$null
            $null = $tcProcess.WaitForExit(5000)
        }
        if ($tcErrors -and $tcErrors.Wait(5000)) { $tcOutput += $tcErrors.Result }
        $tcProcess.Dispose()
    }
    $tcCode = if ($tcOk) { 'MCP_INITIALIZED' } else { Get-TcRuntimeFailure $tcOutput $tcTimedOut }
    return [pscustomobject]@{ ok=$tcOk; code=$tcCode }
}

function Write-TcRuntimeDiagnostic($Result) {
    $tcRoot = Get-TcRuntimeRoot
    $null = New-Item -ItemType Directory -Force -Path $tcRoot
    $tcPath = Join-Path $tcRoot ('diagnostic-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
    $tcReport = [ordered]@{ time=(Get-Date).ToString('o'); package='@smartbear/mcp@0.41.0'; stage='MCP 시작 검사'; scope='isolated_startup_probe'; claude_connection_checked=$false; ok=$Result.ok; code=$Result.code; api_called=$false; raw_output_saved=$false }
    $tcReport.powershell_version = $PSVersionTable.PSVersion.ToString()
    $tcReport.node_version = 'unknown'
    try {
        $tcVersion = (& node.exe --version 2>$null | Out-String).Trim()
        if ($tcVersion -match '^v\d+\.\d+\.\d+$') { $tcReport.node_version = $tcVersion }
    } catch { }
    [IO.File]::WriteAllText($tcPath, ($tcReport | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Host ('진단 파일: ' + $tcPath)
    Write-Host '진단 파일과 화면의 오류 코드를 Claude에 전달할 수 있습니다. 토큰과 npm 원본 출력은 포함하지 않습니다.'
}

function Initialize-TcRuntime([switch]$RepairCache) {
    $tcGeneration = if ($RepairCache) { [guid]::NewGuid().ToString('N') } else { $null }
    $tcCache = if ($RepairCache) { Join-Path (Get-TcRuntimeRoot) ('cache-' + $tcGeneration) } else { Get-TcRuntimeCache }
    Write-Host '별도 MCP 프로세스의 시작을 검사합니다 (최대 90초). Claude 앱에서 사용 중인 연결을 검사하는 단계는 아닙니다.'
    $tcResult = Test-TcRuntime $tcCache
    Write-TcRuntimeDiagnostic $tcResult
    if (-not $tcResult.ok) {
        $tcAdvice = switch -Regex ($tcResult.code) {
            '^START_TIMEOUT$' { '별도 MCP 프로세스에서 90초 안에 초기화 응답을 확인하지 못했습니다. 캐시 손상으로 확정된 것은 아닙니다. 잠시 후 다시 검사하고, 반복되면 진단 파일로 원인을 확인하세요. 토큰 재발급은 필요하지 않습니다.'; break }
            'EINTEGRITY|MODULE_NOT_FOUND|ENOENT' { '패키지 설치/캐시 문제일 수 있습니다. Connect-Zephyr.cmd -RepairCache로 전용 캐시를 새로 준비하세요.'; break }
            'EPERM|EACCES' { '파일 접근이 차단됐습니다. 실행 중인 Claude 작업과 보안 프로그램의 차단 내역을 확인하세요.'; break }
            'EBADENGINE' { 'Node.js 버전을 확인하세요. 22 이상이 필요합니다.'; break }
            'ENOTFOUND|EAI_AGAIN|ECONNRESET|ETIMEDOUT|CERT|E401|E403' { 'npm 저장소의 네트워크·프록시·인증서·접근 권한을 확인하세요. Zephyr API 토큰 재발급으로 해결되지 않습니다.'; break }
            default { 'MCP 시작을 확인하지 못했습니다. 진단 코드로 원인을 확인하고 다시 실행하세요. 캐시 복구는 Connect-Zephyr.cmd -RepairCache로 진행합니다.' }
        }
        throw ('[' + $tcResult.code + '] ' + $tcAdvice)
    }
    if ($RepairCache) {
        $tcState = Join-Path (Get-TcRuntimeRoot) 'cache.json'
        $tcTemp = $tcState + '.' + $tcGeneration + '.tmp'
        [IO.File]::WriteAllText($tcTemp, (@{generation=$tcGeneration} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tcTemp -Destination $tcState -Force
        Write-Host '새 전용 캐시로 전환했습니다. 기존 캐시·사용자 npm 캐시·TC 데이터는 삭제하지 않았습니다.' -ForegroundColor Green
    }
    Write-Host 'MCP 시작 검사 통과. 다음으로 Claude의 승인·연결 상태를 확인합니다.' -ForegroundColor Green
}
