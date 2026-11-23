# PinkPoa — robô do Audience. Baixado de https://www.pinkpoafinanceiro.com.br/robo/ (manual: app > Automação).
# Robô diário Pink Poá: gera todas as exportações do Audience (VerUP) e copia os arquivos para C:\PINKPOA\EXPORT
# (pasta que o app pinkpoafinanceiro.com.br importa em Importação > varredura da pasta).
#
# No fim abre https://www.pinkpoafinanceiro.com.br/#importar, que importa sozinho os arquivos novos.
# Uma vez só, no Chrome: PinkPoa > Importação > Conectar pasta > C:\PINKPOA\EXPORT > "Permitir em todas as visitas".
# Requisitos: PC ligado, Windows DESBLOQUEADO (o robô usa teclado/mouse), tela 1920x1080.
# Não mexer no mouse/teclado enquanto roda (~15 min).
# Log: C:\PINKPOA\logs\AAAA-MM-DD.log  ·  prints de erro: C:\PINKPOA\logs\erro_*.png
param(
  [string[]]$Somente,   # ex.: -Somente vp1252ex,vp1352ex  (testes)
  [switch]$Completo     # roda TODOS os programas (cadastros, produtos, estoques...) em vez da rotina diária
)
# Rotina diária: vp1252ex e vp4254ex de 01/01 do ano atual até hoje; vp1352ex completo
# (o Contas a Receber é a foto da carteira — filtrar por data apagaria títulos antigos ainda em aberto).
$diario = 'vp1252ex', 'vp1352ex', 'vp4254ex'
$inicioAno = '0101' + (Get-Date).Year   # ddMMyyyy digitado na tela vpdatas

$ErrorActionPreference = 'Stop'
# dados da empresa ficam em C:\PINKPOA\robo.conf (criado pelo instalar_robo.ps1; não vai para o site)
$cfg = @{ Empresa = ''; Usuario = ''; Filial = '1' }
if (Test-Path 'C:\PINKPOA\robo.conf') { Get-Content 'C:\PINKPOA\robo.conf' | Where-Object { $_ -match '^\s*(\w+)\s*=\s*(.*)$' } | ForEach-Object { $cfg[$matches[1]] = $matches[2].Trim() } }
if (-not $cfg.Empresa) { throw 'Falta C:\PINKPOA\robo.conf (Empresa=...). Rode o instalar_robo.ps1.' }
$emp     = $cfg.Empresa
$arq     = 'C:\Audience\arq'
$destino = 'C:\PINKPOA\EXPORT'
$extras  = 'C:\PINKPOA\EXTRAS'
$logs    = 'C:\PINKPOA\logs'
New-Item -ItemType Directory -Force $destino, $extras, $logs | Out-Null
$log = Join-Path $logs ((Get-Date -Format 'yyyy-MM-dd') + '.log')
function Log($m) { $l = '{0:HH:mm:ss} {1}' -f (Get-Date), $m; Write-Host $l; Add-Content $log $l -Encoding UTF8 }

# ---------- programas ----------
# Exportação simples: abre, aperta F9=Ok em cada tela (seleções padrão = tudo), espera o zip.
$simples = 'vp1252ex','vp1352ex','vp1155ex','vp1251ex','vp4151ex','vp4251ex','vp3556ex','vp3577ex','vp3351ex','vp3353ex'
# Arquivo do app  <-  programa que gera a versão COMPLETA dele
# (o mesmo nome sai em vários zips; ex.: o vpdpcl1 do vp1155ex é parcial — nunca usar)
$mapa = [ordered]@{
  'vpfatu1.csv'  = 'vp1252ex'; 'vpfatu2.csv' = 'vp1252ex'
  'vpdpcl1.csv'  = 'vp1352ex'; 'vpdpcl2.csv' = 'vp1352ex'
  'vpbanc3.csv'  = 'vp1155ex'; 'vpdocx1.csv' = 'vp1155ex'
  'vpclie1.csv'  = 'vp4151ex'
  'vprepr1.csv'  = 'vp4251ex'
  'vpnatu1.csv'  = 'vp1251ex'
  'vpprod1.csv'  = 'vp3556ex'; 'vpprod4.csv' = 'vp3556ex'
  'vp3577ex.csv' = 'vp3577ex'
}

