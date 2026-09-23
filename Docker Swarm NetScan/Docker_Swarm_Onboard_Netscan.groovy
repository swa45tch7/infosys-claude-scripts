/*
 * Enhanced Script NetScan — Docker Swarm onboarding
 *
 * THIS IS NOT A LAPTOP SCRIPT. Do not run it with groovy/java locally.
 * There is no netscanProps outside a LogicMonitor collector. Paste this file
 * into Settings > NetScans > Add NetScan Options > Add Advanced NetScan,
 * Discovery Method = Enhanced Script NetScan, Embed a Groovy script.
 * Collector 32.400+ that can reach the Swarm manager Engine API.
 *
 * Output is lmEmit.resource(resources, debug) — not println JSON. A job that
 * completes with 0 resources is a failure (this script throws). The NetScan
 * job creates or updates devices. No LogicMonitor write REST APIs.
 *
 * Containers are not emitted as resources. LogicMonitor's Docker Containers
 * DataSource (Docker_Containers_cAdvisor20) discovers them as instances on
 * each node by polling cAdvisor at http://<host>:<docker.port>/api/v2.0/spec.
 *
 * Required NetScan properties (custom credentials on the NetScan):
 *   docker.api.host           Swarm manager Engine API host (IP or DNS).
 *   docker.onboard.folder     Resource group path, e.g. Infosys/Customers/ACME/Docker.
 *
 * Optional NetScan properties:
 *   docker.api.ssl            "true" (default) uses HTTPS; "false" uses HTTP.
 *   docker.api.port           Engine API port. Default 2376 when ssl is true,
 *                             2375 when ssl is false.
 *   docker.api.ssl.verify     Verify TLS hostname and chain. Default true.
 *   docker.api.tls.dir        Collector directory with ca.pem, cert.pem, key.pem.
 *   docker.api.tls.ca         CA PEM path or PEM text. Overrides tls.dir ca.pem.
 *   docker.api.tls.cert       Client cert PEM path or PEM text. Overrides tls.dir.
 *   docker.api.tls.key        Client key PEM path or PEM text. Overrides tls.dir.
 *   docker.api.tls.key.pass   Password for an encrypted PKCS#8 client key.
 *   docker.api.tls.pkcs12     PKCS#12 path on the collector (alternative to PEM).
 *   docker.api.tls.pkcs12.pass
 *                             PKCS#12 password.
 *   docker.port               cAdvisor port set on each resource. Default 8080.
 *   docker.name.by            "address" (default) or "hostname".
 *   docker.collector.id       Numeric collector ID. Omit to let LogicMonitor pick.
 *   docker.include.down       "true" to emit nodes whose Status.State is not ready.
 *                             Default false.
 *   docker.include.drained    "true" to emit nodes with Availability drain.
 *                             Default false.
 *
 * When docker.api.ssl is true and docker.api.ssl.verify is true (the defaults),
 * supply Docker mTLS material: docker.api.tls.dir, or PKCS#12, or the three PEM
 * properties. The script never installs a JVM-wide trust-all socket factory.
 */

import com.santaba.agent.groovy.utils.GroovyScriptHelper as GSH
import com.logicmonitor.mod.Snippets
import groovy.json.JsonSlurper
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import java.security.KeyFactory
import java.security.KeyStore
import java.security.cert.Certificate
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import javax.crypto.EncryptedPrivateKeyInfo
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

def fail(String msg) {
    throw new Exception(msg)
}

Boolean debug = false
def lmEmit
try {
    def modLoader = GSH.getInstance(GroovySystem.version).getScript("Snippets", Snippets.getLoader()).withBinding(getBinding())
    lmEmit = modLoader.load("lm.emit", "1.0")
} catch (Exception ex) {
    fail("Cannot load the official lm.emit snippet. Enhanced Script NetScan requires LM Collector 32.400 or higher. Confirm Settings > NetScans > Add NetScan Options > Add Advanced NetScan, Discovery Method = Enhanced Script NetScan, and Embed a Groovy script. ${ex.message}")
}

def flag(String raw, boolean defaultValue) {
    if (raw == null || raw.toString().trim().isEmpty()) {
        return defaultValue
    }
    def v = raw.toString().trim().toLowerCase()
    if (v == "true") {
        return true
    }
    if (v == "false") {
        return false
    }
    fail("Property value '${raw}' must be exactly true or false.")
}

def wrapHost(String host) {
    if (host.startsWith("[")) {
        return host
    }
    return host.contains(":") ? "[${host}]" : host
}

