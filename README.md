# Audio Transcript EXE (Windows)

这个工具会批量扫描桌面上的 `.m4a` 文件，使用 OpenAI 的转录接口（默认模型 `gpt-4o-mini-transcribe`）转成 `.txt` 文本。

## 功能

- 默认自动读取 Windows 桌面目录（支持 `Desktop` 和 `OneDrive/Desktop`）
- 使用 `ffmpeg` 按固定时长切分音频（默认每段 10 分钟）
- 按分段调用 OpenAI 转录 API
- 自动合并分段文本并输出到 `transcripts` 目录
- 支持失败重试

## 依赖

1. Python 3.10+
2. `ffmpeg`（需在 PATH 中，或与 exe 同目录）
3. OpenAI API Key（可在 exe 内首次运行时输入并保存）

## 本地运行（Python）

```powershell
# 1) 安装依赖
python -m pip install -r requirements.txt

# 2) 运行（默认扫桌面）
python transcribe_desktop.py
```

首次运行如未检测到 API Key，会提示你在程序里输入；也可以直接参数传入：

```powershell
python transcribe_desktop.py --api-key "你的key" --save-api-key
```

可选参数示例：

```powershell
python transcribe_desktop.py --segment-seconds 480 --language zh
python transcribe_desktop.py --input-dir "D:\audio" --output-dir "D:\audio\txt"
```

## 打包 EXE

```powershell
./build_exe.ps1
```

打包后产物：

- `dist/AudioTranscriber.exe`

运行 exe 时，如果没有环境变量，程序会在控制台提示输入 API Key；
选择保存后会写入 `config.json`（位于 exe 同目录），后续无需重复输入。

你也可以用命令行方式传入并保存：

```powershell
AudioTranscriber.exe --api-key "你的key" --save-api-key
```

## 输出说明

- 输入：`桌面\xxx.m4a`
- 输出：`桌面\transcripts\xxx.txt`

## 常见问题

1. **提示找不到 ffmpeg**
   - 安装 ffmpeg 并加到 PATH，或将 `ffmpeg.exe` 放在 `AudioTranscriber.exe` 同目录。

2. **转录失败/超时**
   - 减小 `--segment-seconds`（例如改为 300）
   - 检查网络和 API 配额

3. **API Key 每次都要输入**
   - 运行时选择保存，或执行：`AudioTranscriber.exe --api-key "你的key" --save-api-key`

4. **文件很多，想分批跑**
   - 先把需要处理的 m4a 放到单独目录，再用 `--input-dir` 指定。
