using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace SOPweaveLauncher
{
    internal sealed class LauncherOptions
    {
        public string Root, Data, Identity, InstanceKey, Url;
        public int Port;
        public bool Headless, Stop;

        public static string Hash(string value)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }

        public static LauncherOptions Parse(string[] args)
        {
            var options = new LauncherOptions();
            options.Root = AppDomain.CurrentDomain.BaseDirectory;
            string port = Environment.GetEnvironmentVariable("PORT") ?? "8080";
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--headless": options.Headless = true; break;
                    case "--stop": options.Stop = true; break;
                    case "--app-dir":
                    case "--port":
                        if (++i == args.Length) throw new ArgumentException("启动参数缺少值。");
                        if (args[i - 1] == "--app-dir") options.Root = args[i]; else port = args[i];
                        break;
                    default: throw new ArgumentException("无法识别的启动参数：" + args[i]);
                }
            }
            if (!Int32.TryParse(port, out options.Port) || options.Port < 1 || options.Port > 65535)
                throw new ArgumentException("端口必须是 1 到 65535 之间的数字。");
            options.Root = Path.GetFullPath(options.Root);
            string data = Environment.GetEnvironmentVariable("SOPWEAVE_DATA_DIR")
                ?? Environment.GetEnvironmentVariable("FINANCE_DATA_DIR") ?? "data";
            options.Data = Path.GetFullPath(Path.Combine(options.Root, data));
            options.Identity = Hash(DirectoryIdentity.Canonical(options.Root) + "\n" + DirectoryIdentity.Canonical(options.Data));
            options.InstanceKey = "Local\\SOPweave." + Hash(options.Identity + ":" + options.Port);
            options.Url = "http://127.0.0.1:" + options.Port + "/";
            return options;
        }
    }

    internal sealed class LaunchLog
    {
        private readonly object gate = new object();
        public readonly string FileName;

        public LaunchLog(LauncherOptions options)
        {
            string directory = Path.Combine(options.Data, "logs");
            try { Directory.CreateDirectory(directory); }
            catch (IOException) { directory = Fallback(); }
            catch (UnauthorizedAccessException) { directory = Fallback(); }
            FileName = Path.Combine(directory, "launcher-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Process.GetCurrentProcess().Id + ".log");
            Write("SOPweave launcher started");
        }

        private static string Fallback()
        {
            string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SOPweave", "logs");
            Directory.CreateDirectory(directory);
            return directory;
        }

        public void Write(string message)
        {
            if (String.IsNullOrEmpty(message)) return;
            lock (gate) File.AppendAllText(FileName, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message + Environment.NewLine, new UTF8Encoding(false));
        }
    }

    internal sealed class Launcher : IDisposable
    {
        public readonly LauncherOptions Options;
        public readonly LaunchLog Log;
        public Action<string> BrowserFailure;
        private readonly ManualResetEvent cancelled = new ManualResetEvent(false);
        private EventWaitHandle stopSignal;
        private EventWaitHandle serviceStop;
        private Process service;
        private WindowsJob job;

        public Launcher(LauncherOptions options) { Options = options; Log = new LaunchLog(options); }
        public void Cancel() { cancelled.Set(); }

        public static int RequestStop(LauncherOptions options)
        {
            try
            {
                using (var signal = EventWaitHandle.OpenExisting(options.InstanceKey + ".Stop")) signal.Set();
                return 0;
            }
            catch (WaitHandleCannotBeOpenedException) { return 2; }
        }

        private bool Stopping { get { return cancelled.WaitOne(0) || (stopSignal != null && stopSignal.WaitOne(0)); } }

        public int Run(Action<string> progress)
        {
            bool owner;
            using (var mutex = new Mutex(true, Options.InstanceKey, out owner))
            {
                try
                {
                    if (!owner)
                    {
                        progress("正在等待已启动的 SOPweave…");
                        WaitForReady(null);
                        OpenBrowser();
                        return 0;
                    }
                    stopSignal = new EventWaitHandle(false, EventResetMode.ManualReset, Options.InstanceKey + ".Stop");
                    int pid;
                    if (TryReady(out pid)) { OpenBrowser(); return 0; }
                    EnsurePortAvailable();
                    CheckLayout();
                    progress("正在启动本地服务…");
                    string python = FindPython();
                    if (!Probe(python, new[] { "-I", "-X", "utf8", "scripts/check_runtime.py", "--dependencies-only" }, 30000))
                        throw new InvalidOperationException("应用的 Python 依赖尚未准备完整。请按 README 准备运行环境后重试。");
                    if (Stopping) return 0;
                    StartService(python);
                    WaitForReady(service);
                    Log.Write("READY " + Options.Url);
                    progress("服务已就绪");
                    OpenBrowser();
                    while (!Stopping)
                    {
                        if (service.WaitForExit(250)) throw new InvalidOperationException("本地服务已退出，退出码：" + service.ExitCode);
                    }
                    return 0;
                }
                catch (OperationCanceledException) { return 0; }
                finally
                {
                    StopOwnedService();
                    if (stopSignal != null) { stopSignal.Dispose(); stopSignal = null; }
                    if (owner) mutex.ReleaseMutex();
                }
            }
        }

        private void CheckLayout()
        {
            foreach (string file in new[] { "asgi.py", "scripts/serve.py", "scripts/check_runtime.py", "requirements.lock", "frontend/dist/index.html" })
                if (!File.Exists(Path.Combine(Options.Root, file)))
                    throw new FileNotFoundException("应用文件不完整，缺少 " + file + "。请将启动器与完整软件目录放在一起；桌面入口请使用快捷方式。");
        }

        private string FindPython()
        {
            var candidates = new List<string> {
                Path.Combine(Options.Root, ".runtime/Scripts/python.exe"),
                Path.Combine(Options.Root, ".venv/Scripts/python.exe"),
                Path.Combine(Options.Root, "../.venv/Scripts/python.exe")
            };
            foreach (string item in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            {
                string directory = item.Trim().Trim('"');
                // WindowsApps may be an install alias rather than an interpreter.
                if (directory.Length > 0 && Path.IsPathRooted(directory) && directory.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) < 0)
                    candidates.Add(Path.Combine(directory, "python.exe"));
            }
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string candidate in candidates)
            {
                string path = Path.GetFullPath(candidate);
                if (!seen.Add(path) || !File.Exists(path)) continue;
                if (Probe(path, new[] { "-I", "-c", "import sys,struct; raise SystemExit(sys.version_info[:2] != (3,14) or sys.platform != 'win32' or struct.calcsize('P') != 8)" }, 8000)) return path;
                if (Stopping) throw new OperationCanceledException();
            }
            throw new InvalidOperationException("未找到可用的 Python 3.14 x64。请先按 README 准备 .runtime 环境；启动器不会安装或下载 Python。");
        }

        private ProcessStartInfo PythonStart(string python, string[] args)
        {
            var info = new ProcessStartInfo(python, JoinArguments(args)) {
                WorkingDirectory = Options.Root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, RedirectStandardInput = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            info.EnvironmentVariables["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false";
            return info;
        }

        private void Capture(Process process)
        {
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log.Write(e.Data); };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log.Write(e.Data); };
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }

        private bool Probe(string python, string[] args, int timeout)
        {
            using (var process = new Process { StartInfo = PythonStart(python, args) })
            {
                try
                {
                    process.Start(); Capture(process); process.StandardInput.Close();
                    if (!process.WaitForExit(timeout))
                    {
                        process.Kill(); process.WaitForExit(); Log.Write("Python probe timed out"); return false;
                    }
                    process.WaitForExit();
                    return process.ExitCode == 0;
                }
                catch (System.ComponentModel.Win32Exception error) { Log.Write(error.Message); return false; }
            }
        }

        private void StartService(string python)
        {
            job = new WindowsJob();
            string eventName = "Local\\SOPweave.ServiceStop." + Guid.NewGuid().ToString("N");
            serviceStop = new EventWaitHandle(false, EventResetMode.ManualReset, eventName);
            var candidate = new Process { StartInfo = PythonStart(python, new[] { "-I", "-X", "utf8", "-u", "scripts/serve.py", "--shutdown-event", eventName, "--port", Options.Port.ToString() }) };
            bool started = false;
            try
            {
                candidate.Start(); started = true; job.Add(candidate); service = candidate;
            }
            catch
            {
                if (started && !candidate.HasExited) candidate.Kill();
                candidate.Dispose();
                throw;
            }
            Capture(service);
            service.StandardInput.Close();
            Log.Write("Service PID " + service.Id);
        }

        private void WaitForReady(Process owned)
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed.TotalSeconds < 90)
            {
                if (Stopping) throw new OperationCanceledException();
                if (owned != null && owned.HasExited) throw new InvalidOperationException("服务未能启动，退出码：" + owned.ExitCode);
                int pid;
                if (TryReady(out pid))
                {
                    if (owned != null && !job.Contains(pid)) throw new InvalidOperationException("启动时端口被另一个服务占用，请稍后重试。");
                    return;
                }
                cancelled.WaitOne(250);
            }
            throw new TimeoutException("等待本地服务启动超时（90 秒）。请查看启动日志。");
        }

        private bool TryReady(out int pid)
        {
            pid = 0;
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(Options.Url + "api/health");
                request.Proxy = null; request.Timeout = 700; request.ReadWriteTimeout = 700;
                request.AllowAutoRedirect = false;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    var buffer = new char[4097];
                    int length = stream.ReadBlock(buffer, 0, buffer.Length);
                    if (length > 4096) return false;
                    var body = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(new string(buffer, 0, length));
                    var data = body["data"] as Dictionary<string, object>;
                    if (response.StatusCode != HttpStatusCode.OK || !Object.Equals(body["success"], true) || data == null
                        || !Object.Equals(data["product"], "sopweave") || Convert.ToInt32(data["protocol"]) != 1
                        || !Object.Equals(data["status"], "ready") || !Object.Equals(data["installation"], Options.Identity)) return false;
                    pid = Convert.ToInt32(data["pid"]);
                    return pid > 0;
                }
            }
            catch (WebException) { return false; }
            catch (IOException) { return false; }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
            catch (KeyNotFoundException) { return false; }
            catch (InvalidCastException) { return false; }
            catch (FormatException) { return false; }
            catch (OverflowException) { return false; }
        }

        private void EnsurePortAvailable()
        {
            using (var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
            {
                try { socket.ExclusiveAddressUse = true; socket.Bind(new IPEndPoint(IPAddress.Loopback, Options.Port)); }
                catch (SocketException) { throw new InvalidOperationException("端口 " + Options.Port + " 已被其他程序或另一份 SOPweave 占用。请关闭对应程序或使用 --port 指定其他端口。"); }
            }
        }

        public void OpenBrowser()
        {
            if (Options.Headless) return;
            try { Process.Start(new ProcessStartInfo(Options.Url) { UseShellExecute = true }); }
            catch (Exception error)
            {
                Log.Write("Browser open failed: " + error.Message);
                if (BrowserFailure != null) BrowserFailure("服务已启动，但默认浏览器未能打开。请手动访问：\n" + Options.Url);
            }
        }

        private void StopOwnedService()
        {
            try
            {
                if (service != null && !service.HasExited)
                {
                    Log.Write("Stopping owned service");
                    serviceStop.Set();
                    if (!service.WaitForExit(25000)) Log.Write("Graceful shutdown timed out; closing owned process job");
                }
            }
            finally
            {
                if (job != null) { job.Dispose(); job = null; }
                if (service != null) { service.WaitForExit(5000); service.Dispose(); service = null; }
                if (serviceStop != null) { serviceStop.Dispose(); serviceStop = null; }
            }
        }

        private static string JoinArguments(string[] args)
        {
            var quoted = new List<string>();
            foreach (string argument in args)
            {
                var value = new StringBuilder("\"");
                int slashes = 0;
                foreach (char c in argument)
                {
                    if (c == '\\') { slashes++; continue; }
                    if (c == '"') { value.Append('\\', slashes * 2 + 1); value.Append(c); }
                    else { value.Append('\\', slashes); value.Append(c); }
                    slashes = 0;
                }
                value.Append('\\', slashes * 2); value.Append('"'); quoted.Add(value.ToString());
            }
            return String.Join(" ", quoted);
        }

        public void Dispose() { cancelled.Dispose(); }
    }
}
