using BepInEx;
using BepInEx.Logging;
using EnglishPatch.Support;
using Mono.Cecil;
using Mono.Cecil.Cil;
using SharedAssembly.DynamicStrings;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace EnglishPatch.DynamicStrings;

/// <summary>
/// Used to replace hardcoded strings in IL
/// </summary>
[BepInPlugin($"{MyPluginInfo.PLUGIN_GUID}.DynamicStringDumperPlugin", "DynamicStringDumperPlugin", MyPluginInfo.PLUGIN_VERSION)]
public class StringDumperPlugin : BaseUnityPlugin
{
    internal static new ManualLogSource Logger;
    public static bool Enabled = false;

    //static List<string> SafeFunctions = [];
    //static List<string> UnsafeFunctions = [];

    private void Awake()
    {
        Logger = base.Logger;

        if (Enabled)
        {
            DumpFiles(@"G:/Xyzj2OverLlm/Files/Raw/DynamicStrings/dynamicStrings.txt");
        }
    }

    public void DumpFiles(string outputPath)
    {
        Logger.LogError("Dumping dynamic strings...");

        try
        {
            //!!! Manually copy YamlDotNet.dll to the ManagedDlls folder
            var serializer = Yaml.CreateSerializer();
            string gamePath = Paths.ManagedPath;
            string assemblyPath = Path.Combine(gamePath, "Assembly-CSharp.dll");

            var contracts = new List<DynamicStringContract>();
            var assembly = AssemblyDefinition.ReadAssembly(assemblyPath);

            int count = 0;

            foreach (var module in assembly.Modules)
            {
                foreach (var type in module.Types)
                {
                    ProcessType(type, contracts);
                    count++;
                }
            }

            //YAML is too hard to handle when splitting
            //File.WriteAllText(outputPath, serializer.Serialize(contracts));

            var lines = new List<string>();
            foreach (var contract in contracts)
            {
                var parameters = CleanForCsv($"[{string.Join(",", contract.Parameters)}]");
                lines.Add($"{CleanForCsv(contract.Type)},{CleanForCsv(contract.Method)},{CleanForCsv(contract.ILOffset.ToString())},{CleanForCsv(contract.Raw)},{parameters}");
            }

            File.WriteAllLines(outputPath, lines);
            Logger.LogWarning($"Dumped to: {outputPath}");
        }
        catch (Exception ex)
        {
            Logger.LogError($"Error dumping: {ex.Message}");
        }

        //SafeFunctions.Sort();
        //UnsafeFunctions.Sort();

        //foreach (var f in SafeFunctions)
        //    Logger.LogWarning($"Safe: {f}");

        //foreach (var f in UnsafeFunctions)
        //    Logger.LogWarning($"Unsafe: {f}");
    }

    public string CleanForCsv(string input)
    {
        return input.Replace(",", "，")
            .Replace("\n", "\\n")
            .Replace("\r", "\\r");
    }

    private void ProcessType(TypeDefinition type, List<DynamicStringContract> stringReferences, int recursionLevel = 0)
    {
        // Process nested types
        foreach (var nestedType in type.NestedTypes)
            if (!stringReferences.Any(r => r.Type == nestedType.ToString()) && recursionLevel < 15) //15 should be fine
                ProcessType(nestedType, stringReferences, recursionLevel++);

        // Process methods
        foreach (var method in type.Methods)
        {
            if (!method.HasBody)
                continue;

            foreach (var instruction in method.Body.Instructions)
            {
                string operandValue = string.Empty;

                if (instruction.OpCode == OpCodes.Ldstr && instruction.Operand is string stringValue)
                {
                    operandValue = stringValue;
                }
                else if (instruction.OpCode == OpCodes.Ldc_I4
                    && instruction.Operand is int intValue
                    && intValue >= char.MinValue
                    && intValue <= char.MaxValue
                    && IsLikelyCharParameter(instruction))
                {
                    operandValue = $"INVALIDCHAR: {(char)intValue}";
                }

                // Look for string load operations
                if (!string.IsNullOrWhiteSpace(operandValue)
                    && Regex.IsMatch(operandValue, MainPlugin.ChineseCharPattern))
                {
                    // Skip Debug lines
                    if (IsLikelyDebug(instruction, operandValue))
                        continue;

                    // Add to our list
                    stringReferences.Add(new DynamicStringContract
                    {
                        Type = type.FullName,
                        Method = method.Name,
                        Raw = operandValue,
                        ILOffset = instruction.Offset,
                        Parameters = method.Parameters.Select(p => p.ParameterType.FullName).ToArray(),
                    });
                }
            }
        }
    }

