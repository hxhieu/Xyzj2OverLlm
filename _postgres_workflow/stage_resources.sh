#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

output_dir="${OUTPUT_DIR:-_working/postgres_export}"
stage_dir="${STAGE_DIR:-_working/BepInEx/resources}"
section_status_args=()

if [[ "${STRICT_OCCURRENCES:-0}" == "1" ]]; then
  section_status_args+=(--require-occurrence-status)
fi

mkdir -p "$output_dir" "$stage_dir"

python3 _postgres_workflow/export_db1.py \
  --output "$output_dir/db1.txt" \
  "${section_status_args[@]}"

python3 _postgres_workflow/export_assets.py \
  --file dumpedPrefabText.txt \
  --output "$output_dir/dumpedPrefabText.txt"

python3 _postgres_workflow/export_assets.py \
  --file dynamicStrings.txt \
  --output "$output_dir/dynamicStrings.txt"

cp "$output_dir/db1.txt" "$stage_dir/db1.txt"
cp "$output_dir/dumpedPrefabText.txt" "$stage_dir/dumpedPrefabText.txt"
cp "$output_dir/dynamicStrings.txt" "$stage_dir/dynamicStrings.txt"

echo "staged_resources: $stage_dir"
ls -lh \
  "$stage_dir/db1.txt" \
  "$stage_dir/dumpedPrefabText.txt" \
  "$stage_dir/dynamicStrings.txt"

sha256sum \
  "$stage_dir/db1.txt" \
  "$stage_dir/dumpedPrefabText.txt" \
  "$stage_dir/dynamicStrings.txt"
