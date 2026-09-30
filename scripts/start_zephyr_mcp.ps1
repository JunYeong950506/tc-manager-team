$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'zephyr_runtime.ps1')

function Get-TcMcpUserToken { [Environment]::GetEnvironmentVariable('ZEPHYR_API_TOKEN', 'User') }

function Invoke-TcSmartBear {
    $tcNpx = Get-Command npx.cmd -ErrorAction Stop
    & $tcNpx.Source --cache (Get-TcRuntimeCache) -y '@smartbear/mcp@0.41.0'
    if ($LASTEXITCODE -ne 0) { throw '[MCP_START_FAILED] SmartBear MCP가 비정상 종료됐습니다.' }
}

function Start-TcZephyrMcp {
    $tcToken = Get-TcMcpUserToken
    if ([string]::IsNullOrWhiteSpace($tcToken) -or $tcToken -match '\s' -or $tcToken.StartsWith('${')) {
        throw '[TOKEN_MISSING] Windows 사용자 변수 ZEPHYR_API_TOKEN이 없거나 잘못됐습니다. Connect-Zephyr.cmd를 실행하세요.'
    }
    $tcPrevious = $env:ZEPHYR_API_TOKEN
    try {
        $env:ZEPHYR_API_TOKEN = $tcToken
        $tcToken = $null
        $env:Path = (@($env:Path, [Environment]::GetEnvironmentVariable('Path', 'User'), [Environment]::GetEnvironmentVariable('Path', 'Machine')) -join ';')
        $OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        Invoke-TcSmartBear
    } finally { $env:ZEPHYR_API_TOKEN = $tcPrevious; $tcToken = $null }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-TcZephyrMcp }
    catch { [Console]::Error.WriteLine('[MCP_START_FAILED] Zephyr MCP를 시작하지 못했습니다. Connect-Zephyr.cmd에서 진단하세요. 캐시 복구는 -RepairCache 옵션이며 토큰을 채팅에 붙여 넣지 마세요.'); exit 1 }
}
