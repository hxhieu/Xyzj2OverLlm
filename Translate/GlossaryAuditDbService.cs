using Microsoft.Data.Sqlite;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Translate.Support;
using Translate.Utility;

namespace Translate;

public record GlossaryAuditImportResult(
    string DatabasePath,
    int GlossaryEntries,
    int SourceSplits,
    int Occurrences);

public static class GlossaryAuditDbService
{
    public static async Task<GlossaryAuditImportResult> ImportAsync(string workingDirectory, string databasePath)
    {
        var glossaryPath = Path.Combine(workingDirectory, "Glossary.yaml");
        var stringlangPath = Path.Combine(workingDirectory, "Converted", "stringlang.txt");

        if (!File.Exists(glossaryPath))
            throw new FileNotFoundException($"Glossary file does not exist: {glossaryPath}", glossaryPath);

        if (!File.Exists(stringlangPath))
            throw new FileNotFoundException($"Converted stringlang file does not exist: {stringlangPath}", stringlangPath);

        Directory.CreateDirectory(Path.GetDirectoryName(databasePath) ?? ".");
        if (File.Exists(databasePath))
            File.Delete(databasePath);

        var deserializer = Yaml.CreateDeserializer();
        var glossary = deserializer.Deserialize<List<GlossaryLine>>(await File.ReadAllTextAsync(glossaryPath))
            .Where(line => !string.IsNullOrWhiteSpace(line.Raw))
            .ToList();

        var stringlang = deserializer.Deserialize<List<TranslationLine>>(await File.ReadAllTextAsync(stringlangPath));

        await using var connection = new SqliteConnection($"Data Source={databasePath}");
        await connection.OpenAsync();

        await ExecuteAsync(connection, "PRAGMA journal_mode = DELETE;");
        await ExecuteAsync(connection, "PRAGMA synchronous = NORMAL;");
        await CreateSchemaAsync(connection);

        await using var transaction = await connection.BeginTransactionAsync();

        await InsertMetadataAsync(connection, transaction, glossaryPath, stringlangPath);
        var rawToEntryIds = await InsertGlossaryAsync(connection, transaction, glossary);
        var matcher = new AhoCorasickMatcher(rawToEntryIds.Keys);
        var sourceSplits = await InsertStringlangAndOccurrencesAsync(connection, transaction, stringlang, rawToEntryIds, matcher);

        await transaction.CommitAsync();

        var occurrenceCount = Convert.ToInt32((long)(await ScalarAsync(connection, "SELECT COUNT(*) FROM glossary_occurrences;"))!);
        return new GlossaryAuditImportResult(databasePath, glossary.Count, sourceSplits, occurrenceCount);
    }

    private static async Task CreateSchemaAsync(SqliteConnection connection)
    {
        await ExecuteAsync(connection, """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE glossary_entries (
                id INTEGER PRIMARY KEY,
                source_index INTEGER NOT NULL,
                raw TEXT NOT NULL,
                result TEXT NOT NULL,
                allowed_alternatives_json TEXT NOT NULL,
                transliteration TEXT NOT NULL,
                context TEXT NOT NULL,
                check_bad_translation INTEGER NOT NULL,
                check_misused_translation INTEGER NOT NULL,
                only_files_json TEXT NOT NULL,
                exclude_files_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE stringlang_splits (
                id INTEGER PRIMARY KEY,
                line_index INTEGER NOT NULL,
                split_index INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                translated TEXT NOT NULL,
                raw_line TEXT NOT NULL
            );

            CREATE TABLE glossary_occurrences (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER NOT NULL,
                split_id INTEGER NOT NULL,
                exact_match INTEGER NOT NULL,
                translated_contains_result INTEGER NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES glossary_entries(id),
                FOREIGN KEY(split_id) REFERENCES stringlang_splits(id)
            );

            CREATE INDEX idx_glossary_status ON glossary_entries(status, id);
            CREATE INDEX idx_glossary_raw ON glossary_entries(raw);
            CREATE INDEX idx_stringlang_source_text ON stringlang_splits(source_text);
            CREATE INDEX idx_occurrence_entry ON glossary_occurrences(entry_id);
            CREATE INDEX idx_occurrence_split ON glossary_occurrences(split_id);
            """);
    }

