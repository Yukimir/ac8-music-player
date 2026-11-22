#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod wallpaper;
use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use tauri::{
    AppHandle, CustomMenuItem, Manager, SystemTray, SystemTrayEvent, SystemTrayMenu,
    SystemTrayMenuItem, WindowEvent,
};

struct Worker {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl Worker {
    fn start(base: &Path) -> Result<Self, String> {
        let bundled = base.join("runtime/python.exe");
        let python = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join("Programs/Python/Python310/python.exe");
        let mut command = if bundled.is_file() {
            Command::new(bundled)
        } else if python.is_file() {
            Command::new(python)
        } else {
            Command::new("python.exe")
        };
        let mut child = command
            .args(["-B", "-u", "-X", "utf8"])
            .arg(base.join("app/bridge.py"))
            .current_dir(base)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .creation_flags(0x08000000)
            .spawn()
            .map_err(|e| format!("无法启动本机音频引擎：{e}"))?;
        let input = child.stdin.take().ok_or("无法连接音频引擎")?;
        let output = BufReader::new(child.stdout.take().ok_or("无法读取音频状态")?);
        Ok(Self {
            child,
            input,
            output,
        })
    }
    fn exchange(&mut self, message: &Value) -> Result<Value, String> {
        writeln!(self.input, "{message}")
            .and_then(|_| self.input.flush())
            .map_err(|e| e.to_string())?;
        let mut line = String::new();
        if self
            .output
            .read_line(&mut line)
            .map_err(|e| e.to_string())?
            == 0
        {
            return Err(
                "音频引擎已退出，请完整解压播放器，并检查 runtime、app 和 library 目录。".into(),
            );
        }
        let value: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        if let Some(error) = value.get("error").and_then(Value::as_str) {
            return Err(error.into());
        }
        Ok(value["result"].clone())
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = writeln!(self.input, "{{\"op\":\"quit\"}}");
        let _ = self.input.flush();
        // EOF is also observed if Windows terminates the parent.
        let _ = self.child.try_wait();
    }
}

struct AudioBridge {
    base: PathBuf,
    worker: Mutex<Option<Worker>>,
}

#[tauri::command]
async fn audio(request: Value, state: tauri::State<'_, AudioBridge>) -> Result<Value, String> {
    let mut guard = state.worker.lock().map_err(|_| "音频连接已中断")?;
    if guard.is_none() {
        *guard = Some(Worker::start(&state.base)?);
    }
    guard.as_mut().unwrap().exchange(&request)
}

fn show_player(app: &AppHandle) {
    if let Some(main) = app.get_window("main") {
        let _ = main.unminimize();
        let _ = main.show();
        let _ = main.set_focus();
    }
}

#[tauri::command]
fn player_window(app: AppHandle, action: &str) -> Result<(), String> {
    match action {
        "hide" => app
            .get_window("main")
            .ok_or("找不到主窗口")?
            .hide()
            .map_err(|e| e.to_string()),
        "show" => {
            show_player(&app);
            Ok(())
        }
        "exit" => {
            quit(&app);
            Ok(())
        }
        _ => Err("未知窗口操作".into()),
    }
}

fn quit(app: &AppHandle) {
    let audio = app.state::<AudioBridge>();
    if let Ok(mut worker) = audio.worker.try_lock() {
        worker.take();
    }
    app.exit(0);
}

fn main() {
    let base = std::env::current_exe()
        .expect("executable")
        .parent()
        .expect("directory")
        .to_owned();
    let menu = SystemTrayMenu::new()
        .add_item(CustomMenuItem::new("show", "打开播放器"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("play", "播放"))
        .add_item(CustomMenuItem::new("pause", "暂停"))
        .add_item(CustomMenuItem::new("stop", "停止"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("exit", "退出"));
    tauri::Builder::default()
        .system_tray(
            SystemTray::new()
                .with_menu(menu)
                .with_tooltip("AC8 音乐播放器"),
        )
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
                "show" => show_player(app),
                "exit" => quit(app),
                "play" | "pause" | "stop" => {
                    if let Some(main) = app.get_window("main") {
                        let op = if id == "play" {
                            "startPlayback"
                        } else {
                            id.as_str()
                        };
                        let _ = main.eval(&format!("window.trayPlayback?.('{op}')"));
                    }
                }
                _ => {}
            },
            SystemTrayEvent::LeftClick { .. } | SystemTrayEvent::DoubleClick { .. } => {
                show_player(app)
            }
            _ => {}
        })
        .on_window_event(|event| {
            let window = event.window();
            match event.event() {
                WindowEvent::CloseRequested { api, .. } if window.label() == "main" => {
                    api.prevent_close();
                    let _ = window.eval("document.getElementById('closePrompt')?.showModal()");
                }
                WindowEvent::Destroyed if window.label() == "wallpaper" => {
                    wallpaper::deactivated(&window.app_handle())
                }
                _ => {}
            }
        })
        .manage(wallpaper::DesktopMode::default())
        .manage(AudioBridge {
            base,
            worker: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            audio,
            player_window,
            wallpaper::set_wallpaper,
            wallpaper::desktop_active,
            wallpaper::fit_wallpaper
        ])
        .run(tauri::generate_context!())
        .expect("AC8 Music Player");
}
