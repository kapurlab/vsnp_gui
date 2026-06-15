#!/usr/bin/env bash
# Runs as part of the batch_connect job, on the allocated compute node,
# before script.sh. Allocates a free port on that node for uvicorn.

source_helpers

port=$(find_port)
export port

echo "Port — uvicorn:${port} on host $(hostname)"

# OOD renders script.sh.erb without execute permission; fix that.
chmod +x ./script.sh 2>/dev/null || true
