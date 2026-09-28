import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.JSch
import groovy.json.JsonSlurper

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

def safe = { v -> (v ?: '').toString().replaceAll(/[\r\n]+/, ' ').replace('&', '_').replace('=', '_').replace('#', '_').trim() }
def wild = { v -> (v ?: 'unknown').toString().trim().replaceAll(/[^A-Za-z0-9_.-]+/, '_').replaceAll(/^_+|_+$/, '') }

// Swarm task containers are named <service>.<slot>.<taskId>; the task id changes on every
// redeploy, so key swarm instances on <service>.<slot> to keep history on one instance.
def instanceKey = { c ->
    def labels = c.Config?.Labels ?: [:]
    def task = (labels['com.docker.swarm.task.name'] ?: '').toString()
    def taskId = (labels['com.docker.swarm.task.id'] ?: '').toString()
    def name = (c.Name ?: '').toString().replaceFirst('^/', '')
    (task && taskId && task.endsWith('.' + taskId)) ? task.substring(0, task.length() - taskId.length() - 1) : name
}

try {
    session.connect(30000)

    def ps = exec("${docker} ps -q --no-trunc")
    if (ps.rc != 0) throw new Exception("docker ps failed (rc=${ps.rc}): ${ps.err}")
    def ids = ps.out.readLines()*.trim().findAll { it }
    if (!ids) return 0

    // Containers can stop between ps and inspect; inspect still prints the ones it found.
    def inspect = exec("${docker} inspect ${ids.join(' ')}")
    if (!inspect.out.trim()) throw new Exception("docker inspect failed (rc=${inspect.rc}): ${inspect.err}")

    new JsonSlurper().parseText(inspect.out).each { c ->
        def labels = c.Config?.Labels ?: [:]
        def name = (c.Name ?: '').toString().replaceFirst('^/', '')
        def key = instanceKey(c)
        def svc = labels['com.docker.swarm.service.name'] ?: ''
        def workload = svc ? 'Docker Swarm Service' : 'Standalone Docker Container'
        def props = [
            "auto.container.id=${safe(c.Id)}",
            "auto.container.name=${safe(name)}",
            "auto.container.image=${safe(c.Config?.Image)}",
            "auto.container.status=${safe(c.State?.Status)}",
            "auto.container.workload.type=${safe(workload)}",
            "auto.swarm.service.name=${safe(svc)}",
            "auto.swarm.task.name=${safe(labels['com.docker.swarm.task.name'])}",
            "auto.swarm.node.id=${safe(labels['com.docker.swarm.node.id'])}",
            "auto.container.restart.policy=${safe(c.HostConfig?.RestartPolicy?.Name)}",
            "auto.container.network.mode=${safe(c.HostConfig?.NetworkMode)}"
        ]
        println "${wild(key)}##${safe(key)}##${safe(workload)}: ${safe(c.Config?.Image)}####${props.join('&')}"
    }
    return 0
} catch (e) {
    println "Docker discovery failed on ${host}: ${e.message}"
    return 1
} finally {
    session.disconnect()
}
