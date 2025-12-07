#!/usr/bin/env python3
"""Generate docker-compose.yml with configurable number of secondaries."""
import os
import sys

# Configuration from environment or defaults
NUM_SECONDARIES = int(os.environ.get("NUM_SECONDARIES", "2"))
MASTER_PORT = int(os.environ.get("MASTER_PORT", "8000"))
SECONDARY_DELAYS = os.environ.get("SECONDARY_DELAYS", "0,1500").split(",")

# Port calculation: start at 8001, end at 8001 + (NUM_SECONDARIES - 1)
# For 2 nodes: 8001, 8002
# For 5 nodes: 8001, 8002, 8003, 8004, 8005
SECONDARY_START_PORT = 8001

# Build SECONDARIES list for master
secondaries_list = []
depends_on = []
services = []

for i in range(1, NUM_SECONDARIES + 1):
    secondary_name = f"secondary{i}"
    # Port calculation: 8001 + (i - 1)
    # i=1 -> 8001, i=2 -> 8002, i=3 -> 8003, etc.
    host_port = SECONDARY_START_PORT + (i - 1)
    delay = int(SECONDARY_DELAYS[i - 1]) if i - 1 < len(SECONDARY_DELAYS) else 0
    
    secondaries_list.append(f"http://{secondary_name}:8001")
    depends_on.append(f"      - {secondary_name}")
    
    container_name = f"rl-secondary-{i}"
    services.append(f"""  {secondary_name}:
    build: ./secondary
    container_name: {container_name}
    environment:
      - PORT=8001
      - DELAY_MS={delay}
      - LOG_LEVEL=INFO
    ports:
      - "{host_port}:8001"
""")

# Generate docker-compose.yml
compose_content = f"""services:
  master:
    build: ./master
    container_name: rl-master
    environment:
      - HOST=0.0.0.0
      - PORT={MASTER_PORT}
      - SECONDARIES={",".join(secondaries_list)}
      - LOG_LEVEL=INFO
    ports:
      - "{MASTER_PORT}:{MASTER_PORT}"
    depends_on:
{chr(10).join(depends_on)}

{chr(10).join(services)}"""

# Write to file
with open("docker-compose.yml", "w") as f:
    f.write(compose_content)

print(f"Generated docker-compose.yml with {NUM_SECONDARIES} secondary nodes")
print(f"Master: http://localhost:{MASTER_PORT}")
print(f"Secondaries: ports {SECONDARY_START_PORT} to {SECONDARY_START_PORT + NUM_SECONDARIES - 1}")
for i in range(1, NUM_SECONDARIES + 1):
    host_port = SECONDARY_START_PORT + (i - 1)
    print(f"  Secondary {i}: http://localhost:{host_port}")

