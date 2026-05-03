# Vietnamese Glossary Translation Guidelines

Session goal: translate and audit `Files/Glossary.yaml` entries into Vietnamese using the SQLite workspace in `_viethoa/glossary-audit.db`.

## Language

- Use Vietnamese for all discussion and final glossary results.
- Translate glossary entries only unless the user explicitly asks to update converted game text.
- Prefer concise canonical terms suitable for repeated use in UI, skill names, item names, quest names, and dialogue.
- Prefer compact terms for game UI, measured mainly by word count rather than rendered width.

## Wuxia Style

- The game is Chinese wuxia/xianxia-adjacent and strongly influenced by Kim Dung/Jin Yong style.
- For martial arts, sects, manuals, internal energy, moves, realms, and titles, prefer Hán Việt over literal Vietnamese.
- Keep the tone close to classic Vietnamese wuxia translations.
- Avoid awkward literal translations that sound modern, mechanical, or gamey when a natural Hán Việt term exists.

## Naming Rules

- Skill/manual/martial technique names should normally be Hán Việt title case.
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

## Glossary Workflow

- Use `_viethoa/glossary-audit.db` as the working database.
- Query small batches or individual IDs instead of loading large YAML/text files into context.
- Use `glossary_entries.result` for the chosen Vietnamese canonical translation.
- Mark reviewed entries with `status = 'reviewed'`.
- Put short rationale or caveats in `notes`.
- When useful, inspect occurrences from `glossary_occurrences` joined with `stringlang_splits` before choosing a final term.

## Caution

- Short one-character glossary terms are noisy. Prefer reviewing them with occurrence context.
- If a term appears inside a longer phrase, translate the full phrase naturally rather than blindly composing word by word.
- Keep consistency, but allow grammar and readability to override rigid replacement in dialogue.