# ---------- janelas, mouse, teclado ----------
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class PkWin {
  delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EP f, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int x, int y, int d, int e);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  public static Dictionary<string,IntPtr> Janelas() { var r = new Dictionary<string,IntPtr>(); EnumWindows((h,l)=>{ if (IsWindowVisible(h)) { var sb=new StringBuilder(300); GetWindowText(h,sb,300); if (sb.Length>0 && !r.ContainsKey(sb.ToString())) r[sb.ToString()]=h; } return true; }, IntPtr.Zero); return r; }
}
'@ -ErrorAction SilentlyContinue
[PkWin]::SetProcessDPIAware() | Out-Null
$ws = New-Object -ComObject WScript.Shell

function Titulos { [PkWin]::Janelas().Keys }
function Achar($padrao) { Titulos | Where-Object { $_ -like $padrao } | Select-Object -First 1 }
function Clica($x, $y) { [PkWin]::SetCursorPos($x, $y) | Out-Null; Start-Sleep -Milliseconds 150; [PkWin]::mouse_event(2,0,0,0,0); [PkWin]::mouse_event(4,0,0,0,0); Start-Sleep -Milliseconds 300 }
function Ativa($t) { for ($k = 0; $k -lt 20; $k++) { if ($ws.AppActivate($t)) { Start-Sleep -Milliseconds 400; return $true }; Start-Sleep -Milliseconds 500 }; return $false }
function Print($nome) {
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); $f = Join-Path $logs ("erro_{0:yyyyMMdd_HHmmss}_{1}.png" -f (Get-Date), $nome)
  $bmp.Save($f, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose(); Log "print: $f"
}
function FechaViewer { if ($ws.AppActivate('Audience Viewer')) { Start-Sleep -Milliseconds 400; $ws.SendKeys('%{F4}'); Start-Sleep 1 } }
function ApertaF9($titulo) {
  # acha o botão "F9=Ok" da janela e clica nele; sem botão, manda a tecla F9
  $h = [PkWin]::Janelas()[$titulo]; if (-not $h) { return }
  $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
  $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'F9=Ok')
  $bt = $el.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
  Ativa $titulo | Out-Null
  if ($bt) { $r = $bt.Current.BoundingRectangle; Clica ([int]($r.X + $r.Width/2)) ([int]($r.Y + $r.Height/2)) } else { $ws.SendKeys('{F9}') }
}
function AbrePrograma($p) {
  # campo ao lado do botão EXECUTAR (janela principal maximizada em 1920x1080)
  FechaViewer
  $t = Achar 'VerUP*'; if (-not $t) { throw 'Janela principal do Audience não encontrada.' }
  [PkWin]::ShowWindow([PkWin]::Janelas()[$t], 3) | Out-Null   # maximiza
  Ativa $t | Out-Null
  Clica 1343 208
  $ws.SendKeys("{HOME}+{END}$p~"); Start-Sleep 4
  $j = Achar "$p*"; if (-not $j) { Print $p; throw "A tela do $p não abriu." }
  return $j
}
function TemF9($titulo) {
  # telas de seleção/datas têm o botão "F9=Ok"; a tela de andamento (processando) não tem
  $h = [PkWin]::Janelas()[$titulo]; if (-not $h) { return $false }
  $el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
  $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'F9=Ok')
  return [bool]$el.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
}
function EsperaZip($p, $ini, [switch]$AjudaF9) {
  # espera o zip; enquanto houver janela do programa/vpregix, está processando.
  $parada = 0; $f9 = 0; $nada = 0
  while ($true) {
    Start-Sleep 5
    $z = Get-ChildItem $arq -Filter "$($emp)_*_$($p)_*.zip" | Where-Object { $_.LastWriteTime -gt $ini } | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($z) {
      # espera a tela do programa fechar antes de seguir
      for ($k = 0; $k -lt 24 -and (Achar "$p*"); $k++) { Start-Sleep 5 }
      Start-Sleep 3; return $z
    }
    $tit = Titulos
    $doProg = $tit | Where-Object { $_ -like "$p*" -or $_ -like 'vpdatas*' -or $_ -like 'vpsel*' -or $_ -like 'vpregix*' }
    if (-not $doProg) { $nada += 5 } else { $nada = 0 }
    $comF9 = $doProg | Where-Object { $_ -notlike 'vpregix*' -and (TemF9 $_) } | Select-Object -First 1
    if ($comF9) { $parada += 5 } else { $parada = 0 }
    if ($AjudaF9 -and $comF9 -and $parada -ge 10 -and $f9 -lt 4) { $f9++; Log "  tela '$comF9' -> F9"; ApertaF9 $comF9; $parada = 0 }
    if ($parada -ge 60 -or $nada -ge 60 -or ((Get-Date) - $ini).TotalMinutes -gt 30) { Print $p; throw "O zip do $p não apareceu. Janelas: $($tit -join ' | ')" }
  }
}

