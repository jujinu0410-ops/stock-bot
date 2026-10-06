param(
    [string]$Project = "stock-monitoring-prod",
    [string]$Region = "asia-northeast3",
    [string]$ArtifactRepo = "stockbot-repo",
    [string]$JobName = "v8-portfolio-special-job",
    [string]$SchedulerName = "v8-portfolio-special-weekday-1600",
    [string]$RuntimeServiceAccount = "",
    [string]$V8SourcePath = "",
    [string]$StateBucket = "stock-monitoring-prod-stockbot-state",
    [string]$CurrentObject = "stockbot-state/current.json",
    [string]$StateObject = "v8-portfolio-special/state.json",
    [string]$PortfolioSheetId = "1VZR4-khOQjcWvLvZWoHwjXeDFuTamuaXp7wk7JNbxbM",
    [string]$RawDriveFolderId = "15WiLFWoSNubr3nMLMnmBvIKNx1oARsV3",
    [string]$DartSecret = "stockbot-dart-api-key",
    [string]$KiwoomAppKeySecret = "stockbot-kiwoom-app-key",
    [string]$KiwoomAppSecretSecret = "stockbot-kiwoom-app-secret",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$PinnedV8Sha = "d2d4ffe602fbb92b8df307bcc00edc385061a6b0"
$V8RemoteRepo = "https://github.com/jujinu0410-ops/stock-analysis-system-v8.git"
$Schedule = "0 16 * * 1-5"
$TimeZone = "Asia/Seoul"

function Run-Native {
    param([scriptblock]$Command, [string]$ErrorMessage)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw $ErrorMessage }
}

function Assert-RequiredPath {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Build staging failed. Missing required path [$Label]: $Path"
    }
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { throw "gcloud CLI not found" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found" }
if (-not $RuntimeServiceAccount) {
    $RuntimeServiceAccount = "stockbot-runner-sa@$Project.iam.gserviceaccount.com"
}
if (-not $PortfolioSheetId) { throw "PortfolioSheetId is required" }
if (-not $RawDriveFolderId) { throw "RawDriveFolderId is required" }

$activeAccountRaw = gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
$activeAccount = if ($activeAccountRaw) { [string]$activeAccountRaw.Trim() } else { "" }
if (-not $activeAccount) { throw "No active gcloud account" }
if ($activeAccount -eq $RuntimeServiceAccount) {
    throw "Use the local deployment user, not the runtime service account, for deployment."
}

$secretMap = [ordered]@{
    DART_API_KEY      = $DartSecret
    KIWOOM_APP_KEY    = $KiwoomAppKeySecret
    KIWOOM_APP_SECRET = $KiwoomAppSecretSecret
}

Run-Native { gcloud artifacts repositories describe $ArtifactRepo --location $Region --project $Project --quiet } "Artifact Registry repository is missing or inaccessible: $ArtifactRepo"
Run-Native { gcloud iam service-accounts describe $RuntimeServiceAccount --project $Project --quiet } "Runtime service account is missing: $RuntimeServiceAccount"
foreach ($secretName in $secretMap.Values) {
    Run-Native { gcloud secrets describe $secretName --project $Project --quiet } "Secret is missing: $secretName"
    $state = gcloud secrets versions describe latest --secret $secretName --project $Project --format="value(state)" 2>$null
    if ($LASTEXITCODE -ne 0 -or ([string]$state).Trim() -ne "ENABLED") {
        throw "Secret latest version is not ENABLED: $secretName"
    }
}

$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $V8SourcePath) {
    $siblingV8 = Join-Path (Split-Path $repoRoot -Parent) "stock-analysis-system-v8"
    if (Test-Path (Join-Path $siblingV8 ".git")) { $V8SourcePath = $siblingV8 }
}
$cloneSource = if ($V8SourcePath) { $V8SourcePath } else { $V8RemoteRepo }

