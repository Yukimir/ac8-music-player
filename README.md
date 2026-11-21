# ACE COMBAT 8 互动音乐播放器

在 Windows 上按游戏的段落、循环点和分轨播放《皇牌空战 8》配乐。播放器记录每首曲子上次选择的编排，支持按遍数和小节切换分轨，也可以在自由编排中手动排队或立即切段。

## 使用

完整解压播放器发行 ZIP，运行 `皇牌空战8音乐播放器.exe`。首次启动会寻找本机 Steam 游戏库，也可以手动选择游戏安装目录。导入使用约 2.9 GiB 本地缓存，完成后可离线播放。当前索引对应 Steam App 2288340、构建 26002268，包含 187 首曲目，其中 76 首支持互动编排。

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
```

第二项检查覆盖分轨自动化、循环、过渡和全部默认编排。若要逐采样验证《Shadow 22》的循环，将发行版解压目录及本机已导入的音频库传入测试。

```powershell
python tests/check_arrangements.py --player "D:\AC8 Player" --audio-library "D:\AC8 Audio Cache"
```
