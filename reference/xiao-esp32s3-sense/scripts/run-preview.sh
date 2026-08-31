#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: run-preview.sh SERIAL_DEVICE STAGING_ROOT [host options]" >&2
  exit 2
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: run on hil-01 or another Linux USB host." >&2
  exit 2
fi

serial_device="$1"
staging_root="$2"
shift 2
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${script_dir}/../host/ephy_cam_reference.py" \
  serve "${serial_device}" "${staging_root}" "$@"
