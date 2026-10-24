# Существующий guest-only ngrok tunnel. Секреты не выводятся в консоль.
param(
    [ValidateRange(1,65535)][int]$GatewayPort = 8000,
    [string]$RuntimeDirectory = '',
    [string]$NgrokConfig = '',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $env:LOCALAPPDATA 'NoClipTunnel' }
if (-not [IO.Path]::IsPathRooted($RuntimeDirectory)) { throw 'Нужен абсолютный каталог runtime.' }
New-Item -ItemType Directory -Force -Path $RuntimeDirectory | Out-Null
# Удаляем также явные чужие ACL; /grant:r сам по себе их сохраняет.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
# Статический .NET API не требует автозагрузки несовместимого Set-Acl модуля.
[IO.Directory]::SetAccessControl($RuntimeDirectory, $acl)
if (-not $NgrokConfig -and -not $PrepareOnly) {
    throw 'Нужен явный NgrokConfig; небезопасная конфигурация по умолчанию не используется.'
}
if ($NgrokConfig) {
    if (-not [IO.Path]::IsPathRooted($NgrokConfig) -or -not (Test-Path -LiteralPath $NgrokConfig -PathType Leaf)) {
        throw 'Явный NgrokConfig не найден; туннель не запущен.'
    }
    if ([IO.File]::GetAttributes($NgrokConfig) -band [IO.FileAttributes]::ReparsePoint) {
        throw 'NgrokConfig не должен быть ссылкой на другой файл.'
    }
    # Явный внешний config допустим, но его DACL также заменяется целиком.
    $fileAcl = New-Object Security.AccessControl.FileSecurity
    $fileAcl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
        $fileAcl.AddAccessRule($rule)
    }
    [IO.File]::SetAccessControl($NgrokConfig, $fileAcl)
}
$passwordPath = Join-Path $RuntimeDirectory 'bridge-password.txt'
$policyPath = Join-Path $RuntimeDirectory 'traffic-policy.yml'
if (-not (Test-Path -LiteralPath $passwordPath)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    Set-Content -LiteralPath $passwordPath -Value $password -Encoding Ascii
} else {
    $password = (Get-Content -LiteralPath $passwordPath -Raw).Trim()
}
if ($password -notmatch '^[a-f0-9]{64}$') { throw 'Некорректный bridge-пароль; туннель не запущен.' }
$policy = @"
on_http_request:
  - expressions:
      - "!((req.method == 'POST' && (req.url.path == '/v1/web/session' || req.url.path == '/v1/web/chat/completions')) || (req.method == 'GET' && req.url.path == '/v1/web/me'))"
    actions:
      - type: deny
  - actions:
      - type: basic-auth
        config:
          realm: noclip-bridge
          credentials:
            - "noclip:$password"
          enforce: true
"@
Set-Content -LiteralPath $policyPath -Value $policy -Encoding Ascii
foreach ($file in @($passwordPath, $policyPath)) {
    & "$env:SystemRoot\System32\icacls.exe" $file /reset /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось защитить файл policy.' }
}
$password = $null
$policy = $null
if ($PrepareOnly) { Write-Host 'Защитная policy подготовлена; туннель не запускался.'; exit 0 }
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) { throw 'ngrok не найден.' }
$arguments = @('http', "http://127.0.0.1:$GatewayPort", '--traffic-policy-file', $policyPath, '--inspect=false')
if ($NgrokConfig) { $arguments += @('--config', $NgrokConfig) }
Write-Host 'Запускается защищённый website tunnel. Vercel и публичный режим не меняются.'
& ngrok @arguments
exit $LASTEXITCODE