def derLength(int n) {
    if (n < 128) {
        return [(byte) n] as byte[]
    }
    if (n < 256) {
        return [(byte) 0x81, (byte) n] as byte[]
    }
    return [(byte) 0x82, (byte) ((n >> 8) & 0xff), (byte) (n & 0xff)] as byte[]
}

def derTag(int tag, byte[] body) {
    def len = derLength(body.length)
    def out = new byte[1 + len.length + body.length]
    out[0] = (byte) tag
    System.arraycopy(len, 0, out, 1, len.length)
    System.arraycopy(body, 0, out, 1 + len.length, body.length)
    return out
}

def concatBytes(List parts) {
    int total = 0
    parts.each { total += it.length }
    def out = new byte[total]
    int off = 0
    parts.each {
        System.arraycopy(it, 0, out, off, it.length)
        off += it.length
    }
    return out
}

def pkcs1ToPkcs8(byte[] pkcs1) {
    def algId = [
        (byte) 0x30, (byte) 0x0d, (byte) 0x06, (byte) 0x09,
        (byte) 0x2a, (byte) 0x86, (byte) 0x48, (byte) 0x86,
        (byte) 0xf7, (byte) 0x0d, (byte) 0x01, (byte) 0x01, (byte) 0x01,
        (byte) 0x05, (byte) 0x00
    ] as byte[]
    def version = [(byte) 0x02, (byte) 0x01, (byte) 0x00] as byte[]
    def octet = derTag(0x04, pkcs1)
    return derTag(0x30, concatBytes([version, algId, octet]))
}

def loadText(String value, String label) {
    if (value == null || value.toString().trim().isEmpty()) {
        return null
    }
    def trimmed = value.toString().trim()
    if (trimmed.startsWith("-----BEGIN")) {
        return trimmed.replace("\r\n", "\n")
    }
    def f = new File(trimmed)
    if (!f.isFile()) {
        fail("TLS material '${label}' is not a file on this collector: ${trimmed}")
    }
    return f.getText("UTF-8")
}

def pemBlocks(String pem) {
    def blocks = []
    def matcher = (pem =~ /-----BEGIN ([^-]+)-----([A-Za-z0-9+/=\s]+)-----END \1-----/)
    matcher.each { _full, type, body ->
        blocks << [type: type.trim(), der: body.replaceAll("\\s", "").decodeBase64()]
    }
    if (blocks.isEmpty()) {
        fail("No PEM blocks were parsed. Check ca.pem / cert.pem / key.pem on the collector.")
    }
    return blocks
}

def certsFromPem(String pem) {
    def cf = CertificateFactory.getInstance("X.509")
    def certs = []
    pemBlocks(pem).findAll { it.type.contains("CERTIFICATE") }.each { block ->
        certs << cf.generateCertificate(new ByteArrayInputStream(block.der))
    }
    if (certs.isEmpty()) {
        fail("No CERTIFICATE blocks found in TLS cert/CA PEM.")
    }
    return certs as Certificate[]
}

def privateKeyFromPem(String pem, String password) {
    def blocks = pemBlocks(pem)
    def keyBlock = blocks.find { it.type.contains("PRIVATE KEY") }
    if (!keyBlock) {
        fail("No PRIVATE KEY block found in docker.api.tls.key. Use an unencrypted PKCS#8 or RSA key, or a PKCS#12.")
    }
    def type = keyBlock.type.toUpperCase()
    def der = keyBlock.der
    if (type.contains("ENCRYPTED")) {
        if (!password) {
            fail("Encrypted PKCS#8 key requires docker.api.tls.key.pass.")
        }
        def epki = new EncryptedPrivateKeyInfo(der)
        def skf = SecretKeyFactory.getInstance(epki.algName)
        def secret = skf.generateSecret(new PBEKeySpec(password.toCharArray()))
        der = epki.getKeySpec(secret).encoded
        type = "PRIVATE KEY"
    }
    if (type.contains("RSA PRIVATE KEY") && !type.contains("ENCRYPTED")) {
        der = pkcs1ToPkcs8(der)
        type = "PRIVATE KEY"
    }
    if (type.contains("EC PRIVATE KEY")) {
        fail("SEC1 EC keys are not supported. Convert the client key to PKCS#8 on the collector (BEGIN PRIVATE KEY) or use docker.api.tls.pkcs12.")
    }
    def spec = new PKCS8EncodedKeySpec(der)
    try {
        return KeyFactory.getInstance("RSA").generatePrivate(spec)
    } catch (Exception ignored) {
        return KeyFactory.getInstance("EC").generatePrivate(spec)
    }
}

