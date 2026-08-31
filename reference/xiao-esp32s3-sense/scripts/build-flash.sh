#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: build-flash.sh build [BUILD_PATH]
       build-flash.sh flash SERIAL_DEVICE [BUILD_PATH]

Requires arduino-cli and esp32:esp32 core 3.3.11. SERIAL_DEVICE should be a
stable /dev/serial/by-id path selected from the Physical CI inventory.
EOF
}

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: use hil-01 or another Linux build host." >&2
  exit 2
fi

mode="${1:-}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sketch_dir="$(cd "${script_dir}/../firmware/EphyCamReference" && pwd)"
fqbn='esp32:esp32:XIAO_ESP32S3:PSRAM=opi'

if ! arduino-cli core list | grep -Eq '^esp32:esp32[[:space:]]+3\.3\.11([[:space:]]|$)'; then
  echo "ERROR: esp32:esp32 core 3.3.11 is required." >&2
  exit 1
fi

case "${mode}" in
  build)
    build_path="${2:-${script_dir}/../build}"
    arduino-cli compile --fqbn "${fqbn}" --build-path "${build_path}" "${sketch_dir}"
    ;;
  flash)
    serial_device="${2:-}"
    build_path="${3:-${script_dir}/../build}"
    if [[ -z "${serial_device}" ]]; then
      usage >&2
      exit 2
    fi
    arduino-cli compile --fqbn "${fqbn}" --build-path "${build_path}" "${sketch_dir}"
    arduino-cli upload --fqbn "${fqbn}" --port "${serial_device}" \
      --input-dir "${build_path}" "${sketch_dir}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
