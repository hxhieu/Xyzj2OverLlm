# Agent Instructions

## Project Context

- Project: Vietnamese translation workflow for `Next Stop Jianghu 2`.
- Repo root: `/mnt/nfs/game-server/Xyzj2OverLlm`.
- Main working DB: Postgres database `nextstopjianghu2_translation`.
- Legacy SQLite DB: `_viethoa/glossary-audit.db`, kept only for historical migration/reference unless the user explicitly asks to use it.
- Postgres workflow scripts live under `_postgres_workflow`.
- Detailed Vietnamese translation prompt: `_viethoa/PROMPT.md`. Load it before translation/audit work.
- Use Vietnamese when discussing translation work with the user.

## Translation Style

- Prefer concise Vietnamese suitable for repeated game UI use.
- For wuxia, martial arts, sects, manuals, internal skills, techniques, realms, and titles, prefer classic Hán Việt style.
- Dialogue should read as natural Quốc ngữ Vietnamese. In dialogue, use Hán Việt mainly for names, titles, places, sects, treasures, manuals, skills, and martial techniques; do not translate ordinary sentence grammar word-by-word into Hán Việt.
- Dialogue translation must be reviewed and written sentence by sentence. Do not use override scripts, bulk mechanical Hán Việt, or character-reading automation for dialogue; preserve the wuxia/period tone while making each line idiomatic Vietnamese and faithful to the source meaning.
- Keep skill/manual/martial technique names in Hán Việt title case unless the user asks otherwise.
- Keep Japanese names in Japanese romaji/alphabet form, not Hán Việt.
- Omit redundant UI category words when context is clear, such as `điểm`, `huyệt`, `sách`, `cuộn`, `vật phẩm`, `kỹ năng`.
- For acupoint names, omit `huyệt` when the name/context is already clear.
- Preserve ASCII template/version tokens from `source_text`, such as `_new_3`, literally in `translated`. Audit these from the original source text, not from possibly buggy existing translations. In Vietnamese text, separate such tokens from adjacent Vietnamese words with spaces where they appear inline.
- For compact UI effect text, abbreviate numeric time units: `1s`, `2p`, `3h`, `4ng` instead of spelling out `Giây`, `Phút`, `Giờ`, or `Ngày`.
- For stat modifiers, keep `+` adjacent to the number: `Ngự Tâm +107`, not `Ngự Tâm + 107`.
- For technical numeric suffixes attached to a term in `source_text`, preserve the compact punctuation shape, for example `Phi Kiếm*5-1`, not `Phi Kiếm * 5 - 1`.
- Preserve Latin/ASCII words and codes from `source_text` as whole tokens, for example `debuff`, `BUFF`, `NPC`, `DLC64`; never split them into `d e b u f f` or `D L C 6 4`.

## Han Viet Batch Workflow

- For repeated terms, prefer existing `locked` translations in Postgres `translation_values` before creating a new translation.
- If no locked translation exists, `_viethoa/chinese-hanviet-cognates/inputs/thieuchuu.txt` can be used as a local Thiều Chửu character-reading fallback to draft Hán Việt names.
- Treat Thiều Chửu output as a draft only. Review multi-reading characters and fixed wuxia phrases manually; do not override established DB translations with mechanical readings.
- After using any mechanical Hán Việt draft, run the leftover-Han-character QA query before reporting completion.

## Source Of Truth

- Current workflow is Postgres first. Do translation/review/status work in Postgres, then export runtime resources from Postgres.
- `Files/Raw/DB/db1.txt` is the canonical game DB shape. Postgres stores full section/line shape in `db_sections` and `db_lines`; only translatable fields are stored in `db_fields`.
- `translation_values` stores deduplicated source texts and translations. `translation_occurrences` maps each DB field or asset entry to a translation value. `translation_overrides` stores occurrence-specific translations when a repeated source needs context-specific text.
- `asset_entries` stores non-DB runtime text such as `dumpedPrefabText.txt` and `dynamicStrings.txt`.
- `Files/Glossary.yaml` and the glossary import/export/apply commands are legacy support for the upstream LLM translation workflow. Do not use them for this Vietnamese workflow unless the user explicitly asks.
- Status convention:
  - `pending`: waiting for the agent to translate.
  - `reviewed`: translated by the agent, waiting for user approval.
  - `locked`: accepted/finalized by the user or explicitly agreed as final.
