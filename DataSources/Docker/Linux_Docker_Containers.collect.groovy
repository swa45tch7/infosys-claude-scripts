import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.JSch
import groovy.json.JsonSlurper
import java.time.Instant

def host = hostProps.get("system.hostname")
def user = hostProps.get("ssh.user")
def pass = hostProps.get("ssh.pass")
def cert = hostProps.get("ssh.cert")
def port = (hostProps.get("ssh.port") ?: "22") as int
// Set docker.cmd to "sudo -n docker" when the SSH user is not in the docker group.
def docker = hostProps.get("docker.cmd") ?: "docker"

if (!user || !(pass || cert)) {
    println "ssh.user and ssh.pass (or ssh.cert) must be set on ${host}"
    return 1
}

def jsch = new JSch()
if (cert) jsch.addIdentity(cert)
def session = jsch.getSession(user, host, port)
if (pass) session.setPassword(pass)
session.setConfig("StrictHostKeyChecking", "no")

def exec = { String cmd ->
    ChannelExec ch = (ChannelExec) session.openChannel("exec")
    def out = new ByteArrayOutputStream()
    def err = new ByteArrayOutputStream()
    ch.setCommand(cmd)
    ch.setOutputStream(out)
    ch.setErrStream(err)
    ch.connect(30000)
    long deadline = System.currentTimeMillis() + 90000
    while (!ch.isClosed() && System.currentTimeMillis() < deadline) sleep(100)
    int rc = ch.isClosed() ? ch.getExitStatus() : -1
    ch.disconnect()
    [rc: rc, out: out.toString("UTF-8"), err: err.toString("UTF-8").trim()]
}

def wild = { v -> (v ?: 'unknown').toString().trim().replaceAll(/[^A-Za-z0-9_.-]+/, '_').replaceAll(/^_+|_+$/, '') }

// Must match the instance key used by Active Discovery.
def instanceKey = { c ->
    def labels = c.Config?.Labels ?: [:]
    def task = (labels['com.docker.swarm.task.name'] ?: '').toString()
    def taskId = (labels['com.docker.swarm.task.id'] ?: '').toString()
    def name = (c.Name ?: '').toString().replaceFirst('^/', '')
    (task && taskId && task.endsWith('.' + taskId)) ? task.substring(0, task.length() - taskId.length() - 1) : name
}

def number = { v ->
    try { new BigDecimal((v ?: '0').toString().replace('%', '').replace(',', '').trim()) } catch (ignored) { BigDecimal.ZERO }
}

// docker stats prints SI units for I/O (kB, MB) and binary units for memory (KiB, MiB).
def bytes = { v ->
    def m = ((v ?: '').toString().trim() =~ /(?i)^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?)(i?)b$/)
    if (!m.matches()) return 0L
    def prefix = m.group(2).toLowerCase()
    int power = prefix ? 'kmgtpe'.indexOf(prefix) + 1 : 0
    def base = m.group(3) ? 1024G : 1000G
    (new BigDecimal(m.group(1)) * base.pow(power)).longValue()
}
def pair = { v ->
    def parts = (v ?: '').toString().split(/\s*\/\s*/, 2)
    [bytes(parts[0]), parts.size() > 1 ? bytes(parts[1]) : 0L]
}

try {
    session.connect(30000)

    def ps = exec("${docker} ps -q --no-trunc")
    if (ps.rc != 0) throw new Exception("docker ps failed (rc=${ps.rc}): ${ps.err}")
    def ids = ps.out.readLines()*.trim().findAll { it }
    if (!ids) return 0

    def inspect = exec("${docker} inspect ${ids.join(' ')}")
    if (!inspect.out.trim()) throw new Exception("docker inspect failed (rc=${inspect.rc}): ${inspect.err}")
    def containers = new JsonSlurper().parseText(inspect.out)

    def stats = [:]
    exec("${docker} stats --no-stream --no-trunc --format '{{json .}}' ${ids.join(' ')}").out.readLines().findAll { it.trim() }.each { line ->
        def s = new JsonSlurper().parseText(line)
        [s.ID, s.Container, s.Name].findAll { it }.each { stats[it.toString()] = s }
    }

    containers.each { c ->
        def w = wild(instanceKey(c))
        def name = (c.Name ?: '').toString().replaceFirst('^/', '')
        def s = stats[c.Id?.toString()] ?: stats[name]
        def labels = c.Config?.Labels ?: [:]
        def health = c.State?.Health?.Status?.toString()
        def healthCode = health == 'healthy' ? 1 : health == 'unhealthy' ? 0 : health == 'starting' ? 2 : 3
        long uptime = 0
        if (c.State?.Running) {
            try { uptime = Math.max(0L, Instant.now().epochSecond - Instant.parse(c.State.StartedAt.toString()).epochSecond) } catch (ignored) {}
        }
        def mem = pair(s?.MemUsage)
        def net = pair(s?.NetIO)
        def blk = pair(s?.BlockIO)
        def vals = [
            running            : c.State?.Running ? 1 : 0,
            paused             : c.State?.Paused ? 1 : 0,
            restarting         : c.State?.Restarting ? 1 : 0,
            oomKilled          : c.State?.OOMKilled ? 1 : 0,
            healthStatus       : healthCode,
            healthFailingStreak: (c.State?.Health?.FailingStreak ?: 0),
            swarmServiceMember : labels['com.docker.swarm.service.name'] ? 1 : 0,
            cpuPercent         : number(s?.CPUPerc),
            memoryUsedBytes    : mem[0],
            memoryLimitBytes   : mem[1],
            memoryPercent      : number(s?.MemPerc),
            networkRxBytes     : net[0],
            networkTxBytes     : net[1],
            blockReadBytes     : blk[0],
            blockWriteBytes    : blk[1],
            pids               : number(s?.PIDs),
            restartCount       : (c.RestartCount ?: 0),
            exitCode           : (c.State?.ExitCode ?: 0),
            uptimeSeconds      : uptime
        ]
        vals.each { k, v -> println "${w}.${k}=${v}" }
    }
    return 0
} catch (e) {
    println "Docker collection failed on ${host}: ${e.message}"
    return 1
} finally {
    session.disconnect()
}
