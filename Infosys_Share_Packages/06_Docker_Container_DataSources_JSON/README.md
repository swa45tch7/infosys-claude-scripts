# Docker Container DataSources (JSON)

Two LogicMonitor DataSources that discover the running Docker containers on a
monitored host and collect state, health, CPU, memory, network, block I/O and
process metrics per container. Nothing to install on the Docker hosts.

| File | Applies to | Connects with |
|---|---|---|
| `Linux_Docker_Containers.json` | Devices with `Classification` = `Docker Swarm` | SSH from the Collector |
| `Windows_Docker_Containers.json` | Devices with `Classification` = `Docker Standalone` | WinRM from a Windows Collector |

## What you need (device properties in the LM portal)

**Linux Docker hosts**

- `Classification` = `Docker Swarm`
- `ssh.user` and `ssh.pass`
- The SSH user must be able to run `docker`. Either add it to the docker group
  on the host: `sudo usermod -aG docker <user>`, or set the device property
  `docker.cmd` = `sudo -n /usr/bin/docker` and add this sudoers rule on the host:
  `<user> ALL=(root) NOPASSWD: /usr/bin/docker`

**Windows Docker hosts**

- `Classification` = `Docker Standalone`
- `wmi.user` and `wmi.pass` (a local administrator on the host)
- A **Windows** Collector, with WinRM (TCP 5985) to the host open.
- On the Collector, allow the device IPs in TrustedHosts (PowerShell as admin):
  `Set-Item WSMan:\localhost\Client\TrustedHosts -Value "<device-ip-1>,<device-ip-2>" -Concatenate -Force`
- Optional: `docker.path` = full path to `docker.exe` if it is not on the PATH.

## How to import

1. In LogicMonitor go to **Modules** (or Settings > LogicModules > DataSources).
2. Select **Add > From File** and choose `Linux_Docker_Containers.json`.
3. If a DataSource with the same name already exists, choose to **replace** it.
4. Repeat for `Windows_Docker_Containers.json`.

## What you get

After the next discovery cycle (up to 15 minutes), each matching device shows
one instance per running container under **Linux Docker Containers** or
**Windows Docker Containers**, polled every 3 minutes.

## Common problems

| Symptom | Fix |
|---|---|
| DataSource not on the device | Check `Classification` is exactly `Docker Swarm` / `Docker Standalone`. |
| `ssh.user and ssh.pass ... must be set` | Add both properties to the device or its group. |
| Linux: `docker ps failed` / permission denied | Add the user to the docker group, or set `docker.cmd` plus the sudoers rule above. |
| Windows: `Docker Active Discovery failed` / access denied / WinRM error | Check `wmi.user` is a local admin, port 5985 is open, and the device IP is in the Collector's TrustedHosts. |
