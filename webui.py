#!/usr/bin/env python3
"""
语音转文字 WebUI
基于阿里云百炼 Fun-ASR API，使用 Gradio 构建界面
支持：上传音频文件 / 浏览器麦克风录音 / 实时流式识别
"""

import os
import io
import time
import struct
import tempfile
import threading
import subprocess

import dashscope
import gradio as gr
from http import HTTPStatus
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

TARGET_RATE = 16000
API_MODEL = "fun-asr-realtime"


def get_api_key():
    return os.environ.get("DASHSCOPE_API_KEY", "")


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

def transcribe_file(audio_path, api_key, model, language):
    if not api_key:
        return "❌ 请先填写 API Key"
    if not audio_path:
        return "❌ 请先上传音频文件"

    init_dashscope(api_key)

    try:
        pcm_data = audio_to_pcm16(audio_path)
    except Exception as e:
        return f"❌ 音频转码失败: {e}"

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
        return error_msg[0]

    audio_duration = len(pcm_data) / (TARGET_RATE * 2)
    full_text = "".join(sentences)
    stats = f"\n\n---\n⏱️ 音频时长: {audio_duration:.1f}s | 处理耗时: {elapsed:.1f}s | RTF: {elapsed/max(audio_duration, 0.1):.2f}x"
    return full_text + stats


# ─── 功能2：浏览器麦克风录音识别 ───

def transcribe_mic(audio_data, api_key, model, language):
    if not api_key:
        return "❌ 请先填写 API Key"
    if audio_data is None:
        return "❌ 请先录音"

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
        result = transcribe_file(tmp_path, api_key, model, language)
    finally:
        os.unlink(tmp_path)

    return result


# ─── 功能3：实时流式识别（PipeWire 麦克风） ───

class RealtimeSession:
    def __init__(self):
        self.recognition = None
        self.recorder = None
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
                session.recorder = subprocess.Popen(
                    ["pw-record", "--format", "s16", "--rate", "48000", "--channels", "1", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )

            def on_close(self) -> None:
                if session.recorder:
                    session.recorder.terminate()
                    session.recorder.wait()
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
            mic_rate = 48000
            chunk_bytes = int(mic_rate * 0.1) * 2
            ratio = mic_rate / TARGET_RATE
            while session.running and session.recorder and session.recorder.stdout:
                data = session.recorder.stdout.read(chunk_bytes)
                if not data:
                    break
                samples = struct.unpack(f"<{len(data) // 2}h", data)
                out = []
                pos = 0.0
                while int(pos) < len(samples):
                    out.append(samples[int(pos)])
                    pos += ratio
                downsampled = struct.pack(f"<{len(out)}h", *out)
                session.recognition.send_audio_frame(downsampled)

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


def start_realtime(api_key, model):
    if not api_key:
        return "❌ 请先填写 API Key", gr.update(interactive=False), gr.update(interactive=True)
    realtime_session.start(api_key, model)
    return "🎤 正在录音...", gr.update(interactive=True), gr.update(interactive=False)


def stop_realtime():
    realtime_session.stop()
    text = realtime_session.get_text()
    return text, gr.update(interactive=False), gr.update(interactive=True)


def poll_realtime():
    if realtime_session.running:
        return realtime_session.get_text()
    return realtime_session.get_text()


# ─── UI 构建 ───

MODELS = [
    "fun-asr-realtime",
    "fun-asr-realtime-2026-02-28",
    "fun-asr-realtime-2025-11-07",
    "paraformer-realtime-v2",
]

LANGUAGES = ["auto", "zh", "en", "ja"]

CSS = """
.result-box { min-height: 200px; }
footer { display: none !important; }
"""


def build_ui():
    with gr.Blocks(title="语音转文字") as demo:
        gr.Markdown("# 🎙️ 语音转文字\n基于阿里云百炼 Fun-ASR API")

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

        with gr.Tabs():
            # Tab 1: 上传文件
            with gr.Tab("📁 上传音频文件"):
                file_input = gr.Audio(
                    label="上传音频（支持 wav/mp3/m4a/flac 等）",
                    type="filepath",
                )
                file_btn = gr.Button("开始识别", variant="primary")
                file_output = gr.Textbox(
                    label="识别结果", lines=10, elem_classes="result-box",
                )
                file_btn.click(
                    transcribe_file,
                    inputs=[file_input, api_key_input, model_input, lang_input],
                    outputs=file_output,
                )

            # Tab 2: 浏览器麦克风
            with gr.Tab("🎤 浏览器录音"):
                mic_input = gr.Audio(
                    label="点击录音按钮开始说话",
                    sources=["microphone"],
                    type="numpy",
                )
                mic_btn = gr.Button("识别录音", variant="primary")
                mic_output = gr.Textbox(
                    label="识别结果", lines=10, elem_classes="result-box",
                )
                mic_btn.click(
                    transcribe_mic,
                    inputs=[mic_input, api_key_input, model_input, lang_input],
                    outputs=mic_output,
                )

            # Tab 3: 实时流式
            with gr.Tab("⚡ 实时识别（系统麦克风）"):
                gr.Markdown("使用系统麦克风（PipeWire）进行实时流式语音识别，边说边出文字。")
                with gr.Row():
                    start_btn = gr.Button("🎤 开始录音", variant="primary")
                    stop_btn = gr.Button("⏹ 停止", interactive=False)
                    refresh_btn = gr.Button("🔄 刷新结果")
                realtime_output = gr.Textbox(
                    label="实时识别结果", lines=12, elem_classes="result-box",
                )

                start_btn.click(
                    start_realtime,
                    inputs=[api_key_input, model_input],
                    outputs=[realtime_output, stop_btn, start_btn],
                )
                stop_btn.click(
                    stop_realtime,
                    outputs=[realtime_output, stop_btn, start_btn],
                )
                refresh_btn.click(
                    poll_realtime,
                    outputs=realtime_output,
                )

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft(), css=CSS)
