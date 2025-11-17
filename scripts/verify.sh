#!/usr/bin/env bash
set -euo pipefail

host=${1:-localhost}
echo "Health checks..."
for p in 8000 8001 8002; do
  curl -sf "http://$host:$p/health" >/dev/null || { echo "Health failed on $p"; exit 1; }
done
echo "OK"

echo "Append via master (blocking) ..."
resp=$(curl -s -X POST "http://$host:8000/messages" -H 'Content-Type: application/json' -d '{"msg":"smoke"}')
echo "$resp" | jq '.duration_ms, .acks | length'

echo "Consistency..."
m=$(curl -s "http://$host:8000/messages" | jq -c '.messages')
s1=$(curl -s "http://$host:8001/messages" | jq -c '.messages')
s2=$(curl -s "http://$host:8002/messages" | jq -c '.messages')

echo "master:     $m"
echo "secondary1: $s1"
echo "secondary2: $s2"

[[ "$m" == "$s1" && "$m" == "$s2" ]] || { echo "Mismatch!"; exit 2; }
echo "Consistent ✅"


