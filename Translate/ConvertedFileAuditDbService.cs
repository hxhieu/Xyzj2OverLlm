using Microsoft.Data.Sqlite;
using Translate.Utility;

namespace Translate;

public record ConvertedFileAuditImportResult(
    string DatabasePath,
    string SourceFile,
    int Lines,
    int Splits,
    int FocusCandidates);

public record ConvertedFileAuditExportResult(
    string OutputPath,
    int Lines,
    int Splits);

public static class ConvertedFileAuditDbService
{
    public static async Task<ConvertedFileAuditImportResult> ImportAsync(
        string workingDirectory,
        string databasePath,
        string sourceFile)
    {
        var convertedPath = Path.Combine(workingDirectory, "Converted", sourceFile);
        if (!File.Exists(convertedPath))
            throw new FileNotFoundException($"Converted file does not exist: {convertedPath}", convertedPath);

        Directory.CreateDirectory(Path.GetDirectoryName(databasePath) ?? ".");

        var deserializer = Yaml.CreateDeserializer();
        var lines = deserializer.Deserialize<List<TranslationLine>>(await File.ReadAllTextAsync(convertedPath));

        await using var connection = new SqliteConnection($"Data Source={databasePath}");
        await connection.OpenAsync();

        await ExecuteAsync(connection, "PRAGMA journal_mode = DELETE;");
        await ExecuteAsync(connection, "PRAGMA synchronous = NORMAL;");
        await CreateSchemaAsync(connection);

        await using var transaction = await connection.BeginTransactionAsync();
        await DeleteExistingAsync(connection, (SqliteTransaction)transaction, sourceFile);

        var splitCount = 0;
        var focusCount = 0;

        await using var insertLine = connection.CreateCommand();
        insertLine.Transaction = (SqliteTransaction)transaction;
        insertLine.CommandText = """
            INSERT INTO converted_file_lines(source_file, line_index, raw_line)
            VALUES ($source_file, $line_index, $raw_line);
            SELECT last_insert_rowid();
            """;
        var lineSourceFile = insertLine.Parameters.Add("$source_file", SqliteType.Text);
        var lineIndex = insertLine.Parameters.Add("$line_index", SqliteType.Integer);
        var rawLine = insertLine.Parameters.Add("$raw_line", SqliteType.Text);

        await using var insertSplit = connection.CreateCommand();
        insertSplit.Transaction = (SqliteTransaction)transaction;
        insertSplit.CommandText = """
            INSERT INTO converted_file_splits(
                line_id,
                source_file,
                line_index,
                split_order,
                split_index,
                source_text,
                translated,
                safe_to_translate,
                flagged_for_retranslation,
                flagged_mistranslation,
                flagged_hallucination,
                is_focus_candidate,
                status)
            VALUES (
                $line_id,
                $source_file,
                $line_index,
                $split_order,
                $split_index,
                $source_text,
                $translated,
                $safe_to_translate,
                $flagged_for_retranslation,
                $flagged_mistranslation,
                $flagged_hallucination,
                $is_focus_candidate,
                $status);
            """;
        var splitLineId = insertSplit.Parameters.Add("$line_id", SqliteType.Integer);
        var splitSourceFile = insertSplit.Parameters.Add("$source_file", SqliteType.Text);
        var splitLineIndex = insertSplit.Parameters.Add("$line_index", SqliteType.Integer);
        var splitOrder = insertSplit.Parameters.Add("$split_order", SqliteType.Integer);
        var splitIndex = insertSplit.Parameters.Add("$split_index", SqliteType.Integer);
        var sourceText = insertSplit.Parameters.Add("$source_text", SqliteType.Text);
        var translated = insertSplit.Parameters.Add("$translated", SqliteType.Text);
        var safeToTranslate = insertSplit.Parameters.Add("$safe_to_translate", SqliteType.Integer);
        var flaggedForRetranslation = insertSplit.Parameters.Add("$flagged_for_retranslation", SqliteType.Integer);
        var flaggedMistranslation = insertSplit.Parameters.Add("$flagged_mistranslation", SqliteType.Text);
        var flaggedHallucination = insertSplit.Parameters.Add("$flagged_hallucination", SqliteType.Text);
        var isFocusCandidate = insertSplit.Parameters.Add("$is_focus_candidate", SqliteType.Integer);
        var status = insertSplit.Parameters.Add("$status", SqliteType.Text);

        for (var i = 0; i < lines.Count; i++)
        {
            var line = lines[i];

            lineSourceFile.Value = sourceFile;
            lineIndex.Value = i;
            rawLine.Value = line.Raw ?? string.Empty;

            var lineId = (long)(await insertLine.ExecuteScalarAsync())!;

            for (var j = 0; j < line.Splits.Count; j++)
            {
                var split = line.Splits[j];
                var focusCandidate = IsFocusCandidate(line.Raw, split.Text, split.Translated);

                splitLineId.Value = lineId;
                splitSourceFile.Value = sourceFile;
                splitLineIndex.Value = i;
                splitOrder.Value = j;
                splitIndex.Value = split.Split;
                sourceText.Value = split.Text ?? string.Empty;
                translated.Value = split.Translated ?? string.Empty;
                safeToTranslate.Value = split.SafeToTranslate ? 1 : 0;
                flaggedForRetranslation.Value = split.FlaggedForRetranslation ? 1 : 0;
                flaggedMistranslation.Value = split.FlaggedMistranslation ?? string.Empty;
                flaggedHallucination.Value = split.FlaggedHallucination ?? string.Empty;
                isFocusCandidate.Value = focusCandidate ? 1 : 0;
                status.Value = focusCandidate ? "pending" : "ignored";

                await insertSplit.ExecuteNonQueryAsync();
                splitCount++;

                if (focusCandidate)
                    focusCount++;
            }
        }

        await transaction.CommitAsync();

        return new ConvertedFileAuditImportResult(databasePath, sourceFile, lines.Count, splitCount, focusCount);
    }

