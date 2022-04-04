Import-Module .\setwindow.psm1
Set-Window -ProcessName $pid -X 20 -Y 20 -Width 800 -Height 400
[console]::Title = "WebHook Server"
python.exe webhook.py