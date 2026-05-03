static int ShowUsage()
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  dotnet run --project Translate -- package [--working-directory Files]");
    Console.WriteLine("  dotnet run --project Translate -- package [--working-directory Files] [--stage-resources _working/BepInEx/resources]");
    Console.WriteLine();
    Console.WriteLine("Commands:");
    Console.WriteLine("  package   Generate Files/Mod/db1.txt and Files/Mod/Formatted/* from converted translations.");
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

if (!string.Equals(args[0], "package", StringComparison.OrdinalIgnoreCase))
    return ShowUsage();

var workingDirectory = Path.GetFullPath(GetOption(args, "--working-directory", "-w", "Files"));
if (!Directory.Exists(workingDirectory))
{
    Console.Error.WriteLine($"Working directory does not exist: {workingDirectory}");
    return 1;
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
