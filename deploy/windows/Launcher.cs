using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("AFDS Studio")]
[assembly: AssemblyDescription("AFDS Studio desktop launcher")]
[assembly: AssemblyProduct("AFDS Studio")]

internal sealed class StudioLauncher : Form
{
    private readonly string workspace;
    private readonly int port;
    private readonly bool openWhenReady;
    private readonly Label status;
    private readonly Button openStudio;
    private readonly Button mediaHelp;
    private readonly object logLock = new object();
    private Process service;
    private string studioUrl;
    private string startupError;
    private bool missingMedia;
    private bool closing;

    private StudioLauncher(string workspace, int port, bool openWhenReady)
    {
        this.workspace = workspace;
        this.port = port;
        this.openWhenReady = openWhenReady;
        Text = "AFDS Studio";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(484, 257);
        MinimumSize = new Size(500, 295);
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = new Font("Segoe UI", 10);
        BackColor = Color.FromArgb(22, 25, 31);
        ForeColor = Color.FromArgb(241, 236, 226);

        var title = new Label {
            Text = "AFDS Studio", AutoSize = true, Location = new Point(24, 21),
            Font = new Font("Segoe UI", 25, FontStyle.Bold)
        };
        status = new Label { Text = "正在启动本地工作室…", Location = new Point(26, 77), Size = new Size(426, 60) };
        openStudio = MakeButton("打开工作室", 26, 146);
        openStudio.Enabled = false;
        openStudio.BackColor = Color.FromArgb(233, 180, 113);
        openStudio.ForeColor = Color.FromArgb(22, 25, 31);
        openStudio.Click += delegate { Open(studioUrl); };
        var openFolder = MakeButton("打开数据目录", 159, 146);
        openFolder.Click += delegate { Open(workspace); };
        var stop = MakeButton("退出工作室", 292, 146);
        stop.Click += delegate { Close(); };
        mediaHelp = MakeButton("获取 FFmpeg", 26, 198);
        mediaHelp.Visible = false;
        mediaHelp.Click += delegate { Open("https://www.gyan.dev/ffmpeg/builds/"); };
        Controls.AddRange(new Control[] { title, status, openStudio, openFolder, stop, mediaHelp });
        Shown += delegate { StartService(); };
    }

    private static Button MakeButton(string text, int x, int y)
    {
        return new Button {
            Text = text, Location = new Point(x, y), Size = new Size(122, 36),
            FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(36, 41, 50)
        };
    }

    private void Open(string destination)
    {
        if (String.IsNullOrEmpty(destination)) return;
        try {
            Process.Start(new ProcessStartInfo(destination) { UseShellExecute = true });
        } catch (Exception) {
            status.Text = "无法打开。请在浏览器或资源管理器中打开 " + destination;
        }
    }

    private void OnUi(Action action)
    {
        if (!closing && IsHandleCreated) {
            try { BeginInvoke(action); } catch (InvalidOperationException) { }
        }
    }

    private void Log(string line)
    {
        try {
            lock (logLock) {
                File.AppendAllText(Path.Combine(workspace, "studio.log"), line + Environment.NewLine, Encoding.UTF8);
            }
        } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    private void StartService()
    {
        try {
            Directory.CreateDirectory(workspace);
            File.WriteAllText(Path.Combine(workspace, "studio.log"), "");
            string root = AppDomain.CurrentDomain.BaseDirectory;
            service = new Process();
            service.StartInfo = new ProcessStartInfo {
                FileName = Path.Combine(root, "runtime", "python.exe"),
                Arguments = "-B -u -m app.desktop --data-dir \"" + workspace.TrimEnd('\\') + "\" --port " + port,
                WorkingDirectory = Path.Combine(root, "backend"),
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            service.EnableRaisingEvents = true;
            service.OutputDataReceived += ServiceOutput;
            service.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) {
                if (e.Data != null) Log(e.Data);
            };
            service.Exited += delegate {
                // Wait for redirected status lines before reporting the cause.
                service.WaitForExit();
                OnUi(delegate {
                    openStudio.Enabled = false;
                    status.Text = startupError == "PORT_IN_USE"
                        ? "本地端口 " + port + " 已被占用。请关闭占用程序后重试。"
                        : "工作室已停止。可打开数据目录查看 studio.log 和 worker.log。";
                });
            };
            service.Start();
            service.BeginOutputReadLine();
            service.BeginErrorReadLine();
        } catch (Exception error) {
            Log(error.ToString());
            status.Text = "无法启动。请检查安装是否完整，或在数据目录查看 studio.log。";
        }
    }

    private void ServiceOutput(object sender, DataReceivedEventArgs e)
    {
        if (e.Data == null) return;
        Log(e.Data);
        if (e.Data == "AFDS_MEDIA_MISSING") missingMedia = true;
        if (e.Data.StartsWith("AFDS_ERROR ", StringComparison.Ordinal)) startupError = e.Data.Substring(11);
        string expected = "http://127.0.0.1:" + port;
        if (e.Data == "AFDS_READY " + expected) {
            OnUi(delegate {
                studioUrl = expected;
                openStudio.Enabled = true;
                mediaHelp.Visible = missingMedia;
                status.Text = missingMedia
                    ? "工作室已启动。安装 FFmpeg 并重启后即可生成和导出视频。"
                    : "工作室已就绪。关闭此窗口会停止本机服务。";
                if (openWhenReady) Open(studioUrl);
            });
        }
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        base.OnFormClosing(e);
        if (e.Cancel) return;
        closing = true;
        if (service == null) return;
        try {
            if (!service.HasExited) {
                service.StandardInput.Close();
                if (!service.WaitForExit(7000)) service.Kill();
            }
        } catch (InvalidOperationException) { } catch (IOException) { }
    }

    [STAThread]
    private static void Main(string[] args)
    {
        string workspace = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AFDS", "Workspace");
        int port = 8765;
        bool open = true;
        try {
            for (int i = 0; i < args.Length; i++) {
                if (args[i] == "--data-dir") workspace = Path.GetFullPath(args[++i]);
                else if (args[i] == "--port") port = Int32.Parse(args[++i]);
                else if (args[i] == "--no-browser") open = false;
                else throw new ArgumentException("Unknown argument: " + args[i]);
            }
            if (port < 1024 || port > 65535) throw new ArgumentException("Invalid port");
        } catch (Exception error) {
            MessageBox.Show(error.Message, "AFDS Studio");
            return;
        }
        bool ownsMutex;
        using (var instance = new Mutex(true, "Local\\AFDS.Studio.Active", out ownsMutex)) {
            if (!ownsMutex) {
                MessageBox.Show("工作室已经运行，请切换到 AFDS Studio 启动窗口。", "AFDS Studio");
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new StudioLauncher(workspace, port, open));
        }
    }
}
