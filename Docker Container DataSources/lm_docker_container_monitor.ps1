param(
    [ValidateSet('discover','discovery','collect')]
    [string]$Mode = 'collect',
    [string]$DockerPath = 'docker'
)

$ErrorActionPreference = 'Stop'

function Get-SafeText {
    param([object]$Value)
    if ($null -eq $Value) { return '' }
    $text = [string]$Value
    $text = $text.Replace('%','%25').Replace('&','%26').Replace('=','%3D').Replace('#','%23')
    return $text
}

function Get-SafeInstance {
    param([string]$Value)
    if ($null -eq $Value -or $Value.Trim().Length -eq 0) { return 'unknown' }
    $result = $Value.Trim() -replace '[\s:=\\#]+','_'
    $result = $result -replace '[^A-Za-z0-9_.-]','_'
    return $result
}

function Get-Number {
    param([object]$Value)
    if ($null -eq $Value) { return 0 }
    $text = ([string]$Value).Trim().Replace('%','')
    $number = 0.0
    if ([double]::TryParse($text, [ref]$number)) { return $number }
    return 0
}

function Get-Bytes {
    param([string]$Value)
    if ($null -eq $Value -or $Value.Trim().Length -eq 0) { return 0 }
    $clean = $Value.Trim() -replace '\s',''
    if ($clean -notmatch '^([0-9]+([.][0-9]+)?)([A-Za-z]+)?$') { return 0 }
    $number = [double]$Matches[1]
    $unit = ([string]$Matches[3]).ToUpper()
    $factor = 1
    switch ($unit) {
        'KB'  { $factor = 1000 }
        'MB'  { $factor = 1000000 }
        'GB'  { $factor = 1000000000 }
        'TB'  { $factor = 1000000000000 }
        'KIB' { $factor = 1024 }
        'MIB' { $factor = 1048576 }
        'GIB' { $factor = 1073741824 }
        'TIB' { $factor = 1099511627776 }
        default { $factor = 1 }
    }
    return [long]($number * $factor)
}

function Get-Pair {
    param([string]$Value)
    if ($null -eq $Value) { return @(0,0) }
    $parts = $Value -split '\s*/\s*', 2
    $first = 0
    $second = 0
    if ($parts.Count -ge 1) { $first = Get-Bytes $parts[0] }
    if ($parts.Count -ge 2) { $second = Get-Bytes $parts[1] }
    return @($first,$second)
}

function Write-Metric {
    param([string]$Instance,[string]$Metric,[object]$Value)
    if ($Value -is [bool]) {
        if ($Value) { $Value = 1 } else { $Value = 0 }
    }
    if ($null -eq $Value) { $Value = 0 }
    Write-Output ($Instance + '.' + $Metric + '=' + $Value)
}

& $DockerPath version --format '{{.Server.Version}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Docker CLI, Docker daemon, or Docker access is unavailable.'
    exit 1
}

$ContainerIds = @(& $DockerPath container ls --quiet --no-trunc 2>$null | Where-Object { $_ -and $_.Trim().Length -gt 0 })
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Unable to list running Docker containers.'
    exit 1
}

if ($Mode -eq 'discover' -or $Mode -eq 'discovery') {
    foreach ($ContainerId in $ContainerIds) {
        try {
            $Container = ((& $DockerPath container inspect $ContainerId 2>$null | ConvertFrom-Json)[0])
            if ($null -eq $Container) { continue }
            $Name = ([string]$Container.Name).TrimStart('/')
            if ($Name.Length -eq 0) { $Name = $ContainerId.Substring(0,12) }
            $Instance = Get-SafeInstance ([string]$Container.Id)
            $Alias = Get-SafeInstance $Name
            $Image = Get-SafeText $Container.Config.Image
            $Status = Get-SafeText $Container.State.Status
            $Platform = Get-SafeText $Container.Platform
            $Driver = Get-SafeText $Container.Driver
            $Isolation = Get-SafeText $Container.HostConfig.Isolation
            $Properties = 'auto.docker.name=' + (Get-SafeText $Name) +
                '&auto.docker.image=' + $Image +
                '&auto.docker.status=' + $Status +
                '&auto.docker.platform=' + $Platform +
                '&auto.docker.storageDriver=' + $Driver +
                '&auto.docker.isolation=' + $Isolation
            Write-Output ($Instance + '##' + $Alias + '##Docker container ' + $Alias + '####' + $Properties)
        }
        catch {
            Write-Error ('Unable to inspect container ' + $ContainerId + ': ' + $_.Exception.Message)
        }
    }
    exit 0
}

