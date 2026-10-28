# Docker Container DataSources

**What it does:** Two LogicMonitor DataSources that find the Docker containers running on a host and monitor each one (state, health, CPU, memory, network, disk). Nothing is installed on the Docker hosts.

**Files to import:** `Linux_Docker_Containers.json` (Linux Docker hosts) and `Windows_Docker_Containers.json` (Windows Docker hosts)

**What you need:**
- Rights in the LogicMonitor portal to import DataSources and edit device properties.
- Linux hosts: an SSH login on each host that is allowed to run `docker`.
- Windows hosts: a local administrator login on each host, and a Windows Collector.

**How to run:**

Linux Docker hosts (set these properties on the device, or on its group):
1. `Classification` = `Docker Swarm`
2. `ssh.user` = `<ssh-user>` and `ssh.pass` = `<ssh-user-password>`
3. Let that user run Docker. On the host run: `sudo usermod -aG docker <ssh-user>`
   If that is not allowed, instead set the device property `docker.cmd` = `sudo -n /usr/bin/docker` and add this line on the host with `sudo visudo`: `<ssh-user> ALL=(root) NOPASSWD: /usr/bin/docker`

Windows Docker hosts:
1. On the device, set `Classification` = `Docker Standalone`
2. Set `wmi.user` = `<local-admin-user>` and `wmi.pass` = `<local-admin-password>` (a local administrator on the host).
3. Make sure the device is monitored by a **Windows** Collector that can reach the host on TCP port 5985 (WinRM).
4. On that Collector, open PowerShell as administrator and run:
   `Set-Item WSMan:\localhost\Client\TrustedHosts -Value "<device-ip>" -Concatenate -Force`
5. Optional: if `docker.exe` is not on the host's PATH, set `docker.path` = `<full-path-to-docker.exe>`

Import (both files):
1. In LogicMonitor go to **Modules** (or **Settings > LogicModules > DataSources**).
2. Click **Add > From File** and choose the `.json` file.
3. If a DataSource with the same name already exists, choose to **replace** it.

**What you get:** Within about 15 minutes, each matching device shows one entry per running container under **Linux Docker Containers** or **Windows Docker Containers**, updated every 3 minutes.

**If something goes wrong:**
- Nothing appears on the device — check `Classification` is exactly `Docker Swarm` (Linux) or `Docker Standalone` (Windows).
- Linux: `docker ps failed` or permission denied — redo Linux step 3.
- Windows: access denied or WinRM error — check the user is a local administrator, port 5985 is open, and the device IP is in TrustedHosts (Windows step 4).
