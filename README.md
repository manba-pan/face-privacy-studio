# 影像工作台 0.3.1

本地的视频处理工具：人脸遮挡、手动关键帧补码、截取、旋转、音频处理与批量导出。不需要账号、会员或其他剪辑软件；打包版自带运行环境，处理视频可离线进行。

作者：[manba-pan](https://github.com/manba-pan) · [问题反馈](https://github.com/manba-pan/face-privacy-studio/issues) · [使用许可与支持](COMMERCIAL.md)

**免费剪片，包括接剪辑单、制作广告和商业视频。** 软件本身的销售、收费服务或商业产品集成须事先另行书面授权。项目公开源码，采用自定义许可，非 OSI 标准开源许可；完整条款见 [LICENSE](LICENSE)。

## 启动

公开版本和下载状态见 [Releases](https://github.com/manba-pan/face-privacy-studio/releases)。EXE 安装包已加入构建流程；其公开附件须先完成 [DISTRIBUTION.md](DISTRIBUTION.md) 中的第三方对应源码核对。没有 EXE 附件时，请按下方“从源码运行”操作，GitHub 自动生成的 Source code ZIP 不是安装包。

完整解压便携 ZIP，运行 `影像工作台.exe`。必须保留 `_internal` 文件夹；不要只复制 exe。可拖入视频，也可把视频拖到 exe 图标上。

启动欢迎页显示作者、项目主页、问题反馈和支持入口，可勾选“不再提示”。主界面“作者 / 支持 / 反馈”可以重新打开。打赏自愿，不影响免费功能；目前通过邮箱联系作者，微信／支付宝收款码由作者提供后接入。

构建面向 Windows x64。本机 Windows 11 已验证，尚未在另一台实体电脑或 Windows 10 实测。使用 CPU 推理与软件编码，不要求独立显卡。Qt 界面和带声音播放增加了包体积。

## 操作

1. 导入或拖入视频，支持多选。当前素材自动分析；其余可选择后点击分析，批量导出也会依次分析。
2. “遮挡”选择整脸、眼睛、上下半脸；马赛克、模糊、实色；强度 1～5 档。覆盖范围、眼睛条高度独立可调。
3. 默认完全未检测到人脸时整帧遮黑，空镜也会遮黑。可改成保留画面，再人工检查。分析后会定位到一个连续检测片段的中间帧。
4. Space 播放/暂停，左右键逐帧，点击时间线跳转。“对比原画”只影响预览；导出和快照仍使用处理后的画面。
5. I 设导出起点，O 设终点（包含当前帧）；“恢复整段”取消截取。浅绿色边界显示导出区间。
6. “补码”点击画框，在画面拖矩形，默认覆盖导出区间。选中已有框，在其他时间“重画关键帧”，中间位置和大小线性变化。时间输入的结束时刻不包含在补码范围内。
7. “画面 / 声音”提供旋转、移除音轨、AAC 音量调整、完整音轨提取；“导出”提供画质、尺寸、帧率。
8. 保存项目会记录原视频路径、参数、关键帧，不包含视频。重开会重新分析，原文件须保持位置和内容。退出前保存需要保留的操作。
9. 多素材可各设参数，也可应用当前参数到全部素材。截取范围和手动框分别保留。批量遇重名递增编号；单次导出要求新文件名，不覆盖原片。

## 画质与声音

| 预设 | 编码 | 用途 |
| --- | --- | --- |
| 高质量（默认） | H.264 / CRF 14 / medium / 8 位 4:2:0 | 画质优先的通用成片 |
| 均衡体积 | H.264 / CRF 18 / fast | 日常分享 |
| 后期中间片 | ProRes 422 HQ / 10 位 | 继续剪辑，文件较大 |
| 无损编码 | FFV1 / RGB / MKV | 无损保存处理后 RGB，文件很大 |

默认保留源分辨率与帧率，可以缩小或降帧率，不放大、不允许高于源帧率。奇数宽高补成偶数。变帧率输入按读取到的平均帧率统一成固定帧率，不原样保留逐帧时间戳。

打码必然改像素。H.264 和 ProRes 都有损，未遮挡区域也会重新压缩。FFV1 保证处理后 RGB 的编码无损，不保证源文件字节、原始 YUV 或元数据不变。10 位 SDR 用 RGB48 中间处理，要求 ProRes / FFV1；8 位转成 10 位不会增加原始细节。

当前面向普通 SDR / Rec.709。HDR、HLG、PQ、BT.2020 会阻止视频导出，需先受控转换。没有完整色彩管理、AI 补帧、超分、全部相机元数据保留、字幕或多音轨导出。快照为 8 位 PNG。

音频默认保留第一条音轨的原编码。兼容 MP4 的音轨直接封装 MP4；PCM 等音轨自动用 MOV。完整音轨直接复制；PCM 截取按采样裁切，保持原 PCM 编码，不再有损压缩。AAC 等压缩音轨直拷按包边界裁切，起止可能相差数十毫秒。调音量需选 AAC 320 kbps，它仍有损。无声素材正常输出无音轨。

预览使用最长边不超过 960 像素的缓存和 AAC 预览音轨，播放绘制最高约 32 fps，逐帧检查使用确切帧。预览质量不决定成片；导出重新解码原尺寸的每一帧。

## 能力边界

多尺度 YuNet 找脸，再局部放大给 MediaPipe 定位五官。五官失败时仍使用人脸框及眼睛位置遮挡，并提示回看。检测到的人脸统一处理，最多 6 张；没有人物选择、可靠跨帧追踪或跨镜头识别。

背头、严重侧脸、遮挡、小脸、运动模糊可能漏检。回看列表只提示已知问题，不能证明其他帧没有漏检。整脸主要覆盖面部，头发、耳朵、身体不自动处理。手动关键帧是插值，并非自动追踪。

长视频先逐帧分析，等待、内存和缓存随时长增加，尚未做小时级压力测试。正常退出清理本次缓存，异常退出可能遗留系统临时目录的 `face-privacy-preview-*`。

启动诊断保存在 `%LOCALAPPDATA%/FacePrivacyStudio/last-error.log`，可能含本地路径，不会自动上传。

## 从源码运行

在 Windows 安装 Python 3.12 x64，下载或克隆源码，在项目目录打开终端运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe studio.py
```

安装依赖需要联网；视频处理在本机进行。`app.py` 为保留的旧 Tk 界面。模型说明见 `models/README.md`。

## 构建与开发

使用上述环境，安装 [NSIS 3.12](https://nsis.sourceforge.io/Download) 后可以生成单个 EXE 安装包。请先阅读 [分发说明](DISTRIBUTION.md)。

```text
python collect_notices.py
python -m PyInstaller Studio.spec --noconfirm --distpath releases/0.3.1 --workpath build/studio
python package_studio.py
python build_installer.py --makensis "NSIS目录/makensis.exe"
```

源码包括程序、模型、第三方声明，不包括环境、构建目录、个人视频、预览与测试输出。参与贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，其中说明贡献授权方式。

测试见 `STUDIO_VERIFICATION.md`，原理与优化方向见 `ARCHITECTURE.md`。

## 来源

- [YuNet / OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [MediaPipe Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python)
- [FFmpeg](https://ffmpeg.org/ffmpeg.html)
- [Qt QMediaPlayer](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html)
- 布局参考 [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve/edit) 与 [CapCut Desktop](https://www.capcut.com/tools/desktop-video-editor) 的素材区、监看区、参数区、时间线，未使用其品牌图标或素材。

第三方许可证保留在 `THIRD_PARTY`。捆绑 FFmpeg 自报 GPL v3 或更新版本，Qt/PySide6、模型与其他库分别适用上游条款；项目许可证不替代第三方条款。
