# Next Stop - Jianghu 2 English Patch

Install guide:

Extract the [Latest Release](https://github.com/joshfreitas1984/Xyzj2OverLlm/releases) into your `<Game Folder>` folder where 下一站江湖Ⅱ.exe is.

# Contacting us

You can join us here: [Discord](https://discord.gg/sqXd5ceBWT)

## Pre-requesites
The pre-reqs contains:
  - BepInEx
  - Unstripped Game DLLs
  - Configuration for BepinEx

## Build setup

The plugin project needs game and BepInEx DLLs that are not committed to git. By default the project looks for them under the ignored `_references` folder:

```text
_references/
  Managed/
    Assembly-CSharp.dll
    Unity.TextMeshPro.dll
    UnityEngine.UI.dll
  BepInEx/
    plugins/
      XUnity.ResourceRedirector/
        XUnity.ResourceRedirector.dll
        XUnity.ResourceRedirector.BepInEx.dll
```

Those files come from your game install:

```text
<Game Folder>/下一站江湖Ⅱ_Data/Managed/Assembly-CSharp.dll
<Game Folder>/下一站江湖Ⅱ_Data/Managed/Unity.TextMeshPro.dll
<Game Folder>/下一站江湖Ⅱ_Data/Managed/UnityEngine.UI.dll
<Game Folder>/BepInEx/plugins/XUnity.ResourceRedirector/XUnity.ResourceRedirector.dll
<Game Folder>/BepInEx/plugins/XUnity.ResourceRedirector/XUnity.ResourceRedirector.BepInEx.dll
```

The normal build output is written to `EnglishPatch/bin/<Configuration>/netstandard2.1/`. Builds also copy the plugin DLLs to `_working/BepInEx/plugins` by default.

To deploy directly into a game install instead, create an ignored `Directory.Build.props.user` file in the repo root and set `PluginDeployDir`:

```xml
<Project>
  <PropertyGroup>
    <PluginDeployDir>D:\_Steam\steamapps\common\...\BepInEx\plugins</PluginDeployDir>
  </PropertyGroup>
</Project>
```

## Packaging Text Resources

The plugin DLL build does not regenerate translated text resources.

For the Vietnamese workflow, Postgres is the current source of truth. The normal all-in-one staging command is:

```bash
bash stage_test_build.sh
```

This exports Postgres-backed resources and copies them into:

```text
_working/BepInEx/resources/db1.txt
_working/BepInEx/resources/dynamicStrings.txt
_working/BepInEx/resources/dumpedPrefabText.txt
```

Then it builds `EnglishPatch` and deploys DLLs into:

```text
_working/BepInEx/plugins/
```

For resources only:

```bash
bash _postgres_workflow/stage_resources.sh
```

Useful Postgres workflow commands:

```bash
python3 _postgres_workflow/check_workflow.py --format markdown --section-limit 80 \
  > _working/postgres_workflow_report.md

python3 _postgres_workflow/backup_postgres.py
```

The legacy SQLite workflow (`_viethoa/glossary-audit.db`, `Files/Converted`, `export-converted-db`, and `dotnet run --project Translate -- package`) is deprecated for Vietnamese packaging. It remains only for historical migration/reference unless explicitly needed.

The legacy `Files/Glossary.yaml` workflow is not used for Vietnamese packaging.

For the runtime mod, copy the staged folder into the game install:

```text
_working/BepInEx -> <Game Folder>/BepInEx
```

The plugin loads resources from:

```text
BepInEx/resources/db1.txt
BepInEx/resources/dynamicStrings.txt
BepInEx/resources/dumpedPrefabText.txt
```
  
### Name Changer

If you want to change your name because of an old playthrough with Autotranslator or you simply hate the name.

| Hotkey | Active by default? | Function |
|---|---:|---|
| `KeypadPeriod` | Yes | Opens/closes the Property Changer UI for changing player name. |

### Custom Text Resizer

The text resizer also has a BepInEx config value:

```ini
[General]
FontScale = 1
```

This is created in `BepInEx/config/FanslationStudio.EnglishPatch.TextResizer.cfg`. It applies a global font-size multiplier after individual YAML resizers are applied. For example, `0.5` makes touched text half size, `1` keeps normal size, and `1.25` makes it 125% size. Edit the config, then press `KeypadPlus` to reload and reapply it.

| Hotkey | Active by default? | Function |
|---|---:|---|
| `KeypadMinus` | Yes | Adds a text resizer entry for text under the cursor to `BepInEx/resizers/zzAddedResizers.yaml`. |
| `KeypadPlus` | Yes | Reloads text resizer YAML files and reapplies resizers. |
| `KeypadMultiply` | Yes | Adds resizer entries for all text elements in the current scene. Be warned it will grab a lot. |
| `F1` | No | Same as `KeypadMinus`, only if `TextResizerPlugin.DevMode = true` in code. |
| `F2` | No | Same as `KeypadPlus`, only if `TextResizerPlugin.DevMode = true` in code. |
| `F3` | No | Same as `KeypadMultiply`, only if `TextResizerPlugin.DevMode = true` in code. |

### Sprite Replacer V2 Dev Tools

These are inactive in normal builds because `SpriteReplacerV2Plugin.Enabled` is currently `false`.

| Hotkey | Active by default? | Function |
|---|---:|---|
| `F1` | No | Adds a sprite contract for the object under the cursor, only if `SpriteReplacerV2Plugin.Enabled = true` and BepInEx config `DevMode = true`. |
| `F2` | No | Adds sprite contracts for the current scene, only if `SpriteReplacerV2Plugin.Enabled = true` and BepInEx config `DevMode = true`. |
| `F3` | No | Reloads sprite contracts, only if `SpriteReplacerV2Plugin.Enabled = true` and BepInEx config `DevMode = true`. |

### Font Replacer

Font replacement is disabled by default. Enable it in `BepInEx/config/FanslationStudio.EnglishPatch.FontReplacer.cfg`:

```ini
[General]
Enabled = true
FontName = Arial
FontFile =
AllowOsFont = true
ReloadHotkey = KeypadDivide
```

`FontName` first tries to match a loaded TextMeshPro font asset by name. If none is found and `AllowOsFont` is true, the plugin tries to create a TextMeshPro font asset from an installed OS font family or full font name. Do not wrap the value in quotes, for example use `FontName = Wire One`, not `FontName = "Wire One"`.

If OS font lookup fails, copy the `.ttf` or `.otf` file into `BepInEx/resources` and set `FontFile` to the filename:

```ini
FontName =
FontFile = WIREONE-REGULAR.TTF
AllowOsFont = false
```

`FontFile` also accepts an absolute path. Press `KeypadDivide` to reload the config and reapply the font to currently loaded text.

You can use `*` inside the path to match one Unity hierarchy segment. This is useful for generated list rows, for example `AnswerGrid/*/Text` matches `AnswerGrid/1001/Text` and `AnswerGrid/1002/Text`.

Please note I include zzAddedResizers.yaml in the patch. So if  you want to keep them move them to another yaml file when your done. Please submit any resizers you think make sense!

Here are all the things you can do: (Not including it will keep the controls defaults)

```yaml
- path: "GameStart/GameUIRoot/*/FormRoot" # Gets everything that has a FormRoot in it starting with GameStart/GameUIRoot
  sampleText: "Commission"    # Dumped text so you know what the path was for
  idealFontSize: 30           # The font size you want
  allowWordWrap: false        # Allows word wrapping on component
  allowAutoSizing: false      # Lets the font change sizes depending on width given by dev
  allowLeftTrimText: true     # Allow the text to be left trimmed
  adjustX: 0                  # Positive or negative number to adjust left and right 
  adjustY: 0                  # Positive or negative number to adjust up and down
  adjustWidth: 0              # Positive or negative number to adjust allowed width of control
  adjustHeight: 0             # Positive or negative number to adjust allowed height of control
  minFontSize: 0              # Min Font size when autosizing
  maxFontSize: 0              # Max Font size when autosizing
  lineSpacing: 0.0            # Line spacing for text
  characterSpacing: 0.0       # Character spacing for text
  wordSpacing: 0.0            # Word spacing for text
  fontPercentage: 0.70        # Percentage of font size to use (replaces max/min font size if set above 0)
  alignment: Center           # Control alignment on screen for TextMeshProGUI
  overflow: Overflow          # Overflow mode for TextMeshProGUI
```
