#!/usr/bin/env python3
"""
麦克风实时语音转文字工具
基于阿里云百炼平台 Fun-ASR 实时语音识别 API
Windows 使用 pyaudio、Linux 使用 PipeWire (pw-record) 采集麦克风音频

使用方式:
  1. 设置环境变量: export DASHSCOPE_API_KEY="sk-xxx"  (Windows: set DASHSCOPE_API_KEY=sk-xxx)
  2. 运行: python main.py
  3. 对着麦克风说话，实时输出识别文字
  4. 按 Ctrl+C 停止
"""

import os
import sys
import signal
import struct
import subprocess
import argparse

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from polish import polish_text

IS_WINDOWS = sys.platform == "win32"
MIC_RATE = 48000
TARGET_RATE = 16000
CHANNELS = 1

recognition: Recognition | None = None
mic_stream = None  # PyAudio stream (Windows) 或 Popen (Linux)
pa_instance = None  # PyAudio 实例 (仅 Windows)
output_file = None
final_texts: list[str] = []
enable_polish = False


def downsample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate:
        return data
    ratio = from_rate / to_rate
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    out = []
    pos = 0.0
    while int(pos) < len(samples):
        out.append(samples[int(pos)])
        pos += ratio
    return struct.pack(f"<{len(out)}h", *out)


def open_mic():
    """打开麦克风，返回后可通过 read_mic() 读取数据"""
    global mic_stream, pa_instance
    if IS_WINDOWS:
        import pyaudio
        pa_instance = pyaudio.PyAudio()
        mic_stream = pa_instance.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=TARGET_RATE,
            input=True,
            frames_per_buffer=int(TARGET_RATE * 0.1),
        )
        print(f"🎤 麦克风已就绪 (pyaudio {TARGET_RATE}Hz)，开始说话吧... (Ctrl+C 停止)\n")
    else:
        mic_stream = subprocess.Popen(
            ["pw-record", "--format", "s16", "--rate", str(MIC_RATE),
             "--channels", str(CHANNELS), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        print(f"🎤 麦克风已就绪 ({MIC_RATE}Hz -> {TARGET_RATE}Hz)，开始说话吧... (Ctrl+C 停止)\n")


def read_mic() -> bytes:
    """从麦克风读取一帧 PCM16 数据（已降采样到 TARGET_RATE）"""
    if IS_WINDOWS:
        chunk = int(TARGET_RATE * 0.1)
        return mic_stream.read(chunk, exception_on_overflow=False)
    else:
        chunk_bytes = int(MIC_RATE * 0.1) * 2
        data = mic_stream.stdout.read(chunk_bytes)
        if not data:
            return b""
        return downsample_pcm16(data, MIC_RATE, TARGET_RATE)


def close_mic():
    global mic_stream, pa_instance
    if IS_WINDOWS:
        if mic_stream:
            try:
                mic_stream.stop_stream()
                mic_stream.close()
            except Exception:
                pass
            mic_stream = None
        if pa_instance:
            pa_instance.terminate()
            pa_instance = None
    else:
        if mic_stream:
            try:
                mic_stream.terminate()
                mic_stream.wait()
            except Exception:
                pass
            mic_stream = None


class RealtimeCallback(RecognitionCallback):
    def on_open(self) -> None:
        open_mic()

    def on_close(self) -> None:
        close_mic()

    def on_complete(self) -> None:
        print("\n✅ 识别完成")

    def on_error(self, message) -> None:
        print(f"\n❌ 识别错误: {message.message} (request_id: {message.request_id})")
        cleanup()
        sys.exit(1)

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if "text" not in sentence:
            return

        text = sentence["text"]
        if RecognitionResult.is_sentence_end(sentence):
            final_texts.append(text)
            sys.stdout.write(f"\r\033[K[完成] {text}\n")
            sys.stdout.flush()
            if output_file:
                output_file.write(text + "\n")
                output_file.flush()
        else:
            sys.stdout.write(f"\r\033[K[识别中] {text}")
            sys.stdout.flush()


def cleanup():
    close_mic()
    if output_file:
        output_file.close()


def signal_handler(sig, frame):
    print("\n\n⏹  停止识别...")
    if recognition:
        recognition.stop()
        req_id = recognition.get_last_request_id()
        first_delay = recognition.get_first_package_delay()
        last_delay = recognition.get_last_package_delay()
        print(f"   request_id: {req_id}")
        print(f"   首包延迟: {first_delay}ms, 尾包延迟: {last_delay}ms")

    if final_texts:
        print(f"\n📝 共识别 {len(final_texts)} 句:")
        for i, t in enumerate(final_texts, 1):
            print(f"   {i}. {t}")

        if enable_polish:
            raw = "".join(final_texts)
            print("\n✨ 正在润色...")
            polished = polish_text(raw, dashscope.api_key)
            print(f"\n📝 润色结果:\n   {polished}")
            if output_file:
                output_file.write(f"\n--- 润色结果 ---\n{polished}\n")
                output_file.flush()

    cleanup()
    sys.exit(0)


def main():
    global recognition, output_file, enable_polish

    parser = argparse.ArgumentParser(description="麦克风实时语音转文字工具")
    parser.add_argument(
        "--model",
        default="fun-asr-realtime",
        help="语音识别模型 (默认: fun-asr-realtime)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DashScope API Key (也可通过 DASHSCOPE_API_KEY 环境变量设置)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="将识别结果保存到文件",
    )
    parser.add_argument(
        "--semantic-punctuation",
        action="store_true",
        help="启用语义断句 (默认使用 VAD 断句)",
    )
    parser.add_argument(
        "--polish",
        action="store_true",
        help="停止录音后使用 LLM 润色识别文本（去口头禅、去重复、通顺语序）",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("❌ 请设置 API Key:")
        print("   export DASHSCOPE_API_KEY='sk-xxx'")
        print("   或使用 --api-key 参数")
        sys.exit(1)

    dashscope.api_key = api_key
    dashscope.base_websocket_api_url = (
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )

    enable_polish = args.polish

    if args.output:
        output_file = open(args.output, "a", encoding="utf-8")
        print(f"📄 识别结果将保存到: {args.output}")

    backend = "pyaudio" if IS_WINDOWS else "pw-record"
    print(f"🔧 模型: {args.model}")
    print(f"🔧 录音后端: {backend}")
    if enable_polish:
        print(f"🔧 润色: 已启用 (qwen-turbo-latest)")
    print(f"🔧 输出采样率: {TARGET_RATE}Hz, 格式: PCM, 单声道")
    print("─" * 50)

    callback = RealtimeCallback()
    recognition = Recognition(
        model=args.model,
        format="pcm",
        sample_rate=TARGET_RATE,
        semantic_punctuation_enabled=args.semantic_punctuation,
        callback=callback,
    )

    recognition.start()
    signal.signal(signal.SIGINT, signal_handler)

    while True:
        if mic_stream:
            data = read_mic()
            if not data:
                break
            try:
                recognition.send_audio_frame(data)
            except Exception:
                break
        else:
            break

    recognition.stop()


if __name__ == "__main__":
    main()
