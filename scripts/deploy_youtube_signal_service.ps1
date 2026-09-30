param(
    [string]$Project = "stock-monitoring-prod",
    [string]$Region = "asia-northeast3",
    [string]$Repository = "stockbot-repo",
    [string]$Service = "youtube-signal-service",
    [string]$EnvFile = "C:\Users\jooji\.env"
)

$ErrorActionPreference = "Stop"

function Get-EnvValue([string]$Name) {
    if (Test-Path -LiteralPath $EnvFile) {
        $line = Get-Content -LiteralPath $EnvFile | Where-Object {
            $_ -match "^\s*$([regex]::Escape($Name))\s*="
        } | Select-Object -Last 1
        if ($line) {
            return (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
        }
    }
    return [Environment]::GetEnvironmentVariable($Name)
}

function Ensure-Secret([string]$SecretName, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing value for $SecretName in $EnvFile/environment."
    }
    & gcloud secrets describe $SecretName --project $Project *> $null
    if ($LASTEXITCODE -ne 0) {
        & gcloud secrets create $SecretName --replication-policy=automatic --project $Project | Out-Null
    }
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmp, $Value, (New-Object System.Text.UTF8Encoding($false)))
        & gcloud secrets versions add $SecretName --data-file=$tmp --project $Project | Out-Null
    }
    finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-ApiToken {
    $token = Get-EnvValue "YOUTUBE_SIGNAL_API_TOKEN"
    if (-not [string]::IsNullOrWhiteSpace($token)) { return $token }

    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    Add-Content -LiteralPath $EnvFile -Value "`nYOUTUBE_SIGNAL_API_TOKEN=$token"
    return $token
}

Write-Host "=== YouTube Signal Service: Cloud Run + Static Egress ==="
Write-Host "Project=$Project Region=$Region Service=$Service"

& gcloud config set project $Project | Out-Null
& gcloud services enable run.googleapis.com compute.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com --project $Project | Out-Null

# Artifact Registry repository (existing stockbot-repo is reused when present).
& gcloud artifacts repositories describe $Repository --location=$Region --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud artifacts repositories create $Repository --repository-format=docker --location=$Region --project $Project | Out-Null
}

# Static outbound IP + Cloud NAT. Cloud Run Direct VPC egress uses the default VPC/subnet.
$AddressName = "youtube-signal-egress"
$RouterName = "youtube-signal-router"
$NatName = "youtube-signal-nat"

& gcloud compute networks describe default --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Default VPC does not exist. Stop here and create/choose a VPC before deployment."
}

& gcloud compute addresses describe $AddressName --region=$Region --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud compute addresses create $AddressName --region=$Region --project $Project | Out-Null
}
$StaticIp = (& gcloud compute addresses describe $AddressName --region=$Region --project $Project --format="value(address)").Trim()

& gcloud compute routers describe $RouterName --region=$Region --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud compute routers create $RouterName --network=default --region=$Region --project $Project | Out-Null
}