foreach ($ContainerId in $ContainerIds) {
    try {
        $Container = ((& $DockerPath container inspect $ContainerId 2>$null | ConvertFrom-Json)[0])
        if ($null -eq $Container) { continue }
        $Instance = Get-SafeInstance ([string]$Container.Id)
        $State = [string]$Container.State.Status
        $StateCode = -2
        switch ($State) {
            'running'    { $StateCode = 1 }
            'paused'     { $StateCode = 2 }
            'restarting' { $StateCode = 3 }
            'created'    { $StateCode = 4 }
            'exited'     { $StateCode = 0 }
            'dead'       { $StateCode = -1 }
        }
        $HealthCode = -1
        if ($null -ne $Container.State.Health) {
            switch ([string]$Container.State.Health.Status) {
                'healthy'   { $HealthCode = 1 }
                'starting'  { $HealthCode = 2 }
                'unhealthy' { $HealthCode = 0 }
            }
        }
        $UptimeSeconds = 0
        if ([bool]$Container.State.Running) {
            try {
                $StartedAt = Get-Date ([string]$Container.State.StartedAt)
                $UptimeSeconds = [long]((Get-Date).ToUniversalTime() - $StartedAt.ToUniversalTime()).TotalSeconds
            }
            catch { $UptimeSeconds = 0 }
        }
        Write-Metric $Instance 'containerPresent' 1
        Write-Metric $Instance 'running' ([bool]$Container.State.Running)
        Write-Metric $Instance 'paused' ([bool]$Container.State.Paused)
        Write-Metric $Instance 'restarting' ([bool]$Container.State.Restarting)
        Write-Metric $Instance 'oomKilled' ([bool]$Container.State.OOMKilled)
        Write-Metric $Instance 'dead' ([bool]$Container.State.Dead)
        Write-Metric $Instance 'stateCode' $StateCode
        Write-Metric $Instance 'healthCode' $HealthCode
        Write-Metric $Instance 'exitCode' $Container.State.ExitCode
        Write-Metric $Instance 'restartCount' $Container.RestartCount
        Write-Metric $Instance 'pid' $Container.State.Pid
        Write-Metric $Instance 'uptimeSeconds' $UptimeSeconds
    }
    catch {
        Write-Error ('Unable to collect inspect metrics for ' + $ContainerId + ': ' + $_.Exception.Message)
    }
}

$StatsLines = @(& $DockerPath stats --no-stream --no-trunc --format '{{json .}}' 2>$null | Where-Object { $_ -and $_.Trim().Length -gt 0 })
if ($LASTEXITCODE -ne 0) {
    Write-Error 'docker stats failed.'
    exit 1
}

foreach ($StatsLine in $StatsLines) {
    try {
        $Stats = $StatsLine | ConvertFrom-Json
        $StatsId = [string]$Stats.ID
        if ($StatsId.Length -eq 0) { $StatsId = [string]$Stats.Container }
        if ($StatsId.Length -eq 0) { continue }
        $Instance = Get-SafeInstance $StatsId
        $Memory = Get-Pair ([string]$Stats.MemUsage)
        $Network = Get-Pair ([string]$Stats.NetIO)
        $Block = Get-Pair ([string]$Stats.BlockIO)
        Write-Metric $Instance 'cpuPercent' (Get-Number $Stats.CPUPerc)
        Write-Metric $Instance 'memoryPercent' (Get-Number $Stats.MemPerc)
        Write-Metric $Instance 'memoryUsageBytes' $Memory[0]
        Write-Metric $Instance 'memoryLimitBytes' $Memory[1]
        Write-Metric $Instance 'networkRxBytes' $Network[0]
        Write-Metric $Instance 'networkTxBytes' $Network[1]
        Write-Metric $Instance 'blockReadBytes' $Block[0]
        Write-Metric $Instance 'blockWriteBytes' $Block[1]
        Write-Metric $Instance 'pids' (Get-Number $Stats.PIDs)
    }
    catch {
        Write-Error ('Unable to parse Docker stats: ' + $_.Exception.Message)
    }
}

exit 0
