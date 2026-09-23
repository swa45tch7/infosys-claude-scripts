# Docker Container DataSources

Collector-local Docker container monitoring for LogicMonitor (LM Envision), built
for Infosys delivery teams. Two importable DataSources, two collector scripts.
No cAdvisor. No LogicMonitor write REST APIs.

The Collector runs the scripts against the Docker CLI on the Collector host.
Put the Collector on the Docker host (or give it Docker-socket access). Add
category `docker` on the resource. Import the XML. Discovery and collection
follow the script stdout contract below.

---

## Contents

| File | Purpose |
|---|---|
| `lm_docker_container_monitor.sh` | Linux collector script. Modes: `discover`, `collect`. |
| `lm_docker_container_monitor.ps1` | Windows collector script. `-Mode discover` or `-Mode collect`. |
| `Docker_Linux_Containers.xml` | Importable DataSource. Name: `Docker_Linux_Containers`. |
| `Docker_Windows_Containers.xml` | Importable DataSource. Name: `Docker_Windows_Containers`. |
| `README.md` | This document |

Keep the scripts and XML in this folder in git. On each Collector, copy the
matching script into `agent/local/bin/` as described in section 3.

---

## 1. Prerequisites

**A LogicMonitor Collector** on the Docker host (Linux Collector for Linux
Docker, Windows Collector for Windows Docker). The Collector user must be able
to run `docker info` successfully (membership of the `docker` group on Linux,
or equivalent on Windows).

**Docker CLI** on that same host, talking to the local daemon. These scripts
do not call the remote Engine API and do not use cAdvisor.

**Modules import rights** in the portal (Settings → Users & Roles). You need
permission to import LogicModules. This package never calls add/delete device,
ack/clear alert, or collector restart APIs.

**Resource category** `docker` on each Docker host in monitoring, so AppliesTo
matches. Platform is then split with `isLinux()` / `isWindows()`.

---

## 2. Quick start

1. Copy the collector script onto **every Collector** that will poll Docker hosts
   (section 3).
2. Import the matching XML (section 4).
3. On each Docker host resource, add `docker` to `system.categories` if it is
   not already there.
4. Wait for Active Discovery (15 minutes, or run it from the resource).
5. Confirm instances and datapoints. Test the script on the Collector first if
   discovery is empty (section 8).

```bash
# On a Linux Collector host, after copying the script:
/usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh discover
/usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh collect
```

```powershell
# On a Windows Collector host, after copying the script:
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\Program Files (x86)\LogicMonitor\Agent\local\bin\lm_docker_container_monitor.ps1" `
  -Mode discover

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\Program Files (x86)\LogicMonitor\Agent\local\bin\lm_docker_container_monitor.ps1" `
  -Mode collect
```

Expected discover lines use `##` separators. Expected collect lines are
`INSTANCE.datapoint=value`. Those formats are what the DataSource XML parses.

---

## 3. Where to put the collector scripts

LogicMonitor looks up **file** scripts under the Collector's
`agent/local/bin/` directory. The XML uses the `##AGENTROOT##` token for the
Collector install path.

| Platform | Copy this file | To this Collector path |
|---|---|---|
| Linux | `lm_docker_container_monitor.sh` | `##AGENTROOT##/local/bin/lm_docker_container_monitor.sh` |
| Windows | `lm_docker_container_monitor.ps1` | `##AGENTROOT##/local/bin/lm_docker_container_monitor.ps1` |

Typical expanded paths:

| Collector OS | `##AGENTROOT##` | Full script path |
|---|---|---|
| Linux | `/usr/local/logicmonitor/agent` | `/usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh` |
| Windows | `C:\Program Files (x86)\LogicMonitor\Agent` | `C:\Program Files (x86)\LogicMonitor\Agent\local\bin\lm_docker_container_monitor.ps1` |

Linux, after copy:

```bash
sudo mkdir -p /usr/local/logicmonitor/agent/local/bin
sudo cp lm_docker_container_monitor.sh /usr/local/logicmonitor/agent/local/bin/
sudo chmod 755 /usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh
sudo chown logicmonitor:logicmonitor /usr/local/logicmonitor/agent/local/bin/lm_docker_container_monitor.sh
# Collector service user varies; it must be able to execute the script and docker.
```

