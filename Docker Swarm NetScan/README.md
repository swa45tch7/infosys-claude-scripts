A NetScan job that completes with 0 resources is a failure. Read the script output and Resource Status at **Settings → NetScans → (this NetScan) → Scan History** (open a history row for the details panel).

# Docker Swarm Enhanced Script NetScan

**Do not run this on a laptop.** There is no `netscanProps` outside LogicMonitor.
`groovy Docker_Swarm_Onboard_Netscan.groovy`, `python`, and double-clicking the
file all fail immediately with `MissingPropertyException: netscanProps` (or
"command not found"). This package only runs as a **LogicMonitor Enhanced
Script NetScan** on a **collector that can reach the Swarm manager Engine API**.

Paste the Groovy into the portal (exact clicks below). The collector must be
**32.400 or higher**. Output is `lmEmit.resource(resources, debug)` from the
official `lm.emit` snippet — **not** `println` JSON. A raw `println` of `[]`
completes the job with no errors and adds zero devices; this script throws
instead. The NetScan job creates or updates resources. It does not call
LogicMonitor write REST APIs (`POST /device/devices`, ack/clear alert, collector
restart).

---

## 1. Create the NetScan (portal clicks)

1. In LogicMonitor: **Settings → NetScans → Add NetScan Options → Add Advanced NetScan**.
2. **Name:** `ACME Docker Swarm` (use the customer name).
3. **Description:** `Onboard Swarm nodes from the manager Engine API. Do not emit containers.`
4. **NetScan Group:** the customer's NetScan group, or leave blank for `@default`.
5. **Collector Group** then **Preferred Collector:** pick the collector that already
   has TCP reachability to the Swarm manager Engine API (default **2376/tcp** with
   TLS). That collector also needs reachability to **each node's cAdvisor port**
   (default **8080/tcp**) after onboarding, or container DataSources will apply
   with no instances.
6. **Discovery Method:** **Enhanced Script NetScan**.
7. Leave **Send email notification when the scan is finished** off unless the
   engagement wants a completion mail.
8. **Resource Credentials Options:** **Use custom credentials for this scan**.
   Select **Add** and enter **exactly** these keys (names are case-sensitive).

   **Required — the scan fails closed without both:**

   | Property | Exact value to enter |
   |---|---|
   | `docker.api.host` | Manager IP or DNS the collector uses, e.g. `10.20.30.40` or `swarm-mgr.acme.local`. IPv6 is allowed (`fd00:1:2::10`); do not wrap it in brackets here. |
   | `docker.onboard.folder` | Customer resource path, e.g. `Infosys/Customers/ACME/Docker`. Missing path levels are created. **Never omit this.** |

   **Production TLS (these are the script defaults — set them so the portal matches the code):**

   | Property | Exact value |
   |---|---|
   | `docker.api.ssl` | `true` |
   | `docker.api.port` | `2376` |
   | `docker.api.ssl.verify` | `true` |
   | `docker.api.tls.dir` | Collector path to the Docker client bundle, e.g. `/usr/local/logicmonitor/agent/conf/docker-tls` (Linux) or `C:\Program Files (x86)\LogicMonitor\Agent\conf\docker-tls` (Windows). That directory must contain `ca.pem`, `cert.pem`, and `key.pem`. |

   **Always set for cAdvisor / display:**

   | Property | Exact value |
   |---|---|
   | `docker.port` | `8080` unless cAdvisor was published on another host port |
   | `docker.name.by` | `address` |

   **Set only when the engagement needs them:**

   | Property | Exact value |
   |---|---|
   | `docker.collector.id` | Numeric collector ID, e.g. `171`. Omit to let LogicMonitor assign using the NetScan collector. |
   | `docker.include.down` | `true` only if down/unreachable nodes must appear. Default is omit / `false`. |
   | `docker.include.drained` | `true` only if drained nodes must appear. Default is omit / `false`. |

   **Lab HTTP Engine API only** (unauthenticated 2375 is remote root — not for production):

   | Property | Exact value |
   |---|---|
   | `docker.api.ssl` | `false` |
   | `docker.api.port` | `2375` |
   | Do not set | `docker.api.tls.dir` |

