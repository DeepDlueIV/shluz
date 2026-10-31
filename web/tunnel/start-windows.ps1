# Local gateway stays on this PC. No credentials are committed to GitHub.
param([ValidateRange(1,65535)][int]$GatewayPort = 8000)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    throw 'Install ngrok from https://ngrok.com/download/windows and configure your own ngrok authtoken first.'
}
$homeDir = Join-Path $env:LOCALAPPDATA 'NoClipTunnel'
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
$passwordPath = Join-Path $homeDir 'bridge-password.txt'
$policyPath = Join-Path $homeDir 'traffic-policy.yml'
if (-not (Test-Path $passwordPath)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    Set-Content -LiteralPath $passwordPath -Value $password -Encoding Ascii
} else {
    $password = (Get-Content -LiteralPath $passwordPath -Raw).Trim()
}
if ($password -notmatch '^[a-f0-9]{64}$') { throw 'Invalid saved password. Do not start an unprotected tunnel.' }

# Deny all routes except the temporary guest chat API, then require Basic Auth at ngrok edge.
$policy = @"
on_http_request:
  - expressions:
      - "!((req.method == 'POST' && (req.url.path == '/v1/web/session' || req.url.path == '/v1/web/chat/completions' || req.url.path == '/v1/web/tokens' || req.url.path == '/v1/web/tokens/revoke' || req.url.path == '/v1/web/authorize' || req.url.path == '/v1/web/token')) || (req.method == 'GET' && (req.url.path == '/v1/web/me' || req.url.path == '/v1/web/tokens')))"
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
Write-Host ''
Write-Host 'Vercel -> Settings -> Environment Variables (Production):' -ForegroundColor Cyan
Write-Host '  SHLUZ_BRIDGE_USERNAME = noclip'
Write-Host "  SHLUZ_BRIDGE_PASSWORD = $password"
Write-Host '  SHLUZ_BRIDGE_ORIGIN = the HTTPS forwarding URL shown below'
Write-Host 'Leave NEXT_PUBLIC_GATEWAY_ENABLED=false until a live, protected API test passes.'
Write-Host 'Do not send this password in ChatGPT, email, or GitHub.'
Write-Host ''
& ngrok http "http://127.0.0.1:$GatewayPort" --traffic-policy-file $policyPath
