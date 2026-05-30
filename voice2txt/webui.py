#!/usr/bin/env python3
"""
语音转文字 WebUI
基于阿里云百炼 Fun-ASR API，使用 Gradio 构建界面
支持：上传音频文件 / 浏览器麦克风录音 / 实时流式识别

运行方式:
  python start.py              # 推荐：跨平台快捷入口（默认 toggle）
  python -m voice2txt.webui    # 前台运行
  python -m voice2txt.webui -d # 后台运行，自动打开浏览器
  python -m voice2txt.webui -t # 开关切换
"""

import os
import sys
import io
import json
import time
import signal
import struct
import atexit
import argparse
import tempfile
import threading
import subprocess
from pathlib import Path

import dashscope
import gradio as gr
from http import HTTPStatus
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from voice2txt.polish import polish_text

IS_WINDOWS = sys.platform == "win32"
TARGET_RATE = 16000
API_MODEL = "fun-asr-realtime"
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(_PKG_DIR)
PID_FILE = os.path.join(ROOT_DIR, ".webui.pid")
LOG_FILE = os.path.join(ROOT_DIR, "webui.log")
API_KEY_FILE = os.path.join(ROOT_DIR, ".dashscope_api_key")
DEFAULT_PORT = 7860
DEFAULT_IDLE_TIMEOUT = 300


# ─── 空闲自动关闭 ───

class IdleWatchdog:
    """跟踪用户活动，空闲超时后自动关闭服务"""

    def __init__(self, timeout: int):
        self.timeout = timeout
        self.last_activity = time.time()
        self._stop = threading.Event()

    def touch(self):
        self.last_activity = time.time()

    def start(self):
        if self.timeout <= 0:
            return

        def _watch():
            while not self._stop.wait(15):
                idle = time.time() - self.last_activity
                if idle >= self.timeout:
                    safe_print(f"\n空闲 {int(idle)}s，自动关闭服务")
                    _cleanup_pid()
                    os._exit(0)

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()


idle_watchdog = IdleWatchdog(0)


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        file = kwargs.get("file", sys.stdout)
        flush = kwargs.get("flush", False)
        encoding = getattr(file, "encoding", None) or "utf-8"
        text = sep.join(str(arg) for arg in args)
        fallback = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        file.write(fallback + end)
        if flush:
            file.flush()


def with_activity(fn):
    """装饰器：调用时刷新空闲计时"""
    def wrapper(*args, **kwargs):
        idle_watchdog.touch()
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


# ─── 进程管理 ───

def _write_pid(port: int):
    payload = {
        "pid": os.getpid(),
        "port": port,
    }
    with open(PID_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    atexit.register(_cleanup_pid)


def _cleanup_pid():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def _win_process_exists(pid: int) -> bool:
    """Windows 上不能用 os.kill(pid,0) 可靠判断进程是否存在，会误报 WinError 87。"""
    if pid <= 0:
        return False
    import ctypes
    k = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if h:
        k.CloseHandle(h)
        return True
    if k.GetLastError() == 5:  # ERROR_ACCESS_DENIED，进程在但本进程无权限句柄
        return True
    return False


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _win_process_exists(pid)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError, PermissionError):
        return False
    return True


def _read_pid_file() -> dict | None:
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            _cleanup_pid()
            return None
    except FileNotFoundError:
        return None
    try:
        if raw.startswith("{"):
            data = json.loads(raw)
            pid = int(data["pid"])
            port = int(data.get("port", DEFAULT_PORT))
        else:
            pid = int(raw)
            port = DEFAULT_PORT
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _cleanup_pid()
        return None
    if pid <= 0 or port <= 0:
        _cleanup_pid()
        return None
    return {"pid": pid, "port": port}


