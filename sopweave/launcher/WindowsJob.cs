using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace SOPweaveLauncher
{
    // Only processes started by this launcher enter the job. Closing its last
    // handle also cleans up tool subprocesses after a crash or forced exit.
    internal sealed class WindowsJob : IDisposable
    {
        private IntPtr handle;

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits
        {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcesses;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters { public ulong A, B, C, D, E, F; }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimits
        {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr value);

        public WindowsJob()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            var limits = new ExtendedLimits();
            limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)))
            {
                int error = Marshal.GetLastWin32Error();
                Dispose();
                throw new Win32Exception(error);
            }
        }

        public void Add(Process process)
        {
            if (!AssignProcessToJobObject(handle, process.Handle)) throw new Win32Exception();
        }

        public bool Contains(int pid)
        {
            try
            {
                using (var process = Process.GetProcessById(pid))
                {
                    bool belongs;
                    return IsProcessInJob(process.Handle, handle, out belongs) && belongs;
                }
            }
            catch (ArgumentException) { return false; }
            catch (Win32Exception) { return false; }
        }

        public void Dispose()
        {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }

    internal static class DirectoryIdentity
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(IntPtr file, StringBuilder path, uint length, uint flags);

        public static string Canonical(string directory)
        {
            string path = Path.GetFullPath(directory);
            if (!Directory.Exists(path))
            {
                string parent = Path.GetDirectoryName(path);
                if (!String.IsNullOrEmpty(parent)) path = Path.Combine(Canonical(parent), Path.GetFileName(path));
            }
            else
            {
                IntPtr file = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
                if (file == new IntPtr(-1)) throw new Win32Exception();
                try
                {
                    var result = new StringBuilder(32768);
                    uint length = GetFinalPathNameByHandle(file, result, (uint)result.Capacity, 0);
                    if (length == 0 || length >= result.Capacity) throw new Win32Exception();
                    path = result.ToString();
                    if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) path = @"\\" + path.Substring(8);
                    else if (path.StartsWith(@"\\?\", StringComparison.Ordinal)) path = path.Substring(4);
                }
                finally { WindowsJob.CloseHandle(file); }
            }
            return path.TrimEnd('\\', '/').ToLowerInvariant();
        }
    }
}