# ---------- etapas ----------
function GeraSimples($p) {
  $ini = Get-Date
  $j = AbrePrograma $p
  ApertaF9 $j
  $z = EsperaZip $p $ini -AjudaF9
  Log ("OK  {0}  {1:N0} bytes  {2}s" -f $z.Name, $z.Length, [int]((Get-Date) - $ini).TotalSeconds)
}
function Passo($titulo, $teclas, $espera) {
  if (-not (Ativa $titulo)) { Print 'vp4254ex'; throw "Não achei a janela '$titulo'." }
  $ws.SendKeys($teclas); Start-Sleep -Milliseconds $espera
}
function GeraComissoes($opcao) {
  # vp4254ex — passos do roteiro_relatorios.md (01 = títulos normais -> vp42541a; 02 = antecipações -> vp42541b)
  $ini = Get-Date
  AbrePrograma 'vp4254ex' | Out-Null
  $tipo = '{HOME}'; if ($opcao -eq 2) { $tipo += '{DOWN}' }
  $ws.SendKeys($tipo); Start-Sleep -Milliseconds 400; $ws.SendKeys('{F9}'); Start-Sleep 3
  Passo 'vpsel' '{HOME}' 300;    $ws.SendKeys('{DOWN}'); Start-Sleep -Milliseconds 300; $ws.SendKeys('{ENTER}'); Start-Sleep 2
  $ws.SendKeys('{F5}'); Start-Sleep 1; $ws.SendKeys('{F9}'); Start-Sleep 2
  Passo 'vpsel' '{F9}' 3000
  Passo 'vpdatas' $inicioAno 500; $ws.SendKeys('{F9}'); Start-Sleep 3   # final fica 31/12/2076 = até hoje
  Passo 'vp4254 - Listagem' '{F9}' 2000
  $z = EsperaZip 'vp4254ex' $ini
  Log ("OK  vp4254ex opção 0{0}  {1}  {2}s" -f $opcao, $z.Name, [int]((Get-Date) - $ini).TotalSeconds)
  return $z
}
function Copia {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $hoje = (Get-Date).Date
  $zips = @{}
  foreach ($p in (@($mapa.Values) + $simples | Select-Object -Unique)) {
    $z = Get-ChildItem $arq -Filter "$($emp)_*_$($p)_*.zip" | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($z) { $zips[$p] = $z; if ($z.LastWriteTime -lt $hoje) { Log "ATENÇÃO: $p mais recente é de $($z.LastWriteTime)" } }
  }
  foreach ($nome in $mapa.Keys) {
    $p = $mapa[$nome]; if (-not $zips[$p]) { Log "FALTOU zip do $p para $nome"; continue }
    $zip = [IO.Compression.ZipFile]::OpenRead($zips[$p].FullName)
    $e = $zip.Entries | Where-Object { $_.Name -eq $nome }
    if ($e) { $alvo = Join-Path $destino $nome; [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $alvo, $true); (Get-Item $alvo).LastWriteTime = $zips[$p].LastWriteTime; Log "copiado $nome <- $($zips[$p].Name)" }
    else { Log "FALTOU $nome dentro de $($zips[$p].Name)" }
    $zip.Dispose()
  }
  # zip completo de cada programa (materiais, estoques, preços...) fica em EXTRAS\<programa>, fora da pasta do app
  foreach ($p in $zips.Keys) {
    $d = Join-Path $extras $p; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zips[$p].FullName $d -Force
  }
  Log "EXTRAS atualizado: $(($zips.Keys | Sort-Object) -join ', ')"
}
function CopiaComissoes($a, $b) {
  # mesmo padrão do copiar_comissoes.ps1: vp42541a/b_<mês anterior>.csv
  $comp = (Get-Date).AddMonths(-1).ToString('yyyy-MM')
  $par = @($a, $b); $nomes = 'vp42541a', 'vp42541b'
  for ($i = 0; $i -lt 2; $i++) {
    $tmp = Join-Path $env:TEMP ('pk_' + $nomes[$i]); Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $par[$i].FullName $tmp -Force
    $csvs = @(Get-ChildItem $tmp -File -Recurse -Filter *.csv | Where-Object Name -ne 'vp.csv'); $n = 0
    foreach ($c in $csvs) {
      $n++; $suf = if ($csvs.Count -gt 1) { "_$n" } else { '' }
      $novo = Join-Path $destino ('{0}_{1}{2}.csv' -f $nomes[$i], $comp, $suf)
      Copy-Item $c.FullName $novo -Force; (Get-Item $novo).LastWriteTime = $par[$i].LastWriteTime; Log "copiado $novo"
    }
    Remove-Item $tmp -Recurse -Force
  }
}

