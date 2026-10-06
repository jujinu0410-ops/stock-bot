param(
    [string]$Project = "stock-monitoring-prod",
    [string]$Region = "asia-northeast3",
    [string]$JobName = "v8-portfolio-special-job",
    [string]$RawDriveFileId = "1l6Ar5T29B56m7lH-Vr_0nQiv0DhVsIKL"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found"
}
if (-not $RawDriveFileId) {
    throw "RawDriveFileId is required"
}

$baseScript = Join-Path $PSScriptRoot "deploy_portfolio_special.ps1"
if (-not (Test-Path -LiteralPath $baseScript)) {
    throw "Base deployment script not found: $baseScript"
}

Write-Host "Enabling Drive/Sheets APIs required by the portfolio-special runtime..."
& gcloud services enable sheets.googleapis.com drive.googleapis.com `
    --project $Project `
    --quiet
if ($LASTEXITCODE -ne 0) { throw "Failed to enable Google Drive/Sheets APIs" }

Write-Host "Running the full portfolio-special deployment..."
& $baseScript -Project $Project -Region $Region -JobName $JobName
if (-not $?) { throw "Base portfolio-special deployment failed" }

# Windows PowerShell can split the legacy --args expression in the base script and
# inject a leading space before the module name. Re-apply the command/args using an
# argument array so gcloud receives one exact --args token. Also attach the fixed,
# user-owned Drive bridge file. Updating that file avoids the service-account
# storageQuota limitation of creating files in consumer My Drive.
$jobUpdateArgs = @(
    "run", "jobs", "update", $JobName,
    "--project", $Project,
    "--region", $Region,
    "--command", "python",
    "--args=-m,mobile_v8.portfolio_special_runner",
    "--update-env-vars", "V8_SPECIAL_RAW_FILE_ID=$RawDriveFileId",
    "--quiet"
)

Write-Host "Applying runtime-safe command arguments and Drive bridge file..."
& gcloud @jobUpdateArgs
if ($LASTEXITCODE -ne 0) { throw "Failed to apply portfolio-special runtime hotfix" }

Write-Host ""
Write-Host "PORTFOLIO_V8_SPECIAL_SAFE_DEPLOY=SUCCESS"
Write-Host "JOB_NAME=$JobName"
Write-Host "RAW_DRIVE_FILE_ID=$RawDriveFileId"
Write-Host "SCHEDULE=weekdays 16:00 Asia/Seoul"