Write-Host ""
Write-Host "PORTFOLIO_V8_SPECIAL_PREFLIGHT=PASS"
Write-Host "PROJECT=$Project"
Write-Host "REGION=$Region"
Write-Host "JOB_NAME=$JobName"
Write-Host "SCHEDULER=$SchedulerName"
Write-Host "SCHEDULE_KST=weekday 16:00"
Write-Host "RUNTIME_SERVICE_ACCOUNT=$RuntimeServiceAccount"
Write-Host "PORTFOLIO_SHEET_ID=$PortfolioSheetId"
Write-Host "RAW_DRIVE_FOLDER_ID=$RawDriveFolderId"
Write-Host "STATE_OBJECT=gs://$StateBucket/$StateObject"
Write-Host "PINNED_V8_SHA=$PinnedV8Sha"
Write-Host ""
Write-Host "MANUAL_DRIVE_PERMISSION_REQUIRED=YES"
Write-Host "Share the PORTFOLIO_CONFIG spreadsheet with $RuntimeServiceAccount as Viewer."
Write-Host "Share the V8_보유종목_특집/01_RAW_V8 folder with $RuntimeServiceAccount as Editor."
Write-Host "No service-account key file is used; Drive access uses short-lived scoped self-impersonation."

if ($PreflightOnly) {
    Write-Host "PREFLIGHT_ONLY=YES"
    Write-Host "No GCP resources or IAM policies were changed."
    exit 0
}

Run-Native { gcloud services enable run.googleapis.com cloudscheduler.googleapis.com iamcredentials.googleapis.com --project $Project --quiet } "Failed to enable required APIs"

foreach ($secretName in $secretMap.Values) {
    Run-Native {
        gcloud secrets add-iam-policy-binding $secretName `
            --project $Project `
            --member "serviceAccount:$RuntimeServiceAccount" `
            --role "roles/secretmanager.secretAccessor" `
            --quiet
    } "Failed to grant secret access: $secretName"
}

# This role is granted only on the runtime service account itself. It lets the job
# mint a short-lived Drive-scoped OAuth token as the same identity; it does not
# grant the ability to impersonate another service account.
Run-Native {
    gcloud iam service-accounts add-iam-policy-binding $RuntimeServiceAccount `
        --project $Project `
        --member "serviceAccount:$RuntimeServiceAccount" `
        --role "roles/iam.serviceAccountTokenCreator" `
        --quiet
} "Failed to grant self Token Creator for Drive-scoped access"

$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$registry = "$Region-docker.pkg.dev/$Project/$ArtifactRepo"
$jobImage = "$registry/v8-portfolio-special-job:$tag"
$buildDir = Join-Path $env:TEMP "v8-portfolio-special-build-$tag"

