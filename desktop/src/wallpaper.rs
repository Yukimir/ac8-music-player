use std::ptr::null;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, Window, WindowBuilder, WindowUrl};
use windows_sys::w;
use windows_sys::Win32::Foundation::{GetLastError, SetLastError, HWND, LPARAM, RECT};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

#[derive(Default)]
pub struct DesktopMode {
    pub active: AtomicBool,
}

unsafe fn child(parent: HWND, class: *const u16) -> HWND {
    FindWindowExW(parent, 0, class, null())
}

unsafe extern "system" fn find_classic_host(window: HWND, result: LPARAM) -> i32 {
    if child(window, w!("SHELLDLL_DefView")) != 0 {
        let host = FindWindowExW(0, window, w!("WorkerW"), null());
        if host != 0 {
            *(result as *mut HWND) = host;
            return 0;
        }
    }
    1
}

// Modern Explorer puts the layered icon view and the wallpaper WorkerW under
// Progman. On older builds the wallpaper host is a sibling of the icon host.
unsafe fn host() -> Option<(HWND, HWND)> {
    let shell = GetShellWindow();
    if shell == 0 {
        return None;
    }
    let icons = child(shell, w!("SHELLDLL_DefView"));
    if icons != 0 && child(shell, w!("WorkerW")) != 0 {
        return Some((shell, icons));
    }
    let mut worker = 0;
    EnumWindows(Some(find_classic_host), &mut worker as *mut HWND as LPARAM);
    if worker != 0 {
        Some((worker, HWND_BOTTOM))
    } else {
        None
    }
}

fn last_error() -> String {
    std::io::Error::last_os_error().to_string()
}

unsafe fn set_style(window: HWND, index: i32, style: u32) -> Result<(), String> {
    SetLastError(0);
    if SetWindowLongW(window, index, style as i32) == 0 && GetLastError() != 0 {
        return Err(last_error());
    }
    Ok(())
}

fn place(window: HWND, initial: bool) -> Result<(), String> {
    unsafe {
        let mut target = host();
        if initial && target.is_none() {
            let shell = GetShellWindow();
            if shell != 0 {
                SendMessageTimeoutW(
                    shell,
                    0x052C,
                    0xD,
                    1,
                    SMTO_ABORTIFHUNG,
                    800,
                    std::ptr::null_mut(),
                );
                target = host();
            }
        }
        let (parent, after) = target.ok_or("未找到 Windows 桌面图层，请稍后重试。")?;
        let mut area: RECT = std::mem::zeroed();
        if GetClientRect(parent, &mut area) == 0 || area.right <= 0 || area.bottom <= 0 {
            return Err("无法读取桌面尺寸".into());
        }
        let reparent = GetParent(window) != parent;
        if initial || reparent {
            set_style(
                window,
                GWL_STYLE,
                ((GetWindowLongW(window, GWL_STYLE) as u32) & !(WS_POPUP | WS_OVERLAPPEDWINDOW))
                    | WS_CHILD
                    | WS_DISABLED,
            )?;
            let mut ex = (GetWindowLongW(window, GWL_EXSTYLE) as u32 & !WS_EX_APPWINDOW)
                | WS_EX_NOACTIVATE
                | WS_EX_TOOLWINDOW
                | WS_EX_TRANSPARENT;
            if after != HWND_BOTTOM {
                ex |= WS_EX_LAYERED;
            }
            set_style(window, GWL_EXSTYLE, ex)?;
            if ex & WS_EX_LAYERED != 0 && SetLayeredWindowAttributes(window, 0, 255, LWA_ALPHA) == 0
            {
                return Err(last_error());
            }
            if reparent {
                SetLastError(0);
                if SetParent(window, parent) == 0 && GetLastError() != 0 {
                    return Err(last_error());
                }
            }
        } else {
            let mut current: RECT = std::mem::zeroed();
            if GetClientRect(window, &mut current) != 0
                && current.right == area.right
                && current.bottom == area.bottom
                && (after == HWND_BOTTOM || GetWindow(window, GW_HWNDPREV) == after)
            {
                return Ok(());
            }
        }
        if SetWindowPos(
            window,
            after,
            0,
            0,
            area.right,
            area.bottom,
            SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        ) == 0
        {
            return Err(last_error());
        }
        Ok(())
    }
}

#[tauri::command]
pub async fn set_wallpaper(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let wall = match app.get_window("wallpaper") {
        Some(w) => w,
        None if !enabled => return Ok(false),
        None => WindowBuilder::new(&app, "wallpaper", WindowUrl::App("wallpaper.html".into()))
            .title("AC8 沙盘壁纸")
            .visible(false)
            .focused(false)
            .decorations(false)
            .skip_taskbar(true)
            .build()
            .map_err(|e| e.to_string())?,
    };
    let hwnd = wall.hwnd().map_err(|e| e.to_string())?.0;
    let (tx, rx) = std::sync::mpsc::channel();
    wall.run_on_main_thread(move || {
        // Explorer owns the parent. Match the native show in place() with a
        // native hide even when the framework still considers this window hidden.
        let result = if enabled {
            place(hwnd, true)
        } else {
            unsafe {
                ShowWindow(hwnd, SW_HIDE);
            }
            Ok(())
        };
        if result.is_ok() {
            app.state::<DesktopMode>()
                .active
                .store(enabled, Ordering::Release);
        }
        let _ = tx.send(result);
    })
    .map_err(|e| e.to_string())?;
    rx.recv().map_err(|e| e.to_string())??;
    // Signals a previously hidden page immediately. A newly loaded page also
    // queries desktop_active itself so it cannot miss the first activation.
    let _ = wall.eval(if enabled {
        "window.wakeWallpaper?.()"
    } else {
        "window.sleepWallpaper?.()"
    });
    Ok(enabled)
}

#[tauri::command]
pub fn desktop_active(state: tauri::State<DesktopMode>) -> bool {
    state.active.load(Ordering::Acquire)
}

#[tauri::command]
pub async fn fit_wallpaper(window: Window) -> Result<(), String> {
    if window.label() != "wallpaper"
        || !window.state::<DesktopMode>().active.load(Ordering::Acquire)
    {
        return Ok(());
    }
    let hwnd = window.hwnd().map_err(|e| e.to_string())?.0;
    let app = window.app_handle();
    window
        .run_on_main_thread(move || {
            if app.state::<DesktopMode>().active.load(Ordering::Acquire)
                && place(hwnd, false).is_err()
            {
                if let Some(w) = app.get_window("wallpaper") {
                    unsafe {
                        ShowWindow(hwnd, SW_HIDE);
                    }
                    let _ = w.eval("window.sleepWallpaper?.()");
                }
                deactivated(&app);
            }
        })
        .map_err(|e| e.to_string())
}

pub fn deactivated(app: &AppHandle) {
    if app
        .state::<DesktopMode>()
        .active
        .swap(false, Ordering::AcqRel)
    {
        if let Some(main) = app.get_window("main") {
            let _ = main.eval("window.desktopModeChanged?.(false)");
        }
    }
}
