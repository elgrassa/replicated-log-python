#!/usr/bin/env bash
set -eo pipefail

host=${1:-localhost}

# Detect secondary ports from docker-compose.yml
# Extract port mappings for all secondary services (ports start at 8001)
SECONDARY_PORTS=()

if [ -f "docker-compose.yml" ]; then
  # Extract all port mappings that map to container port 8001 (secondary internal port)
  # Pattern matches: "8001:8001", "8002:8001", "8003:8001", etc.
  while IFS= read -r port_line; do
    if [[ $port_line =~ \"([0-9]+):8001\" ]]; then
      host_port="${BASH_REMATCH[1]}"
      # Skip master port (8000) and add unique ports only
      if [ "$host_port" != "8000" ]; then
        # Check if port already in array
        found=false
        for existing_port in "${SECONDARY_PORTS[@]+"${SECONDARY_PORTS[@]}"}"; do
          if [ "$existing_port" = "$host_port" ]; then
            found=true
            break
          fi
        done
        if [ "$found" = false ]; then
          SECONDARY_PORTS+=("$host_port")
        fi
      fi
    fi
  done < <(grep -E '\"[0-9]+:8001\"' docker-compose.yml)
  
  # Sort ports numerically
  if [ ${#SECONDARY_PORTS[@]} -gt 0 ]; then
    IFS=$'\n' sorted=($(sort -n <<<"${SECONDARY_PORTS[*]}"))
    unset IFS
    SECONDARY_PORTS=("${sorted[@]}")
  fi
fi

# Fallback: try to detect from running containers
if [ ${#SECONDARY_PORTS[@]} -eq 0 ]; then
  if command -v docker &> /dev/null && command -v jq &> /dev/null; then
    while IFS= read -r port; do
      if [ -n "$port" ] && [ "$port" != "null" ] && [ "$port" != "8000" ]; then
        SECONDARY_PORTS+=("$port")
      fi
    done < <(docker compose ps --format json 2>/dev/null | \
             jq -r '.[] | select(.Service | startswith("secondary")) | .Publishers[0].PublishedPort // empty' 2>/dev/null)
    
    # Sort ports
    if [ ${#SECONDARY_PORTS[@]} -gt 0 ]; then
      IFS=$'\n' sorted=($(sort -n <<<"${SECONDARY_PORTS[*]}"))
      unset IFS
      SECONDARY_PORTS=("${sorted[@]}")
    fi
  fi
fi

# Default to 2 secondaries if detection failed
if [ ${#SECONDARY_PORTS[@]} -eq 0 ]; then
  echo "Warning: Could not detect secondaries, defaulting to 2 secondaries (ports 8001, 8002)"
  SECONDARY_PORTS=(8001 8002)
fi

NUM_SECONDARIES=${#SECONDARY_PORTS[@]}

echo "Detected $NUM_SECONDARIES secondary node(s) on ports: ${SECONDARY_PORTS[*]}"
echo "Health checks..."

# Check master health
curl -sf "http://$host:8000/health" >/dev/null || { echo "Health failed on master (8000)"; exit 1; }

# Check all secondary health endpoints
for port in "${SECONDARY_PORTS[@]}"; do
  curl -sf "http://$host:$port/health" >/dev/null || { echo "Health failed on port $port"; exit 1; }
done
echo "OK"

echo "Append via master (blocking) ..."
resp=$(curl -s -X POST "http://$host:8000/messages" -H 'Content-Type: application/json' -d '{"msg":"smoke"}')
duration=$(echo "$resp" | jq -r '.duration_ms')
acks_count=$(echo "$resp" | jq -r '.acks | length')
echo "Duration: ${duration}ms, ACKs: ${acks_count}"

# Verify ACK count matches number of secondaries
if [ "$acks_count" != "$NUM_SECONDARIES" ]; then
  echo "Warning: Expected $NUM_SECONDARIES ACKs, got $acks_count"
fi

echo "Consistency check..."
m=$(curl -s "http://$host:8000/messages" | jq -c '.messages')

# Check all secondaries
all_match=true
for i in "${!SECONDARY_PORTS[@]}"; do
  port="${SECONDARY_PORTS[$i]}"
  sec_num=$((i + 1))
  s=$(curl -s "http://$host:$port/messages" | jq -c '.messages')
  echo "secondary${sec_num} (port $port): $s"
  
  if [ "$m" != "$s" ]; then
    echo "Mismatch detected: master != secondary${sec_num}"
    all_match=false
  fi
done

echo "master:     $m"

if [ "$all_match" = true ]; then
  echo "Consistent ✅"
else
  echo "Mismatch! Not all secondaries match master"
  exit 2
fi
