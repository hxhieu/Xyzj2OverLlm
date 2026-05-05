using Microsoft.Data.Sqlite;
using System.Text.Json;
using Translate.Support;
using Translate.Utility;

namespace Translate;

public record GlossaryDbExportResult(
    string OutputPath,
    int Entries);

public static class GlossaryDbExportService
{
    public static async Task<GlossaryDbExportResult> ExportAsync(
        string workingDirectory,
        string databasePath,
        bool requireLocked)
    {
        await using var connection = new SqliteConnection($"Data Source={databasePath}");
        await connection.OpenAsync();

        if (requireLocked)
        {
            var unlockedCount = Convert.ToInt32((long)(await ScalarAsync(connection, """
                SELECT COUNT(*)
                FROM glossary_entries
                WHERE status <> 'locked';
                """))!);

            if (unlockedCount > 0)
                throw new InvalidOperationException($"Refusing to export Glossary.yaml: {unlockedCount} glossary entries are not locked.");
        }

        var glossary = new List<GlossaryLine>();
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT raw,
                   result,
                   allowed_alternatives_json,
                   transliteration,
                   context,
                   check_misused_translation,
                   check_bad_translation,
                   only_files_json,
                   exclude_files_json
            FROM glossary_entries
            ORDER BY source_index, id;
            """;

        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            glossary.Add(new GlossaryLine
            {
                Raw = reader.GetString(0),
                Result = reader.GetString(1),
                AllowedAlternatives = DeserializeList(reader.GetString(2)),
                Transliteration = reader.GetString(3),
                Context = reader.GetString(4),
                CheckForMisusedTranslation = reader.GetInt32(5) != 0,
                CheckForBadTranslation = reader.GetInt32(6) != 0,
                OnlyOutputFiles = DeserializeList(reader.GetString(7)),
                ExcludeOutputFiles = DeserializeList(reader.GetString(8))
            });
        }

        var outputPath = Path.Combine(workingDirectory, "Glossary.yaml");
        var serializer = Yaml.CreateSerializer();
        await File.WriteAllTextAsync(outputPath, serializer.Serialize(glossary));

        return new GlossaryDbExportResult(outputPath, glossary.Count);
    }

    private static List<string> DeserializeList(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
            return [];

        return JsonSerializer.Deserialize<List<string>>(json) ?? [];
    }

    private static async Task<object?> ScalarAsync(SqliteConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        return await command.ExecuteScalarAsync();
    }
}
