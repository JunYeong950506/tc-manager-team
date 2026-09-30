param([string]$Site, [string]$ProjectKey, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tcInstallArguments = @{} + $PSBoundParameters
. (Join-Path $PSScriptRoot 'connect_zephyr.ps1')

function Test-TcPython {
    foreach ($tcName in @('py.exe', 'python.exe')) {
        $tcCommand = Get-Command $tcName -ErrorAction SilentlyContinue
        if (-not $tcCommand) { continue }
        $tcArgs = @()
        if ($tcName -eq 'py.exe') { $tcArgs += '-3' }
        $tcArgs += @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)')
        & $tcCommand.Source @tcArgs
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw '[PYTHON_MISSING] Python 3.10 이상이 필요합니다. Python 실행기 또는 PATH 등록을 확인하세요.'
}

function Test-TcClaude {
    $tcExecutable = Resolve-TcClaude ''
    $tcVersion = & $tcExecutable --version
    if ($LASTEXITCODE -ne 0 -or -not $tcVersion) { throw '[CLAUDE_START_FAILED] Claude Code를 시작하지 못했습니다.' }
}

function Get-TcMissingPrerequisites {
    $tcRequirements = @(
        @{ Name='Node.js 22+ (npm/npx)'; Id='OpenJS.NodeJS.LTS'; Check={ Test-TcNode } },
        @{ Name='Python 3.10+'; Id='Python.Python.3.12'; Check={ Test-TcPython } },
        @{ Name='Claude Code CLI'; Id='Anthropic.ClaudeCode'; Check={ Test-TcClaude } }
    )
    foreach ($tcRequirement in $tcRequirements) {
        try { & $tcRequirement.Check | Out-Null }
        catch {
            [pscustomobject]@{ Name=$tcRequirement.Name; Id=$tcRequirement.Id; Reason=$_.Exception.Message }
        }
    }
}

function Update-TcProcessPath {
    $tcPaths = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    )
    $env:Path = (($tcPaths -join ';') -split ';' | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Install-TcPrerequisite([string]$PackageId) {
    $tcWinget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $tcWinget) { throw '[WINGET_MISSING] WinGet이 없습니다. IT 담당자에게 App Installer 또는 필요한 프로그램 설치를 요청한 뒤 다시 실행하세요. https://learn.microsoft.com/windows/package-manager/winget/' }
    & $tcWinget.Source install --id $PackageId --exact --source winget
    $tcResult = $LASTEXITCODE
    if ($tcResult -ne 0) {
        throw "[INSTALL_FAILED] $PackageId 설치 실패 (종료 코드 $tcResult). 위 WinGet 출력을 확인하세요. 약관·재시작·관리자 승인은 IT 담당자와 진행하세요. 보안 정책은 변경하지 않았습니다."
    }
}

function Initialize-TcPrerequisites([switch]$CheckOnly) {
    Update-TcProcessPath
    $tcMissing = @(Get-TcMissingPrerequisites)
    if ($tcMissing.Count -eq 0) { Write-Host '필수 프로그램 확인 완료.'; return }
    Write-Host '설치되지 않았거나 버전이 낮거나 실행할 수 없는 프로그램:' -ForegroundColor Yellow
    foreach ($tcItem in $tcMissing) { Write-Host (" - {0} [{1}]: {2}" -f $tcItem.Name, $tcItem.Id, $tcItem.Reason) }
    if ($CheckOnly) { throw '필수 프로그램 확인 실패. -CheckOnly이므로 설치와 설정 변경은 하지 않았습니다.' }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw '[WINGET_MISSING] WinGet이 없습니다. IT 담당자에게 App Installer 또는 필요한 프로그램 설치를 요청하세요. https://learn.microsoft.com/windows/package-manager/winget/'
    }
    $tcConsent = Read-Host '위 프로그램을 WinGet으로 설치할까요? 관리자 승인이 필요할 수 있습니다. [y/N]'
    if ($tcConsent -notmatch '^(?i:y|yes)$') { throw '취소했습니다. 프로그램 설치와 프로젝트 설정은 시작하지 않았습니다.' }
    foreach ($tcItem in $tcMissing) {
        Write-Host ("{0} 설치 중..." -f $tcItem.Name)
        Install-TcPrerequisite $tcItem.Id
        Update-TcProcessPath
    }
    $tcRemaining = @(Get-TcMissingPrerequisites)
    if ($tcRemaining.Count -gt 0) {
        throw ('아직 사용할 수 없는 프로그램: ' + (($tcRemaining | ForEach-Object { $_.Name }) -join ', ') + '. 이 창을 닫고 Install-TC-Manager.cmd를 다시 실행하세요. 계속 실패하면 IT 담당자에게 설치/PATH를 확인받으세요. 플러그인 설정은 시작하지 않았습니다.')
    }
    Write-Host '필수 프로그램 설치·검증 완료.' -ForegroundColor Green
}