Windows, after copy: the Collector service account must be allowed to read the
`.ps1` and to run `docker`. The DataSource invokes PowerShell with
`-ExecutionPolicy Bypass`, so a machine-wide Restricted policy is not a blocker.

Copy the script to **each** Collector that owns a matching Docker host. XML
import does not push the file.

Do not rename the files unless you also edit Collector Attributes and Active
Discovery in the imported DataSource.

---

## 4. How to import the XML

Import **one XML per DataSource**. Do not paste the scripts into the portal
except by copying them onto the Collector as above.

**Modules UI (current):**

1. Exchange / Modules → **Add** → **From File** (or **Import from file**).
2. Choose `Docker_Linux_Containers.xml` for Linux Docker hosts.
3. Repeat for `Docker_Windows_Containers.xml` for Windows Docker hosts.
4. Confirm the name is `Docker_Linux_Containers` / `Docker_Windows_Containers`.
5. Open Collector Attributes and Active Discovery. Script type must be **file**,
   collection method **BatchScript**. Paths should still contain
   `##AGENTROOT##/local/bin/...`.

**Settings UI (legacy):**

Settings → LogicModules → DataSources → **Add** → **From XML file**.

If a DataSource with the same name already exists, the portal will ask to
overwrite. Do not overwrite LogicMonitor's core `Docker_Containers_cAdvisor*`
modules; these names are distinct.

No REST import is required. Do not use `/setting/datasources/importxml` unless
your engagement already automates module import; the GSI path is the UI.

After import, check:

| Field | Linux | Windows |
|---|---|---|
| Name | `Docker_Linux_Containers` | `Docker_Windows_Containers` |
| Displayed as | Docker Linux Containers | Docker Windows Containers |
| Collection method | BatchScript | BatchScript |
| Multi-instance | Yes | Yes |
| Use Wildvalue as Unique Identifier | Yes | Yes |
| Collect interval | 60 seconds | 60 seconds |
| Active Discovery | Script, every 15 minutes | Script, every 15 minutes |
| Delete inactive instances | Yes | Yes |

---

## 5. AppliesTo

| DataSource | AppliesTo |
|---|---|
| `Docker_Linux_Containers` | `hasCategory("docker") && isLinux()` |
| `Docker_Windows_Containers` | `hasCategory("docker") && isWindows()` |

`hasCategory("docker")` is a complete, case-insensitive match on
`system.categories`. It does not match `DockerHost` or `docker-ce`. Add the
token `docker` (comma-separated with any existing categories).

`isLinux()` / `isWindows()` read `system.sysinfo` / `system.categories`. A
Linux Collector cannot run the Windows PowerShell DataSource; keep the split.

If the Collector is **not** on the Docker host, this package will not collect
useful data. Do not apply it to remote Engine-API-only devices unless that
Collector can still run `docker` locally against the right daemon.

---

## 6. Discover versus collect

Both DataSources are **multi-instance BatchScript**. Active Discovery runs
once per schedule and creates instances. Collection runs **once per poll for
all instances** (not once per container).

### Linux — `lm_docker_container_monitor.sh`

```text
lm_docker_container_monitor.sh discover [NAME_FILTER]
lm_docker_container_monitor.sh collect  [NAME_FILTER]
```

`NAME_FILTER` is an awk/regex match against the container **name**. Default is
`.*` (all running containers). You can also set environment
`CONTAINER_NAME_FILTER`. The shipped DataSource passes only `discover` or
`collect`, so every running container is included.

Discovery lists **running** containers (`docker ps --filter status=running`).
A stopped container disappears from AD and, with delete-inactive, is removed.

**Discover stdout** (one instance per line, Active Discovery `##` format):

```text
wildvalue##wildalias##description####auto.container.id=...&auto.container.name=...&...
```

| Field | Source |
|---|---|
| wildvalue | Sanitized container name (spaces `:#\=` become `_`) |
| wildalias | Container name |
| description | `Docker Swarm Service` or `Standalone Docker Container` |
| properties | `auto.container.*` and `auto.swarm.*` (section 8) |