9. **Script source:** **Embed a Groovy script** (recommended size limit **32 KB**).
   Open `Docker_Swarm_Onboard_Netscan.groovy` from this folder, copy the entire
   file, paste it into the script field. Do not add `println` lines; emit is
   `lmEmit.resource(resources, debug)` only.
10. **Parent Group / Default Group:** enter the **same path** as
    `docker.onboard.folder` (e.g. `Infosys/Customers/ACME/Docker`). The script
    always emits `groupName`, so this is only a safety net if emit omitted a group.
11. **Ignore system.ips when checking existing Resources for duplicates:** leave
    **off**, unless nodes legitimately share an IP (NAT).
12. **Exclude duplicate IP addresses:** **Matching resources already discovered by this NetScan**.
13. **Criteria for updating Existing Resources:** **Resources discovered by this NetScan**.
14. **Do not enable any option that removes or deletes devices missing from a later scan**
    (wording varies: "Remove devices that are missing from the scan", "Delete devices
    that no longer exist", similar). Leave it off unless the engagement **explicitly**
    says decommissioned Swarm nodes should be deleted automatically. A manager outage
    or a drained node would otherwise delete production resources. This script never
    calls delete-device APIs; that checkbox is a portal NetScan feature, not something
    to turn on by default.
15. **Run this NetScan on a schedule:** leave **off** until the first Dry Run and
    first live run succeed. Then daily is enough.
16. Select **Save** (not Save and Run) the first time.
17. Dry Run: **Settings → NetScans** → select this NetScan → **Dry Run**.
    Open **Scan History** and confirm devices would be **added** / **updated**, not
    **ignored**. Zero resources here is a **failure** — read the history row
    details panel for the thrown reason.
18. Run live: **Settings → NetScans** → this NetScan → **Run**. Then
    **Resources** → the folder in `docker.onboard.folder`. Confirm Resource
    Status again under **Scan History**.

---

## Contents

| File | Purpose |
|---|---|
| `Docker_Swarm_Onboard_Netscan.groovy` | Groovy pasted into the Enhanced Script NetScan. Not a CLI. |
| `README.md` | This document |

Keep both files in this folder. There is nothing to `pip install` and nothing to
run from a shell.

---

## 2. Collector reachability (do this before Save and Run)

The **Preferred Collector** on the NetScan must already be able to:

1. **Reach the Swarm manager Engine API** at `docker.api.host`:`docker.api.port`
   (HTTPS **2376** by default, with the client certificate the manager trusts).
2. **Reach cAdvisor on every node** at that node's `hostname` (the address the
   script emits) on `docker.port` (**8080** by default). cAdvisor is **not** part
   of Swarm. If it is not running, nodes still onboard; container instances stay
   empty.

From the **collector host** (not your laptop):

```bash
# TLS / mTLS manager (production). Files are the Docker bundle copied onto the collector.
curl -sS --fail --cacert /usr/local/logicmonitor/agent/conf/docker-tls/ca.pem \
  --cert    /usr/local/logicmonitor/agent/conf/docker-tls/cert.pem \
  --key     /usr/local/logicmonitor/agent/conf/docker-tls/key.pem \
  https://<docker.api.host>:2376/info

# Confirm this host is a manager: Swarm.ControlAvailable must be true.
# Lab HTTP only:
# curl -sS --fail http://<docker.api.host>:2375/info
```

Copy the TLS bundle onto the collector **before** the first scan when
`docker.api.ssl` is `true` (the default):

```bash
# Linux collector
sudo mkdir -p /usr/local/logicmonitor/agent/conf/docker-tls
sudo cp ca.pem cert.pem key.pem /usr/local/logicmonitor/agent/conf/docker-tls/
sudo chown -R logicmonitor:logicmonitor /usr/local/logicmonitor/agent/conf/docker-tls
sudo chmod 700 /usr/local/logicmonitor/agent/conf/docker-tls
sudo chmod 600 /usr/local/logicmonitor/agent/conf/docker-tls/*
```

