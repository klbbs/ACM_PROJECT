"""Build a deployment archive from an explicit source allow-list."""
import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DIRECTORIES=('src/acmweb','src/ojcrawler','tests','docs','examples','scripts','deploy','frontend/src','frontend/e2e','frontend/public','frontend/dist','.github')
FILES=('README.md','CHANGELOG.md','pyproject.toml','requirements.lock','accounts.example.toml','.env.example','.gitignore','.dockerignore','Dockerfile','compose.yaml','frontend/package.json','frontend/package-lock.json','frontend/index.html','frontend/tsconfig.json','frontend/vite.config.ts','frontend/vitest.config.ts','frontend/playwright.config.ts','.prettierrc.json')


def package(output,skip_build=False):
    if not skip_build:
        subprocess.run(['npm','run','build'],cwd=ROOT/'frontend',check=True)
    if not (ROOT/'frontend/dist/index.html').exists():
        raise RuntimeError('缺少前端构建产物')
    paths=[]
    for directory in DIRECTORIES:
        if (ROOT/directory).exists():
            paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and not any(x in p.parts for x in ['__pycache__','.pytest_cache','node_modules']) and p.suffix not in {'.pyc','.log'})
    paths.extend(ROOT/name for name in FILES if (ROOT/name).is_file())
    paths=sorted(set(paths))
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    try:commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:commit='unversioned'
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp)
        manifest=[]
        for path in paths:
            relative=path.relative_to(ROOT)
            if path.is_symlink():raise RuntimeError(f'发布包不接受符号链接: {relative}')
            data=path.read_bytes()
            target=base/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            manifest.append(hashlib.sha256(data).hexdigest()+'  '+relative.as_posix())
        (base/'MANIFEST.sha256').write_text('\n'.join(manifest)+'\n')
        (base/'BUILD_INFO.json').write_text(json.dumps({'version':'0.16.0','commit':commit,'built_at':datetime.now(timezone.utc).isoformat()},indent=2)+'\n')
        prefix='acm-lab-0.16.0'
        with tarfile.open(output/(prefix+'.tar.gz'),'w:gz') as archive:
            for path in sorted(base.rglob('*')):
                if path.is_file():archive.add(path,arcname=prefix+'/'+path.relative_to(base).as_posix())
        with zipfile.ZipFile(output/(prefix+'.zip'),'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(base.rglob('*')):
                if path.is_file():archive.write(path,prefix+'/'+path.relative_to(base).as_posix())
    for path in [output/(prefix+'.zip'),output/(prefix+'.tar.gz')]:print(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='构建无凭据的 ACM Lab 部署包')
    parser.add_argument('--output',default=str(ROOT/'releases'))
    parser.add_argument('--skip-build',action='store_true')
    args=parser.parse_args();package(args.output,args.skip_build)
