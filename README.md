# 🎙️ voice2txt — 语音转文字工具

基于阿里云百炼 [Fun-ASR](https://help.aliyun.com/zh/model-studio/developer-reference/funasr-real-time-speech-recognition) 实时语音识别 API，支持 CLI 命令行和 Gradio WebUI 两种使用方式。

## 功能特性

- **上传音频文件识别** — 支持 wav / mp3 / m4a / flac 等格式（需要 ffmpeg）
- **浏览器麦克风录音** — 在 WebUI 中直接录音并识别
- **实时流式识别** — 使用系统麦克风边说边转文字
- **LLM 文本润色** — 通过 qwen-turbo 去除口头禅、修正语序
- **后台运行** — 支持守护进程模式，空闲自动关闭
- **跨平台** — 对外用法一致，底层按平台走各自成熟路径（见下文）

## 环境要求

### 共用（所有平台）

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/)（**上传音频** Tab 转码需要；仅用浏览器录音/实时识别可不装）
- 阿里云百炼 [API Key](https://bailian.console.aliyun.com/)

### 按平台额外安装

| 平台 | 用途 | 需要 |
|------|------|------|
| **Windows** | 实时识别 / CLI / 麦克风测试 | [pyaudio](https://pypi.org/project/PyAudio/)（`pip install pyaudio`） |
| **Linux 桌面** | 实时识别 / CLI / 麦克风测试 | PipeWire 及 `pw-record`（如 `sudo apt install pipewire`） |
| **任意平台** | 浏览器录音 Tab | 仅浏览器麦克风权限，**不依赖** pyaudio / PipeWire |

> macOS 当前未单独适配：非 Windows 会尝试 `pw-record`，建议 macOS 用户优先用 **浏览器录音** Tab。

## 跨平台说明

本项目 **不追求「一个 Python 包装所有平台」**，而是：

- **对外**：同一套命令（`python start.py`）、同一 WebUI、同一复制/润色行为  
- **对内**：麦克风采集、后台进程等 **按平台分叉**，各走各的成熟方案  

### 共用层（与 OS 无关）

| 模块 | 说明 |
|------|------|
| DashScope / Fun-ASR | 语音识别 API |
| `polish.py` | LLM 润色 |
| Gradio WebUI | 界面、复制到剪贴板 |
| `start.py` | 启停命令语义（`start` / `stop` / `open` / `toggle`） |
| 上传文件识别 | ffmpeg 转 PCM → API（ffmpeg 为系统工具，调用方式相同） |
| 浏览器录音 | 录音在浏览器内完成，与系统麦克风后端无关 |

### 平台分叉（刻意分开实现）

| 能力 | Windows | Linux |
|------|---------|-------|
| **系统麦克风采集** | `pyaudio`（`PyAudioRecorder`） | `pw-record` / PipeWire（`PipeWireRecorder`） |
| **后台守护进程** | `subprocess.Popen` + 无窗口 | `fork` + 日志重定向 |
| **停止进程** | `SIGBREAK` | `SIGTERM` |
| **可选快捷脚本** | `scripts/start.ps1` | `scripts/start.sh` |

相关代码集中在 `voice2txt/webui.py`、`voice2txt/cli.py` 的 `MicRecorder`、`_daemonize()`、`_stop_service()`。

### 支持矩阵

| 环境 | WebUI + 浏览器录音 | WebUI + 实时识别 | CLI |
|------|-------------------|------------------|-----|
| Windows 10/11 | ✅ | ✅（需 pyaudio） | ✅ |
| Linux 桌面（PipeWire） | ✅ | ✅（需 `pw-record`） | ✅ |
| Linux 无 PipeWire / WSL | ✅ | ❌ → 请用浏览器录音 | ❌ |
| macOS | ✅ | ⚠️ 未测 | ⚠️ 未测 |

### 日常选用建议

- **口述给 AI、要省事**：`python start.py` → **⚡ 实时识别**（Windows/Linux 各自麦克风后端）  
- **系统麦不可用或不想装 pyaudio**：同一 WebUI 切到 **🎤 浏览器录音**  
- **已有录音文件**：**📁 上传文件**（需 ffmpeg）

## 安装

```bash
git clone https://github.com/HeadFirst-xox/voice2txt.git
cd voice2txt
pip install -r requirements.txt
```

**Windows**（实时识别 / CLI 还需要）：

```bash
pip install pyaudio
```

**Linux**（实时识别 / CLI 还需要，发行版命令示例）：

```bash
# Debian / Ubuntu
sudo apt install pipewire ffmpeg
# 确认 pw-record 可用
pw-record --help
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

**日常推荐（双平台相同命令）：**

```bash
python start.py              # 开关：未运行则后台启动并打开浏览器，已运行则关闭
python start.py open         # 服务已在跑，只打开页面
python start.py status       # 是否在运行
```

或直接调用 WebUI 模块：

```bash
python -m voice2txt.webui          # 前台运行
python -m voice2txt.webui -d       # 后台运行，自动打开浏览器
python -m voice2txt.webui -t       # 同 start.py（开关切换）
python -m voice2txt.webui --open   # 仅打开浏览器
python -m voice2txt.webui --stop   # 停止后台进程
python -m voice2txt.webui --status # 查看运行状态
python -m voice2txt.webui --port 8080
```

Windows 也可：`.\scripts\start.ps1` · Linux/macOS：`./scripts/start.sh`（参数同 `start.py`，如 `stop`、`open`）

启动后访问 http://localhost:7860 。界面默认 **⚡ 实时识别** 页：停止录音后可自动复制润色结果，便于粘贴到 AI 对话；复制时会去掉底部耗时统计行。

### CLI 模式

```bash
python -m voice2txt.cli                  # 实时语音识别
python -m voice2txt.cli --polish           # 识别完成后 LLM 润色
python -m voice2txt.cli --output result.txt
python -m voice2txt.cli --model paraformer-realtime-v2
```

按 `Ctrl+C` 停止录音并输出结果。

### 麦克风测试

```bash
python -m voice2txt.mic_test
```

录制 5 秒音频并分析音量，用于验证麦克风是否正常工作。

## 项目结构

```
voice2txt/
├── README.md
├── requirements.txt
├── start.py              # 推荐入口：启停 WebUI
├── scripts/              # 平台快捷脚本（可选）
│   ├── start.ps1         # Windows → 转发 start.py
│   └── start.sh          # Linux/macOS → 转发 start.py
└── voice2txt/            # 核心代码
    ├── webui.py          # Gradio WebUI
    ├── cli.py            # 命令行实时识别
    ├── polish.py         # LLM 润色
    └── mic_test.py       # 麦克风测试
```

运行时产生的本地文件（已 gitignore）：`.webui.pid`、`webui.log`、`.dashscope_api_key`

## 支持的模型

| 模型 | 说明 |
|------|------|
| `fun-asr-realtime` | 默认模型（最新版） |
| `fun-asr-realtime-2026-02-28` | 2026-02-28 版本快照 |
| `fun-asr-realtime-2025-11-07` | 2025-11-07 版本快照 |
| `paraformer-realtime-v2` | Paraformer v2 模型 |

## License

MIT
