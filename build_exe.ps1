param(
  [string]$Entry = "transcribe_desktop.py",
  [string]$Name = "AudioTranscriber"
)

python -m pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name $Name $Entry

Write-Host "`n构建完成。可执行文件在 dist/$Name.exe"
