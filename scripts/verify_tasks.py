import subprocess
import json

tasks = ['StockBot_1120_Intraday', 'StockBot_1535_PostMarket']
for tn in tasks:
    ps_cmd = (
        "Get-ScheduledTask -TaskName '" + tn + "' | Select-Object TaskName, State, "
        "@{N='WakeToRun';E={$_.Settings.WakeToRun}}, "
        "@{N='BatteryDisallow';E={$_.Settings.DisallowStartIfOnBatteries}}, "
        "@{N='BatteryStop';E={$_.Settings.StopIfGoingOnBatteries}}, "
        "@{N='ExecutionLimit';E={$_.Settings.ExecutionTimeLimit}}, "
        "@{N='RestartCount';E={$_.Settings.RestartCount}}, "
        "@{N='RestartInterval';E={$_.Settings.RestartInterval}}, "
        "@{N='Execute';E={$_.Actions.Execute}} | ConvertTo-Json"
    )
    out = subprocess.check_output(['powershell', '-Command', ps_cmd], text=True)
    print(f"=== {tn} Settings ===")
    print(out.strip())
    
    ps_info = "Get-ScheduledTaskInfo -TaskName '" + tn + "' | Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult | ConvertTo-Json"
    out_info = subprocess.check_output(['powershell', '-Command', ps_info], text=True)
    print(f"=== {tn} Next Run Info ===")
    print(out_info.strip())
    print()
