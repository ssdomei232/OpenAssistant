#!/usr/bin/env pwsh
<#
.SYNOPSIS
  一键构建 OpenAssistant Android 调试安装包。

.DESCRIPTION
  - 构建 Web 资源（pnpm build）
  - 同步 Capacitor 原生工程（npx cap sync android）
  - 调用 Gradle 打包 debug APK
  - 把产物复制到 app/releases/OpenAssistant-<version>-debug.apk

  需要：Node/pnpm、JDK 17 或 21、Android SDK
  （platforms/android-35、build-tools/35.0.0、build-tools/34.0.0、platform-tools）。

.EXAMPLE
  ./scripts/build-android.ps1
  ./scripts/build-android.ps1 -JdkHome "C:\Java\jdk-21" -AndroidSdk "C:\Android\Sdk"
#>
param(
  [string]$JdkHome = "",
  [string]$AndroidSdk = "",
  [switch]$Release
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root "app"
$android = Join-Path $app "android"

function Resolve-Jdk {
  if ($JdkHome) { return $JdkHome }
  if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin/javac.exe"))) { return $env:JAVA_HOME }
  $candidates = @(
    "$env:USERPROFILE\.jabba\jdk\temurin@21.0.11",
    "$env:USERPROFILE\.jabba\jdk\temurin@21.0",
    "$env:USERPROFILE\.jabba\jdk\temurin@17.0",
    "C:\Program Files\Eclipse Adoptium\jdk-21*",
    "C:\Program Files\Eclipse Adoptium\jdk-17*",
    "C:\Program Files\Microsoft\jdk-21*",
    "C:\Program Files\Microsoft\jdk-17*"
  )
  foreach ($pattern in $candidates) {
    $found = Get-Item $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found -and (Test-Path (Join-Path $found.FullName "bin/javac.exe"))) { return $found.FullName }
  }
  throw "未找到 JDK 17/21，请用 -JdkHome 指定（Gradle 8.11 不支持 JDK 25）。"
}

function Resolve-AndroidSdk {
  if ($AndroidSdk) { return $AndroidSdk }
  if ($env:ANDROID_HOME -and (Test-Path $env:ANDROID_HOME)) { return $env:ANDROID_HOME }
  $candidates = @(
    (Join-Path $root ".android-sdk"),
    "$env:LOCALAPPDATA\Android\Sdk",
    "$env:USERPROFILE\AppData\Local\Android\Sdk"
  )
  foreach ($path in $candidates) {
    if ($path -and (Test-Path (Join-Path $path "platforms"))) { return $path }
  }
  throw "未找到 Android SDK，请用 -AndroidSdk 指定，或先运行 android sdk install。"
}

$jdk = Resolve-Jdk
$sdk = Resolve-AndroidSdk
Write-Host "JDK:         $jdk"
Write-Host "Android SDK: $sdk"

$env:JAVA_HOME = $jdk
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$env:PATH = "$jdk\bin;$sdk\platform-tools;$env:PATH"
if (-not $env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME = Join-Path $root ".gradle" }
New-Item -ItemType Directory -Force $env:GRADLE_USER_HOME | Out-Null

Push-Location $app
try {
  Write-Host "==> pnpm build" -ForegroundColor Cyan
  pnpm build
  if ($LASTEXITCODE -ne 0) { throw "pnpm build 失败（exit $LASTEXITCODE）" }
  Write-Host "==> cap sync android" -ForegroundColor Cyan
  npx cap sync android
  if ($LASTEXITCODE -ne 0) { throw "cap sync 失败（exit $LASTEXITCODE）" }
}
finally {
  Pop-Location
}

Push-Location $android
try {
  # local.properties：让 Gradle 找到 SDK（已被 .gitignore 忽略）
  $sdkEscaped = $sdk.Replace("\", "\\").Replace(":", "\:")
  Set-Content -Path "local.properties" -Value "sdk.dir=$sdkEscaped" -Encoding ascii
  $task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
  Write-Host "==> gradlew $task" -ForegroundColor Cyan
  if ($env:OS -eq "Windows_NT") {
    & .\gradlew.bat $task --no-daemon --console=plain
  } else {
    & ./gradlew $task --no-daemon --console=plain
  }
  if ($LASTEXITCODE -ne 0) { throw "Gradle 构建失败（exit $LASTEXITCODE）" }
}
finally {
  Pop-Location
}

$configuration = if ($Release) { "release" } else { "debug" }
$apk = Get-ChildItem (Join-Path $android "app/build/outputs/apk/$configuration") -Filter "*.apk" -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $apk) { throw "未找到 APK 产物" }

$releases = Join-Path $app "releases"
New-Item -ItemType Directory -Force $releases | Out-Null
$version = ([System.IO.File]::ReadAllText((Join-Path $app "package.json"), [System.Text.Encoding]::UTF8) | ConvertFrom-Json).version
$target = Join-Path $releases ("OpenAssistant-{0}-{1}.apk" -f $version, $configuration)
Copy-Item $apk.FullName $target -Force

$hash = (Get-FileHash $target -Algorithm SHA256).Hash
Write-Host ""
Write-Host "构建完成 ✅" -ForegroundColor Green
Write-Host "APK:    $($apk.FullName)"
Write-Host "副本:   $target"
Write-Host "SHA256: $hash"
Write-Host "安装:   adb install -r `"$target`""
