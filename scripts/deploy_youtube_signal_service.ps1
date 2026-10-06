param(
    [string]$Project = "stock-monitoring-prod",
    [string]$Region = "asia-northeast3",
    [string]$Repository = "stockbot-repo",
    [string]$Service = "youtube-signal-service",
    [string]$EnvFile = "C:\Users\jooji\.env"
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.x can surface ordinary gcloud stderr status lines as
# NativeCommandError records. Use gcloud.cmd directly and make exit code the
# source of truth instead of PowerShell's stderr classification.
$GcloudExe = if ($IsWindows -eq $false) {
    (Get-Command gcloud -ErrorAction Stop).Source
} else {
    $candidate = "C:\Users\jooji\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path -LiteralPath $candidate) { $candidate }
    else { (Get-Command gcloud.cmd -ErrorAction Stop).Source }
}

function Invoke-Gcloud {
    param(
        [Parameter(Mandatory=$true)][string[]]$Args,
        [switch]$CaptureStdout,
        [switch]$QuietOutput
    )

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($CaptureStdout) {
            $stdout = & $GcloudExe @Args 2>$null
            $code = $LASTEXITCODE
        }
        elseif ($QuietOutput) {
            & $GcloudExe @Args *> $null
            $code = $LASTEXITCODE
        }
        else {
            & $GcloudExe @Args
            $code = $LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = $oldEap
    }

    if ($code -ne 0) {
        throw "gcloud failed ($code): gcloud $($Args -join ' ')"
    }

    if ($CaptureStdout) {
        return $stdout
    }
}

function Test-Gcloud {
    param([Parameter(Mandatory=$true)][string[]]$Args)
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $GcloudExe @Args *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $oldEap
    }
}

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

    if (-not (Test-Gcloud -Args @('secrets','describe',$SecretName,'--project',$Project))) {
        Invoke-Gcloud -Args @('secrets','create',$SecretName,'--replication-policy=automatic','--project',$Project) -QuietOutput
    }

    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmp, $Value, (New-Object System.Text.UTF8Encoding($false)))
        Invoke-Gcloud -Args @('secrets','versions','add',$SecretName,"--data-file=$tmp",'--project',$Project) -QuietOutput
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

function Wait-ServiceAccount([string]$ServiceAccountEmail) {
    for ($i = 1; $i -le 12; $i++) {
        if (Test-Gcloud -Args @('iam','service-accounts','describe',$ServiceAccountEmail,'--project',$Project)) {
            return
        }
        Start-Sleep -Seconds 5
    }
    throw "Service account propagation timed out: $ServiceAccountEmail"
}

function Grant-SecretAccessor([string]$SecretName, [string]$ServiceAccountEmail) {
    $member = "serviceAccount:$ServiceAccountEmail"
    for ($i = 1; $i -le 12; $i++) {
        try {
            Invoke-Gcloud -Args @(
                'secrets','add-iam-policy-binding',$SecretName,
                "--member=$member",
                '--role=roles/secretmanager.secretAccessor',
                '--project',$Project,
                '--quiet'
            ) -QuietOutput
            return
        }
        catch {
            if ($i -eq 12) { throw }
            Start-Sleep -Seconds 5
        }
    }
}

Write-Host "=== YouTube Signal Service: Cloud Run + Static Egress ==="
Write-Host "Project=$Project Region=$Region Service=$Service"

Invoke-Gcloud -Args @('config','set','project',$Project) -QuietOutput
Invoke-Gcloud -Args @(
    'services','enable',
    'run.googleapis.com','compute.googleapis.com','cloudbuild.googleapis.com',
    'artifactregistry.googleapis.com','secretmanager.googleapis.com','iam.googleapis.com',
    '--project',$Project
) -QuietOutput

# Artifact Registry repository (reuse when present).
if (-not (Test-Gcloud -Args @('artifacts','repositories','describe',$Repository,'--location',$Region,'--project',$Project))) {
    Invoke-Gcloud -Args @(
        'artifacts','repositories','create',$Repository,
        '--repository-format=docker','--location',$Region,'--project',$Project
    ) -QuietOutput
}

# Static outbound IP + Cloud NAT for Cloud Run Direct VPC egress.
$AddressName = "youtube-signal-egress"
$RouterName = "youtube-signal-router"
$NatName = "youtube-signal-nat"

if (-not (Test-Gcloud -Args @('compute','networks','describe','default','--project',$Project))) {
    throw "Default VPC does not exist. Stop here and create/choose a VPC before deployment."
}

if (-not (Test-Gcloud -Args @('compute','addresses','describe',$AddressName,'--region',$Region,'--project',$Project))) {
    Invoke-Gcloud -Args @('compute','addresses','create',$AddressName,'--region',$Region,'--project',$Project) -QuietOutput
}
$StaticIp = ((Invoke-Gcloud -Args @(
    'compute','addresses','describe',$AddressName,'--region',$Region,'--project',$Project,'--format=value(address)'
) -CaptureStdout) | Select-Object -First 1).ToString().Trim()

