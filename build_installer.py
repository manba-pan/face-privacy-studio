"""Build a per-user NSIS installer from the verified PyInstaller folder.

The uninstaller removes only manifest-listed files, never recursively deletes
the install folder, and therefore preserves videos/projects added by users.
"""
from pathlib import Path
import argparse
import hashlib
import subprocess

BASE = Path(__file__).resolve().parent
VERSION = '0.4.1'
PAYLOAD = BASE / 'releases' / VERSION / '视频一键打码工具'


def nsis(text):
    return str(text).replace('$', '$$').replace('"', '$\\"')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--makensis', required=True)
    args = parser.parse_args()
    files = sorted(p for p in PAYLOAD.rglob('*') if p.is_file())
    if not (PAYLOAD / '视频一键打码工具.exe').is_file():
        raise SystemExit('Build Studio.spec and package_studio.py first.')
    for file in files:
        name = file.relative_to(PAYLOAD).as_posix().lower()
        if ('virtualkeyboard' in name or name.endswith(('.mp4', '.mov', '.mkv', '.wav', '.privacy.json'))
                or any(part in ('samples', 'portable_validation') for part in Path(name).parts)):
            raise SystemExit('Unexpected installer file: ' + name)
    build = BASE / 'build' / 'installer'
    build.mkdir(parents=True, exist_ok=True)
    target = BASE / 'releases' / f'VideoRedactor-{VERSION}-Setup.exe'
    lines = [
        'Unicode true', '!include "MUI2.nsh"', '!include "x64.nsh"',
        'Name "视频一键打码工具"', f'OutFile "{nsis(target)}"',
        'InstallDir "$LOCALAPPDATA\\Programs\\FacePrivacyStudio"',
        'RequestExecutionLevel user', 'SetCompressor /SOLID lzma',
        'ShowInstDetails show', 'ShowUninstDetails show',
        f'VIProductVersion "{VERSION}.0"',
        'VIAddVersionKey "ProductName" "视频一键打码工具"',
        f'VIAddVersionKey "FileVersion" "{VERSION}"',
        'VIAddVersionKey "CompanyName" "manba-pan"',
        'VIAddVersionKey "LegalCopyright" "Copyright (c) 2026 manba-pan"',
        'VIAddVersionKey "FileDescription" "视频一键打码工具安装程序"',
        '!define MUI_ABORTWARNING',
        '!define MUI_WELCOMEPAGE_TITLE "欢迎安装视频一键打码工具"',
        '!define MUI_WELCOMEPAGE_TEXT "视频打码，少一点折腾。$\\r$\\n作者：manba-pan$\\r$\\n$\\r$\\n整脸、半脸、眼睛，选好范围就开工。$\\r$\\n$\\r$\\n自动识别配合手动补码；没有检测到脸时，默认保留原画面。$\\r$\\n$\\r$\\n免费用于个人创作、接剪辑单和商业视频。自愿打赏不影响任何功能。$\\r$\\n$\\r$\\n安装到当前用户目录，不需要安装 Python。"',
        '!insertmacro MUI_PAGE_WELCOME',
        f'!insertmacro MUI_PAGE_LICENSE "{nsis(BASE / "LICENSE")}"',
        '!insertmacro MUI_PAGE_INSTFILES', '!insertmacro MUI_PAGE_FINISH',
        '!insertmacro MUI_UNPAGE_CONFIRM', '!insertmacro MUI_UNPAGE_INSTFILES',
        '!insertmacro MUI_LANGUAGE "SimpChinese"',
        'Function .onInit', '${IfNot} ${RunningX64}',
        'MessageBox MB_ICONSTOP "本程序需要 Windows x64。"', 'Abort', '${EndIf}',
        'SetShellVarContext current', 'FunctionEnd',
        'Section "安装"', 'SetShellVarContext current', 'SetOverwrite ifnewer',
        # Migrate only the previous product's registered entry point/shortcuts.
        'ReadRegStr $0 HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FacePrivacyStudio" "DisplayName"',
        'ReadRegStr $1 HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FacePrivacyStudio" "InstallLocation"',
        '${If} $0 == "影像工作台"', '${AndIf} $1 == $INSTDIR',
        'IfFileExists "$INSTDIR\\影像工作台.exe" 0 old_entry_removed',
        'ClearErrors', 'Delete "$INSTDIR\\影像工作台.exe"',
        '${If} ${Errors}',
        'MessageBox MB_ICONSTOP "请先关闭旧版影像工作台，再重新安装。" /SD IDOK',
        'Abort', '${EndIf}', 'old_entry_removed:',
        'Delete "$DESKTOP\\影像工作台.lnk"',
        'Delete "$SMPROGRAMS\\影像工作台\\影像工作台.lnk"',
        'Delete "$SMPROGRAMS\\影像工作台\\卸载.lnk"',
        'RMDir "$SMPROGRAMS\\影像工作台"', '${EndIf}',
    ]
    last_parent = None
    for file in files:
        relative = file.relative_to(PAYLOAD)
        parent = str(relative.parent)
        if parent != last_parent:
            suffix = '' if parent == '.' else '\\' + nsis(parent)
            lines.append(f'SetOutPath "$INSTDIR{suffix}"')
            last_parent = parent
        lines.append(f'File "{nsis(file)}"')
    lines += [
        'SetOutPath "$INSTDIR"', 'WriteUninstaller "$INSTDIR\\Uninstall.exe"',
        'CreateShortcut "$DESKTOP\\视频一键打码工具.lnk" "$INSTDIR\\视频一键打码工具.exe"',
        'CreateDirectory "$SMPROGRAMS\\视频一键打码工具"',
        'CreateShortcut "$SMPROGRAMS\\视频一键打码工具\\视频一键打码工具.lnk" "$INSTDIR\\视频一键打码工具.exe"',
        'CreateShortcut "$SMPROGRAMS\\视频一键打码工具\\卸载.lnk" "$INSTDIR\\Uninstall.exe"',
    ]
    reg = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FacePrivacyStudio'
    for key, value in [('DisplayName', '视频一键打码工具'), ('DisplayVersion', VERSION),
                       ('Publisher', 'manba-pan'), ('InstallLocation', '$INSTDIR'),
                       ('URLInfoAbout', 'https://github.com/manba-pan/face-privacy-studio')]:
        lines.append(f'WriteRegStr HKCU "{reg}" "{key}" "{value}"')
    lines += [f'WriteRegStr HKCU "{reg}" "UninstallString" \'"$INSTDIR\\Uninstall.exe"\'',
              f'WriteRegDWORD HKCU "{reg}" "NoModify" 1',
              f'WriteRegDWORD HKCU "{reg}" "NoRepair" 1', 'SectionEnd',
              'Section "Uninstall"', 'SetShellVarContext current']
    for file in files:
        lines.append(f'Delete "$INSTDIR\\{nsis(file.relative_to(PAYLOAD))}"')
    directories = sorted({p.parent for p in files}, key=lambda p: len(p.parts), reverse=True)
    for directory in directories:
        if directory != PAYLOAD:
            lines.append(f'RMDir "$INSTDIR\\{nsis(directory.relative_to(PAYLOAD))}"')
    lines += ['Delete "$INSTDIR\\Uninstall.exe"', 'RMDir "$INSTDIR"',
              'Delete "$DESKTOP\\视频一键打码工具.lnk"',
              'Delete "$SMPROGRAMS\\视频一键打码工具\\视频一键打码工具.lnk"',
              'Delete "$SMPROGRAMS\\视频一键打码工具\\卸载.lnk"',
              'RMDir "$SMPROGRAMS\\视频一键打码工具"', f'DeleteRegKey HKCU "{reg}"',
              'SectionEnd']
    script = build / 'FacePrivacyStudio.nsi'
    script.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    subprocess.run([args.makensis, '/V2', str(script)], check=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256.txt').write_text(f'{digest}  {target.name}\n', encoding='utf8')
    print(f'Installer: {target}\nBytes: {target.stat().st_size}\nSHA256: {digest}')


if __name__ == '__main__':
    main()