function Get-TcWorkspaceConfiguration([string]$Root, [string]$Site, [string]$ProjectKey) {
    $tcDefaults = Get-Content -LiteralPath (Join-Path $Root 'setup-defaults.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $tcTargetPath = Join-Path $Root '.tc-manager/target.json'
    $tcLibraryPath = Join-Path $Root '.tc-manager/library.json'
    $tcTarget = if (Test-Path -LiteralPath $tcTargetPath) { Get-Content -LiteralPath $tcTargetPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    if ($Site) { $Site = $Site.Trim().TrimEnd('/').ToLowerInvariant() }
    if ($tcTarget.site) {
        $tcSavedSite = $tcTarget.site.Trim().TrimEnd('/').ToLowerInvariant()
        if ($Site -and $Site -ne $tcSavedSite) { throw '기존 사이트와 다릅니다. 다른 사이트는 별도 작업 폴더를 사용하세요.' }
        $Site = $tcSavedSite
    }
    if (-not $Site) { $Site = $tcDefaults.site.Trim().TrimEnd('/').ToLowerInvariant() }
    if ($Site -notmatch '^https://[a-z0-9-]+\.atlassian\.net$') { throw '올바른 Atlassian Cloud 사이트 주소가 필요합니다.' }
    $tcKeys = @($tcTarget.project_keys) + @($tcTarget.project_key)
    if ($Site -eq $tcDefaults.site.TrimEnd('/').ToLowerInvariant()) { $tcKeys += @($tcDefaults.project_keys) }
    if ($ProjectKey) { $tcKeys += @($ProjectKey -split ',') }
    $tcKeys = @($tcKeys | Where-Object { $_ } | ForEach-Object { $_.Trim().ToUpperInvariant() } | Select-Object -Unique)
    if ($tcKeys.Count -eq 0 -or @($tcKeys | Where-Object { $_ -notmatch '^[A-Z][A-Z0-9_]*$' }).Count) { throw 'setup-defaults.json의 project_keys를 설정하거나 다른 사이트는 -ProjectKey로 지정하세요.' }
    $tcTokenProject = $tcTarget.token_project_key
    if (-not $tcTokenProject -and $Site -eq $tcDefaults.site.TrimEnd('/').ToLowerInvariant()) { $tcTokenProject = $tcDefaults.token_project_key }
    if (-not $tcTokenProject) { $tcTokenProject = $tcKeys[0] }
    if ($tcTokenProject -notin $tcKeys) { throw '토큰 발급 페이지의 프로젝트는 project_keys에 포함되어야 합니다.' }
    $tcTarget | Add-Member -NotePropertyName site -NotePropertyValue $Site -Force
    $tcTarget | Add-Member -NotePropertyName project_keys -NotePropertyValue $tcKeys -Force
    $tcTarget | Add-Member -NotePropertyName token_project_key -NotePropertyValue $tcTokenProject -Force
    $tcLibrary = if (Test-Path -LiteralPath $tcLibraryPath) { Get-Content -LiteralPath $tcLibraryPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{ db_path='data/tc-registry.sqlite3'; namespaces=@() } }
    if (-not $tcLibrary.db_path -or $null -eq $tcLibrary.namespaces) { throw '기존 DB 연결 설정을 확인해야 합니다. 덮어쓰지 않았습니다.' }
    $tcIdentities = @{}
    $tcNamespaces = @{}
    foreach ($tcRow in $tcLibrary.namespaces) {
        if (-not $tcRow.site -or -not $tcRow.project_key -or -not $tcRow.namespace) { throw '기존 DB 연결 설정이 잘못되었습니다.' }
        $tcIdentity = $tcRow.site.TrimEnd('/').ToLowerInvariant() + '/' + $tcRow.project_key.ToUpperInvariant()
        if ($tcIdentities.ContainsKey($tcIdentity) -or $tcNamespaces.ContainsKey($tcRow.namespace)) { throw 'DB 대상 또는 namespace가 중복됩니다. 기존 설정은 변경하지 않았습니다.' }
        $tcIdentities[$tcIdentity] = $true
        $tcNamespaces[$tcRow.namespace] = $true
    }
    foreach ($tcKey in $tcKeys) {
        $tcIdentity = $Site + '/' + $tcKey
        if (-not $tcIdentities.ContainsKey($tcIdentity)) {
            $tcNamespace = ([uri]$Site).Host + '/' + $tcKey
            if ($tcNamespaces.ContainsKey($tcNamespace)) { throw '새 namespace가 기존 DB 연결 설정과 충돌합니다.' }
            $tcLibrary.namespaces = @($tcLibrary.namespaces) + @([pscustomobject]@{site=$Site; project_key=$tcKey; namespace=$tcNamespace})
            $tcIdentities[$tcIdentity] = $true
            $tcNamespaces[$tcNamespace] = $true
        }
    }
    return [pscustomobject]@{ Target=$tcTarget; Library=$tcLibrary; TargetPath=$tcTargetPath; LibraryPath=$tcLibraryPath }
}

function Save-TcWorkspaceConfiguration($Configuration) {
    foreach ($tcPair in @(@{Path=$Configuration.TargetPath; Value=$Configuration.Target}, @{Path=$Configuration.LibraryPath; Value=$Configuration.Library})) {
        if (Test-Path -LiteralPath $tcPair.Path) {
            $tcOld = Get-Content -LiteralPath $tcPair.Path -Raw -Encoding UTF8 | ConvertFrom-Json
            if (($tcOld | ConvertTo-Json -Depth 40 -Compress) -ceq ($tcPair.Value | ConvertTo-Json -Depth 40 -Compress)) { continue }
            Copy-Item -LiteralPath $tcPair.Path -Destination ($tcPair.Path + '.before-multiproject-' + [guid]::NewGuid().ToString('N') + '.bak') -ErrorAction Stop
        }
        Write-TcJson $tcPair.Path $tcPair.Value
    }
}

function Invoke-TcInstall {
    param([string]$Site, [string]$ProjectKey, [switch]$CheckOnly)
    $tcRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    Initialize-TcPrerequisites -CheckOnly:$CheckOnly
    $tcClaude = Resolve-TcClaude ''
    $tcPlugin = Join-Path $tcRoot 'plugins/tc-manager'
    & $tcClaude plugin validate $tcPlugin --strict --json
    if ($LASTEXITCODE -ne 0) { throw '[PLUGIN_INVALID] 플러그인 검증 실패. ZIP 전체를 다시 압축 해제하세요.' }
    if ($CheckOnly) { Write-Host '필수 프로그램과 플러그인 구성을 확인했습니다. 설정은 변경하지 않았습니다.'; return }

    $tcWorkspace = Get-TcWorkspaceConfiguration $tcRoot $Site $ProjectKey
    $tcAliasPath = Join-Path $tcRoot '.claude/skills/tc-manager/SKILL.md'
    $tcAliasSource = Join-Path $tcPlugin 'references/short-command/SKILL.md'
    if ((Test-Path -LiteralPath $tcAliasPath) -and
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($tcAliasPath)) -ne [Convert]::ToBase64String([IO.File]::ReadAllBytes($tcAliasSource))) {
        throw '기존 /tc-manager 단축어가 다릅니다. 백업하고 변경 내용을 확인한 뒤 설치하세요.'
    }
    Push-Location -LiteralPath $tcRoot
    try {
        & $tcClaude plugin marketplace add $tcRoot --scope local
        if ($LASTEXITCODE -ne 0) { throw '[MARKETPLACE_FAILED] 마켓플레이스 등록 실패. 기존 항목을 자동 교체하지 않았습니다.' }
        & $tcClaude plugin install 'tc-manager@tc-manager-team' --scope local --json
        if ($LASTEXITCODE -ne 0) { throw '[PLUGIN_INSTALL_FAILED] 플러그인 설치 실패. 위 Claude CLI 출력을 확인하세요.' }
        Save-TcWorkspaceConfiguration $tcWorkspace
        Write-Host ('사용 가능한 프로젝트 설정: ' + ($tcWorkspace.Target.project_keys -join ', ') + '. 실제 작업 대상은 요청 내용에서 결정합니다.')
        if (-not (Test-Path -LiteralPath $tcAliasPath)) {
            $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tcAliasPath)
            Copy-Item -LiteralPath $tcAliasSource -Destination $tcAliasPath
        }
        Write-Host '설치 완료. Connect-Zephyr.cmd를 실행한 뒤 Claude Code에서 이 폴더로 새 세션을 시작하세요.' -ForegroundColor Green
        Write-Host 'Jira/Confluence는 Claude에서 별도로 연결해야 합니다. 기존 TC 데이터는 보존하며, 새 작업 폴더의 DB에는 아직 TC가 없습니다.'
        Write-Host '새 Claude 세션에서 회사 계정으로 로그인하세요. 설치기가 대신 로그인하지 않습니다.'
    } finally { Pop-Location }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-TcInstall @tcInstallArguments }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
