# Docker Container DataSources

Containers as instances on Docker hosts that are **already** in LogicMonitor.
Active Discovery is the script `discover` output. Collection is the script
`collect` output.

| File | Purpose |
|---|---|
| `lm_docker_container_monitor.sh` | Linux collector script (`discover` / `collect`) |
| `lm_docker_container_monitor.ps1` | Windows collector script (`-Mode discover` / `-Mode collect`) |
| `Docker_Linux_Containers.xml` | Linux DataSource — name `Docker_Linux_Containers` |
| `Docker_Windows_Containers.xml` | Windows DataSource — name `Docker_Windows_Containers` |

## What to do

1. **Put the script on the Collector** (`agent/local/bin/`):
   - Linux: `lm_docker_container_monitor.sh`
   - Windows: `lm_docker_container_monitor.ps1`
2. **Import the XML** — Settings → LogicModules → DataSources → Add → From XML file (or Modules → Add → From file). One file per DataSource.
3. **Apply** `Docker_Linux_Containers` to already-monitored Linux Docker hosts, and `Docker_Windows_Containers` to already-monitored Windows Docker hosts.
