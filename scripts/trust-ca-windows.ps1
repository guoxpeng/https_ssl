# 将 https_ssl 根 CA 导入 Windows 受信任根存储，并配置 Node.js 信任该 CA。
#
# Node/Electron 应用用自带 OpenSSL，需 NODE_EXTRA_CA_CERTS 指向 CA 文件；
# 系统栈（schannel，如 curl、部分 .NET 客户端）走受信任根存储，仍会强制
# 检查吊销状态，私有 CA 无 CRL 时会报 CRYPT_E_NO_REVOCATION_CHECK；
# 这类客户端需自行加 --ssl-no-revoke 或改用 Node/Python 客户端。
#
# 用法（PowerShell）：
#   .\trust-ca-windows.ps1 -CaPath .\https-ssl-ca.crt
# 不加管理员也能装进 CurrentUser\Root，Chrome / Edge 即可生效；
# 以管理员运行则装进 LocalMachine\Root，全机器生效。
param(
    [Parameter(Mandatory = $true)][string]$CaPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $CaPath)) {
    throw "找不到 CA 文件: $CaPath"
}

$ca = (Resolve-Path -LiteralPath $CaPath).Path

# 1. 导入受信任根存储
#    管理员      -> LocalMachine\Root，全机器生效
#    普通用户    -> CurrentUser\Root，浏览器即可生效，不需要提权
Write-Host '正在导入到 Windows 受信任根存储...' -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    certutil -addstore -f Root $ca | Out-Null
    Write-Host '  已导入 LocalMachine\Root（全机器生效）' -ForegroundColor Green
} else {
    certutil -user -addstore -f Root $ca | Out-Null
    Write-Host '  已导入 CurrentUser\Root（当前用户，Chrome / Edge 生效）' -ForegroundColor Green
    Write-Host '  如需全机器生效（系统服务、其他账号），以管理员再跑一次：' -ForegroundColor Yellow
    Write-Host "    certutil -addstore -f Root $ca" -ForegroundColor Yellow
}

# 2. 配置 Node.js 信任（用户级，无需管理员）
$caDir = Join-Path $env:USERPROFILE '.https_ssl'
New-Item -ItemType Directory -Path $caDir -Force | Out-Null
$stableCa = Join-Path $caDir 'https-ssl-ca.crt'
Copy-Item -LiteralPath $ca -Destination $stableCa -Force

[Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $stableCa, 'User')
Write-Host "  已设置 NODE_EXTRA_CA_CERTS=$stableCa" -ForegroundColor Green
Write-Host '  重启 Node/Electron 应用后生效' -ForegroundColor Green

Write-Host ''
Write-Host '注意：Firefox 有自己的信任库，不读取 Windows 存储，需要单独处理（二选一）：' -ForegroundColor Yellow
Write-Host '  1) about:config 里把 security.enterprise_roots.enabled 设为 true，重启浏览器；'
Write-Host '  2) 设置 → 隐私与安全 → 证书 → 查看证书 → 证书颁发机构 → 导入本 CA。'

Write-Host ''
Write-Host '完成。' -ForegroundColor Cyan
