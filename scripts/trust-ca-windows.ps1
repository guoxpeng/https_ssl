# 将 https_ssl 根 CA 导入 Windows 受信任根存储，并配置 Node.js 信任该 CA。
#
# Node/Electron 应用用自带 OpenSSL，需 NODE_EXTRA_CA_CERTS 指向 CA 文件；
# 系统栈（schannel，如 curl、部分 .NET 客户端）走受信任根存储，仍会强制
# 检查吊销状态，私有 CA 无 CRL 时会报 CRYPT_E_NO_REVOCATION_CHECK；
# 这类客户端需自行加 --ssl-no-revoke 或改用 Node/Python 客户端。
#
# 用法（PowerShell，需管理员以导入根存储）：
#   .\trust-ca-windows.ps1 -CaPath .\qilin-ca.crt
param(
    [Parameter(Mandatory = $true)][string]$CaPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $CaPath)) {
    throw "找不到 CA 文件: $CaPath"
}

$ca = (Resolve-Path -LiteralPath $CaPath).Path

# 1. 导入受信任根存储（需要管理员）
Write-Host '正在导入到 Windows 受信任根存储...' -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    certutil -addstore -f Root $ca | Out-Null
    Write-Host '  已导入 LocalMachine\Root' -ForegroundColor Green
} else {
    Write-Host '  当前非管理员，跳过受信任根导入（schannel 客户端将仍不受信任）' -ForegroundColor Yellow
}

# 2. 配置 Node.js 信任（用户级，无需管理员）
$caDir = Join-Path $env:USERPROFILE '.https_ssl'
New-Item -ItemType Directory -Path $caDir -Force | Out-Null
$stableCa = Join-Path $caDir 'qilin-ca.crt'
Copy-Item -LiteralPath $ca -Destination $stableCa -Force

[Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $stableCa, 'User')
Write-Host "  已设置 NODE_EXTRA_CA_CERTS=$stableCa" -ForegroundColor Green
Write-Host '  重启 Node/Electron 应用后生效' -ForegroundColor Green

Write-Host ''
Write-Host '完成。' -ForegroundColor Cyan
