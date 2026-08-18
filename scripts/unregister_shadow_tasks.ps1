# ==============================================================================
# V1.0 Shadow Production Windows Task Unregistration Script
# ==============================================================================

$TaskNames = @(
    "Shadow_HealthCheck_0840",
    "Shadow_Scan_0950",
    "Shadow_Scan_1035",
    "Shadow_Scan_1135",
    "Shadow_Scan_1205",
    "Shadow_Scan_1250",
    "Shadow_Scan_1335",
    "Shadow_Scan_1420",
    "Shadow_Scan_1505",
    "Shadow_Maintenance_1610"
)

Write-Host "=================================================="
Write-Host " [V1.0 Shadow Production] Windows Task 해제 시작..."
Write-Host "=================================================="

foreach ($t in $TaskNames) {
    try {
        $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false | Out-Null
            Write-Host " [-] Unregistered: $t"
        }
    } catch {
        Write-Warning "Failed to unregister $t: $_"
    }
}

Write-Host "=================================================="
Write-Host " [V1.0 Shadow Production] Windows Task 해제 완료"
Write-Host "=================================================="