def trustAllManagers() {
    return [
        new X509TrustManager() {
            void checkClientTrusted(X509Certificate[] chain, String authType) {}
            void checkServerTrusted(X509Certificate[] chain, String authType) {}
            X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0] }
        }
    ] as TrustManager[]
}

def buildSsl(Map tls) {
    def keyManagers = null
    def trustManagers = null

    if (tls.pkcs12Path) {
        def ks = KeyStore.getInstance("PKCS12")
        def pass = (tls.pkcs12Pass ?: "").toCharArray()
        new File(tls.pkcs12Path).withInputStream { ks.load(it, pass) }
        def kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        kmf.init(ks, pass)
        keyManagers = kmf.getKeyManagers()
        if (tls.verify && !tls.caPem) {
            def tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            tmf.init(ks)
            trustManagers = tmf.getTrustManagers()
        }
    }

    if (tls.certPem && tls.keyPem) {
        def certs = certsFromPem(tls.certPem)
        def key = privateKeyFromPem(tls.keyPem, tls.keyPass)
        def ks = KeyStore.getInstance(KeyStore.getDefaultType())
        ks.load(null, null)
        def pass = "netscan".toCharArray()
        ks.setKeyEntry("client", key, pass, certs)
        def kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        kmf.init(ks, pass)
        keyManagers = kmf.getKeyManagers()
    }

    if (tls.caPem) {
        def caCerts = certsFromPem(tls.caPem)
        def ts = KeyStore.getInstance(KeyStore.getDefaultType())
        ts.load(null, null)
        caCerts.eachWithIndex { Certificate c, int i ->
            ts.setCertificateEntry("ca-${i}", c)
        }
        def tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(ts)
        trustManagers = tmf.getTrustManagers()
    }

    if (!tls.verify) {
        trustManagers = trustAllManagers()
    }

    if (keyManagers == null && trustManagers == null) {
        return [factory: null, verifier: null]
    }

    def ctx = SSLContext.getInstance("TLS")
    ctx.init(keyManagers, trustManagers, new java.security.SecureRandom())
    def verifier = null
    if (!tls.verify) {
        verifier = { String h, session -> true } as HostnameVerifier
    }
    return [factory: ctx.getSocketFactory(), verifier: verifier]
}

def drain(conn, boolean success) {
    def stream = success ? conn.getInputStream() : conn.getErrorStream()
    if (stream == null) {
        return ""
    }
    try {
        return stream.getText("UTF-8")
    } finally {
        try { stream.close() } catch (Exception ignored) {}
    }
}

def apiHost      = netscanProps.get("docker.api.host")
def folder       = netscanProps.get("docker.onboard.folder")
def useSsl       = flag(netscanProps.get("docker.api.ssl"), true)
def verify       = flag(netscanProps.get("docker.api.ssl.verify"), true)
def includeDown  = flag(netscanProps.get("docker.include.down"), false)
def includeDrain = flag(netscanProps.get("docker.include.drained"), false)
def apiPort      = netscanProps.get("docker.api.port")
def cadvisorPort = netscanProps.get("docker.port") ?: "8080"
def nameBy       = (netscanProps.get("docker.name.by") ?: "address").toString().trim().toLowerCase()
def collectorId  = netscanProps.get("docker.collector.id")
def tlsDir       = netscanProps.get("docker.api.tls.dir")
def pkcs12Path   = netscanProps.get("docker.api.tls.pkcs12")
def pkcs12Pass   = netscanProps.get("docker.api.tls.pkcs12.pass")
def keyPass      = netscanProps.get("docker.api.tls.key.pass")

if (!apiHost || apiHost.toString().trim().isEmpty()) {
    fail("NetScan property 'docker.api.host' is required (a Swarm manager the collector can reach).")
}
apiHost = apiHost.toString().trim()

if (!folder || folder.toString().trim().isEmpty()) {
    fail("NetScan property 'docker.onboard.folder' is required. Use the customer resource path, for example Infosys/Customers/ACME/Docker. Do not leave this unset.")
}
folder = folder.toString().trim()

if (!apiPort || apiPort.toString().trim().isEmpty()) {
    apiPort = useSsl ? "2376" : "2375"
} else {
    apiPort = apiPort.toString().trim()
}

if (!(nameBy in ["address", "hostname"])) {
    fail("NetScan property 'docker.name.by' must be exactly address or hostname, got '${nameBy}'.")
}

if (collectorId && !(collectorId.toString().trim() ==~ /^\d+$/)) {
    fail("NetScan property 'docker.collector.id' must be a numeric collector ID, got '${collectorId}'.")
}

