# Dialogue Chain Translation Workflow

This note records the verified workflow for translating in-game dialogue in
runtime order, especially story/opening scenes where `line_no` does not reflect
chronology.

Use this together with `_viethoa/PROMPT.md`. Translate manually, sentence by
sentence, and update Postgres `translation_values.translated_text` with
`status = 'reviewed'` unless the user explicitly asks to lock/chot.

## Source Of Truth

- Postgres is the translation source of truth.
- Game DB shape lives in `db_lines` / `db_sections`; translatable text is in
  `db_fields`, `translation_occurrences`, and `translation_values`.
- The mounted game data folder, when available, is:

```text
_working/nextstopjianghu2_data
```

Important files:

```text
_working/nextstopjianghu2_data/Managed/Assembly-CSharp.dll
_working/nextstopjianghu2_data/resources.assets
_working/nextstopjianghu2_data/StreamingAssets/DB/db1.txt
```

Compiled graph cache:

```text
_working/extracted_scripts/*.bytes
```

This cache is intended to be reused across translation sessions. Extract again
only when the mounted game data changes, a needed script is missing, or the
cache is suspected stale. At the time this workflow was recorded, all TextAssets
from `resources.assets` had been extracted once: 6414 TextAssets, 6412 unique
`.bytes` filenames because two TextAssets share duplicate names.

## Why `line_no` Is Not Enough

`db_lines.line_no` is file/import order. It is useful for locating records, but
dialogue runtime order is driven by:

1. `condition_group` quest/condition sequencing.
2. `scriptsclient` script id to compiled graph name.
3. Compiled graph transitions in `resources.assets`.
4. `PopDialog(<dialoguelist id>)` calls inside those graphs.
5. `dialoguelist.nextid` for lines inside each displayed dialogue chain.

## Verified `dialoguelist` Field Mapping

The mapping below was verified from `Assembly-CSharp.dll`, class
`SweetPotato.Dialoguelist.LoadCSV`.

```text
1   id
2   nextid
3   chattype
4   chatsubtype
5   cameratarget
6   chatlangid
7   chatlangid1
8   nameid
9   groupId
10  bubbleCondition
11  xiuxianAnimCondition
12  questionFunction1
13  questionFunction2
14  moduleId
15  scriptId
16  emojiId
17  conditionscriptId
18-28 scriptParam[0..10]
29  npcId
```

Text id resolution should use field 6, falling back to field 7:

```sql
COALESCE(
  NULLIF(split_part(dl.raw_text, '#', 6), '0'),
  NULLIF(split_part(dl.raw_text, '#', 7), '0')
) AS stringlang_id
```

Speaker name uses field 8 (`nameid`), usually also resolved through
`stringlang`. Prefer existing locked names, titles, skills, sects, and nouns.
When a dialogue batch uses speaker names that are still pending, translate those
speaker names in the same batch and set them to `reviewed`. Do not leave
displayed speaker names as English placeholders or machine output.

Locked nouns and terminology must be reused inside dialogue sentences whenever
they appear in source/context. This includes personal names, place names, sects,
titles, martial skills, manuals, medicines, items, organizations, and other
established terms. If a locked term sounds awkward in a sentence, keep the term
itself fixed and adjust the surrounding Vietnamese phrasing.

## Dialogue Runtime Logic

`NpcChatView.OnClickNext` follows `dialoguelist.nextid` while `nextid != 0`.
When `nextid = 0`, the next dialogue chain is supplied by the caller/script
queue, not by `dialoguelist`.

Therefore:

- Within a chain: follow `dialoguelist.nextid`.
- Between chains: inspect graph scripts and `PopDialog(...)` transition order.

## Finding The Script For A Quest Step

Use Postgres to inspect condition/script links. Example from the opening flow:

```sql
SELECT
  ds.name AS section,
  dl.line_no,
  dl.game_id,
  dl.raw_text
FROM db_lines dl
JOIN db_sections ds ON ds.id = dl.section_id
WHERE ds.name IN ('condition_group', 'scriptsclient')
  AND (
    dl.raw_text LIKE '%4009601%' OR
    dl.raw_text LIKE '%4009602%' OR
    dl.raw_text LIKE '%40364%' OR
    dl.raw_text LIKE '%40365%'
  )
ORDER BY ds.name, dl.line_no;
```

Verified opening records:

```text
condition_group 4009601 -> script 40364 -> 40364_Quest_101200_Condition_4009601
condition_group 4009602 -> script 40365 -> 40365_Quest_101200_Condition_4009602
condition_group 4009603 -> script 40366 -> 40366_Quest_101200_Condition_4009603
scriptsclient   40368 -> 40368_jiangmenStart_trigger_165608
```

## Extracting Compiled Graph TextAssets

`resources.assets` contains compiled TextAsset `.bytes` graph data. If UnityPy
is not installed, create a local venv under `_working`:

```bash
python3 -m venv _working/venv-unitypy
_working/venv-unitypy/bin/pip install UnityPy
```

Reusable helper for missing/stale cache entries:

```bash
_working/venv-unitypy/bin/python _postgres_workflow/extract_dialogue_graphs.py \
  --contains Quest_101200 \
  --contains jiangmenStart
```

Refresh the full cache when the game data changes:

```bash
_working/venv-unitypy/bin/python _postgres_workflow/extract_dialogue_graphs.py \
  --all \
  --output-dir _working/extracted_scripts
```

List only, without writing files:

```bash
_working/venv-unitypy/bin/python _postgres_workflow/extract_dialogue_graphs.py \
  --contains Quest_101200 \
  --list-only
```

