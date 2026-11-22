# ACE COMBAT 8 互动音乐播放器

在 Windows 上按游戏的段落、循环点和分轨播放《皇牌空战 8》配乐。播放器记录每首曲子上次选择的编排，支持按遍数和小节切换分轨，也可以在自由编排中手动排队或立即切段。

## 使用

完整解压 ZIP，运行 `皇牌空战8音乐播放器.exe`，确认自动找到的 Steam 安装目录或手动选择。默认直接读取游戏，只临时解码当前曲目和启用的音效。右上角的“设置”可以更改目录，也可以提取音频供离线播放。基础配乐和座舱音效约占 2.9 GiB，另选全部原生 7.1.4 素材约增加 3.9 GiB。播放器优先使用程序旁 `audio` 中已提取的文件，缺少时再从游戏读取。提取的素材可以随播放器整个目录搬走。

当前索引对应 Steam App 2288340、构建 26002268。曲库分为 76 首互动配乐和 111 首单曲。点击播放模式按钮或按 `T`，可以切换顺序播放、随机播放和单曲循环。选择单曲循环后，互动配乐会在当前编排放完时从头重播。

播放器自动匹配 Windows 默认设备支持的输出布局。右下角的 `5.1（混音）` 表示当前曲目的分轨扩展为 5.1，`5.1（原生）` 表示原生多声道素材折叠至设备的 5.1。7.1.4 输出会保留这类素材的十二个独立声道，包括原有的 LFE。

在 Windows 上，右上角的显示器按钮可把频谱沙盘放到桌面图标后面。开启后，沙盘只在桌面绘制，旁边的运镜按钮也控制桌面视角。再点一次即可恢复原桌面。点击窗口的 × 可以选择退出或隐藏到系统托盘。左键点击托盘图标可恢复窗口，右键菜单可播放、暂停、停止或退出。暂停保留进度，停止回到当前曲子或编排的开头。

## 从源码构建 Windows x64 发行包

使用 Windows x64、Rust MSVC 工具链、Visual Studio C++ Build Tools（含 Windows SDK）和 Python 3.11 或更高版本。运行桌面界面需要 Microsoft Edge WebView2 Runtime。

```powershell
python scripts/build.py
```

脚本使用 `desktop/Cargo.lock` 构建 Tauri 程序，下载并校验固定版本的便携 Python、NumPy 和解码器，再将发行 ZIP 写入 `dist/`。构建机无需安装游戏。已有完整依赖缓存时，可运行 `python scripts/build.py --offline`。接收者使用随包提供的 Python 运行环境。

## 项目目录

- `desktop/` 是 Tauri 外壳和 HTML、CSS、JavaScript 界面，包括频谱沙盘。
- `app/` 实现播放、编排、本机游戏导入和座舱音效。桌面入口使用 `bridge.py`。
- `library/` 保存曲库、默认编排和导入索引。游戏音频由接收者从自己的安装目录提取。
- `scripts/` 包含发行构建脚本。维护曲库时可使用 `scripts/metadata/` 中的工具和本机准备的音库元数据。
- `notices/` 保存随发行包提供的第三方组件说明。

## 检查

```powershell
node tests/check_import_ui.js
python -m pip install numpy
python tests/check_arrangements.py
python tests/check_lfe.py
```

第二项检查覆盖分轨自动化、循环、过渡和全部默认编排。若要逐采样验证《Shadow 22》的循环，将发行版解压目录及本机已导入的音频库传入测试。

```powershell
python tests/check_arrangements.py --player "D:\AC8 Player" --audio-library "D:\AC8 Audio Cache"
```
