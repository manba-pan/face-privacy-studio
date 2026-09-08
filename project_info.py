"""Public project identity and user-initiated links; no network requests at import."""
from pathlib import Path
import sys
from urllib.parse import quote

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout)

VERSION = '0.3.1'
AUTHOR = 'manba-pan'
EMAIL = '2087725636@qq.com'
PROJECT_URL = 'https://github.com/manba-pan/face-privacy-studio'
# Set only to a payment/support page explicitly supplied by the author.
SUPPORT_URL = ''


def open_link(parent, url):
    if not QDesktopServices.openUrl(QUrl(url)):
        QMessageBox.information(parent, '打开链接', '系统未能打开此链接，请复制后打开：\n\n' + url)


class ProjectDialog(QDialog):
    def __init__(self, parent=None, welcome=False):
        super().__init__(parent)
        self.setWindowTitle('欢迎使用影像工作台' if welcome else '作者 · 支持 · 反馈')
        self.setMinimumWidth(510)
        self.setMaximumWidth(640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(15)
        title = QLabel('影像工作台')
        title.setStyleSheet('font-size:26px;font-weight:700;color:#70dec4;')
        layout.addWidget(title)
        layout.addWidget(QLabel(f'版本 {VERSION}  ·  作者 {AUTHOR}'))
        intro = QLabel('把视频处理留在本机，让日常打码与剪片更方便。\n'
                       '整脸 / 半脸 / 眼睛遮挡 · 手动补码 · 高清导出')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        rights = QLabel('免费用于个人创作、接剪辑单、广告视频和企业内部处理。\n'
                        '销售本软件或做成收费产品、API，请先联系作者取得书面授权。')
        rights.setWordWrap(True)
        layout.addWidget(rights)
        email = QLabel(f'联系邮箱：{EMAIL}')
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(email)
        row = QHBoxLayout()
        links = [('项目主页', PROJECT_URL), ('问题反馈', PROJECT_URL + '/issues/new/choose')]
        for text, url in links:
            control = QPushButton(text)
            control.clicked.connect(lambda _=False, target=url: open_link(self, target))
            row.addWidget(control)
        support = QPushButton('支持 / 打赏')
        support.clicked.connect(self.support)
        row.addWidget(support)
        layout.addLayout(row)
        legal = QPushButton('版权与第三方许可')
        legal.clicked.connect(self.licenses)
        layout.addWidget(legal)
        note = QLabel('反馈由你主动提交；程序不会自动上传视频或诊断日志。\n'
                      '自动检测可能漏检，请在导出后回看确认。')
        note.setWordWrap(True)
        note.setStyleSheet('color:#a7b0bd;font-size:12px;')
        layout.addWidget(note)
        self.dismiss = QCheckBox('以后启动时不再显示此页')
        self.dismiss.setVisible(welcome)
        layout.addWidget(self.dismiss)
        done = QPushButton('开始使用' if welcome else '关闭')
        done.setDefault(True)
        done.setStyleSheet('background:#70dec4;color:#15211f;padding:10px;font-weight:600;')
        done.clicked.connect(self.accept)
        layout.addWidget(done)

    def accept(self):
        if self.dismiss.isVisible() and self.dismiss.isChecked():
            QSettings('manba-pan', 'FacePrivacyStudio').setValue('hideWelcome', True)
        super().accept()

    def support(self):
        if SUPPORT_URL:
            open_link(self, SUPPORT_URL)
            return
        import core
        methods = [('微信', 'wechat.png'), ('支付宝', 'alipay.png')]
        codes = [(name, Path(core.ROOT) / 'assets' / 'support' / file) for name, file in methods]
        codes = [(name, path) for name, path in codes if path.is_file()]
        if codes:
            dialog = QDialog(self)
            dialog.setWindowTitle('自愿打赏支持 · ' + AUTHOR)
            layout = QVBoxLayout(dialog)
            row = QHBoxLayout()
            for name, path in codes:
                column = QVBoxLayout()
                label = QLabel(name)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                column.addWidget(label)
                pixmap = QPixmap(str(path))
                preview = QLabel()
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview.setStyleSheet('background:white;padding:14px;')
                preview.setPixmap(pixmap.scaled(260, 340, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                column.addWidget(preview)
                enlarge = QPushButton('查看原图')
                enlarge.clicked.connect(lambda _=False, p=path: open_link(dialog, QUrl.fromLocalFile(str(p)).toString()))
                column.addWidget(enlarge)
                row.addLayout(column)
            layout.addLayout(row)
            note = QLabel('请用手机扫码，并在付款页面核对收款人。\n'
                          '金额自定；打赏不影响免费功能，也不代表商业分发授权。')
            note.setWordWrap(True)
            layout.addWidget(note)
            close = QPushButton('返回')
            close.clicked.connect(dialog.accept)
            layout.addWidget(close)
            dialog.exec()
            return
        box = QMessageBox(self)
        box.setWindowTitle('支持影像工作台')
        box.setText('感谢你愿意支持这个项目。')
        box.setInformativeText('目前通过邮箱联系作者支持项目：\n' + EMAIL +
                               '\n\n支持是自愿的，不影响免费使用，也不代表取得商业分发授权。')
        contact = box.addButton('写邮件联系', QMessageBox.ButtonRole.ActionRole)
        box.addButton('返回', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == contact:
            open_link(self, 'mailto:' + EMAIL + '?subject=' + quote('支持影像工作台'))

    def licenses(self):
        import core
        root = Path(core.ROOT)
        # The installer keeps project docs next to the exe, third-party notices
        # inside _internal. Source runs keep both next to the Python modules.
        docs = root.parent if getattr(sys, 'frozen', False) else root
        file = docs / 'LICENSE'
        text = f'Copyright © 2026 {AUTHOR}\n\n'
        if file.is_file():
            text += file.read_text(encoding='utf8')
        text += ('\n\n使用 Qt / PySide6（© The Qt Company Ltd. 及贡献者），适用 LGPL v3；'
                 '独立 FFmpeg 适用其 GPL 条款。完整声明见 THIRD_PARTY。'
                 '\n项目的商业限制不适用于第三方组件。')
        box = QMessageBox(self)
        box.setWindowTitle('版权与第三方许可')
        box.setText('本项目采用免费使用与商业分发许可 1.0；第三方组件保留各自许可。')
        box.setDetailedText(text)
        folder = box.addButton('打开第三方声明', QMessageBox.ButtonRole.ActionRole)
        box.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == folder:
            open_link(self, QUrl.fromLocalFile(str(root / 'THIRD_PARTY')).toString())