**Collect stdout** (BatchScript key-value):

```text
WILDVALUE.datapoint=value
```

Example:

```text
payment-api.running=1
payment-api.cpuPercent=2.31
payment-api.memoryUsedBytes=134217728
```

Datapoint keys in the XML are `##WILDVALUE##.running`, `##WILDVALUE##.cpuPercent`,
and so on, matching those lines.

### Windows — `lm_docker_container_monitor.ps1`

```text
lm_docker_container_monitor.ps1 -Mode discover
lm_docker_container_monitor.ps1 -Mode collect
```

`-Mode discovery` is accepted as an alias of `discover`. Optional `-DockerPath`
defaults to `docker`.

Discovery uses `docker container ls` (running containers) and inspects each ID.

**Discover stdout:**

```text
wildvalue##wildalias##Docker container wildalias####auto.docker.name=...&auto.docker.image=...&...
```

| Field | Source |
|---|---|
| wildvalue | Sanitized **container ID** (not the name) |
| wildalias | Sanitized container name |
| description | `Docker container <alias>` |
| properties | `auto.docker.*` (section 8) |

**Collect stdout:** inspect metrics first, then `docker stats --no-stream`
metrics, still as `WILDVALUE.datapoint=value`. `containerPresent=1` is emitted
per inspect success. Stats datapoints (CPU, memory, network, block, pids) come
from the stats loop and use the same sanitized ID.

Windows WILDVALUE is the container ID so a rename does not create a new
instance. Linux WILDVALUE is the container name, which is what the Linux
script prints.

---

## 7. Datapoints

Metric types follow LogicMonitor scripted-DataSource convention:

- **Gauge** (`type=2`): store the reported number.
- **Derive** (`type=3`): store the per-second rate. Used for cumulative byte
  counters from `docker stats` (network and block I/O). Graphs are Bytes/s.
- Percents stay **gauge**, not derive.

All normal datapoints use **multi-line key-value** (`namevalue`) with key
`##WILDVALUE##.<datapoint>`. Stored numeric type is double (`dataType=7`).

### Linux — `Docker_Linux_Containers`

| Datapoint | Type | Unit | Meaning |
|---|---|---|---|
| `running` | gauge | 0/1 | 1 if inspect `State.Running` is true. **Alert:** `< 1 1 1` (critical when not running). |
| `paused` | gauge | 0/1 | 1 if paused. |
| `restarting` | gauge | 0/1 | 1 if restarting. |
| `oomKilled` | gauge | 0/1 | 1 if OOM-killed. |
| `healthStatus` | gauge | enum | `0` unhealthy, `1` healthy, `2` starting, `3` no health check. |
| `healthFailingStreak` | gauge | count | Docker health failing streak; `0` if no health check. |
| `swarmServiceMember` | gauge | 0/1 | 1 if `com.docker.swarm.service.name` is set. |
| `cpuPercent` | gauge | % | `docker stats` CPU percent. **Alert:** `> 80 90 95`. |
| `memoryUsedBytes` | gauge | bytes | Current usage (left side of MemUsage). |
| `memoryLimitBytes` | gauge | bytes | Limit (right side of MemUsage). |
| `memoryPercent` | gauge | % | `docker stats` memory percent. **Alert:** `> 80 90 95`. |
| `networkRxBytes` | derive | bytes/s | Cumulative Rx from NetIO, stored as a rate. |
| `networkTxBytes` | derive | bytes/s | Cumulative Tx from NetIO, stored as a rate. |
| `blockReadBytes` | derive | bytes/s | Cumulative block read, stored as a rate. |
| `blockWriteBytes` | derive | bytes/s | Cumulative block write, stored as a rate. |
| `pids` | gauge | count | `docker stats` PIDs. |
| `restartCount` | gauge | count | Inspect restart count (absolute, not a rate). |
| `exitCode` | gauge | code | Inspect exit code. |
| `uptimeSeconds` | gauge | seconds | `now - StartedAt`, floored at 0. |

### Windows — `Docker_Windows_Containers`

