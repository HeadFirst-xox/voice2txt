# 🎙️ voice2txt — 语音转文字工具

基于阿里云百炼 [Fun-ASR](https://help.aliyun.com/zh/model-studio/developer-reference/funasr-real-time-speech-recognition) 实时语音识别 API，支持 CLI 命令行和 Gradio WebUI 两种使用方式。

## 功能特性

- **上传音频文件识别** — 支持 wav / mp3 / m4a / flac 等格式（需要 ffmpeg）
- **浏览器麦克风录音** — 在 WebUI 中直接录音并识别
- **实时流式识别** — 使用系统麦克风边说边转文字
- **LLM 文本润色** — 通过 qwen-turbo 去除口头禅、修正语序
- **后台运行** — 支持守护进程模式，空闲自动关闭
- **跨平台** — Windows（pyaudio）和 Linux（PipeWire）均可使用

## 环境要求

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/)（音频文件转码需要）
- 阿里云百炼 [API Key](https://bailian.console.aliyun.com/)

## 安装

```bash
git clone https://github.com/HeadFirst-xox/voice2txt.git
cd voice2txt
pip install -r requirements.txt
```

## 使用方式

### 设置 API Key

```bash
# Linux / macOS
export DASHSCOPE_API_KEY="sk-xxx"

# Windows (CMD)
set DASHSCOPE_API_KEY=sk-xxx

# Windows (PowerShell)
$env:DASHSCOPE_API_KEY="sk-xxx"
```

也可以在 WebUI 界面中直接填写，或通过 `--api-key` 参数传入。

### WebUI 模式

```bash
python webui.py                # 前台运行
python webui.py -d             # 后台运行，自动打开浏览器
python webui.py --stop         # 停止后台进程
python webui.py --status       # 查看运行状态
python webui.py --port 8080    # 指定端口
```

启动后访问 http://localhost:7860 。

### CLI 模式

```bash
python main.py                          # 实时语音识别
python main.py --polish                  # 识别完成后 LLM 润色
python main.py --output result.txt       # 结果保存到文件
python main.py --model paraformer-realtime-v2   # 指定模型
```

按 `Ctrl+C` 停止录音并输出结果。

### 麦克风测试

```bash
python test_mic.py
```

录制 5 秒音频并分析音量，用于验证麦克风是否正常工作。

## 项目结构

```
voice2txt/
├── webui.py          # Gradio WebUI 界面
├── main.py           # CLI 命令行工具
├── polish.py         # LLM 文本润色模块
├── test_mic.py       # 麦克风测试脚本
├── requirements.txt  # Python 依赖
└── .gitignore
```

## 支持的模型

| 模型 | 说明 |
|------|------|
| `fun-asr-realtime` | 默认模型（最新版） |
| `fun-asr-realtime-2026-02-28` | 2026-02-28 版本快照 |
| `fun-asr-realtime-2025-11-07` | 2025-11-07 版本快照 |
| `paraformer-realtime-v2` | Paraformer v2 模型 |

## License

MIT
