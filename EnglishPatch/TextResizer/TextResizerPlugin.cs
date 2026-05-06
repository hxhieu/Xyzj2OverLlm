using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using EnglishPatch.Support;
using HarmonyLib;
using SharedAssembly.TextResizer;
using System.Collections;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using TMPro;
using UnityEngine;

namespace EnglishPatch;

[BepInPlugin($"{MyPluginInfo.PLUGIN_GUID}.TextResizer", "TextResizer", MyPluginInfo.PLUGIN_VERSION)]
internal class TextResizerPlugin : BaseUnityPlugin
{
    internal static new ManualLogSource Logger;
    public static bool Enabled = true;
    public static bool DevMode = false;
    public static float FontScale = 1.0f;
    private ConfigEntry<float> _fontScale;

    private KeyCode _addResizerAtCursorHotKey = KeyCode.KeypadMinus;
    private KeyCode _addResizerAtCursorHotKey2 = KeyCode.F1;

    private KeyCode _reloadHotkey = KeyCode.KeypadPlus;
    private KeyCode _reloadHotkey2 = KeyCode.F2;

    private KeyCode _addResizerHotKey = KeyCode.KeypadMultiply;
    private KeyCode _addResizerHotKey2 = KeyCode.F3;

    private string _resizerFolder;
    //private static WildcardMatchingService _wildcardMatcher;

    // Required Static for patches to see it
    public static bool ResizersLoaded = false;
    public static Dictionary<string, TextResizerContract> Resizers = [];

    // Cache for storing previously matched results
    public static Dictionary<string, TextResizerContract> CachedMatchedResizers = [];

    private static TextResizerPlugin Instance;
    private static readonly HashSet<TextMeshProUGUI> PendingReapply = [];
    private static Coroutine ReapplyCoroutine;
    private static bool IsApplyingResizer;

    //TODO: SuperTextMesh for NPC text

    private void Awake()
    {
        Logger = base.Logger;
        Instance = this;

        if (!Enabled)
            return; 

        Harmony.CreateAndPatchAll(typeof(TextResizerPlugin));
        Logger.LogWarning($"TextResizer Plugin should be patched!");

        _fontScale = Config.Bind(
            "General",
            "FontScale",
            1.0f,
            "Global font size multiplier applied to every TextMeshProUGUI element touched by the text resizer. 0.5 = half size, 1.0 = normal.");
        FontScale = _fontScale.Value;

        _resizerFolder = Path.Combine(Paths.BepInExRootPath, "resizers");
        if (!Directory.Exists(_resizerFolder))
            Directory.CreateDirectory(_resizerFolder);

        LoadResizers();
        Logger.LogWarning($"TextResizer Plugin Loaded!");
    }

    internal void Update()
    {
        if (UnityInput.Current.GetKeyDown(_reloadHotkey)
            || (DevMode && UnityInput.Current.GetKeyDown(_reloadHotkey2)))
        {
            ReloadConfiguration();
            LoadResizers();
            ApplyAllResizers();
            Logger.LogWarning("Resizers Reloaded");
        }

        if (UnityInput.Current.GetKeyDown(_addResizerHotKey)
            || (DevMode && UnityInput.Current.GetKeyDown(_addResizerHotKey2)))
        {
            Logger.LogWarning("Adding Resizers for Scene");
            AddTextElementsToResizers(FindAllTextElements());
        }

        if (UnityInput.Current.GetKeyDown(_addResizerAtCursorHotKey)
            || (DevMode && UnityInput.Current.GetKeyDown(_addResizerAtCursorHotKey2)))
        {
            Logger.LogWarning("Adding Resizers at Cursor");
            AddTextElementsToResizers(FindTextElementsUnderCursor(), addUnderCursor: true);
        }
    }

    public void LoadResizers()
    {
        ResizersLoaded = false;

        var deserializer = Yaml.CreateDeserializer();
        Resizers.Clear();
        CachedMatchedResizers.Clear();

        var resizerFiles = Directory.EnumerateFiles(_resizerFolder, "*.yaml");
        foreach (var file in resizerFiles)
        {
            try
            {
                var content = File.ReadAllText(file);
                if (string.IsNullOrWhiteSpace(content))
                    continue;

                var newResizers = deserializer.Deserialize<List<TextResizerContract>>(content);
                AddFoundResizers(newResizers);
            }
            catch (Exception ex)
            {
                Logger.LogError($"Error Loading resizer '{file}': {ex}");
            }
        }

        // Create the wildcard matcher with all loaded resizers
        //_wildcardMatcher = new WildcardMatchingService(Resizers.Values.ToList());

        ResizersLoaded = true;
    }

