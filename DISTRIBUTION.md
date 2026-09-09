# 分发与第三方组件

## 当前公开下载：联网安装 EXE

VideoRedactor-0.4.1-WebSetup.exe 包含应用、模型及可随附的基础运行文件。首次安装另从官方 Python 软件包宿主 files.pythonhosted.org 下载五个固定版本软件包，约 331 MB；无需手动安装 Python，完成后可离线处理视频。

Qt/PySide6/Shiboken 原生运行文件、OpenCV 的 FFmpeg 插件和独立 FFmpeg EXE 不嵌入此公开安装器，由用户安装时直接向上游获取。固定 URL、版本、下载包 SHA-256 和选取文件的 SHA-256 见 [运行组件清单](release_tools/runtime-manifest.json)。文件必须通过完整校验才会安装，不执行软件包中的安装脚本，不在运行期间静默升级组件。

| 官方包 | 固定版本 | 用途 |
| --- | --- | --- |
| PySide6 Essentials / Addons | 6.11.2 | Qt 窗口、原片播放及相关动态库 |
| shiboken6 | 6.11.2 | Python 与 Qt 的绑定运行库 |
| opencv-contrib-python | 5.0.0.93 | 从 wheel 中选取视频读取所需的 FFmpeg 插件 |
| imageio-ffmpeg | 0.6.0 | 从 wheel 中选取 FFmpeg 7.1 命令行程序 |

其余 PySide6 的四个小型 Python 包装模块已随程序冻结，它们未修改的对应源码及许可文本包含在 Qt-Python-wrapper-sources.zip，源码仓库及安装目录都提供该文件。安装辅助脚本见 [InstallRuntime.ps1](release_tools/InstallRuntime.ps1)。

完整下载缓存保存在 %LOCALAPPDATA%/FacePrivacyStudio/setup-downloads，安装失败后可重试。卸载仅删除安装清单文件，保留用户素材、项目与本机缓存；缓存可由用户自行清理。安装目录中的 _internal/PySide6 等动态库可以换成 ABI 兼容的修改版本；设置 IMAGEIO_FFMPEG_EXE 可以使用兼容的外部 FFmpeg。项目不限制为调试 LGPL 库修改而进行的逆向工程。

## 许可与个人数据

本项目权利人有权许可的部分采用根目录 LICENSE。免费使用包括付费剪辑、广告视频和企业内部视频处理；软件销售、收费 API、商业产品集成需单独书面授权。第三方组件继续遵守各自许可，不受本项目商业限制影响。

仓库与公开安装器不包含个人视频、识别缓存、诊断日志、验收截图或项目文件。公开界面图片不含视频；两张收款码经作者授权公开。安装时只下载组件，不上传视频，也不自动提交反馈。

## 离线完整包的状态

本机曾构建包含全部运行组件的离线安装包。其 Gyan FFmpeg 7.1（GPL v3+）、Qt 6.11.2、Qt 播放后端 FFmpeg 7.1.5 及相关依赖的完整对应源码分发资料仍未整理完成，因此该离线包不作为本次公开附件。联网安装方式不表示这些离线包的资料已经补齐，也不授予重新打包上游组件的额外许可。

## 上游资料

- [Qt/PySide6 分发方式](https://doc.qt.io/qtforpython-6/faq/distribution.html)
- [Qt 的 LGPL 义务说明](https://www.qt.io/development/open-source-lgpl-obligations)
- [PySide6 6.11.2 源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)
- [FFmpeg 许可说明](https://ffmpeg.org/legal.html)
- [imageio-ffmpeg 源码及上游二进制说明](https://github.com/imageio/imageio-ffmpeg)

第三方原始声明保留在 THIRD_PARTY。模型来源见 models/README.md。
