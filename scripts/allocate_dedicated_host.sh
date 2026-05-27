#!/usr/bin/env bash
set -euo pipefail

INSTANCE_TYPE="mac2-m2pro.metal"
AZ_ID="use1-az6"
QUANTITY=1
RETRY_INTERVAL=15

# Resolve AZ ID (use1-az4) → AZ name (us-east-1x) — allocate-hosts requires the name
AZ=$(aws ec2 describe-availability-zones \
  --filters "Name=zone-id,Values=$AZ_ID" \
  --query "AvailabilityZones[0].ZoneName" \
  --output text)

if [[ -z "$AZ" || "$AZ" == "None" ]]; then
  echo "Error: could not resolve AZ ID '$AZ_ID' to an AZ name. Check your AWS region/credentials."
  exit 1
fi

echo "Attempting to allocate EC2 dedicated host..."
echo "  Instance type:   $INSTANCE_TYPE"
echo "  AZ ID:           $AZ_ID ($AZ)"
echo "  Quantity:        $QUANTITY"
echo ""

attempt=0
while true; do
  attempt=$((attempt + 1))
  timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$timestamp] Attempt $attempt..."

  output=$(aws ec2 allocate-hosts \
    --instance-type "$INSTANCE_TYPE" \
    --availability-zone "$AZ" \
    --quantity "$QUANTITY" \
    --auto-placement on \
    2>&1) && exit_code=0 || exit_code=$?

  if [[ $exit_code -eq 0 ]]; then
    echo ""
    echo "Success! Dedicated host allocated:"
    echo "$output"
    break
  fi

  if echo "$output" | grep -qi "InsufficientCapacity\|insufficient capacity"; then
    echo "  Insufficient capacity. Retrying in ${RETRY_INTERVAL}s..."
  else
    # Unexpected error — bail out so you can investigate
    echo "  Unexpected error:"
    echo "$output"
    exit 1
  fi

  sleep "$RETRY_INTERVAL"
done
