# Docker Container DataSources (XML + Collector scripts)

Two LogicMonitor DataSources that list the running Docker containers as
instances and collect CPU, memory, network, block I/O, health and restart
metrics. They run a script on the Collector that calls the local `docker`
command.

This is an **alternative** to the JSON DataSources package (Linux over SSH,
Windows over WinRM). Use one or the other on a host, not both.

| File | Purpose |
|---|---|
| `Docker_Linux_Containers.xml` | Linux DataSource `Docker_Linux_Containers` |
| `lm_docker_container_monitor.sh` | Script it runs (`discover` / `collect`) |
| `Docker_Windows_Containers.xml` | Windows DataSource `Docker_Windows_Containers` |
| `lm_docker_container_monitor.ps1` | Script it runs (`-Mode discover` / `-Mode collect`) |

## What you need

- The LogicMonitor **Collector installed on the Docker host itself**: the
  scripts read containers from the machine they run on.
- The Collector service account must be able to run `docker` (Linux: member of
  the `docker` group; Windows: a user allowed to use Docker).
- Linux hosts need `bash`; Windows hosts need PowerShell 5.1 or newer.

## How to set it up

1. Copy the script into the Collector's `agent/local/bin` folder:
   - Linux: `/usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh`,
     then `chmod +x` it.
   - Windows: `<Collector folder>\Agent\local\bin\lm_docker_container_monitor.ps1`, where
     the Collector folder is `C:\Program Files (x86)\LogicMonitor` or
     `C:\Program Files\LogicMonitor`, whichever exists.
2. In LogicMonitor: **Settings > LogicModules > DataSources > Add > From XML file**
   (or **Modules > Add > From File**). Import each XML.
3. Edit each DataSource's **AppliesTo**. It ships with placeholders that match
   nothing: replace `<device-ip-1>|<device-ip-2>` and `<device-name-1>|<device-name-2>`
   with your hosts, or use something like `hasCategory("Docker")`.
4. Save. Instances appear after the next discovery (up to 15 minutes).

To test a script on the host first: `./lm_docker_container_monitor.sh discover`
or `powershell -File lm_docker_container_monitor.ps1 -Mode discover`.

## What you get

One instance per running container on each host, with metrics such as
`running`, `cpuPercent`, `memoryPercent`, `restartCount` and `uptimeSeconds`.

## Common problems

| Symptom | Fix |
|---|---|
| `ERROR: Docker CLI was not found.` | Install Docker CLI on that host or put `docker` on the PATH. |
| `ERROR: Docker daemon is unavailable or the executing user cannot access Docker.` (Windows: `Docker CLI, Docker daemon, or Docker access is unavailable.`) | Start Docker, and give the Collector service account Docker access (Linux: `docker` group), then restart the Collector. |
| DataSource never applies | AppliesTo still has the placeholders. Edit it (step 3). |
| No instances on a remote host | These scripts only see the Collector's own host. Use the JSON package for remote hosts. |
