# Audio Transcript EXE (Windows)

已改为 **GUI 界面版**：打包 exe 后双击启动，会出现窗口，让你直接：

- 选择目标音频文件（可多选）
- 选择输出目录
- 填写 API 地址（Base URL）
- 填写 API Key
- 填写模型名（默认 `gpt-4o-mini-transcribe`）

## 你提到的需求已支持

- ✅ 不是黑窗口自动跑，而是可视化表单
- ✅ 选择“目标音频文件路径”
- ✅ 选择“输出路径”
- ✅ 手动填写“API 地址”，并按该地址连接

## 依赖

1. Python 3.10+
2. `ffmpeg`（需在 PATH 中，或与 exe 同目录）
3. OpenAI Python SDK

## 本地运行

```powershell
python -m pip install -r requirements.txt
python transcribe_desktop.py
```

> 不带参数默认启动 GUI。

## 打包 EXE

```powershell
./build_exe.ps1
```

打包后：

- `dist/AudioTranscriber.exe`

当前打包参数使用 `--windowed`，双击不会弹控制台黑窗。

## GUI 字段说明

- **目标音频文件**：可选多个文件（m4a/mp3/wav/flac）
- **输出目录**：每个音频生成同名 `.txt`
- **API 地址(Base URL)**：例如 `https://api.openai.com/v1`
- **API Key**：你的密钥
- **模型**：默认 `gpt-4o-mini-transcribe`
- **语言**：可空，例如 `zh`
- **切分秒数**：默认 `600`
- **ffmpeg**：默认 `ffmpeg`
- **重试次数/重试等待**：失败重试参数

## CLI 模式（可选）

如果你仍想命令行跑：

```powershell
python transcribe_desktop.py --cli --input-dir "D:\audio" --output-dir "D:\audio\txt" --api-key "你的key" --api-base "https://api.openai.com/v1"
```

## 常见问题

1. **找不到 ffmpeg**
   - 安装 ffmpeg 并加入 PATH，或把 `ffmpeg.exe` 放在 exe 同目录。

2. **接口报错**
   - 检查 API 地址是否正确（必须是服务的 base URL）
   - 检查 API Key、模型名、配额。
