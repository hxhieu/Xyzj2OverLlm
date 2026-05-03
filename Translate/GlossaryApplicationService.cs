using Translate.Support;
using Translate.Utility;

namespace Translate;

public record GlossaryApplicationResult(
    int FilesVisited,
    int FilesChanged,
    int SplitsChanged,
    int ContainingMatchesSkipped);

public static class GlossaryApplicationService
{
    public static async Task<GlossaryApplicationResult> ApplyExactMatchesAsync(string workingDirectory, bool dryRun)
    {
        var glossaryFile = Path.Combine(workingDirectory, "Glossary.yaml");
        if (!File.Exists(glossaryFile))
            throw new FileNotFoundException($"Glossary file does not exist: {glossaryFile}", glossaryFile);

        var deserializer = Yaml.CreateDeserializer();
        var serializer = Yaml.CreateSerializer();
        var glossaryLines = deserializer.Deserialize<List<GlossaryLine>>(await File.ReadAllTextAsync(glossaryFile))
            .Where(line => !string.IsNullOrWhiteSpace(line.Raw) && !string.IsNullOrWhiteSpace(line.Result))
            .GroupBy(line => line.Raw)
            .ToDictionary(group => group.Key, group => group.Last());

        var filesVisited = 0;
        var filesChanged = 0;
        var splitsChanged = 0;
        var containingMatchesSkipped = 0;

        await FileIteration.IterateTranslatedFilesAsync(workingDirectory, async (outputFile, textFile, fileLines) =>
        {
            filesVisited++;
            var changed = false;

            foreach (var line in fileLines)
            {
                foreach (var split in line.Splits)
                {
                    if (string.IsNullOrWhiteSpace(split.Text) || !split.SafeToTranslate)
                        continue;

                    if (glossaryLines.TryGetValue(split.Text, out var glossaryLine)
                        && GlossaryAppliesToFile(glossaryLine, textFile.Path)
                        && split.Translated != glossaryLine.Result)
                    {
                        split.Translated = glossaryLine.Result;
                        split.ResetFlags(true);
                        changed = true;
                        splitsChanged++;
                        continue;
                    }

                    if (glossaryLines.Keys.Any(raw => split.Text.Contains(raw) && raw != split.Text))
                        containingMatchesSkipped++;
                }
            }

            if (changed)
            {
                filesChanged++;
                if (!dryRun)
                    await File.WriteAllTextAsync(outputFile, serializer.Serialize(fileLines));
            }
        });

        return new GlossaryApplicationResult(filesVisited, filesChanged, splitsChanged, containingMatchesSkipped);
    }

    private static bool GlossaryAppliesToFile(GlossaryLine line, string outputFile)
    {
        if (line.OnlyOutputFiles.Count > 0 && !line.OnlyOutputFiles.Contains(outputFile))
            return false;

        if (line.ExcludeOutputFiles.Count > 0 && line.ExcludeOutputFiles.Contains(outputFile))
            return false;

        return true;
    }
}