def caPem   = loadText(netscanProps.get("docker.api.tls.ca"), "docker.api.tls.ca")
def certPem = loadText(netscanProps.get("docker.api.tls.cert"), "docker.api.tls.cert")
def keyPem  = loadText(netscanProps.get("docker.api.tls.key"), "docker.api.tls.key")

if (tlsDir && tlsDir.toString().trim()) {
    def dir = new File(tlsDir.toString().trim())
    if (!dir.isDirectory()) {
        fail("NetScan property 'docker.api.tls.dir' is not a directory on this collector: ${dir.path}")
    }
    if (!caPem) {
        def f = new File(dir, "ca.pem")
        if (f.isFile()) { caPem = f.getText("UTF-8") }
    }
    if (!certPem) {
        def f = new File(dir, "cert.pem")
        if (f.isFile()) { certPem = f.getText("UTF-8") }
    }
    if (!keyPem) {
        def f = new File(dir, "key.pem")
        if (f.isFile()) { keyPem = f.getText("UTF-8") }
    }
}

if (pkcs12Path) {
    pkcs12Path = pkcs12Path.toString().trim()
    if (!new File(pkcs12Path).isFile()) {
        fail("NetScan property 'docker.api.tls.pkcs12' is not a file on this collector: ${pkcs12Path}")
    }
}

def hasClient = (certPem && keyPem) || pkcs12Path
def hasCa = (caPem != null) || pkcs12Path

if (useSsl && verify) {
    if (!hasCa) {
        fail("docker.api.ssl is true and docker.api.ssl.verify is true, but no CA was provided. Copy Docker's ca.pem, cert.pem and key.pem onto the collector and set docker.api.tls.dir to that directory, or set docker.api.tls.pkcs12.")
    }
    if (!hasClient) {
        fail("docker.api.ssl is true and docker.api.ssl.verify is true, but no client certificate was provided. Production Engine API on 2376 uses mTLS. Set docker.api.tls.dir (ca.pem, cert.pem, key.pem) or docker.api.tls.pkcs12.")
    }
}

def ssl = [factory: null, verifier: null]
if (useSsl) {
    ssl = buildSsl([
        verify     : verify,
        caPem      : caPem,
        certPem    : certPem,
        keyPem     : keyPem,
        keyPass    : keyPass,
        pkcs12Path : pkcs12Path,
        pkcs12Pass : pkcs12Pass
    ])
}

def scheme = useSsl ? "https" : "http"
def endpointHost = wrapHost(apiHost)

def dockerGet = { String path ->
    def conn
    try {
        conn = new URL("${scheme}://${endpointHost}:${apiPort}${path}").openConnection()
        conn.setConnectTimeout(10000)
        conn.setReadTimeout(30000)
        conn.setRequestProperty("Accept", "application/json")
        if (conn instanceof HttpsURLConnection) {
            if (ssl.factory) {
                conn.setSSLSocketFactory(ssl.factory)
            }
            if (ssl.verifier) {
                conn.setHostnameVerifier(ssl.verifier)
            }
        }
        def code = conn.getResponseCode()
        def body = drain(conn, code == 200)
        if (code != 200) {
            def hint = ""
            if (code == 400 || code == 403 || code == 401) {
                hint = " Check docker.api.tls.dir (ca.pem, cert.pem, key.pem) and that this collector can present a client certificate the manager trusts."
            }
            fail("Docker API ${path} returned HTTP ${code} from ${scheme}://${endpointHost}:${apiPort}.${hint} Body: ${body.take(300)}")
        }
        return new JsonSlurper().parseText(body)
    } catch (Exception ex) {
        if (ex.message?.startsWith("Docker API") || ex.message?.startsWith("NetScan property") || ex.message?.startsWith("docker.api")) {
            throw ex
        }
        fail("Cannot reach Docker Engine API at ${scheme}://${endpointHost}:${apiPort}${path}: ${ex.message}. The NetScan collector must reach the Swarm manager. Confirm docker.api.host, docker.api.port, docker.api.ssl, and TLS files on the collector.")
    } finally {
        try { conn?.disconnect() } catch (Exception ignored) {}
    }
}

def info  = dockerGet("/info")
def swarm = info.Swarm ?: [:]

if ((swarm.LocalNodeState ?: "inactive").toString() != "active") {
    fail("${apiHost} is not part of an active Swarm (Swarm.LocalNodeState=${swarm.LocalNodeState}). Point docker.api.host at a manager of a running Swarm.")
}
if (!swarm.ControlAvailable) {
    fail("${apiHost} is a Swarm worker. Point docker.api.host at a manager (ControlAvailable must be true).")
}

def clusterId = (swarm.Cluster?.ID ?: "").toString()
def nodes     = dockerGet("/nodes")
if (!nodes) {
    fail("GET /nodes returned no Swarm nodes from ${apiHost}.")
}

