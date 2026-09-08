"""Explicit allowlists keep personal media and project files out of archives."""
from pathlib import Path
import hashlib,shutil,zipfile,json
BASE=Path(__file__).parent
VERSION='0.3.1'
RELEASE=BASE/'releases'/VERSION/'影像工作台'
DOCS=['README.md','ARCHITECTURE.md','STUDIO_VERIFICATION.md','LICENSE','COMMERCIAL.md','CONTRIBUTING.md','DISTRIBUTION.md']
SOURCE_FILES=['studio.py','core.py','exporter.py','project_info.py','acceptance.py','startup_log.py','app.py','Studio.spec','FacePrivacy.spec',
    'requirements.txt','collect_notices.py','package_studio.py','build_installer.py','.gitignore','.gitattributes',*DOCS,
    'tests/verify.py','tests/verify_studio_export.py','tests/studio_ui_smoke.py']

def zip_and_check(target,files):
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path,name in files:archive.write(path,name)
    with zipfile.ZipFile(target) as archive:
        bad=archive.testzip()
        if bad:raise RuntimeError('Archive integrity failure: '+bad)
        names=archive.namelist()
        forbidden=[n for n in names if any(p in ('samples','portable_validation','__pycache__') for p in Path(n).parts) or Path(n).suffix.lower() in ('.mp4','.mov','.mkv','.wav') or n.endswith('.privacy.json')]
        if forbidden:raise RuntimeError('Private/test artifact in archive: '+str(forbidden))
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256.txt').write_text(f'{digest}  {target.name}\n',encoding='utf8')
    return {'file':str(target),'bytes':target.stat().st_size,'sha256':digest,'entries':len(names),'no_video_or_project_files':True}

def main():
    for name in DOCS:shutil.copy2(BASE/name,RELEASE/name)
    portable=BASE/'releases'/f'影像工作台_{VERSION}_Windows_x64_便携版.zip'
    report=[zip_and_check(portable,[(p,p.relative_to(RELEASE.parent).as_posix()) for p in RELEASE.rglob('*') if p.is_file()])]
    files=[(BASE/n,'face-privacy-studio/'+n) for n in SOURCE_FILES]
    for folder in ['models','assets','THIRD_PARTY','.github']:
        files.extend((p,'face-privacy-studio/'+p.relative_to(BASE).as_posix()) for p in (BASE/folder).rglob('*') if p.is_file() and p.suffix.lower() not in ('.py','.pyc','.pyo'))
    source=BASE/'releases'/f'影像工作台_{VERSION}_源码整理包.zip'
    report.append(zip_and_check(source,files))
    (BASE/'releases'/f'{VERSION}_package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
