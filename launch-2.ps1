Start-Process powershell -ArgumentList "-noexit","-command .\webhook.ps1"
Start-Process powershell -ArgumentList "-noexit","-command .\queue.ps1"
Stop-Process -Id $PID