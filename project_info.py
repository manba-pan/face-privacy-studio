"""Author, support and feedback UI. Opening a dialog never contacts a server."""
from pathlib import Path
import shutil
import sys
from urllib.parse import quote

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                              QLabel, QMessageBox, QPushButton, QScrollArea,
                              QVBoxLayout, QWidget)

VERSION = '0.3.2'
AUTHOR = 'manba-pan'
EMAIL = '2087725636@qq.com'
PROJECT_URL = 'https://github.com/manba-pan/face-privacy-studio'
SUPPORT_URL = ''

STYLE = '''
QDialog#projectDialog {background:#13171e;}
QFrame#hero {background:#192923;border:1px solid #345c4e;border-radius:14px;}
QFrame#featureCard,QFrame#creatorCard,QFrame#paymentCard {background:#1b212b;border:1px solid #303b49;border-radius:10px;}
QLabel#eyebrow {color:#78dfbd;font-size:11px;font-weight:600;}
QLabel#heroTitle {color:#effbf5;font-size:29px;font-weight:700;}
QLabel#sectionTitle {color:#f0f3f8;font-size:16px;font-weight:600;}
QLabel#cardTitle {color:#edf3f7;font-size:14px;font-weight:600;}
QLabel#copy {color:#aebccc;font-size:12px;}
QLabel#small {color:#8c9bae;font-size:11px;}
QLabel#step {color:#70dec4;font-size:20px;font-weight:700;}
QLabel#authorAvatar {background:#344c44;color:#9de9cd;font-size:18px;font-weight:700;border-radius:22px;}
QPushButton#primary {background:#70dec4;color:#10271f;border:none;padding:11px 18px;font-weight:700;}
QPushButton#supportButton {background:#344739;color:#d8efbe;border:1px solid #4b6851;}
QScrollArea#projectScroll,QWidget#projectBody {background:transparent;border:none;}
'''


def text_label(text, name='copy'):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def action(text, callback, name=None):
    control = QPushButton(text)
    if name:
        control.setObjectName(name)
    control.clicked.connect(callback)
    return control


def open_link(parent, url):
    if not QDesktopServices.openUrl(QUrl(url)):
        QMessageBox.information(parent, '打开链接', '系统未能打开链接，可以复制后打开：\n\n' + url)


def payment_codes():
    import core
    root = Path(core.ROOT) / 'assets' / 'support'
    result = []
    for name, slug, color in [('支付宝', 'alipay', '#64b9ff'), ('微信', 'wechat', '#70dec4')]:
        for extension in ('.jpg', '.png', '.jpeg'):
            path = root / (slug + extension)
            if path.is_file():
                result.append((name, path, color))
                break
    return result


