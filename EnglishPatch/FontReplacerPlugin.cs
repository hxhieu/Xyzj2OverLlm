using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using HarmonyLib;
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using TMPro;
using UnityEngine;

namespace EnglishPatch;

[BepInPlugin($"{MyPluginInfo.PLUGIN_GUID}.FontReplacer", "FontReplacer", MyPluginInfo.PLUGIN_VERSION)]
public class FontReplacerPlugin : BaseUnityPlugin
{
    internal static new ManualLogSource Logger;

    private static FontReplacerPlugin _instance;
    private static bool _enabled;
    private static TMP_FontAsset _fontAsset;
    private static readonly HashSet<TMP_Text> PendingApply = [];
    private static Coroutine ApplyCoroutine;
    private static bool IsApplyingFont;

    private ConfigEntry<bool> _enabledConfig;
    private ConfigEntry<string> _fontNameConfig;
    private ConfigEntry<string> _fontFileConfig;
    private ConfigEntry<bool> _allowOsFontConfig;
    private ConfigEntry<KeyCode> _reloadHotkeyConfig;

    private void Awake()
    {
        Logger = base.Logger;
        _instance = this;

        _enabledConfig = Config.Bind("General", "Enabled", false,
            "Enable global TextMeshPro font replacement.");
        _fontNameConfig = Config.Bind("General", "FontName", string.Empty,
            "Loaded TMP font asset name, or OS font family/full name when AllowOsFont is true. Do not wrap the value in quotes.");
        _fontFileConfig = Config.Bind("General", "FontFile", string.Empty,
            "Optional .ttf/.otf font file. Relative paths are resolved from BepInEx/resources. Used before OS font lookup when set.");
        _allowOsFontConfig = Config.Bind("General", "AllowOsFont", true,
            "If no loaded TMP font asset matches FontName, try creating a TMP font asset from an OS font family.");
        _reloadHotkeyConfig = Config.Bind("General", "ReloadHotkey", KeyCode.KeypadDivide,
            "Reload font replacement config and reapply to current text.");

        _enabled = _enabledConfig.Value;
        if (!_enabled)
            return;

        Harmony.CreateAndPatchAll(typeof(FontReplacerPlugin));
        ReloadFont();
        ApplyAllFonts();
        Logger.LogWarning("FontReplacer Plugin Loaded!");
    }

    private void Update()
    {
        if (!_enabled)
            return;

        if (UnityInput.Current.GetKeyDown(_reloadHotkeyConfig.Value))
        {
            Config.Reload();
            _enabled = _enabledConfig.Value;
            ReloadFont();
            ApplyAllFonts();
            Logger.LogWarning("Font replacement reloaded.");
        }
    }

    private void ReloadFont()
    {
        _fontAsset = null;

        var fontName = SanitizeConfigString(_fontNameConfig.Value);
        var fontFile = SanitizeConfigString(_fontFileConfig.Value);

        if (!string.IsNullOrEmpty(fontFile))
        {
            _fontAsset = CreateFontAssetFromFile(fontFile);
            if (_fontAsset != null)
                return;
        }

        if (string.IsNullOrEmpty(fontName))
        {
            Logger.LogWarning("Font replacement is enabled but FontName and FontFile are empty.");
            return;
        }

        _fontAsset = FindLoadedFontAsset(fontName);
        if (_fontAsset != null)
        {
            Logger.LogMessage($"Using loaded TMP font asset: {_fontAsset.name}");
            return;
        }

        if (!_allowOsFontConfig.Value)
        {
            Logger.LogWarning($"No loaded TMP font asset found matching '{fontName}'.");
            return;
        }

        try
        {
            foreach (var candidateName in GetFontNameCandidates(fontName))
            {
                var osFont = Font.CreateDynamicFontFromOSFont(candidateName, 16);
                _fontAsset = CreateFontAsset(osFont, $"{candidateName} (OS)");
                if (_fontAsset != null)
                {
                    Logger.LogMessage($"Created TMP font asset from OS font: {candidateName}");
                    return;
                }
            }

            Logger.LogWarning($"Could not create OS font: {fontName}");
            LogInstalledFontNameHints(fontName);
        }
        catch (Exception ex)
        {
            Logger.LogError($"Error creating OS font '{fontName}': {ex}");
        }
    }

    private static string SanitizeConfigString(string value)
    {
        return (value ?? string.Empty).Trim().Trim('"', '\'').Trim();
    }

    private static TMP_FontAsset CreateFontAssetFromFile(string fontFile)
    {
        try
        {
            var resolvedFontFile = ResolveFontFile(fontFile);
            if (resolvedFontFile == null)
            {
                Logger.LogWarning($"FontFile does not exist: {fontFile}");
                return null;
            }

            var unityFont = new Font(resolvedFontFile);
            var asset = CreateFontAsset(unityFont, $"{Path.GetFileNameWithoutExtension(resolvedFontFile)} (File)");
            if (asset != null)
                Logger.LogMessage($"Created TMP font asset from file: {resolvedFontFile}");

            return asset;
        }
        catch (Exception ex)
        {
            Logger.LogError($"Error creating font from file '{fontFile}': {ex}");
            return null;
        }
    }

