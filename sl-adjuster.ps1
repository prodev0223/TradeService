Import-Module .\setwindow.psm1
Set-Window -ProcessName $pid -X 820 -Y 20 -Width 800 -Height 400
[console]::Title = "SL Adjuster Service"
python.exe sl-adjuster.py