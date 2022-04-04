Start-Process powershell -ArgumentList "-noexit","-command .\webhook.ps1"
Start-Process powershell -ArgumentList "-noexit","-command .\queue.ps1"
Start-Process powershell -ArgumentList "-noexit","-command .\sl-adjuster.ps1"
Stop-Process -Id $PID