    private void AddFoundResizers(List<TextResizerContract> newResizers)
    {
        foreach (var newResizer in newResizers)
            if (!Resizers.ContainsKey(newResizer.Path))
                Resizers.Add(newResizer.Path, newResizer);
    }

    public TextMeshProUGUI[] FindTextElementsUnderCursor()
    {
        // Get the current mouse position
        var mousePosition = UnityInput.Current.mousePosition;

        // Create a 10x10 pixel area around the cursor (20 pixel buffer on each side)
        var cursorArea = new Rect(mousePosition.x - 10, mousePosition.y - 10, 20, 20);

        // Find all TextMeshProUGUI components in the scene
        var textElements = FindObjectsOfType<TextMeshProUGUI>();

        var responseElements = new List<TextMeshProUGUI>();

        foreach (TextMeshProUGUI textElement in textElements)
        {
            // Get the RectTransform to check if it contains the cursor position
            var rectTransform = textElement.rectTransform;
            if (rectTransform == null) continue;

            // Check if the text element's screen rect overlaps with our cursor area
            Canvas canvas = textElement.canvas;
            if (canvas == null) continue;

            // Get the screen rect of the text element
            Camera camera = canvas.renderMode == RenderMode.ScreenSpaceOverlay ? null : canvas.worldCamera;
            Rect screenRect = RectTransformUtility.PixelAdjustRect(rectTransform, canvas);

            // Convert the rect to screen coordinates if not in overlay mode
            if (canvas.renderMode != RenderMode.ScreenSpaceOverlay && camera != null)
            {
                Vector3[] corners = new Vector3[4];
                rectTransform.GetWorldCorners(corners);

                // Convert world corners to screen points
                Vector2 min = camera.WorldToScreenPoint(corners[0]);
                Vector2 max = camera.WorldToScreenPoint(corners[2]);
                screenRect = new Rect(min.x, min.y, max.x - min.x, max.y - min.y);
            }

            // Check if the cursor area overlaps with the text element's screen rect
            if (screenRect.Overlaps(cursorArea))
                responseElements.Add(textElement);
        }

        return responseElements.ToArray();
    }

    public static TextMeshProUGUI[] FindAllTextElements()
    {
        // Find all TextMeshProUGUI components in the scene
        return FindObjectsOfType<TextMeshProUGUI>();
    }

    public void AddTextElementsToResizers(TextMeshProUGUI[] textElements, bool addUnderCursor = false, bool copyUnderCursor = false)
    {
        var foundResizers = new List<TextResizerContract>();

        foreach (TextMeshProUGUI textElement in textElements)
        {
            // Log information about the text element
            var path = ObjectHelper.GetGameObjectPath(textElement.gameObject);
            //Logger.LogMessage($"Found text element: {path}");

            if (!Resizers.ContainsKey(path))
            {
                // Create a new resizer contract for this text element
                var newResizer = new TextResizerContract()
                {
                    Path = path,
                    SampleText = textElement.text,
                    IdealFontSize = textElement.fontSize,
                    //AllowAutoSizing = textElement.enableAutoSizing,
                    AllowWordWrap = textElement.enableWordWrapping,
                    //Alignment = textElement.alignment.ToString(),
                    //OverflowMode = textElement.overflowMode.ToString(),
                    //Add More if we want more
                    AllowLeftTrimText = false, //Want to serialise
                };

                foundResizers.Add(newResizer);
            }
        }

        if (foundResizers.Count > 0)
        {
            var serializer = Yaml.CreateSerializer();

            var addedResizersFile = $"{_resizerFolder}/zzAddedResizers.yaml";
            var newText = serializer.Serialize(foundResizers);

            Logger.LogWarning($"Writing to {addedResizersFile}");

            if (!File.Exists(addedResizersFile))
                File.WriteAllText(addedResizersFile, newText);
            else
                File.AppendAllText(addedResizersFile, newText);

            AddFoundResizers(foundResizers);
        }
        else
        {
            Logger.LogMessage("No new text elements found in scene");
        }
    }