    private static async Task InsertMetadataAsync(
        SqliteConnection connection,
        System.Data.Common.DbTransaction transaction,
        string glossaryPath,
        string stringlangPath)
    {
        await using var command = connection.CreateCommand();
        command.Transaction = (SqliteTransaction)transaction;
        command.CommandText = """
            INSERT INTO metadata(key, value)
            VALUES
                ('glossary_path', $glossary_path),
                ('glossary_sha256', $glossary_sha256),
                ('stringlang_path', $stringlang_path),
                ('stringlang_sha256', $stringlang_sha256),
                ('imported_at_utc', $imported_at_utc);
            """;

        command.Parameters.AddWithValue("$glossary_path", glossaryPath);
        command.Parameters.AddWithValue("$glossary_sha256", await Sha256Async(glossaryPath));
        command.Parameters.AddWithValue("$stringlang_path", stringlangPath);
        command.Parameters.AddWithValue("$stringlang_sha256", await Sha256Async(stringlangPath));
        command.Parameters.AddWithValue("$imported_at_utc", DateTime.UtcNow.ToString("O"));
        await command.ExecuteNonQueryAsync();
    }

    private static async Task<Dictionary<string, List<long>>> InsertGlossaryAsync(
        SqliteConnection connection,
        System.Data.Common.DbTransaction transaction,
        List<GlossaryLine> glossary)
    {
        var rawToEntryIds = new Dictionary<string, List<long>>();

        await using var command = connection.CreateCommand();
        command.Transaction = (SqliteTransaction)transaction;
        command.CommandText = """
            INSERT INTO glossary_entries(
                source_index,
                raw,
                result,
                allowed_alternatives_json,
                transliteration,
                context,
                check_bad_translation,
                check_misused_translation,
                only_files_json,
                exclude_files_json)
            VALUES (
                $source_index,
                $raw,
                $result,
                $allowed_alternatives_json,
                $transliteration,
                $context,
                $check_bad_translation,
                $check_misused_translation,
                $only_files_json,
                $exclude_files_json);
            SELECT last_insert_rowid();
            """;

        var sourceIndex = command.Parameters.Add("$source_index", SqliteType.Integer);
        var raw = command.Parameters.Add("$raw", SqliteType.Text);
        var result = command.Parameters.Add("$result", SqliteType.Text);
        var allowedAlternatives = command.Parameters.Add("$allowed_alternatives_json", SqliteType.Text);
        var transliteration = command.Parameters.Add("$transliteration", SqliteType.Text);
        var context = command.Parameters.Add("$context", SqliteType.Text);
        var checkBadTranslation = command.Parameters.Add("$check_bad_translation", SqliteType.Integer);
        var checkMisusedTranslation = command.Parameters.Add("$check_misused_translation", SqliteType.Integer);
        var onlyFiles = command.Parameters.Add("$only_files_json", SqliteType.Text);
        var excludeFiles = command.Parameters.Add("$exclude_files_json", SqliteType.Text);

        for (var i = 0; i < glossary.Count; i++)
        {
            var line = glossary[i];
            sourceIndex.Value = i;
            raw.Value = line.Raw;
            result.Value = line.Result ?? string.Empty;
            allowedAlternatives.Value = JsonSerializer.Serialize(line.AllowedAlternatives ?? []);
            transliteration.Value = line.Transliteration ?? string.Empty;
            context.Value = line.Context ?? string.Empty;
            checkBadTranslation.Value = line.CheckForBadTranslation ? 1 : 0;
            checkMisusedTranslation.Value = line.CheckForMisusedTranslation ? 1 : 0;
            onlyFiles.Value = JsonSerializer.Serialize(line.OnlyOutputFiles ?? []);
            excludeFiles.Value = JsonSerializer.Serialize(line.ExcludeOutputFiles ?? []);

            var entryId = (long)(await command.ExecuteScalarAsync())!;
            if (!rawToEntryIds.TryGetValue(line.Raw, out var entryIds))
            {
                entryIds = [];
                rawToEntryIds[line.Raw] = entryIds;
            }

            entryIds.Add(entryId);
        }

        return rawToEntryIds;
    }