Windows collector: put the same three files in
`C:\Program Files (x86)\LogicMonitor\Agent\conf\docker-tls\` and set
`docker.api.tls.dir` to that path.

If `curl` from the collector fails, the NetScan will fail the same way. Fix
routing, firewall, and certificates first.

Instead of `docker.api.tls.dir` you may set the three PEM properties
(`docker.api.tls.ca`, `docker.api.tls.cert`, `docker.api.tls.key`) to collector
file paths, or set `docker.api.tls.pkcs12` plus `docker.api.tls.pkcs12.pass`.
Use PKCS#8 (`BEGIN PRIVATE KEY`) or RSA (`BEGIN RSA PRIVATE KEY`) keys.
Encrypted PKCS#8 needs `docker.api.tls.key.pass`. SEC1 `BEGIN EC PRIVATE KEY`
is not supported — convert to PKCS#8 or PKCS#12 on the collector.

---

## 3. Every NetScan property

Values are strings. Booleans must be the text `true` or `false`.

| Property | Required | Default in the script | Allowed values |
|---|---|---|---|
| `docker.api.host` | Yes | none — fails if missing | Manager IP or DNS. IPv6 without brackets. |
| `docker.onboard.folder` | Yes | none — fails if missing | Slash path, e.g. `Infosys/Customers/ACME/Docker` |
| `docker.api.ssl` | No | `true` | `true` or `false` |
| `docker.api.port` | No | `2376` when ssl is true; `2375` when ssl is false | TCP port |
| `docker.api.ssl.verify` | No | `true` | `true` or `false`. `false` is lab-only; verification is **per connection**, never JVM-wide. |
| `docker.api.tls.dir` | When ssl+verify | none | Collector directory with `ca.pem`, `cert.pem`, `key.pem` |
| `docker.api.tls.ca` | Alternative | none | Path or PEM starting `-----BEGIN` |
| `docker.api.tls.cert` | Alternative | none | Path or PEM |
| `docker.api.tls.key` | Alternative | none | Path or PEM |
| `docker.api.tls.key.pass` | If key is encrypted PKCS#8 | none | Password |
| `docker.api.tls.pkcs12` | Alternative | none | Collector path to a `.p12` / `.pfx` |
| `docker.api.tls.pkcs12.pass` | With PKCS#12 | none | Password |
| `docker.port` | No | `8080` | cAdvisor host port written onto every resource |
| `docker.name.by` | No | `address` | `address` or `hostname` only |
| `docker.collector.id` | No | omit | Digits only, e.g. `171` |
| `docker.include.down` | No | `false` | `true` or `false` |
| `docker.include.drained` | No | `false` | `true` or `false` |

`docker.api.ssl=true` with `docker.api.ssl.verify=true` (defaults) **requires**
a CA and a client certificate. The scan fails before calling Docker if they are
missing, with a message naming `docker.api.tls.dir`.

Invalid `docker.name.by` or non-numeric `docker.collector.id` fails with a
property error. Do not invent other property names.

---

## 4. What the script queries on Docker

All calls are Engine API **GET**s to the **one** manager in `docker.api.host`.
Workers are not contacted during the scan.

| Method | Path | Why |
|---|---|---|
| GET | `/info` | Require `Swarm.LocalNodeState=active` and `Swarm.ControlAvailable=true`. Read `Swarm.Cluster.ID`. |
| GET | `/nodes` | Every Swarm node (id, hostname, address, role, OS, availability, state, labels, engine version, manager status). |
| GET | `/services` | Map service ID → `Spec.Name` for a stable per-node service list. |
| GET | `/tasks?filters={"desired-state":["running"]}` | Count tasks whose **`Status.State` is `running`** (not merely desired-state). Build the sorted service-name set per node. |

The script does **not** call `docker ps`, does **not** hit cAdvisor, and does
**not** call LogicMonitor REST.

Nodes are skipped unless `Status.State` is `ready`, and unless `Availability`
is not `drain`. `pause` is still emitted (tasks keep running). Set
`docker.include.down=true` / `docker.include.drained=true` only when the
engagement wants those nodes in the portal.

Linux vs Windows comes from `node.Description.Platform.OS`. `Linux` is **not**
forced onto Windows nodes. `isLinux()` in AppliesTo is still sysinfo-based and
fills in after collector discovery; this script sets `hasCategory("Linux")` or
`hasCategory("Windows")` immediately.

---

## 5. What appears in the portal

One **resource per emitted Swarm node**. Containers are **not** devices.

| Field | Source |
|---|---|
| Hostname (what the collector targets) | `Status.Addr` when `docker.name.by=address`; `Description.Hostname` when `hostname` |
| Display name | Swarm node hostname |
| Resource group | `docker.onboard.folder` |
| Collector | `docker.collector.id` if set; otherwise the NetScan collector / group preferred collector |

**Every resource gets these host properties:**

| Property | Meaning |
|---|---|
| `docker.node.id` | Swarm `node.ID`. Stored as a custom property. Use it to recognize the node if DHCP changes `hostname`. (`auto.`, `predef.`, and `system.` hostProps are ignored except `system.categories`; this script does not emit those prefixes.) |
| `docker.port` | cAdvisor port (default `8080`) |
| `docker.swarm.cluster_id` | Cluster ID from `/info` |
| `docker.node.hostname` | Swarm hostname |
| `docker.node.address` | Swarm advertise address |
| `docker.node.role` | `manager` or `worker` |
| `docker.node.availability` | `active`, `pause`, or `drain` |
| `docker.node.state` | `ready`, `down`, … |
| `docker.node.os` | `linux` / `windows` from the node |
| `docker.node.architecture` | e.g. `x86_64` |
| `docker.engine.version` | Engine version on that node |
| `docker.node.running_task_count` | Count of tasks with `Status.State=running` — **not** `docker ps` and not a container-instance count |
| `docker.node.services` | Sorted unique service names on that node (stable order) |
| `docker.node.labels` | Sorted `key=value` node labels |
| `system.categories` | `Docker,DockerSwarm` plus `Linux` or `Windows` from node OS, plus `DockerSwarmManager` on managers |

**Manager resources only** also get `docker.api.port`, `docker.api.ssl`,
`docker.node.is_leader`, `docker.node.reachability`. Workers do **not** receive
the manager Engine API port/ssl — workers usually do not expose the Engine API.

After the collector's discovery cycle: Ping / Host Status against the emitted
hostname, then Docker LogicModules that match `system.categories`. Container
instances appear under **Docker_Containers_cAdvisor20** on that node when
cAdvisor answers (next section).

Check results under **Settings → NetScans → (this NetScan) → Scan History**.
Open a history row for the details panel. Resource Status should show added or
updated. An empty resource list **throws** so a scan cannot complete with 0
resources and no errors.

---

## 6. cAdvisor and container discovery

cAdvisor is a **separate** container/service. Swarm does not install it.

LogicMonitor documents Docker container monitoring as: run cAdvisor, then
**Docker_Containers_cAdvisor20** Active Discovery polls

`http://<resource hostname>:<docker.port>/api/v2.0/spec`

