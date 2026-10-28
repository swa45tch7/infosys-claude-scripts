# Docker Swarm NetScan

A LogicMonitor Enhanced Script NetScan that reads the node list from a Docker
Swarm manager and adds (or updates) one LogicMonitor resource per Swarm node.
It runs **inside LogicMonitor on a Collector**, not on a laptop.

## What you need

- A Collector on version **32.400 or newer** that can reach:
  - the Swarm manager's Docker Engine API: `http://<manager-ip>:2375` (HTTP), or
    `https://<manager-ip>:2376` with client certificates (TLS);
  - each node's cAdvisor port (default 8080) if you want container metrics later.
- Test from the Collector first: `curl -sS http://<manager-ip>:2375/info`

## How to set it up

1. **Settings > NetScans > Add NetScan Options > Add Advanced NetScan.**
2. Name it (for example `<Customer> Docker Swarm`) and pick the Collector above.
3. **Discovery Method:** Enhanced Script NetScan.
4. **Resource Credentials:** Use custom credentials for this scan, then add:

   | Property | Value |
   |---|---|
   | `docker.api.host` | `<manager-ip>` or the manager DNS name (required) |
   | `docker.onboard.folder` | Resource group path, e.g. `Customers/<Customer>/Docker` (required) |
   | `docker.api.port` | `2375` for HTTP, or `2376` for TLS |
   | `docker.port` | `8080` (cAdvisor port) |
   | `docker.name.by` | `address` |

   For TLS only, also set `docker.api.ssl` = `true` and `docker.api.tls.dir` = a
   folder on the Collector holding `ca.pem`, `cert.pem` and `key.pem`.
5. **Script:** Embed a Groovy script, and paste the whole of
   `Docker_Swarm_Onboard_Netscan.groovy`.
6. Set **Default Group** to the same path as `docker.onboard.folder`. Leave any
   "remove/delete missing devices" option **off**.
7. **Save**, then **Dry Run**, then **Run**. Turn on a daily schedule only after
   the first run looks right.

## What you get

One resource per ready, non-drained Swarm node in the `docker.onboard.folder`
group, with `docker.node.*` and `docker.swarm.cluster_id` properties and
`system.categories` set to `Docker,DockerSwarm` plus `Linux` or `Windows`.
Results are under **Settings > NetScans > (this NetScan) > Scan History**.

## Common errors

| Message | Fix |
|---|---|
| `MissingPropertyException: netscanProps` | It was run outside LogicMonitor. Paste it into a NetScan as above. |
| `Cannot load the official lm.emit snippet` | Upgrade the Collector to 32.400+ and use Enhanced Script NetScan. |
| `NetScan property 'docker.api.host' is required` (or `'docker.onboard.folder'`) | Add the property under custom credentials (names are case-sensitive). |
| `Cannot reach Docker Engine API` | Fix firewall/port from the Collector; re-test with `curl`. |
| `is a Swarm worker` / `not part of an active Swarm` | Point `docker.api.host` at a Swarm **manager**. |
