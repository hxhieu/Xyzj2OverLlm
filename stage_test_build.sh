#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

configuration="${CONFIGURATION:-Debug}"

bash _postgres_workflow/stage_resources.sh

dotnet build EnglishPatch/EnglishPatch.csproj \
  --configuration "$configuration"

echo "staged_plugins: _working/BepInEx/plugins"
ls -lh \
  _working/BepInEx/plugins/FanslationStudio.EnglishPatch.dll \
  _working/BepInEx/plugins/FanslationStudio.SharedAssembly.dll

echo "copy_ready: _working/BepInEx"
