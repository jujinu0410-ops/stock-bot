param(
    [string]$Project = "stock-monitoring-prod",
    [string]$ProjectNumber = "958023351745",
    [string]$Region = "asia-northeast3",
    [string]$ArtifactRepo = "stockbot-repo",
    [string]$JobName = "v8-analysis-job",
    [string]$ServiceName = "v8-mobile-web",
    [string]$RuntimeServiceAccount = "",
    [string]$GmailUser = $env:GMAIL_USER,
    [string]$RecipientGmail = $env:RECIPIENT_GMAIL,
    [string]$IapUserEmail = "",
    [string]$V8SourcePath = "",
    [string]$DartSecret = "stockbot-dart-api-key",
    [string]$KiwoomAppKeySecret = "stockbot-kiwoom-app-key",
    [string]$KiwoomAppSecretSecret = "stockbot-kiwoom-app-secret",
    [string]$GmailPasswordSecret = "stockbot-gmail-app-password",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$PinnedV8Sha = "d2d4ffe602fbb92b8df307bcc00edc385061a6b0"
$V8RemoteRepo = "https://github.com/jujinu0410-ops/stock-analysis-system-v8.git"

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

if (-not $Project) { throw "GCP project is required" }
if (-not $ProjectNumber -or $ProjectNumber -notmatch '^\d+$') {
    throw "ProjectNumber is required and must be numeric"
}
if (-not $RuntimeServiceAccount) {
    $RuntimeServiceAccount = "stockbot-runner-sa@$Project.iam.gserviceaccount.com"
}

$activeAccountRaw = gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
$activeAccount = if ($activeAccountRaw) { [string]$activeAccountRaw.Trim() } else { "" }
if (-not $activeAccount) {
    throw "No active gcloud account. Run 'gcloud auth login' with the deployment user first."
}
if ($activeAccount -eq $RuntimeServiceAccount) {
    throw "The active gcloud account is the runtime service account. Use the local deployment user instead of granting deploy-admin permissions to the runtime SA."
}

$secretMap = [ordered]@{
    DART_API_KEY       = $DartSecret
    KIWOOM_APP_KEY     = $KiwoomAppKeySecret
    KIWOOM_APP_SECRET  = $KiwoomAppSecretSecret
    GMAIL_APP_PASSWORD = $GmailPasswordSecret
}
if (($secretMap.Values | Where-Object { -not $_ }).Count -gt 0) {
    throw "Secret Manager secret names must not be empty"
}

# Read-only cloud preflight. We intentionally do NOT call `gcloud projects describe`:
# Cloud Resource Manager API is not required by the existing stock system. Every
# resource lookup is scoped directly to the known project ID and reveals no secret value.
Run-Native { gcloud artifacts repositories describe $ArtifactRepo --location $Region --project $Project --quiet } "Artifact Registry repository is missing or inaccessible: $ArtifactRepo"
Run-Native { gcloud iam service-accounts describe $RuntimeServiceAccount --project $Project --quiet } "Runtime service account is missing or inaccessible: $RuntimeServiceAccount"
foreach ($secretName in $secretMap.Values) {
    Run-Native { gcloud secrets describe $secretName --project $Project --quiet } "Secret Manager secret is missing or inaccessible: $secretName"
    $secretStateRaw = gcloud secrets versions describe latest --secret $secretName --project $Project --format="value(state)" 2>$null
    $secretState = if ($secretStateRaw) { [string]$secretStateRaw.Trim() } else { "" }
    if ($LASTEXITCODE -ne 0 -or $secretState -ne "ENABLED") {
        throw "Secret Manager latest version is missing or not ENABLED: $secretName"
    }
}

$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $V8SourcePath) {
    $siblingV8 = Join-Path (Split-Path $repoRoot -Parent) "stock-analysis-system-v8"
    if (Test-Path (Join-Path $siblingV8 ".git")) {
        $V8SourcePath = $siblingV8
    }
}
$cloneSource = if ($V8SourcePath) { $V8SourcePath } else { $V8RemoteRepo }
$preflightIapUser = if ($IapUserEmail) { $IapUserEmail } elseif ($GmailUser) { $GmailUser } else { "DEFERRED_UNTIL_DEPLOY" }

Write-Host ""
Write-Host "MOBILE_V8_PREFLIGHT=PASS"
Write-Host "ACTIVE_GCLOUD_ACCOUNT=$activeAccount"
Write-Host "PROJECT=$Project"
Write-Host "PROJECT_NUMBER=$ProjectNumber"
Write-Host "REGION=$Region"
Write-Host "ARTIFACT_REPO=$ArtifactRepo"
Write-Host "RUNTIME_SERVICE_ACCOUNT=$RuntimeServiceAccount"
Write-Host "IAP_USER=$preflightIapUser"
Write-Host "V8_SOURCE=$cloneSource"
Write-Host "PINNED_V8_SHA=$PinnedV8Sha"
Write-Host "SECRETS=$($secretMap.Values -join ',')"
Write-Host "SECRET_LATEST_VERSIONS=ENABLED"

