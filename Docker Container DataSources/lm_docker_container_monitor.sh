#!/usr/bin/env bash

###############################################################################
# LogicMonitor Docker Container Monitoring
#
# Modes:
#   discover : Detect running containers and return LogicMonitor instances
#   collect  : Collect metrics for all detected running containers in one poll
#
# Usage:
#   ./lm_docker_container_monitor.sh discover
#   ./lm_docker_container_monitor.sh collect
#   ./lm_docker_container_monitor.sh collect  'payment|order|redis'
#
# LogicMonitor BatchScript output:
#   instanceName.metricName=value
###############################################################################

set -uo pipefail

MODE="${1:-collect}"

# Regular expression matched against the active container name.
# Examples:
#   .*                    All running containers
#   payment               Container names containing "payment"
#   ^payment-api$         Exact container name
#   payment|order|redis   Multiple container name patterns
NAME_FILTER="${2:-${CONTAINER_NAME_FILTER:-.*}}"

###############################################################################
# Validation
###############################################################################

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker CLI was not found." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is unavailable or the executing user cannot access Docker." >&2
    exit 1
fi

###############################################################################
# Functions
###############################################################################

clean_number() {
    local value="${1:-0}"

    value="${value//%/}"
    value="${value//,/}"
    value="${value//[[:space:]]/}"

    if [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        printf '%s' "$value"
    else
        printf '0'
    fi
}

boolean_to_number() {
    case "${1:-false}" in
        true|TRUE|True|1)
            printf '1'
            ;;
        *)
            printf '0'
            ;;
    esac
}

size_to_bytes() {
    local value="${1:-0B}"
    local number
    local unit
    local multiplier=1

    value="$(printf '%s' "$value" | tr -d ',' | xargs)"

    if [[ "$value" =~ ^([0-9]+([.][0-9]+)?)[[:space:]]*([KMGTPEkmgtpe]?i?[Bb])$ ]]; then
        number="${BASH_REMATCH[1]}"
        unit="${BASH_REMATCH[3]}"

        unit="$(printf '%s' "$unit" | tr '[:lower:]' '[:upper:]')"

        case "$unit" in
            B)
                multiplier=1
                ;;
            KB|KIB)
                multiplier=1024
                ;;
            MB|MIB)
                multiplier=1048576
                ;;
            GB|GIB)
                multiplier=1073741824
                ;;
            TB|TIB)
                multiplier=1099511627776
                ;;
            PB|PIB)
                multiplier=1125899906842624
                ;;
            *)
                multiplier=1
                ;;
        esac

        awk -v number="$number" -v multiplier="$multiplier" \
            'BEGIN { printf "%.0f", number * multiplier }'
    else
        printf '0'
    fi
}

sanitize_wildvalue() {
    local value="${1:-unknown}"

    # LogicMonitor WILDVALUE cannot contain:
    # space, colon, hash, backslash or equals.
    printf '%s' "$value" |
        sed -E '
            s/[ :#\\=]+/_/g;
            s/[^A-Za-z0-9_.-]+/_/g;
            s/^_+//;
            s/_+$//
        '
}

sanitize_property_value() {
    local value="${1:-}"

    # Avoid breaking LogicMonitor Active Discovery property separators.
    value="${value//&/_}"
    value="${value//#/_}"
    value="${value//$'\n'/ }"
    value="${value//$'\r'/ }"

    printf '%s' "$value"
}

get_container_inspection() {
    local container_id="$1"

    docker inspect \
        --format \
'{{.State.Running}}|{{.State.Paused}}|{{.State.Restarting}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{if .State.Health}}{{.State.Health.FailingStreak}}{{else}}0{{end}}|{{index .Config.Labels "com.docker.swarm.service.name"}}|{{index .Config.Labels "com.docker.swarm.task.name"}}|{{index .Config.Labels "com.docker.swarm.node.id"}}|{{.HostConfig.RestartPolicy.Name}}|{{.HostConfig.NetworkMode}}' \
        "$container_id" 2>/dev/null
}

###############################################################################
# Detect only active containers matching the configured name
###############################################################################

mapfile -t ACTIVE_CONTAINERS < <(
    docker ps \
        --filter "status=running" \
        --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}' |
    awk -F'|' -v pattern="$NAME_FILTER" '$2 ~ pattern'
)

###############################################################################
# Active Discovery mode
###############################################################################

