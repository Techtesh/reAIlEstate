Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*E:\Projects\Workspace-publish\*' -and
    $_.CommandLine -match 'manage\.py.*(run_extraction_dispatcher|process_outbox)'
  } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }

<#
Service  State   Port ProcessIds
-------  -----   ---- ----------
admin    running 8002 28516,28316
builder  running 8003 29020,28024
buyer    running 8004 26628,22388
worker   running    - 24344,21760
frontend stopped 5173 
#>
