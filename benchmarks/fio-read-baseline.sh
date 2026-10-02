#!/usr/bin/env bash
# Run on a mounted filesystem containing an already populated test file.
# The script is intentionally read-only: it never writes to the test file.
set -euo pipefail

TARGET_FILE=${1:?Usage: fio-read-baseline.sh /path/to/prepared-fio-file [output-dir]}
OUTPUT_DIR=${2:-"$(dirname "$TARGET_FILE")/fio-results"}
RUNTIME=${FIO_RUNTIME_SECONDS:-120}

mkdir -p "$OUTPUT_DIR"

run() {
  local name=$1
  local block_size=$2
  local depth=$3
  local jobs=$4
  fio --name="$name" \
    --filename="$TARGET_FILE" \
    --direct=1 \
    --rw=randread \
    --bs="$block_size" \
    --ioengine=libaio \
    --iodepth="$depth" \
    --numjobs="$jobs" \
    --runtime="$RUNTIME" \
    --time_based \
    --group_reporting \
    --readonly \
    --output-format=json+ \
    --output="$OUTPUT_DIR/$name.json"
}

# OCI Block Volume documentation profiles: IOPS, throughput, then latency.
run iops-4k-randread 4k 256 4
run throughput-256k-randread 256k 64 4
run latency-4k-randread 4k 1 1
