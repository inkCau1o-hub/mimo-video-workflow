using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace MiMo.Windows {
    // Assign the suspended child to a kill-on-close job before it can spawn descendants.
    public sealed class ManagedProcess : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Security { public int Length; public IntPtr Descriptor; public int Inherit; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup {
            public int Size; public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public short Show, ReservedSize; public IntPtr Reserved2, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public int Id, ThreadId; }
        [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcesses;
            public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong A, B, C, D, E, F; }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit {
            public BasicLimit Basic; public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimit info, int length);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref Security attributes, int size);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetHandleInformation(IntPtr handle, int mask, int flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string file, StringBuilder command, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr environment, string directory, ref Startup startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
        [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, int id);
        [DllImport("kernel32.dll")] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);

        IntPtr job, process;
        Task stdoutTask, stderrTask;
        readonly StringBuilder output = new StringBuilder(), error = new StringBuilder();
        readonly object gate = new object();
        public int Id { get; private set; }
        public bool HasExited { get { return process == IntPtr.Zero || WaitForSingleObject(process, 0) == 0; } }
        public int ExitCode { get { uint code; if (process == IntPtr.Zero || !GetExitCodeProcess(process, out code)) return -1; return unchecked((int)code); } }
        public string Output { get { lock (gate) return output.ToString(); } }
        public string ErrorOutput { get { lock (gate) return error.ToString(); } }

        public bool OwnsProcess(int id) {
            if (job == IntPtr.Zero) return false;
            IntPtr handle = OpenProcess(0x1000, false, id);
            if (handle == IntPtr.Zero) return false;
            try { bool member; return IsProcessInJob(handle, job, out member) && member; }
            finally { CloseHandle(handle); }
        }

        public static string Quote(string value) {
            var text = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { text.Append('\\', slashes * 2 + 1); text.Append(c); }
                else { text.Append('\\', slashes); text.Append(c); }
                slashes = 0;
            }
            text.Append('\\', slashes * 2); return text.Append('"').ToString();
        }

        public static string Redact(string value, string[] secrets) {
            if (secrets == null) secrets = new string[0];
            foreach (string secret in secrets) {
                if (!String.IsNullOrEmpty(secret)) {
                    value = value.Replace(secret, "[REDACTED]");
                    value = value.Replace(Uri.EscapeDataString(secret), "[REDACTED]");
                }
            }
            value = Regex.Replace(value, @"(?i)(bearer\s+)[^\s""']+", "$1[REDACTED]");
            return Regex.Replace(value, @"(?i)((?:api[_-]?key|token|password|secret)[""']?\s*[=:]\s*[""']?)[^\s,;""']+", "$1[REDACTED]");
        }

        Task ReadPipe(IntPtr handle, StringBuilder buffer, string log, string[] secrets) {
            return Task.Run(() => {
                using (var stream = new FileStream(new SafeFileHandle(handle, true), FileAccess.Read))
                using (var reader = new StreamReader(stream, new UTF8Encoding(false), true))
                using (var writer = log == null ? null : new StreamWriter(log, false, new UTF8Encoding(false))) {
                    if (writer != null) writer.AutoFlush = true;
                    string line;
                    while ((line = reader.ReadLine()) != null) {
                        line = Redact(line, secrets);
                        lock (gate) { if (buffer.Length < 1048576) buffer.AppendLine(line); }
                        if (writer != null) writer.WriteLine(line);
                    }
                }
            });
        }

        public static ManagedProcess Start(string file, string[] args, string directory, IDictionary environment, string[] secrets, string logPrefix) {
            if (String.IsNullOrEmpty(logPrefix)) logPrefix = null;
            var result = new ManagedProcess();
            IntPtr outRead = IntPtr.Zero, outWrite = IntPtr.Zero, errRead = IntPtr.Zero, errWrite = IntPtr.Zero, envBlock = IntPtr.Zero;
            var pi = new ProcessInfo();
            try {
                result.job = CreateJobObject(IntPtr.Zero, null);
                if (result.job == IntPtr.Zero) throw new Win32Exception();
                var limits = new ExtendedLimit(); limits.Basic.Flags = 0x2000;
                if (!SetInformationJobObject(result.job, 9, ref limits, Marshal.SizeOf(limits))) throw new Win32Exception();
                var security = new Security { Length = Marshal.SizeOf(typeof(Security)), Inherit = 1 };
                if (!CreatePipe(out outRead, out outWrite, ref security, 0) || !CreatePipe(out errRead, out errWrite, ref security, 0)) throw new Win32Exception();
                if (!SetHandleInformation(outRead, 1, 0) || !SetHandleInformation(errRead, 1, 0)) throw new Win32Exception();
                var keys = new List<string>(); foreach (DictionaryEntry pair in environment) keys.Add((string)pair.Key);
                keys.Sort(StringComparer.OrdinalIgnoreCase);
                var block = new StringBuilder(); foreach (string key in keys) block.Append(key).Append('=').Append((string)environment[key]).Append('\0');
                block.Append('\0'); envBlock = Marshal.StringToHGlobalUni(block.ToString());
                var command = new StringBuilder(Quote(file)); foreach (string arg in args) command.Append(' ').Append(Quote(arg));
                var startup = new Startup { Size = Marshal.SizeOf(typeof(Startup)), Flags = 0x100, Output = outWrite, Error = errWrite };
                if (!CreateProcess(file, command, IntPtr.Zero, IntPtr.Zero, true, 0x08000404, envBlock, directory, ref startup, out pi)) throw new Win32Exception();
                result.process = pi.Process; result.Id = pi.Id;
                if (!AssignProcessToJobObject(result.job, result.process)) throw new Win32Exception();
                result.stdoutTask = result.ReadPipe(outRead, result.output, logPrefix == null ? null : logPrefix + ".out.log", secrets); outRead = IntPtr.Zero;
                result.stderrTask = result.ReadPipe(errRead, result.error, logPrefix == null ? null : logPrefix + ".err.log", secrets); errRead = IntPtr.Zero;
                if (ResumeThread(pi.Thread) == UInt32.MaxValue) throw new Win32Exception();
                return result;
            } catch {
                if (result.process != IntPtr.Zero) TerminateProcess(result.process, 1);
                result.Dispose(); throw;
            } finally {
                foreach (IntPtr handle in new [] { outRead, outWrite, errRead, errWrite, pi.Thread }) if (handle != IntPtr.Zero) CloseHandle(handle);
                if (envBlock != IntPtr.Zero) Marshal.FreeHGlobal(envBlock);
            }
        }

        public void Finish() {
            if (job != IntPtr.Zero) { CloseHandle(job); job = IntPtr.Zero; }
            if (process != IntPtr.Zero) WaitForSingleObject(process, 5000);
            var tasks = new List<Task>(); if (stdoutTask != null) tasks.Add(stdoutTask); if (stderrTask != null) tasks.Add(stderrTask);
            if (tasks.Count > 0 && !Task.WaitAll(tasks.ToArray(), 5000)) throw new IOException("Timed out draining child output.");
        }

        public void Dispose() {
            try { Finish(); } finally { if (process != IntPtr.Zero) { CloseHandle(process); process = IntPtr.Zero; } }
        }
    }
}