    private static async Task<int> InsertStringlangAndOccurrencesAsync(
        SqliteConnection connection,
        System.Data.Common.DbTransaction transaction,
        List<TranslationLine> stringlang,
        Dictionary<string, List<long>> rawToEntryIds,
        AhoCorasickMatcher matcher)
    {
        var sourceSplits = 0;

        await using var insertSplit = connection.CreateCommand();
        insertSplit.Transaction = (SqliteTransaction)transaction;
        insertSplit.CommandText = """
            INSERT INTO stringlang_splits(line_index, split_index, source_text, translated, raw_line)
            VALUES ($line_index, $split_index, $source_text, $translated, $raw_line);
            SELECT last_insert_rowid();
            """;

        var lineIndex = insertSplit.Parameters.Add("$line_index", SqliteType.Integer);
        var splitIndex = insertSplit.Parameters.Add("$split_index", SqliteType.Integer);
        var sourceText = insertSplit.Parameters.Add("$source_text", SqliteType.Text);
        var translated = insertSplit.Parameters.Add("$translated", SqliteType.Text);
        var rawLine = insertSplit.Parameters.Add("$raw_line", SqliteType.Text);

        await using var insertOccurrence = connection.CreateCommand();
        insertOccurrence.Transaction = (SqliteTransaction)transaction;
        insertOccurrence.CommandText = """
            INSERT INTO glossary_occurrences(entry_id, split_id, exact_match, translated_contains_result)
            SELECT $entry_id, $split_id, $exact_match,
                CASE
                    WHEN result = '' THEN 0
                    WHEN instr($translated, result) > 0 THEN 1
                    ELSE 0
                END
            FROM glossary_entries
            WHERE id = $entry_id;
            """;

        var occurrenceEntryId = insertOccurrence.Parameters.Add("$entry_id", SqliteType.Integer);
        var occurrenceSplitId = insertOccurrence.Parameters.Add("$split_id", SqliteType.Integer);
        var exactMatch = insertOccurrence.Parameters.Add("$exact_match", SqliteType.Integer);
        var occurrenceTranslated = insertOccurrence.Parameters.Add("$translated", SqliteType.Text);

        for (var i = 0; i < stringlang.Count; i++)
        {
            var line = stringlang[i];

            foreach (var split in line.Splits)
            {
                lineIndex.Value = i;
                splitIndex.Value = split.Split;
                sourceText.Value = split.Text ?? string.Empty;
                translated.Value = split.Translated ?? string.Empty;
                rawLine.Value = line.Raw ?? string.Empty;

                var splitId = (long)(await insertSplit.ExecuteScalarAsync())!;
                sourceSplits++;

                var matchedRaws = matcher.FindMatches(split.Text ?? string.Empty).Distinct();
                foreach (var matchedRaw in matchedRaws)
                {
                    foreach (var entryId in rawToEntryIds[matchedRaw])
                    {
                        occurrenceEntryId.Value = entryId;
                        occurrenceSplitId.Value = splitId;
                        exactMatch.Value = matchedRaw == split.Text ? 1 : 0;
                        occurrenceTranslated.Value = split.Translated ?? string.Empty;
                        await insertOccurrence.ExecuteNonQueryAsync();
                    }
                }
            }
        }

        return sourceSplits;
    }

    private static async Task ExecuteAsync(SqliteConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }

    private static async Task<object?> ScalarAsync(SqliteConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        return await command.ExecuteScalarAsync();
    }

    private static async Task<string> Sha256Async(string path)
    {
        await using var stream = File.OpenRead(path);
        var hash = await SHA256.HashDataAsync(stream);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    private sealed class AhoCorasickMatcher
    {
        private readonly List<Node> _nodes = [new()];

        public AhoCorasickMatcher(IEnumerable<string> patterns)
        {
            foreach (var pattern in patterns.Where(pattern => !string.IsNullOrEmpty(pattern)))
                AddPattern(pattern);

            BuildFailures();
        }

        public IEnumerable<string> FindMatches(string text)
        {
            var nodeIndex = 0;
            foreach (var character in text)
            {
                while (nodeIndex != 0 && !_nodes[nodeIndex].Next.ContainsKey(character))
                    nodeIndex = _nodes[nodeIndex].Fail;

                if (_nodes[nodeIndex].Next.TryGetValue(character, out var nextIndex))
                    nodeIndex = nextIndex;

                foreach (var output in _nodes[nodeIndex].Outputs)
                    yield return output;
            }
        }

        private void AddPattern(string pattern)
        {
            var nodeIndex = 0;
            foreach (var character in pattern)
            {
                if (!_nodes[nodeIndex].Next.TryGetValue(character, out var nextIndex))
                {
                    nextIndex = _nodes.Count;
                    _nodes[nodeIndex].Next[character] = nextIndex;
                    _nodes.Add(new Node());
                }

                nodeIndex = nextIndex;
            }

            _nodes[nodeIndex].Outputs.Add(pattern);
        }

        private void BuildFailures()
        {
            var queue = new Queue<int>();
            foreach (var childIndex in _nodes[0].Next.Values)
                queue.Enqueue(childIndex);

            while (queue.Count > 0)
            {
                var currentIndex = queue.Dequeue();
                var current = _nodes[currentIndex];

                foreach (var (character, childIndex) in current.Next)
                {
                    var failIndex = current.Fail;
                    while (failIndex != 0 && !_nodes[failIndex].Next.ContainsKey(character))
                        failIndex = _nodes[failIndex].Fail;

                    if (_nodes[failIndex].Next.TryGetValue(character, out var failNextIndex))
                        _nodes[childIndex].Fail = failNextIndex;

                    _nodes[childIndex].Outputs.AddRange(_nodes[_nodes[childIndex].Fail].Outputs);
                    queue.Enqueue(childIndex);
                }
            }
        }

        private sealed class Node
        {
            public Dictionary<char, int> Next { get; } = [];
            public int Fail { get; set; }
            public List<string> Outputs { get; } = [];
        }
    }
}