    public static void ApplyResizing(TextMeshProUGUI textComponent)
    {
        if (textComponent == null)
            return;

        if (textComponent.gameObject == null)
            return;

        var path = "<unknown>";
        TextResizerContract resizer = null;

        try
        {
            IsApplyingResizer = true;
            textComponent.wordWrappingRatios = 1.0f; //Disable Word wrapping ratios (should stop eastern rules)
            textComponent.enableKerning = false;

            path = ObjectHelper.GetGameObjectPath(textComponent.gameObject);
            resizer = FindAppropriateResizer(path);

            // Cache the wildcard match so we only have to match once
            if (!CachedMatchedResizers.ContainsKey(path))
                CachedMatchedResizers.Add(path, resizer);

            // Cache components
            var rectTransform = textComponent.rectTransform;
            if (rectTransform == null)
                return;

            var metadata = textComponent.GetComponent<TextMetadata>();

            // If metadata is not attached, add it and store the original values against it
            if (metadata == null)
            {
                metadata = textComponent.gameObject.AddComponent<TextMetadata>();
                metadata.OriginalX = rectTransform.anchoredPosition.x;
                metadata.OriginalY = rectTransform.anchoredPosition.y;
                metadata.OriginalWidth = rectTransform.sizeDelta.x;
                metadata.OriginalHeight = rectTransform.sizeDelta.y;
                metadata.OriginalCharacterSpacing = textComponent.characterSpacing;
                metadata.OriginalLineSpacing = textComponent.lineSpacing;
                metadata.OriginalWordSpacing = textComponent.wordSpacing;
                metadata.OriginalAlignment = textComponent.alignment;
                metadata.OriginalOverflowMode = textComponent.overflowMode;
                metadata.OriginalAllowWordWrap = textComponent.enableWordWrapping;
                metadata.OriginalAllowAutoSizing = textComponent.enableAutoSizing;
                metadata.OriginalFontSize = textComponent.fontSize;
            }

            // Apply the global font scale even when no YAML resizer matches.
            ApplyFontSize(textComponent, metadata, resizer);

            if (resizer == null)
                return;

            // Set this so we can debug bad resizers
            metadata.ActiveResizerPath = resizer.Path;

            // Apply position change if needed
            if (resizer.AdjustX != metadata.AdjustX
                || resizer.AdjustY != metadata.AdjustY)
            {
                metadata.AdjustX = resizer.AdjustX;
                metadata.AdjustY = resizer.AdjustY;
                rectTransform.anchoredPosition = new Vector2(metadata.OriginalX + resizer.AdjustX, metadata.OriginalY + resizer.AdjustY);
            }

            // Apply size change if needed
            if (resizer.AdjustWidth != metadata.AdjustWidth
                || resizer.AdjustHeight != metadata.AdjustHeight)
            {
                metadata.AdjustWidth = resizer.AdjustWidth;
                metadata.AdjustHeight = resizer.AdjustHeight;
                rectTransform.sizeDelta = new Vector2(metadata.OriginalWidth + metadata.AdjustWidth, metadata.OriginalHeight + metadata.AdjustHeight);
            }

            // Text Alignment
            var resizerAlignment = resizer.Alignment ?? string.Empty;
            var validAlignment = Enum.TryParse<TextAlignmentOptions>(resizerAlignment, true, out var alignment);
            if (resizerAlignment != string.Empty && !validAlignment)
                Logger.LogWarning($"Invalid alignment value: {resizer.Alignment} on {resizer.Path}");

            if (validAlignment && textComponent.alignment != alignment)
            {
                textComponent.alignment = alignment;
            }
            else if (!validAlignment && textComponent.alignment != metadata.OriginalAlignment)
            {
                textComponent.alignment = metadata.OriginalAlignment;
            }

            var resizerOverflowMode = resizer.OverflowMode ?? string.Empty;
            var validOverflow = Enum.TryParse<TextOverflowModes>(resizerOverflowMode, true, out var overflowMode);
            if (resizerOverflowMode != string.Empty && !validOverflow)
                Logger.LogWarning($"Invalid overflow value: {resizer.OverflowMode} on {resizer.Path}");

            if (validOverflow && textComponent.overflowMode != overflowMode)
            {
                textComponent.overflowMode = overflowMode;
            }
            else if (!validOverflow && textComponent.overflowMode != metadata.OriginalOverflowMode)
            {
                textComponent.overflowMode = metadata.OriginalOverflowMode;
            }

            // Toggles
            if (resizer.AllowWordWrap.HasValue
                && textComponent.enableWordWrapping != resizer.AllowWordWrap.Value)
            {
                textComponent.enableWordWrapping = resizer.AllowWordWrap.Value;
            }
            else if (!resizer.AllowWordWrap.HasValue
                && textComponent.enableWordWrapping != metadata.OriginalAllowWordWrap)
            {
                textComponent.enableWordWrapping = metadata.OriginalAllowWordWrap;
            }

            if (resizer.AllowAutoSizing.HasValue
                && textComponent.enableAutoSizing != resizer.AllowAutoSizing.Value)
            {
                textComponent.enableAutoSizing = resizer.AllowAutoSizing.Value;
            }
            else if (!resizer.AllowAutoSizing.HasValue
                && textComponent.enableAutoSizing != metadata.OriginalAllowAutoSizing)
            {
                textComponent.enableAutoSizing = metadata.OriginalAllowAutoSizing;
            }

            // Auto Sizing configuration
            if (textComponent.enableAutoSizing)
            {
                if (resizer.MinFontSize.HasValue
                    && resizer.MinFontSize != textComponent.fontSizeMin)
                {
                    textComponent.fontSizeMin = resizer.MinFontSize.Value;
                }

                if (resizer.MaxFontSize.HasValue
                    && resizer.MaxFontSize != textComponent.fontSizeMax)
                {
                    textComponent.fontSizeMax = resizer.MaxFontSize.Value;
                }
            }

            // Spacing
            if (resizer.LineSpacing.HasValue
                && resizer.LineSpacing != textComponent.lineSpacing)
            {
                textComponent.lineSpacing = resizer.LineSpacing.Value;
            }

            if (resizer.WordSpacing.HasValue
                && resizer.WordSpacing != textComponent.wordSpacing)
            {
                textComponent.wordSpacing = resizer.WordSpacing.Value;
            }

            if (resizer.CharacterSpacing.HasValue
                && resizer.CharacterSpacing != textComponent.characterSpacing)
            {
                textComponent.characterSpacing = resizer.CharacterSpacing.Value;
            }

            if (resizer.AllowLeftTrimText)
            {
                //Trim it first so when it initialises it at least trims
                var trimmed = (textComponent.text ?? string.Empty).TrimStart();
                if (textComponent.text != trimmed)
                    textComponent.text = trimmed;
            }

            // Take out the behaviour for now to save perfrormance
            // Only add the behaviour component if it hasn't been added already
            //if (!textComponent.gameObject.TryGetComponent<TextChangedBehaviour>(out var existingBehaviour))
            //{
            //    existingBehaviour = textComponent.gameObject.AddComponent<TextChangedBehaviour>();
            //    // Set the parameter after adding the component
            //    existingBehaviour.SetOptions(resizer);
            //}
            //else if (textComponent.gameObject.TryGetComponent<TextChangedBehaviour>(out var textChangeBehavior))
            //{
            //    Destroy(textChangeBehavior);
            //}
        }
        catch (Exception ex)
        {
            Logger.LogError(
                $"Error applying resizer to '{textComponent.name}' at path '{path}' " +
                $"using resizer '{resizer?.Path ?? "<none>"}' sample '{resizer?.SampleText ?? string.Empty}': {ex}");
        }
        finally
        {
            IsApplyingResizer = false;
        }
    }

