# Docker Swarm NetScan

**What it does:** A LogicMonitor NetScan that asks a Docker Swarm manager for its list of nodes and adds each node to LogicMonitor as a device (or updates it if it is already there). It runs inside LogicMonitor on a Collector, not on your computer.

**File to import:** `Docker_Swarm_Onboard_Netscan.groovy` (you paste its text into a NetScan)

**What you need:**
- Rights in the LogicMonitor portal to create NetScans.
- A Collector on version 32.400 or newer that can reach the Swarm manager on port 2375. Test it from the Collector: `curl -sS http://<manager-ip>:2375/info`

**How to run:**
1. In LogicMonitor go to **Settings > NetScans > Add > Advanced NetScan**.
2. Give it a name, pick the Collector above, and set **Discovery Method** to **Enhanced Script NetScan**.
3. Under **Resource Credentials**, choose custom credentials and add:
   - `docker.api.host` = `<manager-ip>`
   - `docker.onboard.folder` = `<group-path>` (for example `Customers/<customer>/Docker`)
   - Only if the manager uses TLS on port 2376: `docker.api.port` = `2376` and `docker.api.tls.dir` = `<collector-folder-with-ca.pem-cert.pem-key.pem>`
4. Under **Script**, choose **Embed a Groovy script** and paste the whole of `Docker_Swarm_Onboard_Netscan.groovy`.
5. Set **Default Group** to the same path as `docker.onboard.folder`. Leave any "delete missing devices" option off.
6. Click **Save**, then **Dry Run**. If the result looks right, click **Run**.

**What you get:** One device per active Swarm node in the `docker.onboard.folder` group. Results are under the NetScan's **Scan History**.

**If something goes wrong:**
- `NetScan property 'docker.api.host' is required` — add the property in step 3 (names are case-sensitive).
- `Cannot reach Docker Engine API` — the Collector cannot reach the manager; check the firewall, then re-test with `curl`.
- `is a Swarm worker` — point `docker.api.host` at a Swarm **manager** instead.
