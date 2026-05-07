#!/usr/bin/env bash
set -euo pipefail

# Mount the Next Stop Jianghu 2 Unity data folder from SMB into _working.
#
# Required environment:
#   SMB_USER
#   SMB_PASSWORD
#
# Optional environment:
#   SMB_HOST=192.168.0.222
#   SMB_SHARE='下一站江湖Ⅱ_Data'
#   SMB_DOMAIN=<domain-or-workgroup>
#   SMB_VERS=3.0
#   MOUNT_POINT=_working/nextstopjianghu2_data

SMB_HOST="${SMB_HOST:-192.168.0.222}"
SMB_SHARE="${SMB_SHARE:-下一站江湖Ⅱ_Data}"
SMB_VERS="${SMB_VERS:-3.0}"
MOUNT_POINT="${MOUNT_POINT:-_working/nextstopjianghu2_data}"
REMOTE="//${SMB_HOST}/${SMB_SHARE}"

usage() {
  cat <<EOF
Usage:
  SMB_USER=... SMB_PASSWORD=... bash _postgres_workflow/mount_game_data_smb.sh

Optional:
  SMB_HOST=${SMB_HOST}
  SMB_SHARE='${SMB_SHARE}'
  SMB_VERS=${SMB_VERS}
  MOUNT_POINT=${MOUNT_POINT}
  SMB_DOMAIN=<domain-or-workgroup>

Actions:
  --check    only report mount status
  --umount   unmount ${MOUNT_POINT}
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--check" ]]; then
  if mountpoint -q "$MOUNT_POINT"; then
    echo "mounted: $MOUNT_POINT"
  else
    echo "not mounted: $MOUNT_POINT"
  fi
  exit 0
fi

if [[ "${1:-}" == "--umount" ]]; then
  if mountpoint -q "$MOUNT_POINT"; then
    sudo umount "$MOUNT_POINT"
    echo "unmounted: $MOUNT_POINT"
  else
    echo "not mounted: $MOUNT_POINT"
  fi
  exit 0
fi

if [[ -z "${SMB_USERNAME:-}" ]]; then
  echo "SMB_USER is required." >&2
  usage >&2
  exit 2
fi

if [[ -z "${SMB_PASSWORD:-}" ]]; then
  echo "SMB_PASSWORD is required." >&2
  usage >&2
  exit 2
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  echo "mount.cifs not found. Install cifs-utils first." >&2
  exit 2
fi

mkdir -p "$MOUNT_POINT"

if mountpoint -q "$MOUNT_POINT"; then
  echo "already mounted: $MOUNT_POINT"
  exit 0
fi

credentials_file="$(mktemp)"
cleanup() {
  rm -f "$credentials_file"
}
trap cleanup EXIT

chmod 600 "$credentials_file"
{
  printf 'username=%s\n' "$SMB_USER"
  printf 'password=%s\n' "$SMB_PASSWORD"
  if [[ -n "${SMB_DOMAIN:-}" ]]; then
    printf 'domain=%s\n' "$SMB_DOMAIN"
  fi
} > "$credentials_file"

options=(
  "credentials=$credentials_file"
  "iocharset=utf8"
  "uid=$(id -u)"
  "gid=$(id -g)"
  "file_mode=0644"
  "dir_mode=0755"
  "noperm"
  "vers=$SMB_VERS"
)

sudo mount -t cifs "$REMOTE" "$MOUNT_POINT" -o "$(IFS=,; echo "${options[*]}")"
echo "mounted $REMOTE -> $MOUNT_POINT"