# ---------- execução ----------
$erros = 0
try {
  Log '===== início'
  if (-not (Achar 'VerUP*')) { & 'C:\PINKPOA\login_audience.ps1' }
  if (-not (Achar 'VerUP*')) { throw 'Não consegui entrar no Audience.' }
  $lista = if ($Somente) { $Somente } elseif ($Completo) { $simples + 'vp4254ex' } else { $diario }
  Log "programas: $($lista -join ', ')"
  foreach ($p in $lista) {
    try {
      if ($p -eq 'vp4254ex') { $a = GeraComissoes 1; $b = GeraComissoes 2; CopiaComissoes $a $b }
      else { GeraSimples $p }
    } catch {
      $erros++; Log "ERRO $p : $($_.Exception.Message)"
      # fecha só telas paradas (com F9=Ok); nunca mexe em tela processando
      foreach ($t in (Titulos | Where-Object { $_ -like 'vp*' -and $_ -notlike 'VerUP*' -and (TemF9 $_) })) { if (Ativa $t) { $ws.SendKeys('{ESC}'); Start-Sleep 1 } }
      # espera a tela de andamento de outro programa acabar (até 15 min) antes de seguir
      for ($k = 0; $k -lt 180 -and (Titulos | Where-Object { $_ -like 'vp*' -and $_ -notlike 'VerUP*' -and -not (TemF9 $_) }); $k++) { Start-Sleep 5 }
    }
  }
  FechaViewer
  Copia
  # aciona o PinkPoa: o app (v2.14.375+) com #importar varre a pasta conectada (C:\PINKPOA\EXPORT) e importa o que for novo
  if (-not $Somente) {
    Start-Process 'https://www.pinkpoafinanceiro.com.br/#importar'
    Log 'PinkPoa aberto com #importar (a importação roda no navegador em ~1 min; precisa estar logado e com a pasta conectada)'
  }
  Log "===== fim ($erros erro(s))"
} catch {
  Log "ERRO GERAL: $($_.Exception.Message)"; Print 'geral'; exit 1
}
if ($erros) { exit 2 }
