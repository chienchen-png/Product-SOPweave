using System;
using System.Diagnostics;
using System.Drawing;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("SOPweave")]
[assembly: AssemblyProduct("SOPweave")]
[assembly: AssemblyDescription("SOPweave local service launcher")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace SOPweaveLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            bool headless = Array.IndexOf(args, "--headless") >= 0;
            try
            {
                var options = LauncherOptions.Parse(args);
                if (options.Stop) return Launcher.RequestStop(options);
                using (var launcher = new Launcher(options))
                {
                    if (headless)
                    {
                        try { return launcher.Run(launcher.Log.Write); }
                        catch (Exception error) { launcher.Log.Write("ERROR " + error.Message); return 1; }
                    }
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    using (var context = new LauncherContext(launcher))
                    {
                        Application.Run(context);
                        return context.ExitCode;
                    }
                }
            }
            catch (Exception error)
            {
                if (!headless) MessageBox.Show(error.Message, "SOPweave", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
    }

    internal sealed class LauncherContext : ApplicationContext
    {
        private readonly Launcher launcher;
        private readonly NotifyIcon tray;
        private readonly Form window;
        private readonly Label status;
        private readonly ToolStripMenuItem open;
        private bool finished;
        public int ExitCode;

        public LauncherContext(Launcher launcher)
        {
            this.launcher = launcher;
            window = new Form {
                Text = "SOPweave", ClientSize = new Size(360, 105), StartPosition = FormStartPosition.CenterScreen,
                FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false,
                Font = new Font("Microsoft YaHei UI", 9), Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath)
            };
            status = new Label { Text = "正在准备启动…", Left = 20, Top = 20, Width = 320, Height = 24 };
            window.Controls.Add(status);
            window.Controls.Add(new ProgressBar { Left = 20, Top = 57, Width = 320, Height = 9, Style = ProgressBarStyle.Marquee });
            var menu = new ContextMenuStrip();
            open = new ToolStripMenuItem("打开 SOPweave", null, delegate { launcher.OpenBrowser(); }) { Enabled = false };
            menu.Items.Add(open);
            menu.Items.Add("查看启动日志", null, delegate {
                try { Process.Start(new ProcessStartInfo(launcher.Log.FileName) { UseShellExecute = true }); }
                catch (Exception error) { MessageBox.Show(error.Message, "SOPweave"); }
            });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出并停止服务", null, delegate { RequestExit(); });
            tray = new NotifyIcon { Text = "SOPweave — 正在启动", Icon = window.Icon, ContextMenuStrip = menu, Visible = true };
            tray.DoubleClick += delegate { if (open.Enabled) launcher.OpenBrowser(); };
            launcher.BrowserFailure = delegate(string message) {
                window.BeginInvoke(new Action(delegate { MessageBox.Show(message, "SOPweave", MessageBoxButtons.OK, MessageBoxIcon.Information); }));
            };
            window.FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (!finished) { e.Cancel = true; RequestExit(); }
            };
            window.Shown += delegate { StartWorker(); };
            window.Show();
        }

        private void RequestExit()
        {
            status.Text = "正在停止本地服务…";
            open.Enabled = false;
            tray.Text = "SOPweave — 正在停止";
            window.Show();
            launcher.Cancel();
        }

        private void StartWorker()
        {
            Task.Factory.StartNew(delegate {
                return launcher.Run(delegate(string message) {
                    window.BeginInvoke(new Action(delegate {
                        status.Text = message;
                        if (message == "服务已就绪") { open.Enabled = true; tray.Text = "SOPweave — 本地服务运行中"; window.Hide(); }
                    }));
                });
            }).ContinueWith(delegate(Task<int> result) {
                window.BeginInvoke(new Action(delegate {
                    ExitCode = result.IsFaulted ? 1 : result.Result;
                    if (result.IsFaulted)
                    {
                        string message = result.Exception.GetBaseException().Message;
                        launcher.Log.Write("ERROR " + message);
                        MessageBox.Show(message + "\n\n启动日志：" + launcher.Log.FileName, "SOPweave", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }
                    finished = true;
                    tray.Visible = false;
                    window.Close();
                    ExitThread();
                }));
            });
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) { tray.Dispose(); window.Dispose(); }
            base.Dispose(disposing);
        }
    }
}