and creates **instances on the node resource**. Do not expect a device per
container.

**On a single Docker host:**

```bash
sudo docker run \
  --volume=/:/rootfs:ro \
  --volume=/var/run:/var/run:ro \
  --volume=/sys:/sys:ro \
  --volume=/var/lib/docker/:/var/lib/docker:ro \
  --publish=8080:8080 \
  --detach=true \
  --name=cadvisor \
  gcr.io/cadvisor/cadvisor:latest
```

**On Swarm, publish in host mode** (global service, one task per node). Ingress
mesh load-balances 8080 across nodes and mixes container metrics:

```yaml
ports:
  - target: 8080
    published: 8080
    protocol: tcp
    mode: host
```

RHEL/CentOS also needs `--privileged=true` and `--volume=/cgroup:/cgroup:ro`.
cAdvisor does not work on RHEL 7.6.

If `docker.port` is not `8080`, set the NetScan property to the published host
port **before** the first live run so every resource gets the right value.

From the collector, `curl -sS --fail http://<node-ip>:8080/api/v2.0/spec`
must succeed for container instances to appear. Nodes still onboard if this
fails; only container discovery is empty.

---

## 7. Behaviour worth knowing

**This is not the onboarding CSV script and not `python3 -m lm_infosys`.** Those
are different packages. This Groovy never runs in a terminal.

