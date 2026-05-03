using Translate;

static int ShowUsage()
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  dotnet run --project Translate -- package [--working-directory Files]");
    Console.WriteLine("  dotnet run --project Translate -- package [--working-directory Files] [--stage-resources _working/BepInEx/resources]");
    Console.WriteLine("  dotnet run --project Translate -- apply-glossary [--working-directory Files] [--dry-run]");
    Console.WriteLine("  dotnet run --project Translate -- import-glossary-db [--working-directory Files] [--database _working/glossary-audit.db]");
    Console.WriteLine();
    Console.WriteLine("Commands:");
    Console.WriteLine("  package   Generate Files/Mod/db1.txt and Files/Mod/Formatted/* from converted translations.");
    Console.WriteLine("  apply-glossary   Apply exact Glossary.yaml raw/result matches to Files/Converted.");
    Console.WriteLine("  import-glossary-db   Build a SQLite audit DB from Glossary.yaml and Converted/stringlang.txt.");
    return 1;
}

static string GetOption(string[] args, string longName, string shortName, string defaultValue)
{
    for (var i = 0; i < args.Length; i++)
    {
        if ((args[i] == longName || args[i] == shortName) && i + 1 < args.Length)
            return args[i + 1];
    }

    return defaultValue;
}

static bool HasOption(string[] args, string longName, string shortName)
{
    return args.Any(arg => arg == longName || arg == shortName);
}

static void StageResource(string sourceFile, string destinationDirectory)
{
    if (!File.Exists(sourceFile))
        return;

    Directory.CreateDirectory(destinationDirectory);
    File.Copy(sourceFile, Path.Combine(destinationDirectory, Path.GetFileName(sourceFile)), true);
    Console.WriteLine($"Staged: {Path.Combine(destinationDirectory, Path.GetFileName(sourceFile))}");
}

if (args.Length == 0 || args[0] is "-h" or "--help")
    return ShowUsage();

var command = args[0].ToLowerInvariant();
if (command is not ("package" or "apply-glossary" or "import-glossary-db"))
    return ShowUsage();

var workingDirectory = Path.GetFullPath(GetOption(args, "--working-directory", "-w", "Files"));
if (!Directory.Exists(workingDirectory))
{
    Console.Error.WriteLine($"Working directory does not exist: {workingDirectory}");
    return 1;
}

if (command == "apply-glossary")
{
    var dryRun = HasOption(args, "--dry-run", "-n");
    var result = await GlossaryApplicationService.ApplyExactMatchesAsync(workingDirectory, dryRun);

    Console.WriteLine($"Files visited: {result.FilesVisited}");
    Console.WriteLine($"Files changed: {result.FilesChanged}");
    Console.WriteLine($"Splits changed: {result.SplitsChanged}");
    Console.WriteLine($"Containing non-exact glossary matches skipped: {result.ContainingMatchesSkipped}");

    if (dryRun)
        Console.WriteLine("Dry run only: no files were changed.");

    return 0;
}

if (command == "import-glossary-db")
{
    var databasePath = Path.GetFullPath(GetOption(args, "--database", "-d", "_working/glossary-audit.db"));
    var result = await GlossaryAuditDbService.ImportAsync(workingDirectory, databasePath);

    Console.WriteLine($"Database: {result.DatabasePath}");
    Console.WriteLine($"Glossary entries: {result.GlossaryEntries}");
    Console.WriteLine($"Stringlang splits: {result.SourceSplits}");
    Console.WriteLine($"Glossary occurrences: {result.Occurrences}");
    return 0;
}

await FileOutputHandling.PackageFinalTranslationAsync(workingDirectory);

Console.WriteLine($"Generated: {Path.Combine(workingDirectory, "Mod", "db1.txt")}");
Console.WriteLine($"Generated: {Path.Combine(workingDirectory, "Mod", "Formatted")}");

var stageResourcesDirectory = GetOption(args, "--stage-resources", "-s", string.Empty);
if (!string.IsNullOrWhiteSpace(stageResourcesDirectory))
{
    stageResourcesDirectory = Path.GetFullPath(stageResourcesDirectory);
    StageResource(Path.Combine(workingDirectory, "Mod", "db1.txt"), stageResourcesDirectory);
    StageResource(Path.Combine(workingDirectory, "Mod", "Formatted", "dynamicStrings.txt"), stageResourcesDirectory);
    StageResource(Path.Combine(workingDirectory, "Mod", "Formatted", "dumpedPrefabText.txt"), stageResourcesDirectory);
}

return 0;