    public static bool IsLikelyDebug(Mono.Cecil.Cil.Instruction currentInstruction, string currentString)
    {
        string[] methodContains = [
            "Debug", ".Log",
            "ContainsKey", "LitJson", "onError",
            "CustomData", "GetIconSprite"
        ];

        int instructionCheck = 0;
        var nextInstruction = currentInstruction;

        //Logger.LogError($"For: {currentString}");

        //if (Regex.IsMatch(currentString, @"[a-zA-Z]")
        //    && !currentString.Contains("x", StringComparison.OrdinalIgnoreCase) // For quantity x
        //    && !(currentString.Contains("<") && currentString.Contains(">"))
        //    && !currentString.Contains("size")
        //    && !currentString.Contains("color"))
        //{
        //    //Logger.LogError($"HasEnglish: true");
        //    return true;
        //}

        while (instructionCheck < 5) // check four instructions ahead
        {
            // Get the next instruction after loading the string
            nextInstruction = nextInstruction.Next;

            // Check if there's no next instruction
            if (nextInstruction == null)
                return false;

            //Logger.LogError($"Ref {instructionCheck}: {nextInstruction.OpCode}  {nextInstruction.Operand}");

            //TODO: This still wipes out addresses that happen to have a log straight after a valid function call
            //For now less is more.
            if (nextInstruction.OpCode == OpCodes.Call || nextInstruction.OpCode == OpCodes.Callvirt)
            {
                if (nextInstruction.Operand is MethodReference methodRef)
                {
                    //Logger.LogError($"Ref {instructionCheck}: {methodRef.FullName}");

                    if (methodRef.FullName.StartsWith("Log"))
                        return true;

                    if (methodContains.Any(phrase => methodRef.FullName.IndexOf(phrase) >= 0))
                    {
                        //if (!UnsafeFunctions.Contains(methodRef.FullName))
                        //    UnsafeFunctions.Add(methodRef.FullName);

                        //Logger.LogError($"Ref {instructionCheck}: Debug");
                        return true;
                    }
                    //else if (!SafeFunctions.Contains(methodRef.FullName))
                    //    SafeFunctions.Add(methodRef.FullName);
                }

            }
            else if (nextInstruction.OpCode == OpCodes.Newobj
                || nextInstruction.OpCode == OpCodes.Stloc
                || nextInstruction.OpCode == OpCodes.Ret
                || nextInstruction.OpCode == OpCodes.Rem
                //|| nextInstruction.OpCode == OpCodes.Dup
                || nextInstruction.OpCode == OpCodes.Br
                || nextInstruction.OpCode == OpCodes.Break
                || nextInstruction.OpCode == OpCodes.Brfalse
                || nextInstruction.OpCode == OpCodes.Brfalse_S
                || nextInstruction.OpCode == OpCodes.Brtrue
                || nextInstruction.OpCode == OpCodes.Brtrue_S
                || nextInstruction.OpCode == OpCodes.Br_S)
            {
                //Logger.LogError($"Breaking {instructionCheck}: Not part of Stack {nextInstruction.OpCode}");
                break;
            }

            instructionCheck++;
        }

        return false;
    }

    public static bool IsLikelyCharParameter(Mono.Cecil.Cil.Instruction currentInstruction)
    {
        // Get the next instruction after loading the potential character value
        var nextInstruction = currentInstruction.Next;

        // Check if there's no next instruction
        if (nextInstruction == null)
            return false;

        // Case 1: Direct call to a method that takes a char parameter
        if (nextInstruction.OpCode == OpCodes.Call || nextInstruction.OpCode == OpCodes.Callvirt)
        {
            MethodReference calledMethod = nextInstruction.Operand as MethodReference;
            if (calledMethod != null && calledMethod.Parameters.Count > 0)
            {
                // Check if the first parameter is a char
                // (or if any parameter is a char, depending on your needs)
                foreach (ParameterDefinition param in calledMethod.Parameters)
                {
                    if (param.ParameterType.FullName == "System.Char")
                        return true;
                }
            }
        }

        // Case 2: Character being boxed (common for Split and other methods)
        if (nextInstruction.OpCode == OpCodes.Box &&
            nextInstruction.Operand is TypeReference typeRef &&
            typeRef.FullName == "System.Char")
        {
            return true;
        }

        // Case 3: Looking at a string Split method specifically
        // This might require more context - looking ahead several instructions
        if (IsPartOfStringSplit(currentInstruction))
        {
            return true;
        }

        return false;
    }

    public static bool IsPartOfStringSplit(Mono.Cecil.Cil.Instruction loadInstruction)
    {
        // Look ahead several instructions for a pattern that matches String.Split
        var current = loadInstruction;

        // Skip ahead a few instructions looking for the call
        for (int i = 0; i < 5 && current.Next != null; i++)
        {
            current = current.Next;

            if ((current.OpCode == OpCodes.Call || current.OpCode == OpCodes.Callvirt) &&
                current.Operand is MethodReference methodRef)
            {
                // Check if the method is String.Split
                if (methodRef.DeclaringType.FullName == "System.String" &&
                    methodRef.Name == "Split")
                {
                    return true;
                }
            }
        }

        return false;
    }
}