**TLS defaults are production defaults.** `ssl=true`, port `2376`, verify `true`,
mTLS files required. Lab-only HTTP is `docker.api.ssl=false` and
`docker.api.port=2375`.

**No JVM-wide trust-all.** If `docker.api.ssl.verify=false` (lab), the script
builds a **per-connection** socket factory and hostname verifier. It does not
call `HttpsURLConnection.setDefaultSSLSocketFactory`.

**Stable identity.** Hostname follows `docker.name.by` and must stay consistent
across runs (do not flip `address`/`hostname` on a live NetScan). `docker.node.id`
is the Swarm node ID. Keep **Exclude duplicate IP addresses** set to this NetScan
only.

**Down/drained nodes are omitted** unless you set the include properties.

**Service list is sorted** (`TreeSet`) so `docker.node.services` does not churn.

**IPv6 `docker.api.host`** is wrapped in `[]` only in the Engine API URL.

**Error streams are drained** and connections `disconnect()`ed. Non-200 Docker
responses fail the scan with HTTP code and a short body.

**Emit is `lmEmit.resource(resources, debug)` only** (`debug` is `false`). Do
not `println` JSON; current Enhanced Script NetScan ignores that path and a
`[]` print looks like success with zero devices. Failures `throw Exception`
and show in **Settings → NetScans → Scan History** (details panel) and collector
`wrapper.log`. `lm.emit` also throws if a resource uses keys other than
`hostname`, `displayname`, `hostProps`, `groupName`, `collectorId`.

**`system.categories` is appended** by LogicMonitor on later updates (portal
behaviour). Do not keep adding extra category properties by hand on the same
device if you are also scanning.

**Script size:** Enhanced Script Groovy has a recommended **32 KB** limit. Do
not paste unrelated code into the same NetScan.

---

## 8. Recommended engagement workflow

1. Confirm the collector can `curl` the manager Engine API (section 2) and that
   cAdvisor host-mode 8080 is the engagement standard (or record the real port).
2. Copy TLS files onto the collector. Set `docker.api.tls.dir`.
3. Create the NetScan with the clicks in section 1. Set
   `docker.api.host` and `docker.onboard.folder` for **this customer only**.
4. **Save**, then **Dry Run**. Fix every failure in Scan History before a live run.
5. **Run** once. Confirm node count in the resource folder matches ready,
   non-drained Swarm nodes (`docker node ls` on a manager).
6. Wait one discovery cycle. Confirm **Docker_Containers_cAdvisor20** instances
   on a sample node. If the DataSource is applied but instance count is 0,
   cAdvisor is not reachable from the collector on `docker.port`.
