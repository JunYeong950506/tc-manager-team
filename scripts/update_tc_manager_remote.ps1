function Get-TcRemoteSource([string]$Root) {
    $tcConfig = Get-Content -LiteralPath (Join-Path $Root 'update-source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($tcConfig.marketplace -ne 'tc-manager-team') { throw '[UPDATE_SOURCE] 마켓플레이스 이름이 다릅니다.' }
    return Assert-TcRemoteUrl $tcConfig.repository_url
}

function Assert-TcRemoteUrl([string]$Url) {
    $tcUri = $null
    if (-not [uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$tcUri) -or $tcUri.Scheme -ne 'https' -or
        -not $tcUri.Host -or $tcUri.UserInfo -or $tcUri.Query -or $tcUri.Fragment -or
        $Url -match '[\s"<>`]' -or -not $tcUri.AbsolutePath.EndsWith('.git')) {
        throw '[UPDATE_SOURCE] 인증정보 없는 HTTPS Git 저장소 주소(.git)가 필요합니다.'
    }
    return $Url
}

function Get-TcRemoteSources([string]$Root) {
    Get-TcRemoteSource $Root
    $tcConfig = Get-Content -LiteralPath (Join-Path $Root 'update-source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($tcUrl in @($tcConfig.fallback_repository_urls)) {
        if ($tcUrl) { Assert-TcRemoteUrl $tcUrl }
    }
    $tcChoice = Join-Path $Root '.tc-manager/update-source-choice.json'
    if (Test-Path -LiteralPath $tcChoice) {
        Assert-TcRemoteUrl ((Get-Content -LiteralPath $tcChoice -Raw -Encoding UTF8 | ConvertFrom-Json).repository_url)
    }
}

function Test-TcConfiguredRemoteMarket($Market, [string]$Root) {
    foreach ($tcUrl in @(Get-TcRemoteSources $Root)) {
        if (Test-TcRemoteMarket $Market $tcUrl) { return $true }
    }
    return $false
}

function Test-TcRemoteReadable([string]$Url) {
    $null = Assert-TcRemoteUrl $Url
    $tcInfo = New-Object Diagnostics.ProcessStartInfo
    $tcInfo.FileName = (Get-Command git.exe -ErrorAction Stop).Source
    $tcInfo.Arguments = '-c credential.interactive=false ls-remote --exit-code "' + $Url + '" HEAD'
    $tcInfo.UseShellExecute = $false
    $tcInfo.CreateNoWindow = $true
    $tcInfo.RedirectStandardOutput = $true
    $tcInfo.RedirectStandardError = $true
    $tcInfo.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $tcInfo.EnvironmentVariables['GCM_INTERACTIVE'] = 'Never'
    $tcProcess = New-Object Diagnostics.Process
    $tcProcess.StartInfo = $tcInfo
    try {
        $null = $tcProcess.Start()
        $tcOutput = $tcProcess.StandardOutput.ReadToEndAsync()
        $tcError = $tcProcess.StandardError.ReadToEndAsync()
        if (-not $tcProcess.WaitForExit(30000)) {
            $null = & taskkill.exe /PID $tcProcess.Id /T /F 2>$null
            $null = $tcProcess.WaitForExit(5000)
            return $false
        }
        return $tcProcess.ExitCode -eq 0
    } finally { $tcProcess.Dispose() }
}

function Select-TcRemoteSource([string]$Root, [switch]$Interactive) {
    $tcDefault = Get-TcRemoteSource $Root
    $tcConfig = Get-Content -LiteralPath (Join-Path $Root 'update-source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $tcFallbacks = @($tcConfig.fallback_repository_urls | Where-Object { $_ })
    $tcCandidates = @($tcDefault) + $tcFallbacks
    if ($Interactive) {
        Write-Host ('1. 회사 Git (기본): ' + $tcDefault)
        Write-Host ('2. 개인 Git: ' + ($tcFallbacks -join ', '))
        Write-Host '3. Git 주소 직접 입력'
        $tcChoice = Read-Host '저장소 선택 [1/2/3, Enter=1]'
        if ($tcChoice -eq '2') { $tcCandidates = $tcFallbacks }
        elseif ($tcChoice -eq '3') { $tcCandidates = @(Assert-TcRemoteUrl (Read-Host 'HTTPS Git 주소 (.git)')) }
        elseif ($tcChoice -ne '' -and $tcChoice -ne '1') { throw '[UPDATE_SOURCE] 1, 2, 3 중 선택하세요.' }
    }
    foreach ($tcUrl in $tcCandidates) {
        $null = Assert-TcRemoteUrl $tcUrl
        Write-Host ('저장소 연결 확인: ' + $tcUrl)
        if (Test-TcRemoteReadable $tcUrl) { return $tcUrl }
        Write-Host '연결을 확인하지 못했습니다. 인증·접근 권한·네트워크를 확인하세요. 대체 주소가 있으면 계속 확인합니다.' -ForegroundColor Yellow
    }
    throw '[REMOTE_UNAVAILABLE] 접속 가능한 저장소가 없습니다. 기존 설치는 유지했습니다. 2번 실행 후 주소 직접 입력을 선택할 수 있습니다.'
}

function Get-TcRemoteInstall([string]$Claude, [string]$Root) {
    $tcRows = Invoke-TcUpdateCli $Claude @('plugin', 'list', '--json') | ConvertFrom-Json
    $tcMatches = @($tcRows | Where-Object { $_.id -eq 'tc-manager@tc-manager-team' -and $_.scope -eq 'local' -and (Test-TcSamePath $_.projectPath $Root) })
    if ($tcMatches.Count -gt 1) { throw '[INSTALL_CONFLICT] 같은 폴더에 설치 기록이 중복됩니다. Claude 플러그인 상태를 확인하세요.' }
    if ($tcMatches.Count) { return $tcMatches[0] }
    return $null
}

function Test-TcRemoteMarket($Market, [string]$Url) {
    if ($Market.source -eq 'git') { return $Market.url -ceq $Url }
    if ($Market.source -eq 'github') { return ('https://github.com/' + $Market.repo + '.git') -ceq $Url }
    return $false
}

function Set-TcMarketplaceAutoUpdate([string]$Path, [string]$Source, [bool]$Enabled, [switch]$KnownMarketplaces, [switch]$LocalDirectory) {
    $tcOriginal = [IO.File]::ReadAllBytes($Path)
    $tcData = [Text.Encoding]::UTF8.GetString($tcOriginal).TrimStart([char]0xfeff) | ConvertFrom-Json
    $tcEntry = if ($KnownMarketplaces) { $tcData.'tc-manager-team' } else { $tcData.extraKnownMarketplaces.'tc-manager-team' }
    $tcSource = $tcEntry.source
    $tcExpected = if ($LocalDirectory) { $tcSource.source -eq 'directory' -and (Test-TcSamePath $tcSource.path $Source) } else {
        ($tcSource.source -eq 'git' -and $tcSource.url -ceq $Source) -or
        ($tcSource.source -eq 'github' -and ('https://github.com/' + $tcSource.repo + '.git') -ceq $Source)
    }
    if (-not $tcEntry -or -not $tcExpected) { throw '[SOURCE_CHANGED] 등록된 업데이트 위치가 다릅니다. 자동 업데이트 설정을 변경하지 않았습니다.' }
    $tcEntry | Add-Member -NotePropertyName autoUpdate -NotePropertyValue $Enabled -Force
    $tcTemp = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($tcTemp, ($tcData | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($Path)) -cne [Convert]::ToBase64String($tcOriginal)) {
        Remove-Item -LiteralPath $tcTemp
        throw '[SETTINGS_CHANGED] 다른 세션이 설정을 변경했습니다. 다시 실행하세요.'
    }
    Move-Item -LiteralPath $tcTemp -Destination $Path -Force
    $tcSaved = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $tcEnabled = if ($KnownMarketplaces) { $tcSaved.'tc-manager-team'.autoUpdate } else { $tcSaved.extraKnownMarketplaces.'tc-manager-team'.autoUpdate }
    if ($tcEnabled -ne $Enabled) { throw '[AUTO_UPDATE_FAILED] 자동 업데이트 설정 저장을 확인하지 못했습니다.' }
}

function Invoke-TcRemoteUpdate {
    param([switch]$CheckOnly, [switch]$SelectSource)
    $tcRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $tcUrl = Get-TcRemoteSource $tcRoot
    Write-Host ('업데이트 저장소: ' + $tcUrl)
    Write-Host ('작업 폴더: ' + $tcRoot)
    Initialize-TcPrerequisites -CheckOnly
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw '[GIT_MISSING] Git이 필요합니다. Git for Windows 설치 후 이 CMD를 다시 실행하세요.' }
    if (-not $CheckOnly) { $tcUrl = Select-TcRemoteSource $tcRoot -Interactive:$SelectSource }
    Write-Host ('선택한 업데이트 저장소: ' + $tcUrl)
    $tcClaude = Resolve-TcClaude ''
    $tcProfile = if ($env:CLAUDE_CONFIG_DIR) { [IO.Path]::GetFullPath($env:CLAUDE_CONFIG_DIR) } else { Join-Path $env:USERPROFILE '.claude' }
    $tcSettings = Join-Path $tcRoot '.claude/settings.local.json'
    $tcKnown = Join-Path $tcProfile 'plugins/known_marketplaces.json'
    $tcBundled = Join-Path $tcRoot 'plugins/tc-manager'
    $tcBundledVersion = (Get-Content -LiteralPath (Join-Path $tcBundled '.claude-plugin/plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    $tcProtected = @{}
    foreach ($tcName in @('.tc-manager/target.json', '.tc-manager/library.json', '.tc-manager/zephyr-connection.json', '.mcp.json')) {
        $tcPath = Join-Path $tcRoot $tcName
        if (Test-Path -LiteralPath $tcPath) { $tcProtected[$tcName] = Get-TcFileDigest $tcPath }
    }
    if (-not $tcProtected.ContainsKey('.tc-manager/target.json') -or -not $tcProtected.ContainsKey('.tc-manager/library.json')) {
        throw '[SETUP_REQUIRED] 최초에는 1-Install-TC-Manager.cmd를 먼저 실행하세요. Zephyr 연결은 2-Connect-Zephyr.cmd에서 설정합니다.'
    }
    Push-Location -LiteralPath $tcRoot
    try {
        $tcBefore = Get-TcRemoteInstall $tcClaude $tcRoot
        $tcMarkets = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'list', '--json') | ConvertFrom-Json
        $tcMarket = @($tcMarkets | Where-Object { $_.name -eq 'tc-manager-team' })
        if ($tcMarket.Count -gt 1 -or ($tcMarket.Count -eq 1 -and
            -not (Test-TcRemoteMarket $tcMarket[0] $tcUrl) -and
            -not (Test-TcConfiguredRemoteMarket $tcMarket[0] $tcRoot) -and
            -not ($tcMarket[0].source -eq 'directory' -and (Test-TcSamePath $tcMarket[0].path $tcRoot)))) {
            throw '[MARKETPLACE_CONFLICT] 같은 이름의 마켓플레이스가 다른 위치를 사용합니다. 다른 작업 폴더의 연결을 자동 변경하지 않았습니다.'
        }
        $tcAlias = Join-Path $tcRoot '.claude/skills/tc-manager/SKILL.md'
        if (Test-Path -LiteralPath $tcAlias) {
            $tcAllowed = @((Join-Path $tcBundled 'references/short-command/SKILL.md'))
            if ($tcBefore) { $tcAllowed += Join-Path $tcBefore.installPath 'references/short-command/SKILL.md' }
            $tcAliasHash = Get-TcFileDigest $tcAlias
            if (-not @($tcAllowed | Where-Object { (Test-Path -LiteralPath $_) -and (Get-TcFileDigest $_) -eq $tcAliasHash }).Count) {
                throw '[CUSTOM_SHORTCUT] 사용자가 수정한 /tc-manager 단축어가 있습니다. 변경 내용을 먼저 확인하세요. 덮어쓰지 않았습니다.'
            }
        }
        $tcWorkspace = Get-TcDefaultWorkspaceConfiguration $tcRoot
        if ($CheckOnly) { Write-Host '사전 점검 완료. 원격 조회·설치·설정 변경은 하지 않았습니다.'; return }
        $tcBackup = Join-Path $tcRoot ('.tc-manager/backups/remote-update-' + [guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $tcBackup -Force
        foreach ($tcName in @($tcProtected.Keys) + @('.claude/settings.local.json', '.claude/skills/tc-manager/SKILL.md')) {
            $tcPath = Join-Path $tcRoot $tcName
            if (Test-Path -LiteralPath $tcPath) {
                $tcDest = Join-Path $tcBackup $tcName
                $null = New-Item -ItemType Directory -Path (Split-Path -Parent $tcDest) -Force
                Copy-Item -LiteralPath $tcPath -Destination $tcDest
            }
        }
        if (Test-Path -LiteralPath $tcKnown) { Copy-Item -LiteralPath $tcKnown -Destination (Join-Path $tcBackup 'known-marketplaces.json') }
        $tcInstalledFile = Join-Path $tcProfile 'plugins/installed_plugins.json'
        if (Test-Path -LiteralPath $tcInstalledFile) { Copy-Item -LiteralPath $tcInstalledFile -Destination (Join-Path $tcBackup 'installed-plugins.json') }
        Write-Host ('설정 백업: ' + $tcBackup)
        Write-Host 'Git 원격을 등록하고 배포 버전을 확인합니다...'
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'add', $tcUrl, '--scope', 'local')
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'update', 'tc-manager-team')
        $tcMarkets = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'list', '--json') | ConvertFrom-Json
        $tcRemote = @($tcMarkets | Where-Object { $_.name -eq 'tc-manager-team' })
        if ($tcRemote.Count -ne 1 -or -not (Test-TcRemoteMarket $tcRemote[0] $tcUrl)) { throw '[REMOTE_NOT_REGISTERED] 원격 등록을 확인하지 못했습니다.' }
        $tcSource = (Get-Item -LiteralPath (Join-Path $tcRemote[0].installLocation 'plugins/tc-manager')).FullName
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'validate', $tcSource, '--strict', '--json')
        $tcManifest = Get-Content -LiteralPath (Join-Path $tcSource '.claude-plugin/plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $tcVersion = $tcManifest.version
        if ($tcManifest.name -ne 'tc-manager' -or [version]$tcVersion -lt [version]$tcBundledVersion -or
            ($tcBefore -and [version]$tcVersion -lt [version]$tcBefore.version)) {
            throw '[REMOTE_VERSION_OLD] 원격 배포가 현재 설치본/ZIP보다 오래됐습니다. 설치 버전은 낮추지 않았습니다. 배포자에게 확인하세요.'
        }
        $tcAction = if ($tcBefore) { 'update' } else { 'install' }
        Write-Host ('TC Manager ' + $tcVersion + ' 설치/업데이트 중...')
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', $tcAction, 'tc-manager@tc-manager-team', '--scope', 'local', '--json')
        $tcAfter = Get-TcRemoteInstall $tcClaude $tcRoot
        if (-not $tcAfter -or $tcAfter.version -ne $tcVersion) { throw '[VERSION_MISMATCH] 설치 버전이 원격 배포와 다릅니다. 완료되지 않았습니다.' }
        foreach ($tcFile in Get-ChildItem -LiteralPath $tcSource -Recurse -File) {
            $tcRelative = $tcFile.FullName.Substring($tcSource.Length).TrimStart('\', '/')
            if ($tcRelative -match '(^|[\\/])__pycache__([\\/]|$)') { continue }
            $tcCached = Join-Path $tcAfter.installPath $tcRelative
            if (-not (Test-Path -LiteralPath $tcCached) -or (Get-TcFileDigest $tcCached) -ne (Get-TcFileDigest $tcFile.FullName)) {
                throw ('[CONTENT_MISMATCH] 설치 내용이 원격 배포와 다릅니다: ' + $tcRelative + '. 같은 버전의 파일 변경 여부를 배포자에게 확인하세요.')
            }
        }
        foreach ($tcName in $tcProtected.Keys) {
            if ((Get-TcFileDigest (Join-Path $tcRoot $tcName)) -ne $tcProtected[$tcName]) { throw '[WORKSPACE_CHANGED] 기존 연결 설정이 변경됐습니다. 백업과 비교하세요. 완료로 처리하지 않았습니다.' }
        }
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $tcAlias) -Force
        Copy-Item -LiteralPath (Join-Path $tcAfter.installPath 'references/short-command/SKILL.md') -Destination $tcAlias -Force
        if ($tcWorkspace) { Save-TcWorkspaceConfiguration $tcWorkspace }
        Set-TcMarketplaceAutoUpdate $tcSettings $tcUrl $true
        Set-TcMarketplaceAutoUpdate $tcKnown $tcUrl $true -KnownMarketplaces
        Write-TcJson (Join-Path $tcRoot '.tc-manager/update-source-choice.json') ([pscustomobject]@{repository_url=$tcUrl})
        $tcDisabled = @('DISABLE_UPDATES', 'DISABLE_AUTOUPDATER', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC') | Where-Object { [Environment]::GetEnvironmentVariable($_) -eq '1' }
        if ($tcDisabled -and $env:FORCE_AUTOUPDATE_PLUGINS -ne '1') {
            throw ('[AUTO_UPDATE_DISABLED] 설치는 완료했지만 환경 설정으로 자동 갱신이 차단됩니다: ' + ($tcDisabled -join ', ') + '. 해당 설정은 변경하지 않았습니다.')
        }
        Write-Host ('완료: TC Manager ' + $tcVersion + ' / Git 원격 연결 / 자동 업데이트 켜짐') -ForegroundColor Green
        Write-Host '기존 토큰·연결 설정·TC DB는 유지했습니다. 같은 작업 폴더에서 Claude 새 세션을 열고 /tc-manager:help로 버전을 확인하세요.'
        Write-Host '이후 스킬은 원격 업데이트를 받습니다. 설치·연결 CMD 자체는 자동 교체되지 않습니다.'
    } catch {
        Write-Host '원격 업데이트를 완료하지 못했습니다. 위 오류와 설정 백업을 확인한 뒤 같은 CMD를 다시 실행하세요. 토큰 재발급은 필요하지 않습니다.' -ForegroundColor Yellow
        throw
    } finally { Pop-Location }
}