- When translating Postgres rows, update `translation_values.translated_text` and set `translation_values.status = 'reviewed'`, not `locked`, unless the user explicitly says to lock/chốt them.
- Keep `translation_occurrences.status` for occurrence-level review/export policy. A pending occurrence may point to a locked deduped value; this is expected dedup behavior. Use strict export only when the user wants occurrence-level reviewed/locked output.
- Do not assume pending or legacy ignored rows are junk. If the user asks to finish a whole section or asset, translate the pending values for that scope.
- For targeted audit fixes in Postgres, prefer direct Postgres `UPDATE` statements through MCP. Do not create throwaway override scripts for small or medium correction batches unless the user explicitly asks for a reusable script.

## Postgres Workflow

Install Python dependency if needed:

```bash
python3 -m pip install -r _postgres_workflow/requirements.txt
```

Run read-only workflow QA, including section breakdown:

```bash
python3 _postgres_workflow/check_workflow.py --format markdown --section-limit 80
```

Create a Postgres backup under `_working/backups/postgres`:

```bash
python3 _postgres_workflow/backup_postgres.py
```

The backup script uses `DATABASE_URL` or the Postgres MCP connection string in `.codex/config.toml`; it falls back to Docker `postgres:<server-major>` when local `pg_dump` is too old.

## Test Game Packaging Workflow

When the user asks to generate `db1.txt`, distribution files, or a test-game build, run the full workflow needed so `_working/BepInEx` ends in a copy-ready state. The user should only need to copy `_working/BepInEx` into the game install.

Use the root all-in-one script:

```bash
bash stage_test_build.sh
```

This exports Postgres-backed runtime resources and stages them here:

```text
_working/BepInEx/resources/db1.txt
_working/BepInEx/resources/dumpedPrefabText.txt
_working/BepInEx/resources/dynamicStrings.txt
```

Then it builds `EnglishPatch` and deploys plugin DLLs to `_working/BepInEx/plugins`.

For resources only, use:

```bash
bash _postgres_workflow/stage_resources.sh
```

Set `STRICT_OCCURRENCES=1` for `db1.txt` export that only applies translations whose individual occurrence status is reviewed/locked.

## Build Notes

- Building `EnglishPatch` copies plugin DLLs to `_working/BepInEx/plugins` by default.
- The plugin DLL build does not regenerate text resources.
- Text resources require the Postgres stage workflow above.
- If plugin code changed or the user asks for distribution files, build `EnglishPatch` as needed so `_working/BepInEx/plugins` contains current DLLs.

## Cautions

- When the user asks to translate or audit DB rows, update Postgres only and leave `Files/Converted` untouched unless the user explicitly asks for legacy files.
- After translating a Postgres batch, check reviewed/edited rows for leftover Han characters in `translated_text`, for example `translated_text ~ '[一-鿿]'`, and fix those defects before reporting completion.
- When checking underscore/template tokens in Postgres, use literal checks such as `strpos(source_text, '_') > 0`; do not use unescaped SQL `LIKE '%_%'` because `_` is a wildcard there.
- Avoid loading huge YAML/text files into context. Query small batches from Postgres instead.
- Keep backups under `_working/backups` so they stay outside version control.

## Legacy SQLite Workflow

- `_viethoa/glossary-audit.db`, `converted_file_lines`, `converted_file_splits`, and `Files/Converted` are deprecated for the Vietnamese workflow.
- Use SQLite only if the user explicitly asks to inspect legacy migration data or recover old translations.
- Do not re-import converted files into SQLite as part of normal translation or packaging.
- Do not use `dotnet run --project Translate -- import-converted-db`, `export-converted-db`, or `package` for the normal Vietnamese workflow unless the user explicitly asks for the legacy pipeline.