| Datapoint | Type | Unit | Meaning |
|---|---|---|---|
| `containerPresent` | gauge | 0/1 | 1 when inspect succeeded this poll. |
| `running` | gauge | 0/1 | Inspect `State.Running`. **Alert:** `< 1 1 1`. |
| `paused` | gauge | 0/1 | Inspect `State.Paused`. |
| `restarting` | gauge | 0/1 | Inspect `State.Restarting`. |
| `oomKilled` | gauge | 0/1 | Inspect `State.OOMKilled`. |
| `dead` | gauge | 0/1 | Inspect `State.Dead`. |
| `stateCode` | gauge | enum | `1` running, `2` paused, `3` restarting, `4` created, `0` exited, `-1` dead, `-2` unknown. |
| `healthCode` | gauge | enum | `1` healthy, `2` starting, `0` unhealthy, `-1` no health check. |
| `exitCode` | gauge | code | Inspect exit code. |
| `restartCount` | gauge | count | Inspect restart count. |
| `pid` | gauge | pid | Inspect `State.Pid`. |
| `uptimeSeconds` | gauge | seconds | Uptime while running; `0` if not running. |
| `cpuPercent` | gauge | % | `docker stats` CPU percent. **Alert:** `> 80 90 95`. |
| `memoryPercent` | gauge | % | `docker stats` memory percent. **Alert:** `> 80 90 95`. |
| `memoryUsageBytes` | gauge | bytes | Current usage (left side of MemUsage). |
| `memoryLimitBytes` | gauge | bytes | Limit (right side of MemUsage). |
| `networkRxBytes` | derive | bytes/s | Cumulative Rx, stored as a rate. |
| `networkTxBytes` | derive | bytes/s | Cumulative Tx, stored as a rate. |
| `blockReadBytes` | derive | bytes/s | Cumulative block read, stored as a rate. |
| `blockWriteBytes` | derive | bytes/s | Cumulative block write, stored as a rate. |
| `pids` | gauge | count | `docker stats` PIDs. |

Windows memory datapoint name is `memoryUsageBytes` (not `memoryUsedBytes`).
That is what the PowerShell script prints. Do not rename it in the portal
without changing the script.

Thresholds are only on **not running** and **high CPU / high memory**. CPU and
memory use a 1-poll trigger interval. Tune per customer after the first week.

---

## 8. Instance auto-properties from discover

These are instance-level `auto.*` properties from Active Discovery. Empty
values are still emitted (as empty strings) so the property exists.

### Linux (`auto.container.*` / `auto.swarm.*`)

| Property | Content |
|---|---|
| `auto.container.id` | Container ID from `docker ps`. |
| `auto.container.name` | Container name. |
| `auto.container.image` | Image name. |
| `auto.container.status` | Status text from `docker ps` (for example `Up 3 hours`). |
| `auto.container.workload.type` | `Docker Swarm Service` or `Standalone Docker Container`. |
| `auto.swarm.service.name` | `com.docker.swarm.service.name` label, or empty. |
| `auto.swarm.task.name` | `com.docker.swarm.task.name` label, or empty. |
| `auto.swarm.node.id` | `com.docker.swarm.node.id` label, or empty. |
| `auto.container.restart.policy` | HostConfig restart policy name. |
| `auto.container.network.mode` | HostConfig network mode. |

`&`, `#`, and newlines are stripped from property values so they cannot break
the `##` / `&` AD separators.

### Windows (`auto.docker.*`)

| Property | Content |
|---|---|
| `auto.docker.name` | Container name (leading `/` stripped). |
| `auto.docker.image` | `Config.Image`. |
| `auto.docker.status` | `State.Status`. |
| `auto.docker.platform` | `Platform`. |
| `auto.docker.storageDriver` | `Driver`. |
| `auto.docker.isolation` | `HostConfig.Isolation`. |

Windows property values percent-encode `%`, `&`, `=`, and `#` so AD parsing
stays intact.

---

## 9. Graphs

Each DataSource ships instance graphs plus two overview graphs (top instances
by the first line).

