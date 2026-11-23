# PinkPoa — robô do Audience: entra no Audience (VerUP).
# Empresa, usuário e filial vêm de C:\PINKPOA\robo.conf; a senha, de C:\PINKPOA\audience.cred
# (criptografada pelo Windows — só o usuário que a gravou, nesta máquina, consegue ler). Os dois são criados pelo instalar_robo.ps1.
$cfg = @{ Empresa = ''; Usuario = ''; Filial = '1' }
if (Test-Path 'C:\PINKPOA\robo.conf') { Get-Content 'C:\PINKPOA\robo.conf' | Where-Object { $_ -match '^\s*(\w+)\s*=\s*(.*)$' } | ForEach-Object { $cfg[$matches[1]] = $matches[2].Trim() } }
if (-not $cfg.Empresa -or -not $cfg.Usuario) { throw 'Falta C:\PINKPOA\robo.conf (Empresa/Usuario). Rode o instalar_robo.ps1.' }
if (-not (Test-Path 'C:\PINKPOA\audience.cred')) { throw 'Falta a senha (C:\PINKPOA\audience.cred). Rode o instalar_robo.ps1.' }

$ws = New-Object -ComObject WScript.Shell
if ($ws.AppActivate('VerUP')) { Write-Host 'Audience já está aberto.' -ForegroundColor Green; return }
Write-Host 'Abrindo o Audience...' -ForegroundColor Yellow
Start-Process 'C:\Audience\sp2tccli.exe' -WorkingDirectory 'C:\Audience'
$ok = $false
for ($k = 0; $k -lt 60; $k++) { Start-Sleep 1; if ($ws.AppActivate('VerUP')) { $ok = $true; break } }
if (-not $ok) { throw 'O Audience não abriu em 60 segundos.' }
Start-Sleep 4
$ws.SendKeys($cfg.Empresa); Start-Sleep -Milliseconds 500; $ws.SendKeys('{ENTER}'); Start-Sleep 2
$ws.SendKeys($cfg.Usuario); Start-Sleep -Milliseconds 500; $ws.SendKeys('{ENTER}'); Start-Sleep 3
$ok = $false
for ($k = 0; $k -lt 20; $k++) { if ($ws.AppActivate('vpsenha')) { $ok = $true; break }; Start-Sleep -Milliseconds 500 }
if (-not $ok) { throw 'A janela de senha (vpsenha) não apareceu. Parei.' }
$sec = Get-Content 'C:\PINKPOA\audience.cred' | ConvertTo-SecureString
$pw  = [System.Net.NetworkCredential]::new('', $sec).Password
Start-Sleep -Milliseconds 500
$ws.SendKeys($pw); $pw = $null
Start-Sleep -Milliseconds 500
$ws.SendKeys('{F9}')
Start-Sleep 5
for ($k = 0; $k -lt 20; $k++) {
  if ($ws.AppActivate('vpsegu')) { Start-Sleep -Milliseconds 500; $ws.SendKeys('{F9}'); Write-Host 'Aviso vpsegu fechado.'; Start-Sleep 3; break }
  Start-Sleep -Milliseconds 500
}
if ($ws.AppActivate('VerUP')) { Start-Sleep -Milliseconds 500; $ws.SendKeys($cfg.Filial); Start-Sleep -Milliseconds 500; $ws.SendKeys('{ENTER}'); Start-Sleep 4 }
Write-Host "Login concluído (empresa $($cfg.Empresa), filial $($cfg.Filial))." -ForegroundColor Green