if [[ "$MODE" == "discover" ]]; then

    for container_row in "${ACTIVE_CONTAINERS[@]}"; do
        IFS='|' read -r \
            container_id \
            container_name \
            container_image \
            container_status \
            <<< "$container_row"

        wildvalue="$(sanitize_wildvalue "$container_name")"

        inspect_output="$(get_container_inspection "$container_id" || true)"

        IFS='|' read -r \
            running \
            paused \
            restarting \
            oom_killed \
            exit_code \
            restart_count \
            started_at \
            health_status \
            health_failing_streak \
            swarm_service \
            swarm_task \
            swarm_node_id \
            restart_policy \
            network_mode \
            <<< "$inspect_output"

        [[ "$swarm_service" == "<no value>" ]] && swarm_service=""
        [[ "$swarm_task" == "<no value>" ]] && swarm_task=""
        [[ "$swarm_node_id" == "<no value>" ]] && swarm_node_id=""

        if [[ -n "$swarm_service" ]]; then
            workload_type="Docker Swarm Service"
        else
            workload_type="Standalone Docker Container"
        fi

        container_name_property="$(sanitize_property_value "$container_name")"
        container_image_property="$(sanitize_property_value "$container_image")"
        container_status_property="$(sanitize_property_value "$container_status")"
        swarm_service_property="$(sanitize_property_value "$swarm_service")"
        swarm_task_property="$(sanitize_property_value "$swarm_task")"
        swarm_node_property="$(sanitize_property_value "$swarm_node_id")"
        workload_type_property="$(sanitize_property_value "$workload_type")"
        restart_policy_property="$(sanitize_property_value "$restart_policy")"
        network_mode_property="$(sanitize_property_value "$network_mode")"

        printf '%s##%s##%s####' \
            "$wildvalue" \
            "$container_name_property" \
            "$workload_type_property"

        printf 'auto.container.id=%s' "$container_id"
        printf '&auto.container.name=%s' "$container_name_property"
        printf '&auto.container.image=%s' "$container_image_property"
        printf '&auto.container.status=%s' "$container_status_property"
        printf '&auto.container.workload.type=%s' "$workload_type_property"
        printf '&auto.swarm.service.name=%s' "$swarm_service_property"
        printf '&auto.swarm.task.name=%s' "$swarm_task_property"
        printf '&auto.swarm.node.id=%s' "$swarm_node_property"
        printf '&auto.container.restart.policy=%s' "$restart_policy_property"
        printf '&auto.container.network.mode=%s' "$network_mode_property"
        printf '\n'
    done

    exit 0
fi

###############################################################################
# Collection mode
###############################################################################

if [[ "$MODE" != "collect" ]]; then
    echo "ERROR: Invalid mode '$MODE'. Use discover or collect." >&2
    exit 1
fi

