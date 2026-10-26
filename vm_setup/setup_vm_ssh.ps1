# Configurare VM pentru Nexus: server SSH (doar din reteaua Tailscale) + cheia PC-ului + Tailscale.
# Se ruleaza O SINGURA DATA pe VM, intr-un PowerShell deschis ca Administrator:
#     powershell -ExecutionPolicy Bypass -File C:\setup_vm_ssh.ps1
# La final scrie rezultatul in C:\nexus_vm_info.txt (il descarci prin DWService -> Files).
# Scriptul poate fi rulat de mai multe ori fara probleme (sare peste ce e deja facut).
# Fara diacritice: Windows PowerShell 5.1 citeste fisierele fara BOM ca ANSI.

$ErrorActionPreference = 'Stop'
$out = 'C:\nexus_vm_info.txt'
$key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGFQmHaBXuLzgeyPcEJtT2THApPwqT+o4rqBzOjvwWWd nexus-pc-to-vm'
$log = New-Object System.Collections.Generic.List[string]

function Step($name, [scriptblock]$body) {
    try {
        & $body
        $log.Add("[OK]     $name")
    } catch {
        $log.Add("[EROARE] ${name}: $($_.Exception.Message)")
    }
    Write-Host $log[-1]
}

# ---------- 1. Server SSH ----------
Step 'OpenSSH Server instalat' {
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    if ($cap.State -ne 'Installed') {
        Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
    }
}

Step 'Serviciul sshd pornit si automat la boot' {
    Set-Service -Name sshd -StartupType Automatic
    Start-Service sshd
}

# Conexiunile SSH acceptate doar din reteaua Tailscale (100.64.0.0/10), nu de pe internet.
Step 'Firewall: SSH doar din Tailscale' {
    if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
    Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -RemoteAddress '100.64.0.0/10'
}

Step 'PowerShell ca shell implicit pentru SSH' {
    if (-not (Test-Path 'HKLM:\SOFTWARE\OpenSSH')) {
        New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' | Out-Null
    }
    New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
        -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force | Out-Null
}

# ---------- 2. Cheia publica a PC-ului ----------
# Conturile de administrator citesc cheile din administrators_authorized_keys (cu permisiuni stricte);
# celelalte conturi din ~\.ssh\authorized_keys. O punem in ambele, ca sa mearga oricare ar fi contul.
Step 'Cheia PC-ului adaugata (administratori)' {
    $ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
    if (-not ((Test-Path $ak) -and (Select-String -Path $ak -SimpleMatch $key -Quiet))) {
        Add-Content -Path $ak -Value $key -Encoding ascii
    }
    # SID-urile in loc de nume: merge si pe Windows in romana (Administratori / SYSTEM).
    icacls.exe $ak /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls a esuat ($LASTEXITCODE)" }
}

Step 'Cheia PC-ului adaugata (contul curent)' {
    $dir = Join-Path $env:USERPROFILE '.ssh'
    $uk = Join-Path $dir 'authorized_keys'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    if (-not ((Test-Path $uk) -and (Select-String -Path $uk -SimpleMatch $key -Quiet))) {
        Add-Content -Path $uk -Value $key -Encoding ascii
    }
}

Step 'sshd repornit' { Restart-Service sshd }

# ---------- 3. Tailscale ----------
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
Step 'Tailscale instalat' {
    if (-not (Test-Path $ts)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id Tailscale.Tailscale -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
        }
        if (-not (Test-Path $ts)) {
            $msi = Join-Path $env:TEMP 'tailscale-setup.msi'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi' -OutFile $msi
            Start-Process msiexec.exe -ArgumentList '/i', "`"$msi`"", '/quiet', '/norestart' -Wait
        }
        if (-not (Test-Path $ts)) { throw 'instalarea nu a reusit; instaleaza manual de pe tailscale.com/download/windows' }
    }
}

# ---------- 4. Informatii pentru Claude ----------
# Continue: in PowerShell 5.1, stderr-ul unui program extern (ex. tailscale nelogat) ar opri scriptul.
$ErrorActionPreference = 'Continue'
$info = @()
$info += '===== Rezultat pasi ====='
$info += $log
$info += ''
$info += '===== whoami ====='
$info += (whoami)
$info += "admin: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
$info += ''
$info += '===== Tailscale ====='
if (Test-Path $ts) {
    $info += (& $ts status 2>$null | Out-String)
    $info += "IP: $(& $ts ip -4 2>$null | Out-String)"
} else {
    $info += 'neinstalat'
}
$info += ''
$info += '===== Sistem ====='
$os = Get-CimInstance Win32_OperatingSystem
$info += "OS: $($os.Caption) $($os.Version)"
$info += "RAM: $([math]::Round($os.TotalVisibleMemorySize / 1MB, 1)) GB"
$info += "CPU: $((Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum) nuclee logice"
foreach ($d in Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3') {
    $info += "Disc $($d.DeviceID) liber $([math]::Round($d.FreeSpace / 1GB, 1)) GB din $([math]::Round($d.Size / 1GB, 1)) GB"
}
$info += "Virtualizare imbricata (pentru Docker): $((Get-CimInstance Win32_Processor | Select-Object -First 1).VirtualizationFirmwareEnabled)"
$info += "sshd: $((Get-Service sshd -ErrorAction SilentlyContinue).Status)"

$info | Out-File -FilePath $out -Encoding utf8
Write-Host ''
Write-Host "Gata. Rezultatul e in $out"
Write-Host 'Daca Tailscale nu e logat: click pe iconita Tailscale din bara de jos -> Log in (acelasi cont ca pe PC), apoi ruleaza scriptul inca o data.'
