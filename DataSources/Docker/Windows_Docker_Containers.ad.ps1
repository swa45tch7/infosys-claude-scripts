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

        function Invoke-Docker([string[]]$DockerArgs) {
            $result = & $Docker @DockerArgs 2>&1
            $code = $LASTEXITCODE
            [pscustomobject]@{
                ExitCode = $code
                Out      = @($result | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
                Err      = (@($result | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ }) -join ' ')
            }
        }

        function Get-SafeText([object]$Value) {
            if ($null -eq $Value) { return '' }
            return ([string]$Value -replace '[\r\n]+', ' ').Replace('&', '_').Replace('=', '_').Replace('#', '_').Trim()
        }

        function Get-SafeInstance([string]$Value) {
            if ([string]::IsNullOrWhiteSpace($Value)) { return 'unknown' }
            return ($Value.Trim() -replace '[^A-Za-z0-9_.-]+', '_').Trim('_')
        }

        $ps = Invoke-Docker @('container', 'ls', '--quiet', '--no-trunc')
        if ($ps.ExitCode -ne 0) { throw "docker container ls failed (exit $($ps.ExitCode)): $($ps.Err)" }
        $ids = @($ps.Out | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($ids.Count -eq 0) { return }

        # Containers can stop between ls and inspect; inspect still prints the ones it found.
        $inspect = Invoke-Docker (@('container', 'inspect') + $ids)
        $json = $inspect.Out -join "`n"
        if ([string]::IsNullOrWhiteSpace($json)) { throw "docker container inspect failed (exit $($inspect.ExitCode)): $($inspect.Err)" }
        $containers = ConvertFrom-Json $json

        foreach ($c in $containers) {
            $name = ([string]$c.Name).TrimStart('/')
            if ([string]::IsNullOrWhiteSpace($name)) { $name = ([string]$c.Id).Substring(0, 12) }

            $properties = @(
                'auto.docker.id=' + (Get-SafeText $c.Id)
                'auto.docker.name=' + (Get-SafeText $name)
                'auto.docker.image=' + (Get-SafeText $c.Config.Image)
                'auto.docker.status=' + (Get-SafeText $c.State.Status)
                'auto.docker.platform=' + (Get-SafeText $c.Platform)
                'auto.docker.storageDriver=' + (Get-SafeText $c.Driver)
                'auto.docker.isolation=' + (Get-SafeText $c.HostConfig.Isolation)
                'auto.docker.restart.policy=' + (Get-SafeText $c.HostConfig.RestartPolicy.Name)
            ) -join '&'

            # Container name (not id) is the instance key so history survives a container re-create.
            (Get-SafeInstance $name) + '##' + (Get-SafeText $name) + '##' + (Get-SafeText $c.Config.Image) + '####' + $properties
        }
    }

    foreach ($line in $lines) { Write-Host ([string]$line) }
}
catch {
    Write-Host ('Docker Active Discovery failed for ' + $hostname + ': ' + $_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $session) { Remove-PSSession $session }
}

exit 0