if (-not (Test-Gcloud -Args @('compute','routers','describe',$RouterName,'--region',$Region,'--project',$Project))) {
    Invoke-Gcloud -Args @('compute','routers','create',$RouterName,'--network=default','--region',$Region,'--project',$Project) -QuietOutput
}

if (-not (Test-Gcloud -Args @('compute','routers','nats','describe',$NatName,'--router',$RouterName,'--region',$Region,'--project',$Project))) {
    Invoke-Gcloud -Args @(
        'compute','routers','nats','create',$NatName,
        "--router=$RouterName","--region=$Region",
        "--nat-external-ip-pool=$AddressName",
        '--nat-all-subnet-ip-ranges',"--project=$Project"
    ) -QuietOutput
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
$JevApiKey = Get-EnvValue "JEV_API_KEY"
if ([string]::IsNullOrWhiteSpace($JevApiKey)) {
    throw "JEV_API_KEY is required in $EnvFile/environment for Jev alert-gate deployment."
}

$SecretMap = [ordered]@{
    'youtube-kiwoom-app-key'       = $KiwoomKey
    'youtube-kiwoom-app-secret'    = $KiwoomSecret
    'youtube-gmail-app-password'   = $GmailPassword
    'youtube-signal-api-token'     = $ApiToken
    'youtube-jev-api-key'          = $JevApiKey
}
foreach ($entry in $SecretMap.GetEnumerator()) {
    Ensure-Secret $entry.Key $entry.Value
}

# Dedicated runtime service account. Wait for IAM propagation, then grant
# Secret Accessor on each secret directly (avoids project-policy condition issues).
$ServiceAccountName = "youtube-signal-sa"
$ServiceAccountEmail = "$ServiceAccountName@$Project.iam.gserviceaccount.com"
if (-not (Test-Gcloud -Args @('iam','service-accounts','describe',$ServiceAccountEmail,'--project',$Project))) {
    Invoke-Gcloud -Args @(
        'iam','service-accounts','create',$ServiceAccountName,
        '--display-name=YouTube Signal Service','--project',$Project
    ) -QuietOutput
}
Wait-ServiceAccount $ServiceAccountEmail
foreach ($secretName in $SecretMap.Keys) {
    Grant-SecretAccessor $secretName $ServiceAccountEmail
}

$Image = "$Region-docker.pkg.dev/$Project/$Repository/youtube-signal-service:latest"
Write-Host "Building image..."
Invoke-Gcloud -Args @(
    'builds','submit','.',
    '--config=cloudbuild.youtube-signal.yaml',
    "--substitutions=_REGION=$Region,_REPOSITORY=$Repository,_IMAGE=youtube-signal-service,_TAG=latest",
    '--project',$Project
)

Write-Host "Deploying Cloud Run service..."
Invoke-Gcloud -Args @(
    'run','deploy',$Service,
    "--image=$Image",
    "--region=$Region",
    '--platform=managed',
    '--allow-unauthenticated',
    "--service-account=$ServiceAccountEmail",
    '--network=default',
    '--subnet=default',
    '--vpc-egress=all-traffic',
    "--set-env-vars=STOCKBOT_CLOUD_MODE=1,GMAIL_USER=$GmailUser,GMAIL_SENDER_EMAIL=$GmailUser,RECIPIENT_GMAIL=$GmailUser,TZ=Asia/Seoul,JEV_ALERT_GATE_MODE=SHADOW,JEV_ALERT_GATE_YOUTUBE_MODE=ACTIVE,JEV_ALERT_GATE_ETF_MODE=ACTIVE,JEV_ALERT_GATE_HELD_MODE=ACTIVE,JEV_ALERT_GATE_SEND_THRESHOLD=0.70,JEV_ALERT_GATE_DETERIORATION_THRESHOLD=0.70,JEV_ALERT_GATE_MIN_URGENCY=2.0",
    '--set-secrets=KIWOOM_APP_KEY=youtube-kiwoom-app-key:latest,KIWOOM_APP_SECRET=youtube-kiwoom-app-secret:latest,GMAIL_APP_PASSWORD=youtube-gmail-app-password:latest,YOUTUBE_SIGNAL_API_TOKEN=youtube-signal-api-token:latest,JEV_API_KEY=youtube-jev-api-key:latest',
    '--min-instances=0','--max-instances=2','--memory=512Mi','--cpu=1','--timeout=60','--quiet',
    '--project',$Project
)

$ServiceUrl = ((Invoke-Gcloud -Args @(
    'run','services','describe',$Service,'--region',$Region,'--project',$Project,'--format=value(status.url)'
) -CaptureStdout) | Select-Object -First 1).ToString().Trim()

# Token is copied locally for Apps Script Script Properties; it is never printed.
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