def qr_label(parent, path, side, name):
    image = QLabel()
    image.setObjectName(name)
    image.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image.setFixedSize(side+24, side+24)
    image.setStyleSheet('background:white;border-radius:8px;color:#222;')
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        image.setText('图片加载失败，请联系作者。')
    else:
        dpr = parent.devicePixelRatioF()
        # Area resampling avoids uneven QR modules at fractional Windows DPI.
        # This changes only the display pixmap; save/export uses the original.
        shown = pixmap.scaled(round(side*dpr), round(side*dpr), Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        shown.setDevicePixelRatio(dpr)
        image.setPixmap(shown)
    return image


class ScrollDialog(QDialog):
    def __init__(self, parent, title, width, height):
        super().__init__(parent)
        self.setObjectName('projectDialog')
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        available = self.screen().availableGeometry()
        self.resize(min(width, available.width()-48), min(height, available.height()-64))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setObjectName('projectScroll')
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('projectBody')
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(12, 10, 12, 10)
        self.body.setSpacing(15)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(12, 6, 12, 4)
        layout.addLayout(self.footer)


class QRDialog(ScrollDialog):
    def __init__(self, parent, name, path):
        super().__init__(parent, name + '收款码 · ' + AUTHOR, 640, 720)
        self.body.addWidget(text_label(name + ' · 自愿支持', 'sectionTitle'))
        side = min(530, self.width()-100, self.height()-170)
        self.body.addWidget(qr_label(self, path, side, 'fullPaymentCode'), 0, Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(text_label('扫码后请核对收款人，金额随心。', 'small'))
        self.footer.addWidget(action('保存原图', lambda: self.save_code(name, path)))
        self.footer.addStretch()
        self.footer.addWidget(action('返回', self.accept))

    def save_code(self, name, path):
        selected, _ = QFileDialog.getSaveFileName(self, '保存收款码原图',
            f'{AUTHOR}-{name}{path.suffix}', f'原始图片 (*{path.suffix})')
        if selected:
            try:
                if Path(selected).resolve() != path.resolve():
                    shutil.copyfile(path, selected)
            except OSError as error:
                QMessageBox.information(self, '保存未完成', str(error))


class SupportDialog(ScrollDialog):
    def __init__(self, parent=None):
        super().__init__(parent, '给作者加点续航 · 自愿打赏', 740, 680)
        self.body.addWidget(text_label('SUPPORT THE PROJECT', 'eyebrow'))
        self.body.addWidget(text_label('觉得好用？给作者加点续航。', 'sectionTitle'))
        self.body.addWidget(text_label('一杯茶、一句建议、一个 Bug 反馈，都能让这个小工具再往前走一点。'))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.qr_labels = []
        self.codes = payment_codes()
        for name, path, color in self.codes:
            frame = QFrame()
            frame.setObjectName('paymentCard')
            column = QVBoxLayout(frame)
            column.setContentsMargins(16, 16, 16, 16)
            column.setSpacing(12)
            title = text_label(name, 'sectionTitle')
            title.setStyleSheet(f'color:{color};')
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(title)
            image = qr_label(self, path, 256, 'paymentCode')
            column.addWidget(image, 0, Qt.AlignmentFlag.AlignCenter)
            self.qr_labels.append(image)
            subtitle = text_label('用' + name + '扫一扫 · 金额自定', 'small')
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(subtitle)
            column.addWidget(action('放大查看 / 保存原图',
                lambda _=False, n=name, p=path: QRDialog(self, n, p).exec()))
            row.addWidget(frame)
        if self.codes:
            self.body.addLayout(row)
        else:
            self.body.addWidget(text_label('收款码尚未配置，可以通过邮箱联系作者：' + EMAIL))
        self.body.addWidget(text_label('随心支持，量力而行。付不付款，功能都一样。\n'
            '打赏不等于商业分发授权；付款前请在支付页面核对收款人。', 'small'))
        self.body.addStretch()
        self.footer.addWidget(action('联系作者',
            lambda: open_link(self, 'mailto:' + EMAIL + '?subject=' + quote('支持视频一键打码工具'))))
        self.footer.addStretch()
        self.footer.addWidget(action('继续剪片', self.accept, 'primary'))


class ProjectDialog(ScrollDialog):
    def __init__(self, parent=None, welcome=False):
        super().__init__(parent, '欢迎使用视频一键打码工具' if welcome else '作者 · 支持 · 反馈', 740, 610)
        self.welcome = welcome
        hero = QFrame()
        hero.setObjectName('hero')
        heading = QVBoxLayout(hero)
        heading.setContentsMargins(23, 19, 23, 20)
        heading.setSpacing(10)
        heading.addWidget(text_label(f'视频一键打码工具   /   FACE PRIVACY STUDIO   /   {VERSION}', 'eyebrow'))
        heading.addWidget(text_label('视频一键打码工具', 'heroTitle'))
        heading.addWidget(text_label('人脸可以打码，操作别太烧脑。\n整脸、半脸、眼睛，选好范围就开工。'))
        self.body.addWidget(hero)
        cards = QHBoxLayout()
        cards.setSpacing(10)
        for number, title, detail in [('01', '遮得有分寸', '整脸、半脸、眼睛\n范围和强度由你定。'),
                                      ('02', '原画默认保留', '没检测到脸？\n默认保留原画面。'),
                                      ('03', '成片认真交', '保留源尺寸与帧率\n原音轨可直接复制。')]:
            frame = QFrame()
            frame.setObjectName('featureCard')
            column = QVBoxLayout(frame)
            column.setContentsMargins(15, 13, 15, 15)
            column.addWidget(text_label(number, 'step'))
            column.addWidget(text_label(title, 'cardTitle'))
            column.addWidget(text_label(detail, 'small'))
            cards.addWidget(frame, 1)
        self.body.addLayout(cards)
        self.body.addWidget(text_label('拖入视频   →   选择遮挡   →   回看补码   →   导出收工'))
        creator = QFrame()
        creator.setObjectName('creatorCard')
        row = QHBoxLayout(creator)
        row.setContentsMargins(16, 14, 16, 14)
        avatar = text_label('mp', 'authorAvatar')
        avatar.setFixedSize(44, 44)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(avatar)
        identity = QVBoxLayout()
        identity.addWidget(text_label('由 ' + AUTHOR + ' 维护', 'cardTitle'))
        email = text_label(EMAIL, 'small')
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity.addWidget(email)
        row.addLayout(identity, 1)
        row.addWidget(action('支持 / 打赏', self.support, 'supportButton'))
        self.body.addWidget(creator)
        links = QHBoxLayout()
        for text, url in [('项目主页', PROJECT_URL), ('问题反馈 · 来抓虫', PROJECT_URL + '/issues/new/choose')]:
            links.addWidget(action(text, lambda _=False, target=url: open_link(self, target)))
        links.addWidget(action('使用许可', self.licenses))
        self.body.addLayout(links)
        self.body.addWidget(text_label('免费使用，如有商业化等请联系作者\n'
            '模型可能漏脸，支持人工复查', 'small'))
        self.body.addStretch()
        self.dismiss = QCheckBox('以后启动时不再显示')
        self.dismiss.setVisible(welcome)
        self.footer.addWidget(self.dismiss)
        self.footer.addStretch()
        done = action('开始处理视频' if welcome else '返回视频', self.accept, 'primary')
        done.setDefault(True)
        self.footer.addWidget(done)

    def accept(self):
        if self.welcome and self.dismiss.isChecked():
            QSettings('manba-pan', 'FacePrivacyStudio').setValue('hideWelcome', True)
        super().accept()

    def support(self):
        if SUPPORT_URL:
            open_link(self, SUPPORT_URL)
        else:
            SupportDialog(self).exec()

    def licenses(self):
        import core
        root = Path(core.ROOT)
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