7. Only then enable a **daily** schedule.
8. Leave remove-missing-from-scan **off**.

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `MissingPropertyException: netscanProps` or `groovy: command not found` | The file was run on a laptop or collector shell. Do not. Create it with **Settings → NetScans → Add NetScan Options → Add Advanced NetScan**, Discovery Method = **Enhanced Script NetScan**, **Embed a Groovy script**. |
| `Cannot load the official lm.emit snippet` | Collector is below **32.400**, or Discovery Method is not **Enhanced Script NetScan**. Upgrade the Preferred Collector and re-create with the click path in section 1. |
| Job completes with 0 resources and no errors | Old script that `println` JSON (`[]` / `return 0`). Re-paste this file. This version throws if the resource list is empty; the reason is in **Settings → NetScans → Scan History** (open the row). |
| `docker.api.host` is required | Custom credentials are missing or the key is misspelled. Add `docker.api.host` under **Use custom credentials for this scan**. |
| `docker.onboard.folder` is required | Same place. Set the customer path. There is no default folder. |
| `docker.api.ssl is true ... no CA was provided` / `no client certificate` | Copy `ca.pem`, `cert.pem`, `key.pem` onto the collector and set `docker.api.tls.dir`, or set PKCS#12. Defaults require mTLS. |
| `TLS material '...' is not a file on this collector` | Path is on your laptop, not the collector, or the collector service user cannot read it. Use the collector filesystem path from section 2. |
| `Cannot reach Docker Engine API` | Collector cannot connect to host:port. Test with `curl` **from the collector**. Fix firewall, `docker.api.host`, `docker.api.port`, `docker.api.ssl`. For TLS hostname mismatch, `docker.api.host` must match a SAN/CN on the server certificate (or, lab only, `docker.api.ssl.verify=false`). |
| `Docker API ... returned HTTP 400/401/403` | mTLS rejected. Wrong client cert, CA, or you pointed HTTP at 2376. Align `docker.api.ssl`/`port` with how `dockerd` is listening. |
| `not part of an active Swarm` | `docker.api.host` is a standalone Engine. Point at a Swarm manager. |
| `is a Swarm worker` | `ControlAvailable` is false. Use a manager IP/DNS. |
| `docker.name.by` must be exactly address or hostname | Typo. Use `address` or `hostname`. |
| `docker.collector.id` must be a numeric collector ID | Use digits only (`171`), not the collector hostname. |
| `No Swarm nodes were emitted` | Every node was skipped (down, drained, or empty address/hostname) or `/nodes` was empty. The message lists skip reasons. Set `docker.include.down=true` / `docker.include.drained=true` only if the engagement wants those nodes. |
| Scan History success but no devices | The pasted script still uses `println JsonOutput.toJson(resources)` instead of `lmEmit.resource`. Re-paste this file. |
| Duplicate resources after DHCP or after changing `docker.name.by` | Do not change `docker.name.by` on an existing NetScan. Find the device by `docker.node.id` and delete duplicates **in the portal UI** only if the engagement agrees. |
| Windows node tagged Linux | You are not on this script. This version sets OS from `Platform.OS`. Re-paste. |
| Worker has `docker.api.port` / `docker.api.ssl` | Same — re-paste this file. Workers must not get manager Engine API props. |
| `docker.node.services` changes order each run | Old script. This version sorts names. Re-paste. |
| DataSource applied, 0 container instances | cAdvisor missing, not published in **host mode**, or collector cannot open `docker.port` on the node. `curl` the spec URL from the collector. |
| Nodes onboard, no Docker DataSources | Wait for discovery. Confirm `system.categories` contains `Docker`. Install the Docker LogicModules in the portal if the account does not have them. `isLinux()` may be false until sysinfo runs; `hasCategory("Linux")` is set from Swarm OS at scan time. |
| Collector log hang / leak on repeated failures | You are not on this script (error streams now drained). Re-paste. |
| IPv6 URL error | `docker.api.host` must be the raw address; the script adds brackets for the URL. Do not paste `https://...` into the property. |

Portal output (use this first): **Settings → NetScans → (this NetScan) → Scan History** → open the run → details panel / Device Discovery Logs.

Collector logs (same thrown text): Linux
`/usr/local/logicmonitor/agent/logs/wrapper.log`; Windows
`C:\Program Files (x86)\LogicMonitor\Agent\logs\wrapper.log`.

---

## 10. Safety

- Enhanced Script NetScan emit only: `lmEmit.resource(resources, debug)`. No raw `println` JSON.
- No `POST /device/devices`, no ack/clear alert, no collector restart.
- Do not enable remove-missing-from-scan unless the engagement says so (section 1 step 14).
- Unauthenticated Engine API on **2375** is remote root. Production uses **2376** + mTLS.
- Do not put two customers in one `docker.onboard.folder`.

---

## 11. Pushing updates from iPad (iSH)

```bash
cd ~/infosys-claude-scripts
git add "Docker Swarm NetScan"
git commit -m "Describe your change"
git push origin main
```

When prompted, enter your GitHub username and **paste** your personal access
token as the password. The token will not display as you paste it — that is expected.
