# ==============================================================================
# V1.0 Shadow Production Windows Task Scheduler Registration Script
# ==============================================================================

$ErrorActionPreference = "Stop"

$PythonExe = "C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe"
$WorkingDir = "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"

Write-Host "=================================================="
Write-Host " [V1.0 Shadow Production] Windows Task 등록 시작..."
Write-Host "=================================================="

# 1. Shadow Scan Timetable Configuration
$ShadowSchedule = @(
    @{ Name = "Shadow_HealthCheck_0840"; Time = "08:40"; Task = "PRE_MARKET_HEALTH_CHECK"; StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_0950";        Time = "09:50"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1035";        Time = "10:35"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1135";        Time = "11:35"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1205";        Time = "12:05"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1250";        Time = "12:50"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1335";        Time = "13:35"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1420";        Time = "14:20"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Scan_1505";        Time = "15:05"; Task = "INTRADAY_SHADOW_SCAN";      StartWhenAvail = $false },
    @{ Name = "Shadow_Maintenance_1610"; Time = "16:10"; Task = "OUTCOME_AND_JOURNAL_MAINTENANCE"; StartWhenAvail = $true }
)

foreach ($item in $ShadowSchedule) {
    $TaskName = $item.Name
    $TaskTime = $item.Time
    $TaskArg = "-m src.runtime.runtime_scheduler --task $($item.Task)"
    $StartAvail = $item.StartWhenAvail

    $Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $TaskArg -WorkingDirectory $WorkingDir
    
    # Trigger: Mon - Fri at specified time
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TaskTime

    # Settings: IgnoreNew if running, StartWhenAvailable policy
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable:$StartAvail `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

    # Register Task
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host " [+] Registered: $TaskName ($TaskTime KST, Task=$($item.Task), StartWhenAvail=$StartAvail)"
}

# 2. Existing Task State Adjustment
Write-Host "`n[기존 운영 Task 상태 조정]"

# 11:20 Task 복구 (StockBot_Intraday_1120)
try {
    $task1120 = Get-ScheduledTask -TaskName "StockBot_Intraday_1120" -ErrorAction SilentlyContinue
    if ($task1120) {
        Enable-ScheduledTask -TaskName "StockBot_Intraday_1120" | Out-Null
        Write-Host " [+] Enabled: StockBot_Intraday_1120 (11:20 KST 장중 보유종목 감시 정상 복구)"
    }
} catch {
    Write-Warning "StockBot_Intraday_1120 활성화 중 경고: $_"
}

# 15:35 중복 작업 단일화 (StockAnalysisDailyReport 유지, Daily_Stock_Report_1535 비활성화)
try {
    $task1535Canonical = Get-ScheduledTask -TaskName "StockAnalysisDailyReport" -ErrorAction SilentlyContinue
    if ($task1535Canonical) {
        Enable-ScheduledTask -TaskName "StockAnalysisDailyReport" | Out-Null
        Write-Host " [+] Canonical Enabled: StockAnalysisDailyReport (15:35 KST 장마감 정밀 리포트 유지)"
    }

    $task1535Dup = Get-ScheduledTask -TaskName "Daily_Stock_Report_1535" -ErrorAction SilentlyContinue
    if ($task1535Dup) {
        Disable-ScheduledTask -TaskName "Daily_Stock_Report_1535" | Out-Null
        Write-Host " [-] Duplicate Disabled: Daily_Stock_Report_1535 (중복 메일 발송 방지)"
    }
} catch {
    Write-Warning "15:35 작업 정리 중 경고: $_"
}

Write-Host "=================================================="
Write-Host " [V1.0 Shadow Production] Windows Task 등록 완료!"
Write-Host "=================================================="