| Graph | Datapoints |
|---|---|
| CPU | `cpuPercent` |
| Memory | `memoryPercent` |
| Memory Bytes | used + limit (Linux: `memoryUsedBytes`; Windows: `memoryUsageBytes`) |
| Network Throughput | `networkRxBytes`, `networkTxBytes` (Bytes/s) |
| Block I/O | `blockReadBytes`, `blockWriteBytes` (Bytes/s) |
| Container State | running / paused / restarting / oomKilled (Windows also `dead`) |
| CPU Overview | `cpuPercent` per instance |
| Memory Overview | `memoryPercent` per instance |

---

## 10. Behaviour worth knowing

**Linux discovers running containers only.** A container that exits is dropped
on the next AD cycle. Collection also iterates running containers plus
`docker stats`, so a just-stopped instance can show NoData until AD deletes it.

**Windows wildvalue is the container ID.** The instance display name is the
sanitized container name. Use Wildvalue as Unique Identifier is on, so a
display-name change keeps history.

**Linux wildvalue is the sanitized container name.** Two containers whose
names collapse to the same wildvalue would collide; that is a Docker naming
problem, not a DataSource one.

**Byte datapoints are derive.** Raw script output is a cumulative counter from
`docker stats`. LogicMonitor stores bytes per second. Do not change those
datapoints to gauge unless you intend to graph the cumulative total.

**This is not LogicMonitor's cAdvisor Docker package.** Core module
`Docker_Containers_cAdvisor20` is a different AppliesTo and collection path.
You can run both only if you understand the double-monitoring cost.

**No portal write APIs.** Scripts only print AD/collect stdout. They do not
add devices, ack alerts, or restart Collectors.

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| DataSource does not apply | Resource lacks category `docker`, or `isLinux()` / `isWindows()` does not match. Check `system.categories` and `system.sysinfo`. |
| Active Discovery returns 0 instances | Script not on the Collector, not executable, or `docker ps` is empty. SSH to the Collector and run the discover command from section 2 as the Collector user. |
| `ERROR: Docker CLI was not found` | Install Docker CLI on the Collector host, or fix `PATH` for the Collector service. |
| `ERROR: Docker daemon is unavailable` | Collector user cannot access the daemon. On Linux, add the service user to group `docker` and restart the Collector service. |
| NoData on all datapoints | Collection script path in Collector Attributes does not match the file on disk. Confirm `##AGENTROOT##/local/bin/...` exists on that Collector. |
| NoData on CPU/memory only (Windows) | Inspect succeeded but `docker stats` failed. Run `-Mode collect` manually and read stderr. |
| Instances named with `_` instead of spaces | Expected. WILDVALUE cannot contain space, `:`, `#`, `\`, or `=`. |
| Import rejected / script type wrong | Re-import and then set Collection Method to BatchScript, Script Type to file, and paste the paths from section 3. |
| Alerts for stopped containers | Linux AD deletes inactive instances. If delete-inactive was turned off in the portal, turn it back on or disable alerting on stale instances. |
| High CPU on the Collector | `docker stats --no-stream` is invoked once per poll per host. Keep the collect interval at 60s or longer; do not drop it to 10s. |

Collector debug (Settings → Collectors → debug) can run the same command lines
the DataSource uses. Look at the Active Discovery and collection task output
for the raw stdout.

---

## 12. Recommended engagement workflow

1. Confirm the Collector is on the Docker host and `docker info` works as the
   Collector user.
2. Copy the script into `agent/local/bin/` on that Collector. Test discover and
   collect locally; keep a sample of stdout with the engagement notes.
3. Import the XML for that OS. Do not import the Windows XML onto a portal
   that has only Linux Docker hosts if you want to avoid unused modules; it is
   harmless but unused.
4. Add `docker` to `system.categories` on the host resource.
5. Run Active Discovery from the resource. Confirm instance names and
   `auto.*` properties.
6. Wait one collect interval. Confirm CPU, memory, and `running`.
7. Review the default thresholds with the customer before go-live.
8. Attach this folder's commit / PR to the engagement record.

---

## 13. Pushing updates from iPad (iSH)

```bash
cd ~/infosys-claude-scripts
git add "Docker Container DataSources"
git commit -m "Describe your change"
git push origin main
```

When prompted, enter your GitHub username and **paste** your personal access
token as the password. The token will not display as you paste it — that is
expected.