    public static TextResizerContract FindAppropriateResizer(string path)
    {
        if (Resizers.TryGetValue(path, out var tryResizer))
            return tryResizer;

        // Check cache first
        if (CachedMatchedResizers.TryGetValue(path, out var cachedResizer))
            return cachedResizer;

        // Try wildcard matching for the remaining resizers. Multiple wildcard
        // patterns can match the same object, so prefer the most specific one.
        TextResizerContract bestWildcardResizer = null;
        var bestWildcardScore = -1;

        foreach (var resizerPair in Resizers)
        {
            var resizer = resizerPair.Value;

            if (!WildcardPathMatches(resizer.Path, path))
                continue;

            var score = GetWildcardSpecificity(resizer.Path);
            if (score > bestWildcardScore)
            {
                bestWildcardScore = score;
                bestWildcardResizer = resizer;
            }
        }

        return bestWildcardResizer;
    }

    private static int GetWildcardSpecificity(string patternPath)
    {
        return NormalizePath(patternPath).Count(c => c != '*');
    }

    internal static bool WildcardPathMatches(string patternPath, string path)
    {
        if (string.IsNullOrWhiteSpace(patternPath)
            || string.IsNullOrWhiteSpace(path)
            || !patternPath.Contains("*"))
            return false;

        patternPath = NormalizePath(patternPath);
        path = NormalizePath(path);

        var regexPattern = "^" + Regex.Escape(patternPath).Replace("\\*", "[^/]+") + "$";
        return Regex.IsMatch(path, regexPattern);
    }

