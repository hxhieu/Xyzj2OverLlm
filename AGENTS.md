# Agent Instructions

## Project Context

- Project: Vietnamese translation workflow for `Next Stop Jianghu 2`.
- Repo root: `/mnt/nfs/game-server/Xyzj2OverLlm`.
- Main working DB: `_viethoa/glossary-audit.db`.
- Detailed Vietnamese translation prompt: `_viethoa/PROMPT.md`. Load it before translation/audit work.
- Use Vietnamese when discussing translation work with the user.

## Translation Style

- Prefer concise Vietnamese suitable for repeated game UI use.
- For wuxia, martial arts, sects, manuals, internal skills, techniques, realms, and titles, prefer classic Hán Việt style.
- Keep skill/manual/martial technique names in Hán Việt title case unless the user asks otherwise.
- Keep Japanese names in Japanese romaji/alphabet form, not Hán Việt.
- Omit redundant UI category words when context is clear, such as `điểm`, `huyệt`, `sách`, `cuộn`, `vật phẩm`, `kỹ năng`.
- For acupoint names, omit `huyệt` when the name/context is already clear.

## Source Of Truth

- Current workflow is SQLite first, then export back to files.
- The working DB is intentionally limited to converted-file tables: `metadata`, `converted_file_lines`, and `converted_file_splits`.
- `Files/Glossary.yaml` and the glossary import/export/apply commands are legacy support for the upstream LLM translation workflow. Do not use them for this Vietnamese workflow unless the user explicitly asks.
- Status convention:
  - `pending`: waiting for the agent to translate.
  - `reviewed`: translated by the agent, waiting for user approval.
  - `locked`: accepted/finalized by the user or explicitly agreed as final.
- For converted files, use `converted_file_lines` and `converted_file_splits`.
- For converted file focus candidates, use `pending` while untranslated/unreviewed. Do not leave focus candidates as `ignored`; reserve `ignored` for non-target rows.
- When translating converted file rows, set them to `reviewed`, not `locked`, unless the user explicitly says to lock/chốt them.
- Preserve all imported rows. Non-target rows may stay `ignored`, but export writes the whole file back.

## Converted File Workflow

Import one converted file into SQLite:

```bash
dotnet run --project Translate -- import-converted-db --working-directory Files --database _viethoa/glossary-audit.db --file game_manual.txt
```

Export one converted file from SQLite back to `Files/Converted`:

```bash
dotnet run --project Translate -- export-converted-db --working-directory Files --database _viethoa/glossary-audit.db --file game_manual.txt
```

Before importing, exporting, or bulk editing, create a DB backup under `_working/backups` so backups stay outside version control:

```bash
mkdir -p _working/backups
cp _viethoa/glossary-audit.db _working/backups/glossary-audit.db.bak-before-<task>
```

## Test Game Packaging Workflow

When the user asks to generate `db1.txt`, distribution files, or a test-game build, run the full workflow needed so `_working/BepInEx` ends in a copy-ready state. The user should only need to copy `_working/BepInEx` into the game install.

For a test build, the usual flow is:

1. Export any imported converted files that were edited in DB:

```bash
dotnet run --project Translate -- export-converted-db --working-directory Files --database _viethoa/glossary-audit.db --file <file>
```

Common imported files include:

```text
game_manual.txt
item_base.txt
item_base_dangmojianghu.txt
item_base_xianejianghu.txt
item_base_zhenshijianghu.txt
spelleffect.txt
spellprotype.txt
stringlang.txt
```

2. Regenerate runtime resources and stage them for local BepInEx testing:

```bash
dotnet run --project Translate -- package --working-directory Files --stage-resources _working/BepInEx/resources
```

Without `--stage-resources`, `package` only writes:

```text
Files/Mod/db1.txt
Files/Mod/Formatted/dynamicStrings.txt
Files/Mod/Formatted/dumpedPrefabText.txt
```

With `--stage-resources`, it also copies these into `_working/BepInEx/resources`:

```text
db1.txt
dynamicStrings.txt
dumpedPrefabText.txt
```

## Build Notes

- Building `EnglishPatch` copies plugin DLLs to `_working/BepInEx/plugins` by default.
- The plugin DLL build does not regenerate text resources.
- Text resources require the package workflow above.
- If plugin code changed or the user asks for distribution files, build `EnglishPatch` as needed so `_working/BepInEx/plugins` contains current DLLs.

## Cautions

- Do not re-import a converted file after DB edits unless the user wants to discard/refresh those DB edits from `Files/Converted`.
- Avoid loading huge YAML/text files into context. Query small batches from SQLite instead.
- Keep `_viethoa/glossary-audit.db` under GitHub's 100MB file limit. Do not add optional audit indexes, uniqueness constraints, or provenance columns such as `notes` unless the user explicitly accepts the DB growing past that limit.
