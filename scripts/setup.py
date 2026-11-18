#!/usr/bin/env python3
"""Install the native snapshot. No containers, no application-policy changes."""
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,tempfile,urllib.request
from pathlib import Path
from common import ROOT,RUNTIME,PYTHON,env_for,run,ensure_ollama,seed_settings
ASSETS={
 'Darwin':('google_maps_scraper-1.18.2-darwin-amd64','c166042f5bd00bc8ae48b9e05ab38dc029b55b8613c4d8ad2602b2f4e6b40ff4'),
 'Linux':('google_maps_scraper-1.18.2-linux-amd64','468eec927939b3bcbdd94c59d6d7eef7472b5365409d66545bbe78cc910b309f')}

def python313(explicit):
    choices=[explicit] if explicit else [sys.executable,shutil.which('python3.13')]
    for choice in choices:
        if choice and subprocess.check_output([choice,'-c','import sys; print("%s.%s"%sys.version_info[:2])'],text=True).strip()=='3.13':return choice
    raise RuntimeError('Install Python 3.13; run python3.13 scripts/setup.py (or pass --python /path/to/python3.13).')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--python');parser.add_argument('--dev',action='store_true');parser.add_argument('--install-system-deps',action='store_true',help='Linux only: let Playwright install required OS libraries; may require sudo.')
    args=parser.parse_args();system=platform.system()
    if system not in ASSETS or (system=='Linux' and platform.machine() not in {'x86_64','AMD64'}):raise RuntimeError('Supported targets: macOS (Intel/Apple Silicon with Rosetta), Linux x86-64, or Windows via x86-64 WSL2. Native Windows/ARM Linux are not verified.')
    py=python313(args.python)
    if system=='Darwin' and subprocess.run(['xcode-select','-p'],capture_output=True).returncode:
        raise RuntimeError('Install Apple Command Line Tools (xcode-select --install); some pinned Python packages build native extensions.')
    if system=='Linux' and not shutil.which('cc'):
        raise RuntimeError('Install a C compiler and Python 3.13 development headers before setup (for example build-essential/python3.13-dev on Ubuntu).')
    if not shutil.which('node') or not shutil.which('npm'):raise RuntimeError('Install Node.js 22 (includes npm).')
    if subprocess.check_output(['node','-p','process.versions.node.split(".")[0]'],text=True).strip()!='22':raise RuntimeError('Use Node.js 22 for this snapshot.')
    chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') if system=='Darwin' else shutil.which('google-chrome') or shutil.which('google-chrome-stable')
    if not chrome or not Path(chrome).exists():raise RuntimeError('Install Google Chrome from https://www.google.com/chrome/; the current native app uses BROWSER_CHANNEL=chrome.')
    if system=='Darwin' and platform.machine()=='arm64':
        if subprocess.run(['/usr/bin/arch','-x86_64','/usr/bin/true'],capture_output=True).returncode:raise RuntimeError('The pinned upstream Gosom release is Intel-only. Install Rosetta 2 using the macOS prompt, then retry.')
    env=env_for();(RUNTIME/'bin').mkdir(parents=True,exist_ok=True)
    if not PYTHON.exists():run([py,'-m','venv',ROOT/'.venv'])
    run([PYTHON,'-m','pip','install','-c',ROOT/'requirements/constraints-python313.txt','-r',ROOT/'backend/requirements-browser.txt','-r',ROOT/'maps/requirements.txt',*(['-r',ROOT/'backend/requirements-dev.txt'] if args.dev else [])],cwd=ROOT,env=env)
    run([PYTHON,'-m','pip','check'],env=env)
    run(['npm','ci','--no-audit','--no-fund'],cwd=ROOT/'frontend',env=env)
    if args.install_system_deps:
        if system!='Linux':raise RuntimeError('--install-system-deps is for Linux only.')
        run([PYTHON,'-m','playwright','install-deps','chromium'],env=env)
    name,digest=ASSETS[system];binary=RUNTIME/'bin/google-maps-scraper'
    if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest()!=digest:
        url='https://github.com/gosom/google-maps-scraper/releases/download/v1.18.2/'+name
        print('Downloading official Gosom v1.18.2...',flush=True)
        with urllib.request.urlopen(url,timeout=120) as source:
            temporary=binary.with_suffix('.download')
            with temporary.open('wb') as target:shutil.copyfileobj(source,target)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:temporary.unlink();raise RuntimeError('Gosom SHA-256 verification failed.')
        temporary.chmod(0o755);temporary.replace(binary)
    driver=RUNTIME/'maps-driver';driver.mkdir(exist_ok=True)
    for file in ['package.json','package-lock.json']:shutil.copy2(ROOT/'requirements/maps-driver'/file,driver/file)
    run(['npm','ci','--no-audit','--no-fund'],cwd=driver,env=env)
    for link,target in [(driver/'node',Path(shutil.which('node'))),(driver/'package',driver/'node_modules/playwright-core')]:
        if link.is_symlink():link.unlink()
        if not link.exists():link.symlink_to(target,target_is_directory=target.is_dir())
    run(['node',driver/'package/cli.js','install','chromium'],env=env_for('maps'))
    seed_settings()
    model=ensure_ollama(pull=True)
    receipt={'python':subprocess.check_output([PYTHON,'--version'],text=True).strip(),'node':subprocess.check_output(['node','--version'],text=True).strip(),'platform':system,'architecture':platform.machine(),'gosomVersion':'1.18.2','gosomSha256':digest,'mapsDriver':'1.61.1','ollama':model}
    (RUNTIME/'installation.json').write_text(json.dumps(receipt,indent=2))
    print('Setup complete. Start: python3 scripts/manage.py start',flush=True)
if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError,OSError) as exc:print(f'Setup failed: {exc}',file=sys.stderr);sys.exit(1)