    private static string NormalizePath(string path)
    {
        return path.Trim().Replace('\\', '/');
    }

    public static void ApplyAllResizers()
    {
        foreach (var textElement in FindAllTextElements())
            ApplyResizing(textElement);
    }

    [HarmonyPostfix, HarmonyPatch(typeof(GameObject), nameof(GameObject.SetActive), [typeof(bool)])]
    public static void Postfix_GameObject_SetActive(GameObject __instance)
    {
        if (!ResizersLoaded)
            return;

        //TODO: This should be most efficient but we could use Object.Instantiate
        //to get it at as the objects created. But maybe there is post processing occuring after.
        var items = __instance.GetComponentsInChildren<TextMeshProUGUI>();
        foreach (var item in items)
            QueueReapply(item);
    }

    [HarmonyPostfix, HarmonyPatch(typeof(TMP_Text), nameof(TMP_Text.text), MethodType.Setter)]
    public static void Postfix_TMP_Text_SetText(TMP_Text __instance)
    {
        if (__instance is TextMeshProUGUI textComponent)
            QueueReapply(textComponent);
    }

    [HarmonyPostfix, HarmonyPatch(typeof(TMP_Text), nameof(TMP_Text.fontSize), MethodType.Setter)]
    public static void Postfix_TMP_Text_SetFontSize(TMP_Text __instance)
    {
        if (__instance is TextMeshProUGUI textComponent)
            QueueReapply(textComponent);
    }

    private static void QueueReapply(TextMeshProUGUI textComponent)
    {
        if (!ResizersLoaded
            || IsApplyingResizer
            || Instance == null
            || textComponent == null
            || textComponent.gameObject == null)
        {
            return;
        }

        PendingReapply.Add(textComponent);
        if (ReapplyCoroutine == null)
            ReapplyCoroutine = Instance.StartCoroutine(ReapplyPendingOverNextFrames());
    }

    private static IEnumerator ReapplyPendingOverNextFrames()
    {
        for (var frame = 0; frame < 3; frame++)
        {
            yield return null;

            var pending = PendingReapply.ToArray();
            PendingReapply.Clear();

            foreach (var textComponent in pending)
            {
                if (textComponent != null && textComponent.gameObject != null)
                    ApplyResizing(textComponent);
            }
        }

        ReapplyCoroutine = null;
    }

    private void ReloadConfiguration()
    {
        Config.Reload();
        FontScale = _fontScale.Value;
        Logger.LogMessage($"TextResizer FontScale: {FontScale}");
    }

    private static void ApplyFontSize(TextMeshProUGUI textComponent, TextMetadata metadata, TextResizerContract resizer)
    {
        var fontSize = metadata.OriginalFontSize;

        if (resizer != null)
        {
            if (resizer.IdealFontSize.HasValue)
                fontSize = resizer.IdealFontSize.Value;
            else if (resizer.FontPercentage.HasValue)
                fontSize = metadata.OriginalFontSize * resizer.FontPercentage.Value;
        }

        textComponent.fontSize = fontSize * FontScale;
    }
}
