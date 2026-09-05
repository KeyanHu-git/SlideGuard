#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    time::Duration,
};
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;

const MAX_REPLY: usize = 40 * 1024 * 1024;
fn read_frame(reader: &mut impl BufRead, limit: usize) -> Result<Vec<u8>, String> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().map_err(|e| e.to_string())?;
        if available.is_empty() {
            return Err("导出引擎意外退出".into());
        }
        let count = available
            .iter()
            .position(|b| *b == b'\n')
            .map(|n| n + 1)
            .unwrap_or(available.len());
        if line.len() + count > limit {
            return Err("引擎响应超过限制".into());
        }
        let complete = available[count - 1] == b'\n';
        line.extend_from_slice(&available[..count]);
        reader.consume(count);
        if complete {
            return Ok(line);
        }
    }
}
struct Worker {
    child: Child,
    input: ChildStdin,
    replies: mpsc::Receiver<Result<Vec<u8>, String>>,
    sequence: u64,
    failed: bool,
}
impl Worker {
    fn start() -> Result<Self, String> {
        let mut command;
        if cfg!(debug_assertions) {
            let python = std::env::var("SLIDEGUARD_WORKER_PYTHON")
                .map_err(|_| "开发环境未设置 SLIDEGUARD_WORKER_PYTHON")?;
            command = Command::new(python);
            command.args(["-m", "slideguard.desktop.server"]);
        } else {
            let executable = std::env::current_exe().map_err(|e| e.to_string())?;
            let worker = executable
                .parent()
                .ok_or("Missing application directory")?
                .join("worker")
                .join("slideguard-worker.exe");
            if !worker.is_file() {
                return Err("缺少导出引擎，请使用完整发行包".into());
            }
            command = Command::new(worker);
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        command.env("PYTHONUTF8", "1");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动导出引擎：{e}"))?;
        let input = child.stdin.take().ok_or("Missing worker input")?;
        let mut output = BufReader::new(child.stdout.take().ok_or("Missing worker output")?);
        let (sender, replies) = mpsc::sync_channel(0);
        std::thread::spawn(move || loop {
            let frame = read_frame(&mut output, MAX_REPLY);
            let failed = frame.is_err();
            if sender.send(frame).is_err() || failed {
                break;
            }
        });
        Ok(Self {
            child,
            input,
            replies,
            sequence: 0,
            failed: false,
        })
    }
    fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        if self.failed {
            return Err("导出引擎连接已中断，请重新打开应用；旧预览不能继续导出".into());
        }
        self.sequence += 1;
        let id = self.sequence.to_string();
        let message = json!({"version":1, "id":id, "method":method, "params":params});
        let encoded = serde_json::to_vec(&message).map_err(|e| e.to_string())?;
        if encoded.len() > 1024 * 1024 {
            return Err("请求过大".into());
        }
        let transport = (|| {
            self.input
                .write_all(&encoded)
                .and_then(|_| self.input.write_all(b"\n"))
                .and_then(|_| self.input.flush())
                .map_err(|e| e.to_string())?;
            let line = self
                .replies
                .recv_timeout(Duration::from_secs(30))
                .map_err(|_| "导出引擎 30 秒未响应或连接已关闭".to_string())??;
            let value: Value = serde_json::from_slice(&line).map_err(|e| e.to_string())?;
            if value["version"] != 1 || value["id"] != id || !value["ok"].is_boolean() {
                return Err("引擎响应标识或结构不匹配".into());
            }
            Ok(value)
        })();
        let value: Value = match transport {
            Ok(value) => value,
            Err(error) => {
                self.failed = true;
                let _ = self.child.kill();
                return Err(error);
            }
        };
        if value["ok"] != true {
            return Err(value["error"]["message"]
                .as_str()
                .unwrap_or("引擎请求失败")
                .into());
        }
        Ok(value["result"].clone())
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
#[derive(Clone, Default)]
struct Bridge(Arc<Mutex<Option<Worker>>>);
async fn request(bridge: Bridge, method: String, params: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut worker = bridge.0.lock().map_err(|_| "Worker lock poisoned")?;
        if worker.is_none() {
            *worker = Some(Worker::start()?);
        }
        let result = worker.as_mut().unwrap().call(&method, params);
        result
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frames_do_not_consume_the_next_response() {
        let mut input = std::io::Cursor::new(b"one\ntwo\n");
        assert_eq!(read_frame(&mut input, 4).unwrap(), b"one\n");
        assert_eq!(read_frame(&mut input, 4).unwrap(), b"two\n");
        assert!(read_frame(&mut input, 4).is_err());
    }
    #[test]
    fn incomplete_and_oversized_frames_fail() {
        assert!(read_frame(&mut std::io::Cursor::new(b"abc"), 4).is_err());
        assert!(read_frame(&mut std::io::Cursor::new(b"12345\n"), 5).is_err());
        assert!(read_frame(&mut std::io::Cursor::new(b"12345678"), 5).is_err());
    }
}
#[tauri::command]
async fn desktop_call(
    bridge: State<'_, Bridge>,
    method: String,
    params: Value,
) -> Result<Value, String> {
    // Input/output paths can only enter through native dialogs below.
    if ![
        "state", "page", "edit", "check", "export", "cancel", "asset", "verify",
    ]
    .contains(&method.as_str())
    {
        return Err("该操作不向界面开放".into());
    }
    request(bridge.inner().clone(), method, params).await
}
#[tauri::command]
async fn choose_input(app: tauri::AppHandle, bridge: State<'_, Bridge>) -> Result<Value, String> {
    let selected = tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .add_filter("PowerPoint", &["pptx"])
            .blocking_pick_file()
    })
    .await
    .map_err(|e| e.to_string())?;
    match selected {
        Some(path) => {
            request(
                bridge.inner().clone(),
                "open".into(),
                json!({"path":path.to_string()}),
            )
            .await
        }
        None => request(bridge.inner().clone(), "state".into(), json!({})).await,
    }
}
#[tauri::command]
async fn choose_output(app: tauri::AppHandle, bridge: State<'_, Bridge>) -> Result<Value, String> {
    let selected =
        tauri::async_runtime::spawn_blocking(move || app.dialog().file().blocking_pick_folder())
            .await
            .map_err(|e| e.to_string())?;
    match selected {
        Some(path) => {
            request(
                bridge.inner().clone(),
                "output".into(),
                json!({"path":path.to_string()}),
            )
            .await
        }
        None => request(bridge.inner().clone(), "state".into(), json!({})).await,
    }
}
#[tauri::command]
fn window_action(window: tauri::WebviewWindow, action: String) -> Result<(), String> {
    match action.as_str() {
        "minimize" => window.minimize(),
        "maximize" => {
            if window.is_maximized().unwrap_or(false) {
                window.unmaximize()
            } else {
                window.maximize()
            }
        }
        "close" => window.close(),
        _ => return Err("Unknown window action".into()),
    }
    .map_err(|e| e.to_string())
}
fn main() {
    tauri::Builder::default()
        .manage(Bridge::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            desktop_call,
            choose_input,
            choose_output,
            window_action
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let bridge = window.state::<Bridge>().inner().clone();
                let window = window.clone();
                tauri::async_runtime::spawn(async move {
                    if let Ok(state) = request(bridge.clone(), "state".into(), json!({})).await {
                        if state["busy"].as_str().unwrap_or("") != "" {
                            return;
                        }
                        let _ = request(bridge, "close".into(), json!({})).await;
                    }
                    let _ = window.destroy();
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("SlideGuard desktop runtime failed");
}
