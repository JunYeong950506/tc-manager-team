param([switch]$CheckOnly)
$tcUpdateArguments = @{} + $PSBoundParameters
. (Join-Path $PSScriptRoot 'install_tc_manager.ps1')

function Invoke-TcUpdateCli([string]$Claude, [string[]]$Arguments) {
    $tcOutput = & $Claude @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Claude command failed: ' + ($Arguments -join ' ') + '. Existing token and TC data were not changed. Rerun after resolving the error.') }
    return ($tcOutput -join "`n")
}

function Test-TcSamePath([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    return [IO.Path]::GetFullPath($Left).TrimEnd('\', '/').Equals([IO.Path]::GetFullPath($Right).TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)
}

function Get-TcFileDigest([string]$Path) {
    $tcSha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($tcSha.ComputeHash([IO.File]::ReadAllBytes($Path))) }
    finally { $tcSha.Dispose() }
}

function Get-TcUpdateInstallation([string]$Claude, [string]$Root) {
    $tcInstalled = Invoke-TcUpdateCli $Claude @('plugin', 'list', '--json') | ConvertFrom-Json
    $tcMatch = @($tcInstalled | Where-Object { $_.id -eq 'tc-manager@tc-manager-team' -and $_.scope -eq 'local' -and (Test-TcSamePath $_.projectPath $Root) })
    if ($tcMatch.Count -ne 1) { throw 'No unique local TC Manager installation exists at THIS exact folder. Keep the original folder path, or run Install-TC-Manager.cmd for a new installation.' }
    return $tcMatch[0]
}

function Invoke-TcUpdate {
    param([switch]$CheckOnly)
    $tcRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    Initialize-TcPrerequisites -CheckOnly
    $tcClaude = Resolve-TcClaude ''
    $tcPlugin = Join-Path $tcRoot 'plugins/tc-manager'
    $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'validate', $tcPlugin, '--strict', '--json')
    $tcVersion = (Get-Content -LiteralPath (Join-Path $tcPlugin '.claude-plugin/plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    Push-Location -LiteralPath $tcRoot
    try {
        $tcBefore = Get-TcUpdateInstallation $tcClaude $tcRoot
        if ([version]$tcVersion -lt [version]$tcBefore.version) { throw 'The copied package is older than the installed version. Downgrade was not performed.' }
        $tcMarkets = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'list', '--json') | ConvertFrom-Json
        $tcMarket = @($tcMarkets | Where-Object { $_.name -eq 'tc-manager-team' })
        if ($tcMarket.Count -ne 1 -or $tcMarket[0].source -ne 'directory' -or -not (Test-TcSamePath $tcMarket[0].path $tcRoot)) {
            throw 'The team marketplace points to a different folder. Copy the new contents into the original installed folder; no marketplace was redirected.'
        }
        $tcProtected = @{}
        foreach ($tcName in @('.tc-manager/target.json', '.tc-manager/library.json', '.tc-manager/zephyr-connection.json', '.mcp.json')) {
            $tcPath = Join-Path $tcRoot $tcName
            if (Test-Path -LiteralPath $tcPath) { $tcProtected[$tcName] = Get-TcFileDigest $tcPath }
        }
        if (-not $tcProtected.ContainsKey('.tc-manager/target.json') -or -not $tcProtected.ContainsKey('.tc-manager/library.json')) {
            throw 'Existing workspace settings are missing. Restore them or run Install-TC-Manager.cmd for a new workspace.'
        }
        $tcAlias = Join-Path $tcRoot '.claude/skills/tc-manager/SKILL.md'
        $tcAliasSource = Join-Path $tcPlugin 'references/short-command/SKILL.md'
        if (Test-Path -LiteralPath $tcAlias) {
            $tcAliasHash = Get-TcFileDigest $tcAlias
            $tcOldAlias = Join-Path $tcBefore.installPath 'references/short-command/SKILL.md'
            if ($tcAliasHash -ne (Get-TcFileDigest $tcAliasSource) -and
                (-not (Test-Path -LiteralPath $tcOldAlias) -or $tcAliasHash -ne (Get-TcFileDigest $tcOldAlias))) {
                throw 'The /tc-manager shortcut was customized. Back it up and reconcile it with the new references/short-command/SKILL.md before updating; it was not overwritten.'
            }
        }
        Write-Host ("Workspace: {0}`nPlugin: {1} -> {2}" -f $tcRoot, $tcBefore.version, $tcVersion)
        if ($CheckOnly) { Write-Host 'Update prerequisites checked. Nothing changed; installed content was not updated.'; return }

        $tcBackup = Join-Path $tcRoot ('.tc-manager/backups/update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        foreach ($tcName in @($tcProtected.Keys) + @('.claude/settings.local.json', '.claude/skills/tc-manager/SKILL.md')) {
            $tcPath = Join-Path $tcRoot $tcName
            if (Test-Path -LiteralPath $tcPath) {
                $tcDestination = Join-Path $tcBackup $tcName
                $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tcDestination)
                Copy-Item -LiteralPath $tcPath -Destination $tcDestination
            }
        }
        Write-TcJson (Join-Path $tcBackup 'update.json') ([pscustomobject]@{previous_version=$tcBefore.version; target_version=$tcVersion; previous_install_path=$tcBefore.installPath})
        Write-Host ('Settings backup: ' + $tcBackup)
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'marketplace', 'update', 'tc-manager-team')
        $null = Invoke-TcUpdateCli $tcClaude @('plugin', 'update', 'tc-manager@tc-manager-team', '--scope', 'local', '--json')
        $tcAfter = Get-TcUpdateInstallation $tcClaude $tcRoot
        if ($tcAfter.version -ne $tcVersion) { throw 'Installed version does not match the copied package. Update is incomplete; rerun after checking Claude plugin status.' }
        foreach ($tcFile in Get-ChildItem -LiteralPath $tcPlugin -Recurse -File) {
            $tcRelative = $tcFile.FullName.Substring($tcPlugin.Length).TrimStart('\', '/')
            if ($tcRelative -match '(^|[\\/])__pycache__([\\/]|$)') { continue }
            $tcCached = Join-Path $tcAfter.installPath $tcRelative
            if (-not (Test-Path -LiteralPath $tcCached) -or (Get-TcFileDigest $tcCached) -ne (Get-TcFileDigest $tcFile.FullName)) {
                throw 'Installed plugin content differs from the copied package. Update is incomplete. The publisher must use a new plugin version for changed plugin files.'
            }
        }
        foreach ($tcName in $tcProtected.Keys) {
            if ((Get-TcFileDigest (Join-Path $tcRoot $tcName)) -ne $tcProtected[$tcName]) { throw ('Workspace setting changed during update: ' + $tcName + '. Review the saved backup; update was not reported as complete.') }
        }
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tcAlias)
        Copy-Item -LiteralPath $tcAliasSource -Destination $tcAlias -Force
        $tcMcpPath = Join-Path $tcRoot '.mcp.json'
        if (Test-Path -LiteralPath $tcMcpPath) {
            $tcMcp = Get-Content -LiteralPath $tcMcpPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $tcMigrated = $false
            foreach ($tcEntry in @($tcMcp.mcpServers.PSObject.Properties)) {
                if (Test-TcLegacyMcpDefinition $tcEntry.Value) {
                    $tcEntry.Value = Get-TcMcpDefinition $tcRoot
                    $tcMigrated = $true
                }
            }
            if ($tcMigrated) {
                Write-TcJson $tcMcpPath $tcMcp
                Write-Host 'Zephyr startup now reads the saved Windows USER token directly. Restart the Claude session; approve the changed server if Claude asks.'
            }
        }
        Write-Host ('Update complete: ' + $tcVersion + '. Token, connection settings and TC data were retained.') -ForegroundColor Green
        Write-Host 'Open a new Claude Code session in THIS same folder to load the updated plugin. No token entry or Connect-Zephyr.cmd is needed unless the connection itself has changed.'
    } finally { Pop-Location }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-TcUpdate @tcUpdateArguments }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