& gcloud compute routers nats describe $NatName --router=$RouterName --region=$Region --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud compute routers nats create $NatName `
        --router=$RouterName `
        --region=$Region `
        --nat-external-ip-pool=$AddressName `
        --nat-all-subnet-ip-ranges `
        --project=$Project | Out-Null
}

Write-Host ""
Write-Host "Kiwoom 등록용 고정 IP: $StaticIp" -ForegroundColor Cyan
Write-Host "※ 이 IP를 Kiwoom REST API 허용 IP에 등록해야 /confirm이 정상 동작합니다." -ForegroundColor Yellow

$KiwoomKey = Get-EnvValue "KIWOOM_APP_KEY"
$KiwoomSecret = Get-EnvValue "KIWOOM_APP_SECRET"
$GmailPassword = Get-EnvValue "GMAIL_APP_PASSWORD"
$GmailUser = Get-EnvValue "GMAIL_USER"
if ([string]::IsNullOrWhiteSpace($GmailUser)) { $GmailUser = Get-EnvValue "GMAIL_SENDER_EMAIL" }
if ([string]::IsNullOrWhiteSpace($GmailUser)) { $GmailUser = Get-EnvValue "USER_EMAIL" }
if ([string]::IsNullOrWhiteSpace($GmailUser)) { throw "GMAIL_USER/GMAIL_SENDER_EMAIL missing." }
$ApiToken = Ensure-ApiToken

Ensure-Secret "youtube-kiwoom-app-key" $KiwoomKey
Ensure-Secret "youtube-kiwoom-app-secret" $KiwoomSecret
Ensure-Secret "youtube-gmail-app-password" $GmailPassword
Ensure-Secret "youtube-signal-api-token" $ApiToken

# Dedicated runtime service account; it only needs Secret Manager read access.
$ServiceAccountName = "youtube-signal-sa"
$ServiceAccountEmail = "$ServiceAccountName@$Project.iam.gserviceaccount.com"
& gcloud iam service-accounts describe $ServiceAccountEmail --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud iam service-accounts create $ServiceAccountName --display-name="YouTube Signal Service" --project $Project | Out-Null
}
& gcloud projects add-iam-policy-binding $Project `
    --member="serviceAccount:$ServiceAccountEmail" `
    --role="roles/secretmanager.secretAccessor" `
    --condition=None `
    --quiet | Out-Null

$Image = "$Region-docker.pkg.dev/$Project/$Repository/youtube-signal-service:latest"
Write-Host "Building image..."
& gcloud builds submit . `
    --config=cloudbuild.youtube-signal.yaml `
    --substitutions="_REGION=$Region,_REPOSITORY=$Repository,_IMAGE=youtube-signal-service,_TAG=latest" `
    --project=$Project
if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed." }

Write-Host "Deploying Cloud Run service..."
& gcloud run deploy $Service `
    --image=$Image `
    --region=$Region `
    --platform=managed `
    --allow-unauthenticated `
    --service-account=$ServiceAccountEmail `
    --network=default `
    --subnet=default `
    --vpc-egress=all-traffic `
    --set-env-vars="STOCKBOT_CLOUD_MODE=1,GMAIL_USER=$GmailUser,GMAIL_SENDER_EMAIL=$GmailUser,RECIPIENT_GMAIL=$GmailUser,TZ=Asia/Seoul" `
    --set-secrets="KIWOOM_APP_KEY=youtube-kiwoom-app-key:latest,KIWOOM_APP_SECRET=youtube-kiwoom-app-secret:latest,GMAIL_APP_PASSWORD=youtube-gmail-app-password:latest,YOUTUBE_SIGNAL_API_TOKEN=youtube-signal-api-token:latest" `
    --min-instances=0 `
    --max-instances=2 `
    --memory=512Mi `
    --cpu=1 `
    --timeout=60 `
    --quiet `
    --project=$Project
if ($LASTEXITCODE -ne 0) { throw "Cloud Run deploy failed." }

$ServiceUrl = (& gcloud run services describe $Service --region=$Region --project=$Project --format="value(status.url)").Trim()

# Token is copied locally for Apps Script Script Properties; it is not printed.
try {
    Set-Clipboard -Value $ApiToken
    $ClipboardNote = "API token copied to Windows clipboard (do not paste it into chat)."
} catch {
    $ClipboardNote = "API token remains in $EnvFile as YOUTUBE_SIGNAL_API_TOKEN."
}

Write-Host ""
Write-Host "=== DEPLOY COMPLETE ===" -ForegroundColor Green
Write-Host "Service URL : $ServiceUrl"
Write-Host "Static IP  : $StaticIp"
Write-Host $ClipboardNote
Write-Host "Health     : $ServiceUrl/health"
Write-Host ""
Write-Host "Next: register Static IP with Kiwoom, then set SIGNAL_API_URL=$ServiceUrl and SIGNAL_API_TOKEN in Apps Script Script Properties."