Important: use `surrogateescape`, not `surrogatepass`, so bytes such as
`fe ff` survive round-trip correctly.

## Parsing Extracted `.bytes` Graphs

The compiled graph format was inferred from
`StreamingAssets/Tools/Scripts/DiagramLoader.py`.

Reusable helper:

```bash
python3 _postgres_workflow/inspect_dialogue_graph.py \
  _working/extracted_scripts/40365_Quest_101200_Condition_4009602.bytes \
  --mode walk
```

Other modes:

```bash
python3 _postgres_workflow/inspect_dialogue_graph.py _working/extracted_scripts/*.bytes --mode popdialogs
python3 _postgres_workflow/inspect_dialogue_graph.py _working/extracted_scripts/40365_Quest_101200_Condition_4009602.bytes --mode full
```

Follow transitions from `Entry`. When a state has `PopDialog(<id>)`, that id is
the first `dialoguelist.game_id` of a displayed chain. Then expand that chain by
following `dialoguelist.nextid`.

## SQL: Expand A `PopDialog` Chain

Use this query to expand one displayed chain and show current translated text:

```sql
WITH RECURSIVE chain AS (
  SELECT
    1 AS line_order,
    dl.game_id AS dialog_id,
    split_part(dl.raw_text, '#', 2) AS nextid,
    split_part(dl.raw_text, '#', 8) AS nameid,
    COALESCE(
      NULLIF(split_part(dl.raw_text, '#', 6), '0'),
      NULLIF(split_part(dl.raw_text, '#', 7), '0')
    ) AS stringlang_id
  FROM db_lines dl
  JOIN db_sections ds ON ds.id = dl.section_id AND ds.name = 'dialoguelist'
  WHERE dl.game_id = '893333'

  UNION ALL

  SELECT
    c.line_order + 1,
    dl.game_id,
    split_part(dl.raw_text, '#', 2),
    split_part(dl.raw_text, '#', 8),
    COALESCE(
      NULLIF(split_part(dl.raw_text, '#', 6), '0'),
      NULLIF(split_part(dl.raw_text, '#', 7), '0')
    )
  FROM chain c
  JOIN db_lines dl ON dl.game_id = c.nextid
  JOIN db_sections ds ON ds.id = dl.section_id AND ds.name = 'dialoguelist'
  WHERE c.nextid <> '0' AND c.line_order < 50
)
SELECT
  c.line_order,
  c.dialog_id,
  c.nextid,
  c.nameid,
  name_tv.translated_text AS speaker_name,
  tv.id AS translation_value_id,
  tv.status,
  tv.source_text,
  tv.translated_text
FROM chain c
JOIN db_lines sl ON sl.game_id = c.stringlang_id
JOIN db_sections ss ON ss.id = sl.section_id AND ss.name = 'stringlang'
JOIN db_fields df ON df.line_id = sl.id
JOIN translation_occurrences occ ON occ.db_field_id = df.id
JOIN translation_values tv ON tv.id = occ.translation_value_id
LEFT JOIN db_lines name_sl ON name_sl.game_id = c.nameid
LEFT JOIN db_sections name_ss ON name_ss.id = name_sl.section_id AND name_ss.name = 'stringlang'
LEFT JOIN db_fields name_df ON name_df.line_id = name_sl.id
LEFT JOIN translation_occurrences name_occ ON name_occ.db_field_id = name_df.id
LEFT JOIN translation_values name_tv ON name_tv.id = name_occ.translation_value_id
ORDER BY c.line_order;
```

Change `WHERE dl.game_id = '893333'` to the `PopDialog` id you are expanding.

## Verified Opening Flow Sample

The following sample was checked against observed runtime order.

```text
40368_jiangmenStart_trigger_165608:
  893342 -> 893303 -> 893359

40364_Quest_101200_Condition_4009601:
  893358

40365_Quest_101200_Condition_4009602:
  893350 -> 893338 -> 893334 -> 893335 -> 893346 -> 893301 -> 893333

893333 expands by nextid:
  893333 -> 893332 -> 893331 -> 893330 -> 893329

40366_Quest_101200_Condition_4009603:
  893328 -> 893327 -> 893324 -> 893323 -> 893318

893327 expands by nextid:
  893327 -> 893326 -> 893325

893323 expands by nextid:
  893323 -> 893322 -> 893321 -> 893320 -> 893319
```

Known locked name:

```text
stringlang 3306000: 月兰 -> Nguyệt Lan
```

## Translation Procedure

1. Load `_viethoa/PROMPT.md`.
2. Find the current quest/condition step in `condition_group`.
3. Map its script id through `scriptsclient`.
4. Extract/parse the compiled graph from `resources.assets`.
5. Walk from `Entry` and record `PopDialog(...)` ids in transition order.
6. Expand each `PopDialog` id through `dialoguelist.nextid`.
7. Query `stringlang` rows and existing `translated_text`.
8. Resolve all `nameid` speaker names used by the batch.
9. Reuse locked names; translate any pending speaker names used by the batch.
10. Check and reuse locked nouns/terms inside each dialogue sentence.
11. Translate each pending line manually into Vietnamese, preserving tone and tokens.
12. Update `translation_values.translated_text` and set status to `reviewed`.
13. Run QA for leftover Han characters and template/token preservation.
14. Lock only when the user explicitly says to lock/chot.

Do not bulk-mechanically translate dialogue. Existing English `translated_text`
is context only; rewrite into literary, natural Vietnamese according to
`_viethoa/PROMPT.md`.
