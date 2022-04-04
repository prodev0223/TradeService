Import-Module .\setwindow.psm1
Set-Window -ProcessName $pid -X 20 -Y 420 -Width 1600 -Height 400
[console]::Title = "Queue Service"
python.exe queue_service.py | Out-Host