    public static async Task<ConvertedFileAuditExportResult> ExportAsync(
        string workingDirectory,
        string databasePath,
        string sourceFile)
    {
        var outputPath = Path.Combine(workingDirectory, "Converted", sourceFile);

        await using var connection = new SqliteConnection($"Data Source={databasePath}");
        await connection.OpenAsync();

        var lines = new List<TranslationLine>();
        await using var lineCommand = connection.CreateCommand();
        lineCommand.CommandText = """
            SELECT id, raw_line
            FROM converted_file_lines
            WHERE source_file = $source_file
            ORDER BY line_index;
            """;
        lineCommand.Parameters.AddWithValue("$source_file", sourceFile);

        var lineIds = new List<long>();
        await using (var reader = await lineCommand.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                lineIds.Add(reader.GetInt64(0));
                lines.Add(new TranslationLine
                {
                    Raw = reader.GetString(1),
                    Splits = []
                });
            }
        }

        var splitCount = 0;
        await using var splitCommand = connection.CreateCommand();
        splitCommand.CommandText = """
            SELECT line_id,
                   split_index,
                   source_text,
                   translated,
                   safe_to_translate,
                   flagged_for_retranslation,
                   flagged_mistranslation,
                   flagged_hallucination
            FROM converted_file_splits
            WHERE source_file = $source_file
            ORDER BY line_index, split_order;
            """;
        splitCommand.Parameters.AddWithValue("$source_file", sourceFile);

        var lineIdToIndex = lineIds.Select((id, index) => (id, index)).ToDictionary(item => item.id, item => item.index);
        await using (var reader = await splitCommand.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                var lineId = reader.GetInt64(0);
                if (!lineIdToIndex.TryGetValue(lineId, out var targetLineIndex))
                    continue;

                lines[targetLineIndex].Splits.Add(new TranslationSplit
                {
                    Split = reader.GetInt32(1),
                    Text = reader.GetString(2),
                    Translated = reader.GetString(3),
                    SafeToTranslate = reader.GetInt32(4) != 0,
                    FlaggedForRetranslation = reader.GetInt32(5) != 0,
                    FlaggedMistranslation = reader.GetString(6),
                    FlaggedHallucination = reader.GetString(7)
                });
                splitCount++;
            }
        }

        Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? ".");
        var serializer = Yaml.CreateSerializer();
        await File.WriteAllTextAsync(outputPath, serializer.Serialize(lines));

        return new ConvertedFileAuditExportResult(outputPath, lines.Count, splitCount);
    }

    private static async Task CreateSchemaAsync(SqliteConnection connection)
    {
        await ExecuteAsync(connection, """
            CREATE TABLE IF NOT EXISTS converted_file_lines (
                id INTEGER PRIMARY KEY,
                source_file TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                raw_line TEXT NOT NULL,
                UNIQUE(source_file, line_index)
            );

            CREATE TABLE IF NOT EXISTS converted_file_splits (
                id INTEGER PRIMARY KEY,
                line_id INTEGER NOT NULL,
                source_file TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                split_order INTEGER NOT NULL,
                split_index INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                translated TEXT NOT NULL,
                safe_to_translate INTEGER NOT NULL,
                flagged_for_retranslation INTEGER NOT NULL,
                flagged_mistranslation TEXT NOT NULL,
                flagged_hallucination TEXT NOT NULL,
                is_focus_candidate INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(line_id) REFERENCES converted_file_lines(id) ON DELETE CASCADE,
                UNIQUE(source_file, line_index, split_order)
            );

            CREATE INDEX IF NOT EXISTS idx_converted_splits_file_status
                ON converted_file_splits(source_file, status, is_focus_candidate, id);
            CREATE INDEX IF NOT EXISTS idx_converted_splits_file_text
                ON converted_file_splits(source_file, source_text);
            """);
    }

    private static async Task DeleteExistingAsync(
        SqliteConnection connection,
        SqliteTransaction transaction,
        string sourceFile)
    {
        await using var deleteSplits = connection.CreateCommand();
        deleteSplits.Transaction = transaction;
        deleteSplits.CommandText = "DELETE FROM converted_file_splits WHERE source_file = $source_file;";
        deleteSplits.Parameters.AddWithValue("$source_file", sourceFile);
        await deleteSplits.ExecuteNonQueryAsync();

        await using var deleteLines = connection.CreateCommand();
        deleteLines.Transaction = transaction;
        deleteLines.CommandText = "DELETE FROM converted_file_lines WHERE source_file = $source_file;";
        deleteLines.Parameters.AddWithValue("$source_file", sourceFile);
        await deleteLines.ExecuteNonQueryAsync();
    }

    private static bool IsFocusCandidate(string? rawLine, string? sourceText, string? translated)
    {
        var text = string.Concat(rawLine ?? string.Empty, "\n", sourceText ?? string.Empty, "\n", translated ?? string.Empty);
        string[] martialKeywords =
        [
            "武学", "心法", "功法", "神功", "内功", "轻功", "真经", "宝典", "经",
            "诀", "决", "法", "谱", "典", "招", "式", "势", "技", "术",
            "拳", "掌", "指", "手", "腿", "脚", "剑", "刀", "枪", "棍", "棒", "杖",
            "劲", "气", "内力", "真气", "经脉", "穴", "脉"
        ];

        return martialKeywords.Any(text.Contains);
    }

    private static async Task ExecuteAsync(SqliteConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }
}