if ($PreflightOnly) {
    Write-Host "PREFLIGHT_ONLY=YES"
    Write-Host "No GCP resources or IAM policies were changed."
    exit 0
}

# Deployment-only identity inputs are intentionally validated after the read-only
# preflight so `-PreflightOnly` never needs Gmail/IAP identity configuration.
if (-not $GmailUser) {
    throw "GMAIL_USER is required for deployment. Set `$env:GMAIL_USER before running without -PreflightOnly."
}
if (-not $RecipientGmail) { $RecipientGmail = $GmailUser }
if (-not $IapUserEmail) { $IapUserEmail = $GmailUser }
if ($GmailUser -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "GmailUser is not a valid email address"
}
if ($RecipientGmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "RecipientGmail is not a valid email address"
}
if ($IapUserEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "IapUserEmail is not a valid email address"
}

# Mutations begin only after a successful preflight and deployment-input validation.
foreach ($secretName in $secretMap.Values) {
    Run-Native {
        gcloud secrets add-iam-policy-binding $secretName `
            --project $Project `
            --member "serviceAccount:$RuntimeServiceAccount" `
            --role "roles/secretmanager.secretAccessor" `
            --quiet
    } "Failed to grant Secret Manager access: $secretName"
}

$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$registry = "$Region-docker.pkg.dev/$Project/$ArtifactRepo"
$jobImage = "$registry/v8-mobile-job:$tag"
$serviceImage = "$registry/v8-mobile-service:$tag"
$buildDir = Join-Path $env:TEMP "v8-mobile-build-$tag"

