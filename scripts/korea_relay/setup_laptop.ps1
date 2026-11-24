# 한국 통로 노트북(윈도우 10, 24시간 켜둔 멜론 노트북) 설정 스크립트.
# 노트북이 오라클1에 먼저 접속해 오라클1 안쪽 127.0.0.1:18081에 '페이지 가져오기 통로(SOCKS)'를 열어 둔다.
# 오라클2는 해외 접속을 막은 한국 기관 사이트를 이 통로로 읽는다(노트북의 한국 가정용 인터넷 주소로 나감).
# __KNOWN_HOST__(오라클1 서버 공개 신원)만 배포 때 채운다. 열쇠는 노트북이 만들고 공개 열쇠만 Claude가 오라클1에 등록한다.
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

# 다시 실행할 때: 돌던 통로를 먼저 멈춘다
$name = 'Korea Relay Tunnel'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" | Where-Object { $_.CommandLine -like '*18081*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name   # PC이름\사용자 (같은 이름 다른 계정과 헷갈리지 않게)

# 1) 노트북에서 열쇠를 직접 만든다(비밀 열쇠는 노트북 밖으로 나가지 않음). 공개 열쇠만 오라클2 Claude에게 전달.
$key = Join-Path $dir 'key'
if (-not (Test-Path $key)) {
  & $ssh.Replace('ssh.exe', 'ssh-keygen.exe') -q -t ed25519 -N '""' -C korea-relay-laptop -f $key
  if ($LASTEXITCODE -ne 0) { Write-Host '[실패] 열쇠를 만들지 못했습니다. 이 화면을 캡처해 알려 주세요.'; return }
}
icacls $key /setowner "$me" | Out-Null
icacls $key /inheritance:r /grant:r "$($me):(R)" | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host '[실패] 열쇠 파일 권한을 정리하지 못했습니다. 이 화면을 캡처해 알려 주세요.'; return }
$pub = (Get-Content "$key.pub" -Raw).Trim()

# 2) 오라클1 서버 신원(가짜 서버에 붙지 않게 미리 고정)
$kh = Join-Path $dir 'known_hosts'
Set-Content -Path $kh -Value '__KNOWN_HOST__' -Encoding Ascii

# 3) 끊기면 30초 뒤 다시 붙는 통로 스크립트(인수는 배열로 넘겨 따옴표·빈칸 문제 없게)
# OpenSSH 8.7 이상이면 통로로 갈 수 있는 곳을 웹(80·443 포트)으로만 제한한다(윈도우10 기본 8.1은 이 설정을 모름)
$ver = (cmd /c "`"$ssh`" -V 2>&1" | Out-String)   # PowerShell 5.1은 2>&1 오류 줄을 Stop에서 예외로 바꾸므로 cmd로 받음
$limit = ''
if ($ver -match 'OpenSSH_for_Windows_(\d+)\.(\d+)' -and ([int]$Matches[1] -gt 8 -or ([int]$Matches[1] -eq 8 -and [int]$Matches[2] -ge 7))) {
  $limit = "'-o', 'PermitRemoteOpen=*:80 *:443', "
}
$run = Join-Path $dir 'run.ps1'
Set-Content -Path $run -Encoding UTF8 -Value @"
`$opts = @($limit'-N', '-i', '$key', '-o', 'IdentitiesOnly=yes', '-o', 'UserKnownHostsFile=$kh', '-o', 'StrictHostKeyChecking=yes',
  '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
  '-R', '127.0.0.1:18081', 'ubuntu@155.248.170.127')
while (`$true) {
  & '$ssh' @opts
  Start-Sleep -Seconds 30
}
"@

# 4) 로그인할 때마다 숨은 창으로 자동 실행(작업 스케줄러), 지금도 바로 시작
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$run`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name

# 5) 확인: 노트북에서 마이홈포털이 열리는지(해외 차단 여부), 통로가 붙었는지
foreach ($u in 'https://www.myhome.go.kr/', 'https://nhuf.molit.go.kr/') {
  try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 20; Write-Host "[확인] $u 열림($($r.StatusCode))" }
  catch { Write-Host "[확인] $u 안 열림: $($_.Exception.Message)" }
}
Start-Sleep -Seconds 8
$p = Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" | Where-Object { $_.CommandLine -like '*18081*' }
Write-Host "[정보] SSH 버전: $($ver.Trim())  웹 포트 제한: $(if ($limit) {'켬'} else {'못 켬(구버전)'})"
if (-not $p) { Write-Host '[주의] 통로 프로그램이 아직 안 보입니다. 이 화면을 캡처해 알려 주세요.' }
Set-Clipboard -Value $pub
Write-Host ''
Write-Host '[마지막] 아래 공개 열쇠 한 줄(비밀 아님)을 복사해 두었습니다. Claude 대화에 그대로 붙여넣어 주세요.'
Write-Host '         등록되면 1분 안에 통로가 저절로 연결됩니다. 이 창은 닫아도 됩니다.'
Write-Host $pub
