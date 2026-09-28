$hostname = '##system.hostname##'
# Here-strings keep passwords containing quotes intact after token substitution.
$user = @'
##wmi.user##
'@
$pass = @'
##wmi.pass##
'@
$dockerPath = @'
##docker.path##
'@

# Undefined tokens are left as literal ##...## text in Active Discovery and become empty in collection.
function Test-LmValue([string]$Value) {
    -not [string]::IsNullOrWhiteSpace($Value) -and -not $Value.Trim().StartsWith('##')
}
if (-not (Test-LmValue $dockerPath)) { $dockerPath = 'docker' }

$sessionArgs = @{ ComputerName = $hostname; ErrorAction = 'Stop' }
if ((Test-LmValue $user) -and (Test-LmValue $pass)) {
    $securePass = ConvertTo-SecureString $pass -AsPlainText -Force
    $sessionArgs.Credential = New-Object System.Management.Automation.PSCredential($user.Trim(), $securePass)
}

$session = $null
try {
    $session = New-PSSession @sessionArgs

    $lines = Invoke-Command -Session $session -ArgumentList $dockerPath -ErrorAction Stop -ScriptBlock {
        param([string]$Docker)

        # Windows PowerShell turns native stderr into terminating errors under 'Stop'.
        $ErrorActionPreference = 'Continue'
        $invariant = [System.Globalization.CultureInfo]::InvariantCulture

        function Invoke-Docker([string[]]$DockerArgs) {
            $result = & $Docker @DockerArgs 2>&1
            $code = $LASTEXITCODE
            [pscustomobject]@{
                ExitCode = $code
                Out      = @($result | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
                Err      = (@($result | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ }) -join ' ')
            }
        }

        # Must match the instance key used by Active Discovery.
        function Get-SafeInstance([string]$Value) {
            if ([string]::IsNullOrWhiteSpace($Value)) { return 'unknown' }
            return ($Value.Trim() -replace '[^A-Za-z0-9_.-]+', '_').Trim('_')
        }

        function Get-Number([object]$Value) {
            if ($null -eq $Value) { return 0 }
            $number = 0.0
            $text = ([string]$Value).Trim().Replace('%', '')
            if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Float, $invariant, [ref]$number)) { return $number }
            return 0
        }

        # docker stats prints SI units for I/O (kB, MB) and binary units for memory (KiB, MiB).
        function Get-Bytes([string]$Value) {
            if ([string]::IsNullOrWhiteSpace($Value)) { return 0 }
            if ($Value.Trim() -notmatch '^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?)(i?)b$') { return 0 }
            $power = 0
            if ($Matches[2]) { $power = 'kmgtpe'.IndexOf($Matches[2].ToLower()) + 1 }
            $base = 1000
            if ($Matches[3]) { $base = 1024 }
            return [long]([double]::Parse($Matches[1], $invariant) * [math]::Pow($base, $power))
        }

        function Get-Pair([string]$Value) {
            if ($null -eq $Value) { return @(0, 0) }
            $parts = $Value -split '\s*/\s*', 2
            $second = 0
            if ($parts.Count -ge 2) { $second = Get-Bytes $parts[1] }
            return @((Get-Bytes $parts[0]), $second)
        }

        function Get-Metric([string]$Instance, [string]$Metric, [object]$Value) {
            if ($Value -is [bool]) { $Value = [int]$Value }
            if ($null -eq $Value) { $Value = 0 }
            return $Instance + '.' + $Metric + '=' + ([System.Convert]::ToString($Value, $invariant))
        }

        $ps = Invoke-Docker @('container', 'ls', '--quiet', '--no-trunc')
        if ($ps.ExitCode -ne 0) { throw "docker container ls failed (exit $($ps.ExitCode)): $($ps.Err)" }
        $ids = @($ps.Out | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($ids.Count -eq 0) { return }

        $inspect = Invoke-Docker (@('container', 'inspect') + $ids)
        $json = $inspect.Out -join "`n"
        if ([string]::IsNullOrWhiteSpace($json)) { throw "docker container inspect failed (exit $($inspect.ExitCode)): $($inspect.Err)" }
        $containers = ConvertFrom-Json $json

        $instanceById = @{}
        foreach ($c in $containers) {
            $name = ([string]$c.Name).TrimStart('/')
            if ([string]::IsNullOrWhiteSpace($name)) { $name = ([string]$c.Id).Substring(0, 12) }
            $instance = Get-SafeInstance $name
            $instanceById[[string]$c.Id] = $instance

            $stateCode = -2
            switch ([string]$c.State.Status) {
                'running'    { $stateCode = 1 }
                'paused'     { $stateCode = 2 }
                'restarting' { $stateCode = 3 }
                'created'    { $stateCode = 4 }
                'exited'     { $stateCode = 0 }
                'dead'       { $stateCode = -1 }
            }
            $healthCode = -1
            if ($null -ne $c.State.Health) {
                switch ([string]$c.State.Health.Status) {
                    'healthy'   { $healthCode = 1 }
                    'starting'  { $healthCode = 2 }
                    'unhealthy' { $healthCode = 0 }
                }
            }
            $uptimeSeconds = 0
            if ([bool]$c.State.Running) {
                try {
                    $startedAt = $c.State.StartedAt
                    if ($startedAt -isnot [datetime]) {
                        # DateTime parsing accepts at most 7 fractional-second digits; Docker can emit 9.
                        $text = ([string]$startedAt) -replace '(\.\d{7})\d+', '$1'
                        $startedAt = [datetime]::Parse($text, $invariant, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
                    }
                    $uptimeSeconds = [long][math]::Max(0, ([datetime]::UtcNow - $startedAt.ToUniversalTime()).TotalSeconds)
                }
                catch { $uptimeSeconds = 0 }
            }

            Get-Metric $instance 'containerPresent' 1
            Get-Metric $instance 'running' ([bool]$c.State.Running)
            Get-Metric $instance 'paused' ([bool]$c.State.Paused)
            Get-Metric $instance 'restarting' ([bool]$c.State.Restarting)
            Get-Metric $instance 'oomKilled' ([bool]$c.State.OOMKilled)
            Get-Metric $instance 'dead' ([bool]$c.State.Dead)
            Get-Metric $instance 'stateCode' $stateCode
            Get-Metric $instance 'healthCode' $healthCode
            Get-Metric $instance 'exitCode' $c.State.ExitCode
            Get-Metric $instance 'restartCount' $c.RestartCount
            Get-Metric $instance 'pid' $c.State.Pid
            Get-Metric $instance 'uptimeSeconds' $uptimeSeconds
        }

        $stats = Invoke-Docker (@('stats', '--no-stream', '--no-trunc', '--format', '{{json .}}') + $ids)
        foreach ($line in $stats.Out) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            try { $s = ConvertFrom-Json $line } catch { continue }
            $instance = $instanceById[[string]$s.ID]
            if (-not $instance) { $instance = Get-SafeInstance ([string]$s.Name) }

            # Windows containers report only the private working set, so memory limit/percent read 0.
            $memory = Get-Pair ([string]$s.MemUsage)
            $network = Get-Pair ([string]$s.NetIO)
            $block = Get-Pair ([string]$s.BlockIO)
            Get-Metric $instance 'cpuPercent' (Get-Number $s.CPUPerc)
            Get-Metric $instance 'memoryPercent' (Get-Number $s.MemPerc)
            Get-Metric $instance 'memoryUsageBytes' $memory[0]
            Get-Metric $instance 'memoryLimitBytes' $memory[1]
            Get-Metric $instance 'networkRxBytes' $network[0]
            Get-Metric $instance 'networkTxBytes' $network[1]
            Get-Metric $instance 'blockReadBytes' $block[0]
            Get-Metric $instance 'blockWriteBytes' $block[1]
            Get-Metric $instance 'pids' (Get-Number $s.PIDs)
        }
    }

    foreach ($line in $lines) { Write-Host ([string]$line) }
}
catch {
    Write-Host ('Docker collection failed for ' + $hostname + ': ' + $_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $session) { Remove-PSSession $session }
}

exit 0
