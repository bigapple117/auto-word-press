# 한국 통로 노트북(윈도우 10, 24시간 켜둔 멜론 노트북) 설정 스크립트.
# 노트북이 오라클1에 먼저 접속해 오라클1 안쪽 127.0.0.1:18081에 '페이지 가져오기 통로(SOCKS)'를 열어 둔다.
# 오라클2는 해외 접속을 막은 한국 기관 사이트를 이 통로로 읽는다(노트북의 한국 가정용 인터넷 주소로 나감).
# 이 파일은 틀이다. __PRIVATE_KEY__·__KNOWN_HOST__는 배포할 때만 채워 한 번 받으면 지우는 임시 주소로 준다(Git에 키 없음).
# 열쇠 제한(오라클1 authorized_keys): restrict, 18081번 역방향 통로만, 정방향 통로·명령 실행 불가.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# 작업 스케줄러 '로그인 시 실행' 등록에 관리자 권한이 필요하다
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host '[실패] PowerShell을 "관리자 권한으로 실행"해서 다시 붙여넣어 주세요.'; return }
$dir = Join-Path $env:USERPROFILE '.korea-relay'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$ssh = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source
if (-not $ssh) { Write-Host '[실패] 윈도우 기본 SSH(OpenSSH 클라이언트)가 없습니다. 설정 > 앱 > 선택적 기능에서 OpenSSH 클라이언트를 추가해 주세요.'; return }

# 1) 열쇠 저장 + 본인만 읽게 권한 정리(권한이 넓으면 SSH가 열쇠를 거부함)
$key = Join-Path $dir 'key'
if (Test-Path $key) { icacls $key /grant:r "$($env:USERNAME):(F)" | Out-Null }   # 다시 실행할 때 덮어쓸 수 있게
Set-Content -Path $key -Value @'
__PRIVATE_KEY__
'@ -Encoding Ascii -NoNewline:$false
icacls $key /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null

# 2) 오라클1 서버 신원(가짜 서버에 붙지 않게 미리 고정)
$kh = Join-Path $dir 'known_hosts'
Set-Content -Path $kh -Value '__KNOWN_HOST__' -Encoding Ascii

# 3) 끊기면 30초 뒤 다시 붙는 통로 스크립트
$run = Join-Path $dir 'run.ps1'
Set-Content -Path $run -Encoding UTF8 -Value @"
while (`$true) {
  & '$ssh' -N -i '$key' -o IdentitiesOnly=yes -o UserKnownHostsFile='$kh' -o StrictHostKeyChecking=yes ``
    -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 ``
    -R 127.0.0.1:18081 ubuntu@155.248.170.127
  Start-Sleep -Seconds 30
}
"@

# 4) 로그인할 때마다 숨은 창으로 자동 실행(작업 스케줄러), 지금도 바로 시작
$name = 'Korea Relay Tunnel'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$run`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name

# 5) 확인: 노트북에서 마이홈포털이 열리는지(해외 차단 여부), 통로가 붙었는지
foreach ($u in 'https://www.myhome.go.kr/', 'https://nhuf.molit.go.kr/') {
  try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 20; Write-Host "[확인] $u 열림($($r.StatusCode))" }
  catch { Write-Host "[확인] $u 안 열림: $($_.Exception.Message)" }
}
Start-Sleep -Seconds 8
$p = Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" | Where-Object { $_.CommandLine -like '*18081*' }
if ($p) { Write-Host '[완료] 통로가 열렸습니다. 이 창은 닫아도 됩니다.' } else { Write-Host '[주의] 통로 프로그램이 아직 안 보입니다. 이 화면을 캡처해 알려 주세요.' }