try {
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
    $mobileStageDir = Join-Path $buildDir "mobile_v8"
    $dailyStageDir = Join-Path $buildDir "daily_v8"
    New-Item -ItemType Directory -Path $mobileStageDir -Force | Out-Null
    New-Item -ItemType Directory -Path $dailyStageDir -Force | Out-Null

    Copy-Item -Path (Join-Path $PSScriptRoot "*") -Destination $mobileStageDir -Recurse -Force
    Copy-Item -Path (Join-Path $repoRoot "daily_v8\*") -Destination $dailyStageDir -Recurse -Force

    Assert-RequiredPath (Join-Path $mobileStageDir "cloudbuild-job-only.yaml") "mobile_v8/cloudbuild-job-only.yaml"
    Assert-RequiredPath (Join-Path $mobileStageDir "Dockerfile.job") "mobile_v8/Dockerfile.job"
    Assert-RequiredPath (Join-Path $mobileStageDir "portfolio_special_runner.py") "mobile_v8/portfolio_special_runner.py"
    Assert-RequiredPath (Join-Path $mobileStageDir "portfolio_special_selector.py") "mobile_v8/portfolio_special_selector.py"
    Assert-RequiredPath (Join-Path $mobileStageDir "portfolio_special_cloud.py") "mobile_v8/portfolio_special_cloud.py"
    Assert-RequiredPath (Join-Path $dailyStageDir "v8_runner.py") "daily_v8/v8_runner.py"
    Assert-RequiredPath (Join-Path $dailyStageDir "strategy_engine.py") "daily_v8/strategy_engine.py"

    Write-Host "Preparing pinned V8 from: $cloneSource"
    Run-Native { git clone --quiet $cloneSource (Join-Path $buildDir "v8_engine") } "Failed to clone private V8 repository"
    Run-Native { git -C (Join-Path $buildDir "v8_engine") checkout --quiet $PinnedV8Sha } "Failed to checkout pinned V8 SHA"
    $actualSha = (git -C (Join-Path $buildDir "v8_engine") rev-parse HEAD).Trim()
    if ($actualSha -ne $PinnedV8Sha) { throw "Pinned V8 SHA mismatch: $actualSha" }
    Remove-Item -Recurse -Force (Join-Path $buildDir "v8_engine\.git")

    Run-Native {
        gcloud builds submit $buildDir `
            --project $Project `
            --config (Join-Path $mobileStageDir "cloudbuild-job-only.yaml") `
            --substitutions "_JOB_IMAGE=$jobImage" `
            --quiet
    } "Cloud Build failed"

    $secretBindings = "DART_API_KEY=${DartSecret}:latest,KIWOOM_APP_KEY=${KiwoomAppKeySecret}:latest,KIWOOM_APP_SECRET=${KiwoomAppSecretSecret}:latest"
    $jobEnv = "STOCKBOT_CLOUD_MODE=1,V8_ENGINE_PATH=/app/v8_engine,V8_PINNED_SHA=$PinnedV8Sha,V8_SPECIAL_STATE_BUCKET=$StateBucket,V8_SPECIAL_CURRENT_OBJECT=$CurrentObject,V8_SPECIAL_STATE_OBJECT=$StateObject,V8_SPECIAL_PORTFOLIO_SHEET_ID=$PortfolioSheetId,V8_SPECIAL_RAW_FOLDER_ID=$RawDriveFolderId,V8_SPECIAL_DRIVE_PRINCIPAL=$RuntimeServiceAccount"

    Run-Native {
        gcloud run jobs deploy $JobName `
            --project $Project `
            --region $Region `
            --image $jobImage `
            --service-account $RuntimeServiceAccount `
            --set-secrets $secretBindings `
            --set-env-vars $jobEnv `
            --command python `
            --args=-m,mobile_v8.portfolio_special_runner `
            --cpu 2 `
            --memory 2Gi `
            --tasks 1 `
            --max-retries 0 `
            --task-timeout 3600s `
            --quiet
    } "Portfolio V8 Cloud Run Job deployment failed"

    Run-Native {
        gcloud run jobs add-iam-policy-binding $JobName `
            --project $Project `
            --region $Region `
            --member "serviceAccount:$RuntimeServiceAccount" `
            --role "roles/run.invoker" `
            --quiet
    } "Failed to grant scheduler identity Job invocation permission"

    $runUri = "https://run.googleapis.com/v2/projects/$Project/locations/$Region/jobs/$JobName`:run"
    $exists = $true
    gcloud scheduler jobs describe $SchedulerName --location $Region --project $Project --quiet 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $exists = $false }

    $schedulerArgs = @(
        "--project", $Project,
        "--location", $Region,
        "--schedule", $Schedule,
        "--time-zone", $TimeZone,
        "--uri", $runUri,
        "--http-method", "POST",
        "--oauth-service-account-email", $RuntimeServiceAccount,
        "--oauth-token-scope", "https://www.googleapis.com/auth/cloud-platform",
        "--message-body", "{}",
        "--quiet"
    )

    if ($exists) {
        $updateArgs = $schedulerArgs + @("--update-headers", "Content-Type=application/json")
        & gcloud scheduler jobs update http $SchedulerName @updateArgs
        if ($LASTEXITCODE -ne 0) { throw "Cloud Scheduler update failed" }
    } else {
        $createArgs = $schedulerArgs + @("--headers", "Content-Type=application/json")
        & gcloud scheduler jobs create http $SchedulerName @createArgs
        if ($LASTEXITCODE -ne 0) { throw "Cloud Scheduler creation failed" }
    }

    Write-Host ""
    Write-Host "PORTFOLIO_V8_SPECIAL_DEPLOY=SUCCESS"
    Write-Host "JOB_NAME=$JobName"
    Write-Host "SCHEDULER_NAME=$SchedulerName"
    Write-Host "SCHEDULE=weekdays 16:00 Asia/Seoul"
    Write-Host "RAW_DRIVE_FOLDER_ID=$RawDriveFolderId"
    Write-Host "PINNED_V8_SHA=$PinnedV8Sha"
}
finally {
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
}
