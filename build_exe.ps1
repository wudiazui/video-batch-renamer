$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppName = -join ([char[]](0x89C6, 0x9891, 0x6279, 0x91CF, 0x91CD, 0x547D, 0x540D, 0x5DE5, 0x5177))
$Version = "v1.2.0"
$OutName = "$AppName-$Version"   # 输出文件名带版本号，如 视频批量重命名工具-v1.2.0.exe
$ReleaseDir = Join-Path $ProjectRoot "release"
$BuildDir = Join-Path $ProjectRoot "build"
$SpecPath = Join-Path $ProjectRoot "$OutName.spec"
$Entry = Join-Path $ProjectRoot "src\main.py"

# 打包前的环境自检，给出清晰的错误提示而不是中途崩溃。
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 python，请先安装 Python 3.10+ 并加入 PATH。"
    exit 1
}
if (-not (Test-Path $Entry)) {
    Write-Error "找不到入口文件：$Entry"
    exit 1
}
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "未安装 PyInstaller，请先运行：pip install pyinstaller"
    exit 1
}

python -B -m unittest discover -s (Join-Path $ProjectRoot "tests")

if (Test-Path $ReleaseDir) {
    Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
if (Test-Path $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
if (Test-Path $SpecPath) {
    Remove-Item -LiteralPath $SpecPath -Force
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $OutName `
    --distpath $ReleaseDir `
    --workpath $BuildDir `
    --specpath $ProjectRoot `
    $Entry

if (Test-Path $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
if (Test-Path $SpecPath) {
    Remove-Item -LiteralPath $SpecPath -Force
}

Write-Host "Build complete: $(Join-Path $ReleaseDir ($OutName + '.exe'))"