    private static string ResolveFontFile(string fontFile)
    {
        if (Path.IsPathRooted(fontFile))
            return File.Exists(fontFile) ? fontFile : null;

        var candidates = new[]
        {
            Path.Combine(Paths.BepInExRootPath, "resources", fontFile),
            Path.Combine(Paths.BepInExRootPath, fontFile),
            Path.GetFullPath(fontFile)
        };

        return candidates.FirstOrDefault(File.Exists);
    }

    private static TMP_FontAsset CreateFontAsset(Font unityFont, string assetName)
    {
        if (unityFont == null)
            return null;

        try
        {
            var asset = TMP_FontAsset.CreateFontAsset(unityFont);
            if (asset == null)
                return null;

            asset.name = assetName;
            asset.atlasPopulationMode = AtlasPopulationMode.Dynamic;
            return asset;
        }
        catch (Exception ex)
        {
            Logger.LogWarning($"Could not create TMP font asset '{assetName}': {ex.GetType().Name}: {ex.Message}");
            return null;
        }
    }

    private static IEnumerable<string> GetFontNameCandidates(string fontName)
    {
        yield return fontName;

        if (!fontName.EndsWith(" Regular", StringComparison.OrdinalIgnoreCase))
            yield return $"{fontName} Regular";
    }

    private static void LogInstalledFontNameHints(string fontName)
    {
        try
        {
            var hints = Font.GetOSInstalledFontNames()
                .Where(name => name.IndexOf(fontName, StringComparison.OrdinalIgnoreCase) >= 0
                    || fontName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                .Take(10)
                .ToArray();

            if (hints.Length > 0)
                Logger.LogMessage($"Installed OS font names matching '{fontName}': {string.Join(", ", hints)}");
        }
        catch (Exception ex)
        {
            Logger.LogDebug($"Could not enumerate OS fonts: {ex.Message}");
        }
    }

    private static TMP_FontAsset FindLoadedFontAsset(string fontName)
    {
        var loadedFonts = Resources.FindObjectsOfTypeAll<TMP_FontAsset>();

        return loadedFonts.FirstOrDefault(font =>
            string.Equals(font.name, fontName, StringComparison.OrdinalIgnoreCase))
            ?? loadedFonts.FirstOrDefault(font =>
                font.name.IndexOf(fontName, StringComparison.OrdinalIgnoreCase) >= 0);
    }

    private static void ApplyAllFonts()
    {
        if (!_enabled || _fontAsset == null)
            return;

        foreach (var text in Resources.FindObjectsOfTypeAll<TMP_Text>())
            ApplyFont(text);
    }

    private static void ApplyFont(TMP_Text text)
    {
        if (!_enabled || _fontAsset == null || text == null)
            return;

        if (text.font == _fontAsset)
            return;

        try
        {
            IsApplyingFont = true;
            text.font = _fontAsset;
            text.SetAllDirty();
        }
        finally
        {
            IsApplyingFont = false;
        }
    }

    [HarmonyPostfix, HarmonyPatch(typeof(GameObject), nameof(GameObject.SetActive), [typeof(bool)])]
    public static void Postfix_GameObject_SetActive(GameObject __instance)
    {
        if (!_enabled || _fontAsset == null || __instance == null)
            return;

        foreach (var text in __instance.GetComponentsInChildren<TMP_Text>(true))
        {
            ApplyFont(text);
            QueueApply(text);
        }
    }

    [HarmonyPostfix, HarmonyPatch(typeof(TMP_Text), nameof(TMP_Text.text), MethodType.Setter)]
    public static void Postfix_TMP_Text_SetText(TMP_Text __instance)
    {
        ApplyFont(__instance);
        QueueApply(__instance);
    }

    [HarmonyPostfix, HarmonyPatch(typeof(TMP_Text), nameof(TMP_Text.font), MethodType.Setter)]
    public static void Postfix_TMP_Text_SetFont(TMP_Text __instance)
    {
        ApplyFont(__instance);
        QueueApply(__instance);
    }

    private static void QueueApply(TMP_Text text)
    {
        if (!_enabled
            || _fontAsset == null
            || _instance == null
            || IsApplyingFont
            || text == null
            || text.gameObject == null)
        {
            return;
        }

        PendingApply.Add(text);
        if (ApplyCoroutine == null)
            ApplyCoroutine = _instance.StartCoroutine(ApplyPendingOverNextFrames());
    }

    private static IEnumerator ApplyPendingOverNextFrames()
    {
        for (var frame = 0; frame < 1; frame++)
        {
            yield return null;

            var pending = PendingApply.ToArray();
            PendingApply.Clear();

            foreach (var text in pending)
            {
                if (text != null && text.gameObject != null)
                    ApplyFont(text);
            }
        }

        if (PendingApply.Count > 0)
            ApplyCoroutine = _instance.StartCoroutine(ApplyPendingOverNextFrames());
        else
            ApplyCoroutine = null;
    }
}
