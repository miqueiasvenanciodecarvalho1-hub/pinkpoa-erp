# PinkPoa — instalador do robô do Audience (rodar na máquina onde o robô vai trabalhar, com o usuário dela).
# O que faz:
#   1. cria C:\PINKPOA (EXPORT, EXTRAS, logs) e baixa os scripts de https://www.pinkpoafinanceiro.com.br/robo/
#   2. pergunta empresa/usuário/filial do Audience -> C:\PINKPOA\robo.conf
#   3. pede a senha do Audience -> C:\PINKPOA\audience.cred (criptografada pelo Windows; ninguém vê)
#   4. agenda a tarefa diária "PinkPoa - Robo Audience" (horário perguntado; padrão 06:00)
# Pode rodar de novo a qualquer momento: atualiza os scripts e só pergunta o que você quiser trocar.
# Manual completo: app PinkPoa > Automação > "Robô do Audience — manual".
$ErrorActionPreference = 'Stop'
$base  = 'https://www.pinkpoafinanceiro.com.br/robo/'
$pasta = 'C:\PINKPOA'
$tarefa = 'PinkPoa - Robo Audience'
function Titulo($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Pergunta($texto, $padrao) { $r = Read-Host ("{0}{1}" -f $texto, $(if ($padrao) { " [$padrao]" } else { '' })); if ([string]::IsNullOrWhiteSpace($r)) { $padrao } else { $r.Trim() } }

Titulo '1/4 Pastas e scripts'
New-Item -ItemType Directory -Force $pasta, "$pasta\EXPORT", "$pasta\EXTRAS", "$pasta\logs" | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
foreach ($f in 'gerar_tudo.ps1', 'login_audience.ps1', 'instalar_robo.ps1', 'robo.conf.exemplo') {
  $alvo = Join-Path $pasta $f
  if (Test-Path $alvo) { Copy-Item $alvo "$alvo.anterior" -Force }
  Invoke-WebRequest ($base + $f + '?v=' + (Get-Date -Format yyyyMMddHHmmss)) -OutFile $alvo -UseBasicParsing
  Write-Host "  baixado $alvo"
}
if (-not (Test-Path 'C:\Audience\sp2tccli.exe')) { Write-Host '  ATENÇÃO: o Audience não está instalado em C:\Audience (instale antes de agendar).' -ForegroundColor Yellow }

Titulo '2/4 Dados do Audience (robo.conf)'
$cfg = @{ Empresa = ''; Usuario = ''; Filial = '1' }
$conf = Join-Path $pasta 'robo.conf'
if (Test-Path $conf) { Get-Content $conf | Where-Object { $_ -match '^\s*(\w+)\s*=\s*(.*)$' } | ForEach-Object { $cfg[$matches[1]] = $matches[2].Trim() } }
$cfg.Empresa = Pergunta 'Código da empresa no Audience' $cfg.Empresa
$cfg.Usuario = Pergunta 'Código do usuário no Audience' $cfg.Usuario
$cfg.Filial  = Pergunta 'Filial' $cfg.Filial
if (-not $cfg.Empresa -or -not $cfg.Usuario) { throw 'Empresa e usuário são obrigatórios.' }
@('# PinkPoa — robô do Audience (local; não publicar)', "Empresa=$($cfg.Empresa)", "Usuario=$($cfg.Usuario)", "Filial=$($cfg.Filial)") | Set-Content $conf -Encoding UTF8
Write-Host "  gravado $conf"

Titulo '3/4 Senha do Audience'
$cred = Join-Path $pasta 'audience.cred'
$trocar = if (Test-Path $cred) { (Pergunta 'Já existe senha gravada. Trocar? (s/n)' 'n') -match '^s' } else { $true }
if ($trocar) {
  $s = Read-Host 'Senha do Audience (não aparece na tela)' -AsSecureString
  $s | ConvertFrom-SecureString | Set-Content $cred
  Write-Host "  gravada em $cred (só este usuário do Windows, nesta máquina, consegue ler)"
}

Titulo '4/4 Agendamento diário'
$hora = Pergunta 'Horário diário (HH:mm)' '06:00'
$cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File $pasta\gerar_tudo.ps1"
schtasks /Create /TN $tarefa /SC DAILY /ST $hora /IT /F /TR $cmd | Out-Host
Write-Host "  tarefa '$tarefa' todo dia às $hora (roda só com este usuário logado e a tela desbloqueada)"

Write-Host "`nPronto. Falta, uma vez só, no Chrome desta máquina:" -ForegroundColor Green
Write-Host '  - entrar no PinkPoa (www.pinkpoafinanceiro.com.br)'
Write-Host "  - Importação > Conectar pasta > $pasta\EXPORT > 'Permitir em todas as visitas'"
Write-Host "Testar agora: schtasks /Run /TN `"$tarefa`"   (não mexer no mouse/teclado; log em $pasta\logs)"
