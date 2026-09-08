# 分发与第三方组件

## 项目许可

本项目权利人有权许可的部分采用根目录 LICENSE。免费使用包括付费剪辑委托、广告视频及企业内部视频处理。软件销售、收费 API 和商业产品集成需要单独书面授权。第三方组件不受本项目商业限制影响。

## 源码仓库

仓库包含本项目 Python 源码、构建脚本、模型、界面资源和第三方声明。文档中的欢迎页截图不含视频，支付宝和微信收款码由作者授权公开。仓库不包含 Python 环境、Qt/FFmpeg 可执行文件、个人视频、预览缓存、诊断日志和视频验收截图。源码运行需要另行安装 requirements.txt 中的依赖。

## Windows 安装包

安装包采用 NSIS，把程序和动态库安装到当前用户目录。Qt/PySide6 保持动态链接，库文件位于 `_internal/PySide6`，可以替换为 ABI 兼容的修改版本。独立 FFmpeg 通过命令行和标准媒体数据管道调用；也可通过 `IMAGEIO_FFMPEG_EXE` 环境变量指定兼容的替代程序。项目不限制为修改 LGPL 库而进行的调试或逆向工程。

Studio.spec 排除了应用不使用的 Qt Virtual Keyboard 库和插件。此模块的开源选项为 GPL，不应由 Qt 打包钩子无意加入本项目安装包。

### 二进制公开发布前尚需完成

当前内部验证版本使用 imageio-ffmpeg 0.6.0 附带的 Gyan FFmpeg 7.1，内含 x264 等库，自报 GPL v3 或更新版本。已有版权声明、上游地址和构建配置不等于完整对应源码。公开分发之前需要落实该构建及其所含依赖的对应源码、补丁和构建资料的可获取方式。

还需核对所分发 Qt/PySide6 6.11.2、其 FFmpeg 后端及其他库的版本、许可文本和对应源码位置。安装包本地构建成功或测试通过不能替代这些分发要求。在这项工作完成前，EXE 仅用于本地验收，不作为公开 Release 附件。

## 上游资料

- Qt/PySide6：https://doc.qt.io/qt-6/licensing.html
- LGPL v3：https://www.gnu.org/licenses/lgpl-3.0.html
- Qt 源码：https://download.qt.io/official_releases/qt/6.11/6.11.2/
- Qt for Python 源码：https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/
- FFmpeg 许可与分发：https://ffmpeg.org/legal.html
- FFmpeg 7.1：https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz
- 原二进制供应方：https://github.com/GyanD/codexffmpeg/releases/tag/7.1

第三方原始声明在 THIRD_PARTY。模型来源见 models/README.md。这份说明不会改变上游许可，也不替代对应源码和版本核对。