def serviceNames = [:]
dockerGet("/services").each { svc ->
    serviceNames[svc.ID] = (svc.Spec?.Name ?: "").toString()
}

def tasksPerNode    = [:]
def servicesPerNode = [:]
def taskFilter      = URLEncoder.encode('{"desired-state":["running"]}', "UTF-8")
dockerGet("/tasks?filters=${taskFilter}").each { task ->
    def nodeId = task.NodeID
    if (!nodeId) {
        return
    }
    if ((task.Status?.State ?: "").toString().equalsIgnoreCase("running")) {
        tasksPerNode[nodeId] = (tasksPerNode[nodeId] ?: 0) + 1
        def svcName = serviceNames[task.ServiceID]
        if (svcName) {
            if (!servicesPerNode.containsKey(nodeId)) {
                servicesPerNode[nodeId] = new TreeSet()
            }
            servicesPerNode[nodeId].add(svcName)
        }
    }
}

List<Map> resources = []
def skipped = []

nodes.each { node ->
    def nodeId   = (node.ID ?: "").toString()
    def hostname = (node.Description?.Hostname ?: "").toString()
    def address  = (node.Status?.Addr ?: "").toString()
    def role     = (node.Spec?.Role ?: "").toString()
    def state    = (node.Status?.State ?: "").toString()
    def avail    = (node.Spec?.Availability ?: "").toString()
    def osName   = (node.Description?.Platform?.OS ?: "").toString()

    if (!nodeId) {
        skipped << "a node with no ID"
        return
    }
    if (!state.equalsIgnoreCase("ready") && !includeDown) {
        skipped << "${nodeId} state=${state ?: 'unknown'} (set docker.include.down=true to include)"
        return
    }
    if (avail.equalsIgnoreCase("drain") && !includeDrain) {
        skipped << "${nodeId} availability=drain (set docker.include.drained=true to include)"
        return
    }

    def target = (nameBy == "hostname") ? hostname : address
    if (!target) {
        skipped << "${nodeId} has no ${nameBy} (Status.Addr or Description.Hostname is empty)"
        return
    }

    def labels = (node.Spec?.Labels ?: [:]).collect { k, v -> "${k}=${v}" }.sort().join(",")
    def categories = ["Docker", "DockerSwarm"]
    def osLower = osName.toLowerCase()
    if (osLower == "linux") {
        categories << "Linux"
    } else if (osLower == "windows") {
        categories << "Windows"
    }
    if (role.equalsIgnoreCase("manager")) {
        categories << "DockerSwarmManager"
    }

    def serviceList = (servicesPerNode[nodeId] ?: new TreeSet()).join(",")
    def props = [
        "docker.port"                   : cadvisorPort.toString(),
        "docker.swarm.cluster_id"       : clusterId,
        "docker.node.id"                : nodeId,
        "docker.node.hostname"          : hostname,
        "docker.node.address"           : address,
        "docker.node.role"              : role,
        "docker.node.availability"      : avail,
        "docker.node.state"             : state,
        "docker.node.os"                : osName,
        "docker.node.architecture"      : (node.Description?.Platform?.Architecture ?: "").toString(),
        "docker.engine.version"         : (node.Description?.Engine?.EngineVersion ?: "").toString(),
        "docker.node.running_task_count": (tasksPerNode[nodeId] ?: 0).toString(),
        "docker.node.services"          : serviceList,
        "docker.node.labels"            : labels,
        "system.categories"             : categories.join(",")
    ]

    if (role.equalsIgnoreCase("manager")) {
        props["docker.api.port"] = apiPort.toString()
        props["docker.api.ssl"] = useSsl.toString()
        props["docker.node.is_leader"] = (node.ManagerStatus?.Leader ? "true" : "false")
        props["docker.node.reachability"] = (node.ManagerStatus?.Reachability ?: "").toString()
    }

    props = props.findAll { k, v -> v != null && v != "" }

    def resource = [
        "hostname"    : target.toString(),
        "displayname" : (hostname ?: target).toString(),
        "hostProps"   : props,
        "groupName"   : [folder]
    ]
    if (collectorId) {
        resource["collectorId"] = collectorId.toString().trim().toInteger()
    }
    resources << resource
}

if (!resources) {
    def detail = skipped ? skipped.join("; ") : "GET /nodes returned nodes but none had a usable address/hostname."
    fail("No Swarm nodes were emitted. A NetScan job that completes with 0 resources is a failure (LogicMonitor treats an empty emit as success and adds nothing). ${detail}")
}

lmEmit.resource(resources, debug)