try {
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

    # PowerShell wildcard copy into a non-existent destination is not stable across
    # platforms. Create the exact Cloud Build directory layout first, then copy only
    # the contents into those directories.
    $mobileStageDir = Join-Path $buildDir "mobile_v8"
    $dailyStageDir = Join-Path $buildDir "daily_v8"
    New-Item -ItemType Directory -Path $mobileStageDir -Force | Out-Null
    New-Item -ItemType Directory -Path $dailyStageDir -Force | Out-Null

    Copy-Item -Path (Join-Path $PSScriptRoot "*") -Destination $mobileStageDir -Recurse -Force
    Copy-Item -Path (Join-Path $repoRoot "daily_v8\*") -Destination $dailyStageDir -Recurse -Force

    Assert-RequiredPath (Join-Path $mobileStageDir "cloudbuild.yaml") "mobile_v8/cloudbuild.yaml"
    Assert-RequiredPath (Join-Path $mobileStageDir "Dockerfile.job") "mobile_v8/Dockerfile.job"
    Assert-RequiredPath (Join-Path $mobileStageDir "Dockerfile.service") "mobile_v8/Dockerfile.service"
    Assert-RequiredPath (Join-Path $mobileStageDir "job_runner.py") "mobile_v8/job_runner.py"
    Assert-RequiredPath (Join-Path $mobileStageDir "web_app.py") "mobile_v8/web_app.py"
    Assert-RequiredPath (Join-Path $dailyStageDir "v8_runner.py") "daily_v8/v8_runner.py"
    Assert-RequiredPath (Join-Path $dailyStageDir "strategy_engine.py") "daily_v8/strategy_engine.py"

    Write-Host "BUILD_STAGE_MOBILE_V8=PASS"
    Write-Host "BUILD_STAGE_DAILY_V8=PASS"

    Write-Host "Preparing pinned V8 from: $cloneSource"
    Run-Native { git clone --quiet $cloneSource (Join-Path $buildDir "v8_engine") } "Failed to clone private pinned V8 repository"
    Run-Native { git -C (Join-Path $buildDir "v8_engine") checkout --quiet $PinnedV8Sha } "Failed to checkout pinned V8 SHA"
    $actualSha = (git -C (Join-Path $buildDir "v8_engine") rev-parse HEAD).Trim()
    if ($actualSha -ne $PinnedV8Sha) { throw "Pinned V8 SHA mismatch: $actualSha" }
    Assert-RequiredPath (Join-Path $buildDir "v8_engine\requirements.txt") "v8_engine/requirements.txt"
    Remove-Item -Recurse -Force (Join-Path $buildDir "v8_engine\.git")

    Write-Host "BUILD_STAGE_V8_ENGINE=PASS"
    Write-Host "Building Cloud Run images from pinned V8 $PinnedV8Sha"
    Run-Native {
        gcloud builds submit $buildDir `
            --project $Project `
            --config (Join-Path $mobileStageDir "cloudbuild.yaml") `
            --substitutions "_JOB_IMAGE=$jobImage,_SERVICE_IMAGE=$serviceImage" `
            --quiet
    } "Cloud Build failed"

    $secretBindings = "DART_API_KEY=${DartSecret}:latest,KIWOOM_APP_KEY=${KiwoomAppKeySecret}:latest,KIWOOM_APP_SECRET=${KiwoomAppSecretSecret}:latest,GMAIL_APP_PASSWORD=${GmailPasswordSecret}:latest"
    $jobEnv = "GMAIL_USER=$GmailUser,RECIPIENT_GMAIL=$RecipientGmail,STOCKBOT_CLOUD_MODE=1,V8_ENGINE_PATH=/app/v8_engine,V8_PINNED_SHA=$PinnedV8Sha"

    Run-Native {
        gcloud run jobs deploy $JobName `
            --project $Project `
            --region $Region `
            --image $jobImage `
            --service-account $RuntimeServiceAccount `
            --set-secrets $secretBindings `
            --set-env-vars $jobEnv `
            --cpu 2 `
            --memory 2Gi `
            --tasks 1 `
            --max-retries 0 `
            --task-timeout 3600s `
            --quiet
    } "Cloud Run Job deployment failed"

    Run-Native {
        gcloud run jobs add-iam-policy-binding $JobName `
            --project $Project `
            --region $Region `
            --member "serviceAccount:$RuntimeServiceAccount" `
            --role "roles/run.jobsExecutorWithOverrides" `
            --quiet
    } "Failed to grant Job execute-with-overrides permission"

    # Direct Cloud Run IAP requires the IAP API and service agent. The service
    # identity command is currently exposed by gcloud in the beta surface.
    Run-Native { gcloud services enable iap.googleapis.com --project $Project --quiet } "Failed to enable IAP API"
    Run-Native { gcloud beta services identity create --service iap.googleapis.com --project $Project --quiet } "Failed to provision IAP service identity"

    # web_app.py reads V8_JOB_NAME. Keep this name in sync with the deployed Job.
    $serviceEnv = "GCP_PROJECT=$Project,GCP_REGION=$Region,V8_JOB_NAME=$JobName"
    Run-Native {
        gcloud run deploy $ServiceName `
            --project $Project `
            --region $Region `
            --image $serviceImage `
            --service-account $RuntimeServiceAccount `
            --set-env-vars $serviceEnv `
            --cpu 1 `
            --memory 256Mi `
            --min 0 `
            --max 2 `
            --no-allow-unauthenticated `
            --iap `
            --quiet
    } "Cloud Run Service deployment failed. If this is the project's first IAP setup, enable IAP once in Cloud Run Console and rerun."

    Run-Native {
        gcloud run services add-iam-policy-binding $ServiceName `
            --project $Project `
            --region $Region `
            --member "serviceAccount:service-$ProjectNumber@gcp-sa-iap.iam.gserviceaccount.com" `
            --role "roles/run.invoker" `
            --quiet
    } "Failed to grant IAP service agent Cloud Run invoker"

    Run-Native {
        gcloud iap web add-iam-policy-binding `
            --project $Project `
            --member "user:$IapUserEmail" `
            --role "roles/iap.httpsResourceAccessor" `
            --region $Region `
            --resource-type cloud-run `
            --service $ServiceName `
            --quiet
    } "Failed to grant IAP access to $IapUserEmail"

    $serviceUrlRaw = gcloud run services describe $ServiceName --project $Project --region $Region --format "value(status.url)" 2>$null
    $serviceUrl = if ($serviceUrlRaw) { [string]$serviceUrlRaw.Trim() } else { "" }
    if ($LASTEXITCODE -ne 0 -or -not $serviceUrl) { throw "Cloud Run service URL was not returned" }

    Write-Host ""
    Write-Host "MOBILE_V8_DEPLOY=SUCCESS"
    Write-Host "SERVICE_URL=$serviceUrl"
    Write-Host "JOB_NAME=$JobName"
    Write-Host "SERVICE_ENV_V8_JOB_NAME=$JobName"
    Write-Host "IAP_USER=$IapUserEmail"
    Write-Host "PINNED_V8_SHA=$PinnedV8Sha"
    Write-Host "Next: open SERVICE_URL on the phone and run one KOSPI and one KOSDAQ test stock."
}
finally {
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
}
