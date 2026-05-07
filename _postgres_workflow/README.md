# Postgres Translation Workflow

This folder contains the current normalized Postgres workflow for Vietnamese
translation.

Current source-of-truth:

- `Files/Raw/DB/db1.txt` is the canonical game DB shape.
- Postgres stores sections, lines, translatable fields, deduplicated translations,
  prefab text, and dynamic string text.
- Export still writes full expanded runtime resources for the game plugin.
- `db_lines.raw_text` keeps the full original line shape. `db_fields` stores only
  fields that are probably translatable, so numeric/code/blank fields are not
  exploded into millions of unnecessary rows.
- Legacy SQLite (`_viethoa/glossary-audit.db`) and `Files/Converted` are
  deprecated for normal Vietnamese translation/packaging. Use them only for
  historical migration or explicit recovery tasks.

Main one-command test build:

```bash
bash stage_test_build.sh
```

This exports Postgres-backed resources to `_working/BepInEx/resources`, builds
plugin DLLs, and leaves `_working/BepInEx` copy-ready.

## Python Dependencies

Install the workflow dependency into the Python environment you use to run these
scripts:

```bash
python3 -m pip install -r _postgres_workflow/requirements.txt
```

On Debian/Ubuntu system Python, this also works if you prefer apt packages:

```bash
sudo apt install python3-psycopg2
```

## Import Raw DB Shape

Dry-run only:

```bash
python3 _postgres_workflow/import_db1.py \
  --database-url "$DATABASE_URL" \
  --source Files/Raw/DB/db1.txt
```

Apply import:

```bash
python3 _postgres_workflow/import_db1.py \
  --database-url "$DATABASE_URL" \
  --source Files/Raw/DB/db1.txt \
  --apply
```

If the same source hash was already imported and you want to replace that import:

```bash
python3 _postgres_workflow/import_db1.py \
  --database-url "$DATABASE_URL" \
  --source Files/Raw/DB/db1.txt \
  --apply \
  --replace-existing
```

The script intentionally imports only raw DB shape by default. Legacy translation
backfill is a one-time migration step, not the normal workflow.

## Initialize Dedup Layer

Create one `translation_values` row per unique source text, then map every
translatable DB field to it:

```bash
python3 _postgres_workflow/init_dedup.py
```

Reset and rebuild the dedup layer:

```bash
python3 _postgres_workflow/init_dedup.py --reset
```

## Legacy Backfill

Backfill existing translated DB fields from the legacy SQLite audit DB. This is
for migration/recovery only:

```bash
python3 _postgres_workflow/backfill_from_sqlite.py
```

This uses only `locked` and `reviewed` SQLite split rows. When the same source
text has multiple translations, the most common translation becomes the deduped
default and occurrence-specific conflicts are written to `translation_overrides`.

## Import Asset Text

Import legacy prefab asset text that does not live in `db1.txt`:

```bash
python3 _postgres_workflow/import_assets_from_sqlite.py --file dumpedPrefabText.txt
```

Import raw dynamic strings as pending assets:

```bash
python3 _postgres_workflow/import_dynamic_strings.py
```

## Export DB1

Export a full expanded `db1.txt` from Postgres:

```bash
python3 _postgres_workflow/export_db1.py
```

By default this writes `_working/postgres_export/db1.txt` and includes
translations whose status is `locked` or `reviewed`.

For verification against the legacy SQLite export, require each occurrence to
have an allowed status instead of applying deduped values globally:

```bash
python3 _postgres_workflow/export_db1.py --require-occurrence-status
```

## Export Asset Resources

Export `dumpedPrefabText.txt` from Postgres:

```bash
python3 _postgres_workflow/export_assets.py --file dumpedPrefabText.txt
```

Export translated dynamic string contracts from Postgres:

```bash
python3 _postgres_workflow/export_assets.py \
  --file dynamicStrings.txt \
  --output _working/postgres_export/dynamicStrings.txt
```

## Stage Runtime Resources

Export all Postgres-backed runtime resources and copy them to
`_working/BepInEx/resources`:

```bash
bash _postgres_workflow/stage_resources.sh
```

Use strict occurrence status for `db1.txt` export:

```bash
STRICT_OCCURRENCES=1 bash _postgres_workflow/stage_resources.sh
```

Optional output/stage directories:

```bash
OUTPUT_DIR=_working/postgres_export STAGE_DIR=_working/BepInEx/resources \
  bash _postgres_workflow/stage_resources.sh
```

From the repo root, stage resources and build/copy plugin DLLs in one command:

```bash
bash stage_test_build.sh
```

Set build configuration if needed:

```bash
CONFIGURATION=Release bash stage_test_build.sh
```

## Backup Postgres

Create a tar-format `pg_dump` backup under `_working/backups/postgres`:

```bash
python3 _postgres_workflow/backup_postgres.py
```

The script reads `DATABASE_URL`; if absent, it falls back to the Postgres MCP
connection string in `.codex/config.toml`. It also writes a small JSON manifest
next to the `.tar` file with size and restore hint.

If the local `pg_dump` major version is older than the server, the script falls
back to Docker image `postgres:<server-major>`.

## Check Workflow

Run read-only integrity and status checks:

```bash
python3 _postgres_workflow/check_workflow.py
```

Limit section breakdown rows:

```bash
python3 _postgres_workflow/check_workflow.py --section-limit 30
```

Write a Markdown report:

```bash
python3 _postgres_workflow/check_workflow.py --format markdown --section-limit 80 \
  > _working/postgres_workflow_report.md
```

## Dialogue Chain Order

For story dialogue translation, `line_no` is not runtime chronology. Use
`condition_group`, `scriptsclient`, compiled graph `PopDialog(...)` transitions,
and `dialoguelist.nextid` to translate in game flow order.

When translating a dialogue batch, also resolve every `dialoguelist.nameid`.
Reuse locked speaker names. If a speaker name used by the batch is still
pending, translate it together with the dialogue batch and set it to `reviewed`.
Also reuse locked nouns/terms when they appear inside dialogue sentences, such
as names, places, sects, titles, skills, manuals, medicines, items, and other
established terminology.

See `_postgres_workflow/dialogue_chain_workflow.md`.

Reusable helpers:

```bash
_working/venv-unitypy/bin/python _postgres_workflow/extract_dialogue_graphs.py \
  --contains Quest_101200

python3 _postgres_workflow/inspect_dialogue_graph.py \
  _working/extracted_scripts/40365_Quest_101200_Condition_4009602.bytes \
  --mode walk
```

Default to reusing `_working/extracted_scripts/*.bytes`. Do not re-extract from
`resources.assets` during normal dialogue work unless the user indicates the
game data changed, the cache may be stale, or a needed script is missing.

## Mount Game Data SMB

If the server restarts and the mounted Unity data folder is missing, remount it
with:

```bash
SMB_USER=... SMB_PASSWORD=... bash _postgres_workflow/mount_game_data_smb.sh
```

Default mount:

```text
//192.168.0.222/下一站江湖Ⅱ_Data -> _working/nextstopjianghu2_data
```

Check status without mounting:

```bash
bash _postgres_workflow/mount_game_data_smb.sh --check
```