# No running containers matching the name pattern.
if (( ${#ACTIVE_CONTAINERS[@]} == 0 )); then
    exit 0
fi

###############################################################################
# Build container ID array
###############################################################################

CONTAINER_IDS=()

for container_row in "${ACTIVE_CONTAINERS[@]}"; do
    IFS='|' read -r container_id container_name _ <<< "$container_row"
    CONTAINER_IDS+=("$container_id")
done

###############################################################################
# Collect all container runtime metrics in a single docker stats execution
###############################################################################

while IFS='|' read -r \
    container_id \
    container_name \
    cpu_percent \
    memory_usage \
    memory_percent \
    network_io \
    block_io \
    pid_count
do
    if [[ ! "$container_name" =~ $NAME_FILTER ]]; then
        continue
    fi

    wildvalue="$(sanitize_wildvalue "$container_name")"

    ###########################################################################
    # Parse Docker statistics
    ###########################################################################

    memory_used="${memory_usage%% / *}"
    memory_limit="${memory_usage##* / }"

    network_rx="${network_io%% / *}"
    network_tx="${network_io##* / }"

    block_read="${block_io%% / *}"
    block_write="${block_io##* / }"

    cpu_percent_number="$(clean_number "$cpu_percent")"
    memory_percent_number="$(clean_number "$memory_percent")"

    memory_used_bytes="$(size_to_bytes "$memory_used")"
    memory_limit_bytes="$(size_to_bytes "$memory_limit")"

    network_rx_bytes="$(size_to_bytes "$network_rx")"
    network_tx_bytes="$(size_to_bytes "$network_tx")"

    block_read_bytes="$(size_to_bytes "$block_read")"
    block_write_bytes="$(size_to_bytes "$block_write")"

    pid_count_number="$(clean_number "$pid_count")"

    ###########################################################################
    # Inspect container state and identify Docker Swarm service
    ###########################################################################

    inspect_output="$(get_container_inspection "$container_id" || true)"

    IFS='|' read -r \
        running \
        paused \
        restarting \
        oom_killed \
        exit_code \
        restart_count \
        started_at \
        health_status \
        health_failing_streak \
        swarm_service \
        swarm_task \
        swarm_node_id \
        restart_policy \
        network_mode \
        <<< "$inspect_output"

    [[ "$swarm_service" == "<no value>" ]] && swarm_service=""
    [[ "$swarm_task" == "<no value>" ]] && swarm_task=""
    [[ "$swarm_node_id" == "<no value>" ]] && swarm_node_id=""

    running_number="$(boolean_to_number "$running")"
    paused_number="$(boolean_to_number "$paused")"
    restarting_number="$(boolean_to_number "$restarting")"
    oom_killed_number="$(boolean_to_number "$oom_killed")"

    exit_code_number="$(clean_number "$exit_code")"
    restart_count_number="$(clean_number "$restart_count")"
    health_failing_streak_number="$(clean_number "$health_failing_streak")"

    if [[ -n "$swarm_service" ]]; then
        swarm_service_member=1
    else
        swarm_service_member=0
    fi

    ###########################################################################
    # Health-status numeric mapping
    #
    # 0 = unhealthy
    # 1 = healthy
    # 2 = starting
    # 3 = no Docker health check configured
    ###########################################################################

    case "$health_status" in
        healthy)
            health_status_number=1
            ;;
        unhealthy)
            health_status_number=0
            ;;
        starting)
            health_status_number=2
            ;;
        *)
            health_status_number=3
            ;;
    esac

    ###########################################################################
    # Calculate container uptime
    ###########################################################################

    current_epoch="$(date +%s)"
    started_epoch="$(date -d "$started_at" +%s 2>/dev/null || printf '%s' "$current_epoch")"

    uptime_seconds=$((current_epoch - started_epoch))

    if (( uptime_seconds < 0 )); then
        uptime_seconds=0
    fi

    ###########################################################################
    # LogicMonitor BatchScript output
    #
    # Format:
    #   WILDVALUE.datapoint=value
    ###########################################################################

    printf '%s.running=%s\n' \
        "$wildvalue" "$running_number"

    printf '%s.paused=%s\n' \
        "$wildvalue" "$paused_number"

    printf '%s.restarting=%s\n' \
        "$wildvalue" "$restarting_number"

    printf '%s.oomKilled=%s\n' \
        "$wildvalue" "$oom_killed_number"

    printf '%s.healthStatus=%s\n' \
        "$wildvalue" "$health_status_number"

    printf '%s.healthFailingStreak=%s\n' \
        "$wildvalue" "$health_failing_streak_number"

    printf '%s.swarmServiceMember=%s\n' \
        "$wildvalue" "$swarm_service_member"

    printf '%s.cpuPercent=%s\n' \
        "$wildvalue" "$cpu_percent_number"

    printf '%s.memoryUsedBytes=%s\n' \
        "$wildvalue" "$memory_used_bytes"

    printf '%s.memoryLimitBytes=%s\n' \
        "$wildvalue" "$memory_limit_bytes"

    printf '%s.memoryPercent=%s\n' \
        "$wildvalue" "$memory_percent_number"

    printf '%s.networkRxBytes=%s\n' \
        "$wildvalue" "$network_rx_bytes"

    printf '%s.networkTxBytes=%s\n' \
        "$wildvalue" "$network_tx_bytes"

    printf '%s.blockReadBytes=%s\n' \
        "$wildvalue" "$block_read_bytes"

    printf '%s.blockWriteBytes=%s\n' \
        "$wildvalue" "$block_write_bytes"

    printf '%s.pids=%s\n' \
        "$wildvalue" "$pid_count_number"

    printf '%s.restartCount=%s\n' \
        "$wildvalue" "$restart_count_number"

    printf '%s.exitCode=%s\n' \
        "$wildvalue" "$exit_code_number"

    printf '%s.uptimeSeconds=%s\n' \
        "$wildvalue" "$uptime_seconds"

done < <(
    docker stats \
        --no-stream \
        --format '{{.ID}}|{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}|{{.PIDs}}' \
        "${CONTAINER_IDS[@]}"
)

exit 0
