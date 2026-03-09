#!/usr/bin/env python3
"""测试麦克风：通过 PipeWire 录制 5 秒音频并保存为 WAV"""

import subprocess
import struct
import sys
import time


def list_sources():
    """列出 PipeWire 可用的输入源"""
    result = subprocess.run(
        ["pw-cli", "list-objects", "Node"],
        capture_output=True, text=True
    )
    print("=== PipeWire 音频输入源 ===")
    print("(通过系统设置或 wpctl 选择默认麦克风)\n")
    subprocess.run(["wpctl", "status"], capture_output=False)


def test_record(duration=5):
    filename = "test_recording.wav"

    print(f"🎤 通过 PipeWire 录制 {duration} 秒...")
    print(f"   (使用系统默认输入源: Razer Seiren Elite)")
    print(f"   开始说话！\n")

    proc = subprocess.Popen(
        [
            "pw-record",
            "--format", "s16",
            "--rate", "48000",
            "--channels", "1",
            filename,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    for i in range(duration, 0, -1):
        sys.stdout.write(f"\r   剩余 {i} 秒...")
        sys.stdout.flush()
        time.sleep(1)

    proc.terminate()
    proc.wait()

    print(f"\r\n\n📁 录音已保存到: {filename}")
    print(f"   播放验证: pw-play {filename}")

    import wave
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
