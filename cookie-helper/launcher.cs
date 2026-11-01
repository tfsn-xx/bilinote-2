using System;
using System.Diagnostics;
using System.IO;

internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            string node = File.ReadAllText(Path.Combine(folder, "node-path.txt")).Trim();
            Process.Start(new ProcessStartInfo
            {
                FileName = node,
                Arguments = "\"" + Path.Combine(folder, "launch.cjs") + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            });
        }
        catch (Exception error)
        {
            File.AppendAllText(Path.Combine(folder, "helper.log"), DateTime.UtcNow.ToString("o") + " " + error.Message + Environment.NewLine);
        }
    }
}
