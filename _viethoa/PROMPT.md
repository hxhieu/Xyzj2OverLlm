# Vietnamese Postgres Translation Guidelines

Session goal: translate and audit game text in Vietnamese using the Postgres workflow. The main working DB is `nextstopjianghu2_translation`; legacy SQLite `_viethoa/glossary-audit.db` is for migration/recovery only.

## Language

- Use Vietnamese for all discussion and final translation results.
- Prefer concise canonical terms suitable for repeated use in UI, skill names, item names, quest names, and dialogue.
- Prefer compact terms for game UI, measured mainly by word count rather than rendered width.
- For UI labels, omit redundant unit/category words when context already makes them clear. For example, use `Thiên phú` instead of `Điểm thiên phú`, and `Kinh nghiệm` instead of `Điểm kinh nghiệm`.
- For acupoint/meridian point names, omit the redundant word `huyệt` when the name is already clear or UI context indicates acupoints. For example, use `Thái dương` instead of `Thái dương huyệt`.
- For compound nouns in the same UI/context group, remove generic category nouns when the specific part is enough. Do not keep words like `điểm`, `huyệt`, `sách`, `cuộn`, `vật phẩm`, `kỹ năng`, or similar category labels just because they exist in the source, unless dropping them would make the term ambiguous.

## Wuxia Style

- The game is Chinese wuxia/xianxia-adjacent and strongly influenced by Kim Dung/Jin Yong style.
- For martial arts, sects, manuals, internal energy, moves, realms, and titles, prefer Hán Việt over literal Vietnamese.
- Keep the tone close to classic Vietnamese wuxia translations.
- Avoid awkward literal translations that sound modern, mechanical, or gamey when a natural Hán Việt term exists.

## Naming Rules

- Skill/manual/martial technique names should normally be Hán Việt title case.
- Japanese names should stay in Japanese romaji/alphabet form, not Hán Việt. For example, use `Miyamoto Kai` instead of `Cung Bản Hải`, and `Marume Hayato` instead of `Hoàn Mục Chuẩn Nhân`.
- If a martial art, sect, title, or move clearly comes from classic wuxia sources such as Kim Dung/Jin Yong, use the familiar Vietnamese Hán Việt rendering rather than literal translation. Examples: `七伤拳` -> `Thất Thương Quyền`, `打狗棍法` -> `Đả Cẩu Bổng Pháp`, `九阳真经` -> `Cửu Dương Chân Kinh`, `葵花宝典` -> `Quỳ Hoa Bảo Điển`.
- Job/class labels may use plain Vietnamese when short and natural. If the Quốc ngữ option has the same word count and is clearer, prefer Quốc ngữ.
- Use Hán Việt for job/class labels mainly when the plain Quốc ngữ option needs more words or sounds too modern/explanatory.
- Preserve established wuxia terms such as:
  - `降龙` -> `Hàng Long`
  - `擒龙` -> `Cầm Long`
  - `手` in technique names -> `Thủ`
  - `心诀` -> `Tâm Quyết`
  - `真意` -> `Chân Ý`
  - `劲` -> `Kình`
- Example UI/job tradeoff:
  - `渔夫` -> `Ngư phu`, not `Người câu cá`
  - `铸工` -> `Thợ đúc`, because it is also two words and clearer than `Chú công`
  - `酿酒师` -> `Tửu sư`, not `Thợ nấu rượu`, for compact UI labels.
- Example:
  - `碧霄擒龙手` -> `Bích Tiêu Cầm Long Thủ`
- Do not translate martial names into plain descriptive Vietnamese unless the user asks.

## Postgres Workflow

- Query small batches from Postgres instead of loading large text/YAML files into context.
- `Files/Raw/DB/db1.txt` is the canonical DB shape. Runtime exports are generated from Postgres, not from `Files/Converted`.
- Main tables:
  - `translation_values`: deduplicated source text and its canonical translation.
  - `translation_occurrences`: each DB field or asset occurrence that uses a translation value.
  - `translation_overrides`: occurrence-specific translation when a repeated source needs different wording.
  - `db_sections`, `db_lines`, `db_fields`: raw `db1.txt` shape and translatable DB fields.
  - `asset_entries`: non-DB assets such as `dumpedPrefabText.txt` and `dynamicStrings.txt`.
- When translating a batch, update `translation_values.translated_text` and set `translation_values.status = 'reviewed'`.
- Only set `status = 'locked'` when the user explicitly says to lock/chốt.
- A pending occurrence may point to a locked deduped value. This is expected; use strict export only when occurrence-level approval matters.
- Use `_postgres_workflow/check_workflow.py --format markdown --section-limit 80` to inspect section/asset breakdown.
- Use `bash stage_test_build.sh` to export all Postgres-backed runtime resources and build/copy plugin DLLs into `_working/BepInEx`.
- `Files/Glossary.yaml`, SQLite converted-file tables, and the old `dotnet ... import/export/package` workflow are legacy and should not be used unless the user explicitly asks.

## Caution

- Short one-character rows are noisy. Prefer reviewing them with nearby file context.
- If a term appears inside a longer phrase, translate the full phrase naturally rather than blindly composing word by word.
- Keep consistency, but allow grammar and readability to override rigid replacement in dialogue.
