$Action = New-ScheduledTaskAction -Execute 'C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\scripts\run_scheduled_report.bat' -WorkingDirectory 'C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system'

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)

# Task 1: 11:20 Weekdays (Mon-Fri)
$Trigger1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '11:20'
Register-ScheduledTask -TaskName 'StockBot_1120_Intraday' -Action $Action -Trigger $Trigger1 -Settings $Settings -Force

# Task 2: 15:35 Weekdays (Mon-Fri)
$Trigger2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:35'
Register-ScheduledTask -TaskName 'StockBot_1535_PostMarket' -Action $Action -Trigger $Trigger2 -Settings $Settings -Force

Write-Host "Tasks Registered Successfully!"