def _read_process_commandline(pid: int) -> str:
    if pid <= 0:
        return ""
    if IS_WINDOWS:
        query = (
            f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
            "if ($p) { $p.CommandLine }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="ignore").strip()
    except OSError:
        return ""


def _port_is_listening(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _pid_matches_webui(pid_info: dict) -> bool:
    pid = pid_info["pid"]
    port = pid_info["port"]
    if not _pid_is_alive(pid):
        return False
    commandline = _read_process_commandline(pid).lower()
    if not commandline:
        return False
    if "voice2txt.webui" not in commandline and "webui.py" not in commandline:
        return False
    return _port_is_listening(port)


def _read_pid() -> dict | None:
    pid_info = _read_pid_file()
    if not pid_info:
        return None
    if not _pid_matches_webui(pid_info):
        _cleanup_pid()
        return None
    return pid_info


def _stop_service() -> bool:
    pid_info = _read_pid()
    if pid_info is None:
        return False
    pid = pid_info["pid"]
    if IS_WINDOWS:
        os.kill(pid, signal.SIGBREAK)
    else:
        os.kill(pid, signal.SIGTERM)
    _cleanup_pid()
    return True


def _cmd_stop():
    if not _stop_service():
        safe_print("没有找到运行中的 WebUI 进程")
        sys.exit(1)
    safe_print("已停止 WebUI")
    sys.exit(0)


def _cmd_open():
    pid_info = _read_pid()
    if pid_info is None:
        safe_print("WebUI 未运行。启动: python start.py")
        sys.exit(1)
    import webbrowser
    url = f"http://localhost:{pid_info['port']}"
    webbrowser.open(url)
    safe_print(f"已打开 {url}")
    sys.exit(0)


def _cmd_toggle(port: int):
    existing = _read_pid()
    if existing:
        if _stop_service():
            safe_print(f"已关闭 WebUI (原 PID: {existing['pid']})")
        else:
            safe_print("关闭 WebUI 失败")
        sys.exit(0)
    _daemonize(port)


def _cmd_status():
    pid_info = _read_pid()
    if pid_info:
        safe_print(f"WebUI 正在运行 (PID: {pid_info['pid']})")
        safe_print(f"访问: http://localhost:{pid_info['port']}")
    else:
        safe_print("WebUI 未运行")
    sys.exit(0)


def _daemonize(port: int):
    if IS_WINDOWS:
        import webbrowser
        log = open(LOG_FILE, "a")
        proc = subprocess.Popen(
            [sys.executable, "-m", "voice2txt.webui", "--port", str(port)],
            stdout=log, stderr=log,
            cwd=ROOT_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        safe_print(f"WebUI 已在后台启动 (PID: {proc.pid})")
        safe_print(f"访问: http://localhost:{port}")
        safe_print("关闭: python start.py")
        time.sleep(2)
        webbrowser.open(f"http://localhost:{port}")
        sys.exit(0)
    else:
        pid = os.fork()
        if pid > 0:
            safe_print(f"WebUI 已在后台启动 (PID: {pid})")
            safe_print(f"访问: http://localhost:{port}")
            safe_print("关闭: python start.py")

            import webbrowser
            time.sleep(1)
            webbrowser.open(f"http://localhost:{port}")
            sys.exit(0)
        os.setsid()
        with open(LOG_FILE, "a") as log:
            os.dup2(log.fileno(), sys.stdout.fileno())
            os.dup2(log.fileno(), sys.stderr.fileno())


def get_api_key():
    env_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return Path(API_KEY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_api_key(api_key: str):
    key = (api_key or "").strip()
    if not key:
        return
    try:
        Path(API_KEY_FILE).write_text(key, encoding="utf-8")
    except OSError as e:
        safe_print(f"保存 API Key 失败: {e}")


def init_dashscope(api_key: str):
    dashscope.api_key = api_key
    dashscope.base_websocket_api_url = (
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )


def audio_to_pcm16(audio_path: str) -> bytes:
    """用 ffmpeg 将任意音频转为 16kHz 单声道 PCM16"""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ac", "1", "-ar", str(TARGET_RATE),
            "-"
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {result.stderr.decode()}")
    return result.stdout


# ─── 功能1：上传音频文件识别 ───

@with_activity
def transcribe_file(audio_path, api_key, model, language, enable_polish=False):
    """返回 (原始识别文本, 润色文本)，润色未启用时第二项为空"""
    if not api_key:
        return "❌ 请先填写 API Key", ""
    save_api_key(api_key)
    if not audio_path:
        return "❌ 请先上传音频文件", ""

    init_dashscope(api_key)

    try:
        pcm_data = audio_to_pcm16(audio_path)
    except Exception as e:
        return f"❌ 音频转码失败: {e}", ""

    sentences = []
    done_event = threading.Event()
    error_msg = []

    class FileCallback(RecognitionCallback):
        def on_complete(self) -> None:
            done_event.set()

        def on_error(self, message) -> None:
            error_msg.append(f"❌ API 错误: {message.message}")
            done_event.set()

        def on_event(self, result: RecognitionResult) -> None:
            sentence = result.get_sentence()
            if "text" in sentence and RecognitionResult.is_sentence_end(sentence):
                sentences.append(sentence["text"])

    callback = FileCallback()
    recognition = Recognition(
        model=model,
        format="pcm",
        sample_rate=TARGET_RATE,
        callback=callback,
    )

    start_time = time.time()
    recognition.start()

    chunk_size = 3200
    offset = 0
    while offset < len(pcm_data):
        end = min(offset + chunk_size, len(pcm_data))
        recognition.send_audio_frame(pcm_data[offset:end])
        offset = end
        time.sleep(0.02)

    recognition.stop()
    done_event.wait(timeout=30)
    elapsed = time.time() - start_time

    if error_msg:
        return error_msg[0], ""

    audio_duration = len(pcm_data) / (TARGET_RATE * 2)
    full_text = "".join(sentences)
    stats = f"\n\n---\n⏱️ 音频时长: {audio_duration:.1f}s | 处理耗时: {elapsed:.1f}s | RTF: {elapsed/max(audio_duration, 0.1):.2f}x"
    raw_result = full_text + stats

    if enable_polish and full_text.strip():
        polished = polish_text(full_text, api_key)
        return raw_result, polished

    return raw_result, ""


# ─── 功能2：浏览器麦克风录音识别 ───

@with_activity
def transcribe_mic(audio_data, api_key, model, language, enable_polish=False):
    """返回 (原始识别文本, 润色文本)"""
    if not api_key:
        return "❌ 请先填写 API Key", ""
    if audio_data is None:
        return "❌ 请先录音", ""

    sample_rate, samples = audio_data

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        import wave
        wf = wave.open(f.name, "wb")
        wf.setnchannels(1 if len(samples.shape) == 1 else samples.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if samples.dtype != "int16":
            import numpy as np
            if samples.dtype in ("float32", "float64"):
                samples = (samples * 32767).astype(np.int16)
            else:
                samples = samples.astype(np.int16)
        wf.writeframes(samples.tobytes())
        wf.close()
        tmp_path = f.name

    try:
        result = transcribe_file(tmp_path, api_key, model, language, enable_polish)
    finally:
        os.unlink(tmp_path)

    return result


# ─── 功能3：实时流式识别（系统麦克风） ───


class MicRecorder:
    """麦克风录音抽象层，根据平台自动选择后端"""

    def open(self):
        raise NotImplementedError

    def read(self) -> bytes:
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class PyAudioRecorder(MicRecorder):
    """Windows / macOS: 使用 pyaudio 直接采集 16kHz PCM"""

    def __init__(self):
        import pyaudio
        self._pyaudio = pyaudio
        self._pa = None
        self._stream = None
        self._chunk = int(TARGET_RATE * 0.1)

    def open(self):
        self._pa = self._pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=self._pyaudio.paInt16,
            channels=1,
            rate=TARGET_RATE,
            input=True,
            frames_per_buffer=self._chunk,
        )

    def read(self) -> bytes:
        return self._stream.read(self._chunk, exception_on_overflow=False)

    def close(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None


class PipeWireRecorder(MicRecorder):
    """Linux: 使用 pw-record 采集 48kHz 后降采样到 16kHz"""

    def __init__(self):
        self._proc = None
        self._mic_rate = 48000
        self._chunk_bytes = int(self._mic_rate * 0.1) * 2
        self._ratio = self._mic_rate / TARGET_RATE

    def open(self):
        self._proc = subprocess.Popen(
            ["pw-record", "--format", "s16", "--rate", "48000", "--channels", "1", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def read(self) -> bytes:
        data = self._proc.stdout.read(self._chunk_bytes)
        if not data:
            return b""
        samples = struct.unpack(f"<{len(data) // 2}h", data)
        out = []
        pos = 0.0
        while int(pos) < len(samples):
            out.append(samples[int(pos)])
            pos += self._ratio
        return struct.pack(f"<{len(out)}h", *out)

    def close(self):
        if self._proc:
            self._proc.terminate()
            self._proc.wait()
            self._proc = None


def _create_mic_recorder() -> MicRecorder:
    if IS_WINDOWS:
        return PyAudioRecorder()
    return PipeWireRecorder()


class RealtimeSession:
    def __init__(self):
        self.recognition = None
        self.recorder: MicRecorder | None = None
        self.running = False
        self.sentences = []
        self.current_text = ""
        self.lock = threading.Lock()

    def start(self, api_key, model):
        if self.running:
            return
        init_dashscope(api_key)
        self.sentences = []
        self.current_text = ""
        self.running = True

        session = self

        class StreamCallback(RecognitionCallback):
            def on_open(self) -> None:
                session.recorder = _create_mic_recorder()
                session.recorder.open()

            def on_close(self) -> None:
                if session.recorder:
                    session.recorder.close()
                    session.recorder = None

            def on_complete(self) -> None:
                pass

            def on_error(self, message) -> None:
                with session.lock:
                    session.current_text = f"❌ 错误: {message.message}"
                session.running = False

            def on_event(self, result: RecognitionResult) -> None:
                sentence = result.get_sentence()
                if "text" not in sentence:
                    return
                with session.lock:
                    if RecognitionResult.is_sentence_end(sentence):
                        session.sentences.append(sentence["text"])
                        session.current_text = ""
                    else:
                        session.current_text = sentence["text"]

        self.recognition = Recognition(
            model=model, format="pcm", sample_rate=TARGET_RATE,
            semantic_punctuation_enabled=False, callback=StreamCallback(),
        )
        self.recognition.start()

        def feed_audio():
            while session.running and session.recorder:
                try:
                    data = session.recorder.read()
                except Exception:
                    break
                if not data:
                    break
                try:
                    session.recognition.send_audio_frame(data)
                except Exception:
                    break

        self.feed_thread = threading.Thread(target=feed_audio, daemon=True)
        self.feed_thread.start()

    def stop(self):
        self.running = False
        if self.recognition:
            try:
                self.recognition.stop()
            except Exception:
                pass
            self.recognition = None

    def get_text(self):
        with self.lock:
            lines = list(self.sentences)
            if self.current_text:
                lines.append(f"🔵 {self.current_text}")
            return "\n".join(lines) if lines else "（等待语音输入...）"


realtime_session = RealtimeSession()


@with_activity
def start_realtime(api_key, model):
    if not api_key:
        return "❌ 请先填写 API Key", "", gr.update(interactive=False), gr.update(interactive=True)
    save_api_key(api_key)
    realtime_session.start(api_key, model)
    return "🎤 正在录音...", "", gr.update(interactive=True), gr.update(interactive=False)


@with_activity
def stop_realtime(api_key="", enable_polish=False):
    realtime_session.stop()
    raw = realtime_session.get_text()
    polished = ""
    if enable_polish and raw.strip() and not raw.startswith("（"):
        polished = polish_text(raw, api_key)
    return raw, polished, gr.update(interactive=False), gr.update(interactive=True)


@with_activity
def poll_realtime():
    return realtime_session.get_text()


# ─── UI 构建 ───

MODELS = [
    "fun-asr-realtime",
    "fun-asr-realtime-2026-02-28",
    "fun-asr-realtime-2025-11-07",
    "paraformer-realtime-v2",
]

LANGUAGES = ["auto", "zh", "en", "ja"]

# 复制时去掉统计行、实时预览前缀，并弹出轻提示（便于粘贴到 AI 对话）
COPY_JS = """
(text) => {
  const strip = (s) => {
    let t = (s || "").trim();
    const marker = "\\n\\n---\\n⏱️";
    const i = t.indexOf(marker);
    if (i >= 0) t = t.slice(0, i).trim();
    t = t.split("\\n").map((l) => (l.startsWith("🔵 ") ? l.slice(3) : l)).join("\\n");
    if (t === "（等待语音输入...）") t = "";
    return t;
  };
  const v = strip(text);
  navigator.clipboard.writeText(v);
  const el = document.createElement("div");
  el.textContent = v ? "已复制到剪贴板" : "没有可复制的内容";
  Object.assign(el.style, {
    position: "fixed", bottom: "24px", left: "50%", transform: "translateX(-50%)",
    background: "#1f2937", color: "#fff", padding: "10px 18px", borderRadius: "8px",
    zIndex: 99999, fontSize: "14px", boxShadow: "0 4px 12px rgba(0,0,0,.25)",
  });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1600);
}
"""

COPY_PRIMARY_JS = """
(polished, raw, auto) => {
  if (!auto) return;
  const strip = (s) => {
    let t = (s || "").trim();
    const marker = "\\n\\n---\\n⏱️";
    const i = t.indexOf(marker);
    if (i >= 0) t = t.slice(0, i).trim();
    t = t.split("\\n").map((l) => (l.startsWith("🔵 ") ? l.slice(3) : l)).join("\\n");
    if (t === "（等待语音输入...）") t = "";
    return t;
  };
  const v = strip(polished) || strip(raw);
  if (!v) return;
  navigator.clipboard.writeText(v);
  const el = document.createElement("div");
  el.textContent = "已复制主结果（润色优先）";
  Object.assign(el.style, {
    position: "fixed", bottom: "24px", left: "50%", transform: "translateX(-50%)",
    background: "#1f2937", color: "#fff", padding: "10px 18px", borderRadius: "8px",
    zIndex: 99999, fontSize: "14px", boxShadow: "0 4px 12px rgba(0,0,0,.25)",
  });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1600);
}
"""

TEXTBOX_KW = {"lines": 10, "elem_classes": ["result-box"]}

CSS = """
.result-box { min-height: 220px; font-size: 15px !important; line-height: 1.55 !important; }
.result-box textarea { font-family: ui-monospace, "Cascadia Code", Consolas, monospace !important; }
.copy-btn { min-height: 44px !important; font-size: 15px !important; }
.copy-primary-btn { min-height: 48px !important; font-weight: 600 !important; }
footer { display: none !important; }
"""


def _copy_buttons(raw_output, polished_output):
    """为每个 Tab 生成复制按钮行（含一键复制主结果）。"""
    with gr.Row():
        copy_raw = gr.Button("📋 复制原文", scale=1)
        copy_pol = gr.Button("📋 复制润色", scale=1)
        copy_primary = gr.Button(
            "📋 一键复制主结果",
            variant="primary",
            scale=2,
            elem_classes=["copy-primary-btn"],
        )
    copy_raw.click(fn=None, inputs=[raw_output], js=COPY_JS)
    copy_pol.click(fn=None, inputs=[polished_output], js=COPY_JS)
    copy_primary.click(
        fn=None,
        inputs=[polished_output, raw_output],
        js="(p, r) => { const strip = (s) => { let t = (s||'').trim(); const m='\\n\\n---\\n⏱️'; const i=t.indexOf(m); if(i>=0)t=t.slice(0,i).trim(); t=t.split('\\n').map(l=>l.startsWith('🔵 ')?l.slice(3):l).join('\\n'); if(t==='（等待语音输入...）')t=''; return t; }; const v=strip(p)||strip(r); if(!v)return; navigator.clipboard.writeText(v); const el=document.createElement('div'); el.textContent='已复制主结果（润色优先）'; Object.assign(el.style,{position:'fixed',bottom:'24px',left:'50%',transform:'translateX(-50%)',background:'#1f2937',color:'#fff',padding:'10px 18px',borderRadius:'8px',zIndex:99999,fontSize:'14px'}); document.body.appendChild(el); setTimeout(()=>el.remove(),1600); }",
    )


def build_ui():
    with gr.Blocks(title="语音转文字") as demo:
        gr.Markdown(
            "# 🎙️ 语音转文字\n"
            "基于阿里云百炼 Fun-ASR · 日常建议用 **实时识别** → 停止后自动复制，粘贴到 AI 对话\n\n"
            "快捷命令：`python start.py` 开关服务 · `python start.py open` 只打开页面"
        )

        with gr.Row():
            api_key_input = gr.Textbox(
                label="API Key",
                value=get_api_key(),
                type="password",
                placeholder="sk-xxx",
                scale=3,
            )
            model_input = gr.Dropdown(
                label="模型", choices=MODELS, value=MODELS[0], scale=2,
            )
            lang_input = gr.Dropdown(
                label="语言", choices=LANGUAGES, value="auto", scale=1,
            )
            polish_toggle = gr.Checkbox(
                label="✨ 润色",
                value=True,
                info="去口头禅；复制时优先用润色结果",
                scale=1,
            )

        with gr.Tabs():
            # Tab 1（默认）: 实时流式 — 日常口述 / AI 输入主场景
            with gr.Tab("⚡ 实时识别"):
                gr.Markdown(
                    "系统麦克风边说边出字。**停止** 后可自动复制主结果；也可随时点「一键复制」。"
                )
                with gr.Row():
                    start_btn = gr.Button("🎤 开始", variant="primary", scale=2)
                    stop_btn = gr.Button("⏹ 停止并复制", interactive=False, scale=2)
                    refresh_btn = gr.Button("🔄 刷新", scale=1)
                    auto_copy_toggle = gr.Checkbox(
                        label="停止后自动复制",
                        value=True,
                        scale=2,
                    )
                with gr.Row():
                    realtime_raw_output = gr.Textbox(
                        label="原始识别", **{**TEXTBOX_KW, "lines": 14},
                    )
                    realtime_polished_output = gr.Textbox(
                        label="✨ 润色结果（复制优先）", **{**TEXTBOX_KW, "lines": 14},
                    )
                _copy_buttons(realtime_raw_output, realtime_polished_output)

                start_btn.click(
                    start_realtime,
                    inputs=[api_key_input, model_input],
                    outputs=[realtime_raw_output, realtime_polished_output, stop_btn, start_btn],
                )
                stop_btn.click(
                    stop_realtime,
                    inputs=[api_key_input, polish_toggle],
                    outputs=[realtime_raw_output, realtime_polished_output, stop_btn, start_btn],
                ).then(
                    fn=None,
                    inputs=[realtime_polished_output, realtime_raw_output, auto_copy_toggle],
                    js=COPY_PRIMARY_JS,
                )
                refresh_btn.click(
                    poll_realtime,
                    outputs=realtime_raw_output,
                )

            with gr.Tab("🎤 浏览器录音"):
                mic_input = gr.Audio(
                    label="点击录音按钮开始说话",
                    sources=["microphone"],
                    type="numpy",
                )
                mic_btn = gr.Button("识别录音", variant="primary")
                with gr.Row():
                    mic_raw_output = gr.Textbox(label="原始识别", **TEXTBOX_KW)
                    mic_polished_output = gr.Textbox(label="✨ 润色结果", **TEXTBOX_KW)
                _copy_buttons(mic_raw_output, mic_polished_output)
                mic_btn.click(
                    transcribe_mic,
                    inputs=[mic_input, api_key_input, model_input, lang_input, polish_toggle],
                    outputs=[mic_raw_output, mic_polished_output],
                )

            with gr.Tab("📁 上传文件"):
                file_input = gr.Audio(
                    label="上传音频（支持 wav/mp3/m4a/flac 等）",
                    type="filepath",
                )
                file_btn = gr.Button("开始识别", variant="primary")
                with gr.Row():
                    file_raw_output = gr.Textbox(label="原始识别", **TEXTBOX_KW)
                    file_polished_output = gr.Textbox(label="✨ 润色结果", **TEXTBOX_KW)
                _copy_buttons(file_raw_output, file_polished_output)
                file_btn.click(
                    transcribe_file,
                    inputs=[file_input, api_key_input, model_input, lang_input, polish_toggle],
                    outputs=[file_raw_output, file_polished_output],
                )

    return demo


def parse_args():
    parser = argparse.ArgumentParser(description="语音转文字 WebUI")
    parser.add_argument(
        "-d", "--daemon", action="store_true",
        help="后台运行，自动打开浏览器",
    )
    parser.add_argument(
        "-t", "--toggle", action="store_true",
        help="开关切换：已运行则停止，未运行则后台启动",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="仅打开浏览器（需 WebUI 已在运行）",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="停止后台运行的 WebUI 进程",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="查看 WebUI 运行状态",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"监听端口 (默认: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--idle-timeout", type=int, default=None,
        help=f"空闲自动关闭秒数，0 为禁用 (后台模式默认: {DEFAULT_IDLE_TIMEOUT}s)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.stop:
        _cmd_stop()
    if args.status:
        _cmd_status()
    if args.open:
        _cmd_open()
    if args.toggle:
        _cmd_toggle(args.port)

    existing = _read_pid()
    if existing:
        safe_print(
            f"WebUI 已在运行 (PID: {existing['pid']}, 端口: {existing['port']})"
        )
        safe_print("  打开页面: python start.py open")
        safe_print("  关闭服务: python start.py stop")
        sys.exit(1)

    timeout = args.idle_timeout
    if timeout is None:
        timeout = DEFAULT_IDLE_TIMEOUT if args.daemon else 0

    if args.daemon:
        _daemonize(args.port)

    _write_pid(args.port)
    idle_watchdog.timeout = timeout
    idle_watchdog.start()

    if timeout > 0:
        safe_print(f"空闲自动关闭: {timeout}s")

    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=args.port,
        theme=gr.themes.Soft(),
        css=CSS,
        quiet=args.daemon,
    )
