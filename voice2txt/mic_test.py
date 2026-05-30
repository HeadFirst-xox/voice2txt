#!/usr/bin/env python3
"""测试麦克风：录制 5 秒音频并保存为 WAV（Windows 用 pyaudio，Linux 用 PipeWire）"""

import struct
import subprocess
import sys
import time
import wave

IS_WINDOWS = sys.platform == "win32"
SAMPLE_RATE = 16000 if IS_WINDOWS else 48000


def test_record(duration=5):
    filename = "test_recording.wav"
    backend = "pyaudio" if IS_WINDOWS else "PipeWire"

    print(f"🎤 通过 {backend} 录制 {duration} 秒...")
    print(f"   (使用系统默认输入源)")
    print(f"   开始说话！\n")

    if IS_WINDOWS:
        import pyaudio
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16, channels=1,
            rate=SAMPLE_RATE, input=True,
            frames_per_buffer=1024,
        )
        frames_data = []
        chunks_per_sec = SAMPLE_RATE // 1024
        for i in range(duration, 0, -1):
            sys.stdout.write(f"\r   剩余 {i} 秒...")
            sys.stdout.flush()
            for _ in range(chunks_per_sec):
                frames_data.append(stream.read(1024, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        pa.terminate()

        wf = wave.open(filename, "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames_data))
        wf.close()
    else:
        proc = subprocess.Popen(
            ["pw-record", "--format", "s16", "--rate", str(SAMPLE_RATE),
             "--channels", "1", filename],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for i in range(duration, 0, -1):
            sys.stdout.write(f"\r   剩余 {i} 秒...")
            sys.stdout.flush()
            time.sleep(1)
        proc.terminate()
        proc.wait()

    print(f"\r\n\n📁 录音已保存到: {filename}")

    try:
        wf = wave.open(filename, "rb")
        frames = wf.readframes(wf.getnframes())
        wf.close()
        if len(frames) < 4:
            print("\n⚠️  录音文件为空，请检查麦克风")
            return

        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        max_amp = max(abs(s) for s in samples)
        avg_amp = sum(abs(s) for s in samples) // len(samples)

        print(f"\n   最大音量: {max_amp}  平均音量: {avg_amp}")
        if max_amp < 100:
            print("   ⚠️  几乎没有声音，请检查麦克风是否正确连接")
        elif max_amp < 1000:
            print("   ⚠️  音量较低，可能麦克风距离太远")
        else:
            print("   ✅ 麦克风工作正常！")
    except Exception as e:
        print(f"\n   分析录音时出错: {e}")


if __name__ == "__main__":
    test_record()
