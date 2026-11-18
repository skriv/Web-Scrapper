"""Native packaging helpers only; extraction code is unchanged."""
from pathlib import Path
import json,os,shutil,socket,subprocess,time,urllib.request,urllib.error
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
PYTHON=ROOT/'.venv/bin/python'
MODEL='qwen3:4b'
MODEL_DIGEST='359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7'
OLLAMA_VERSION='0.40.16'

def load_env():
    values={}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            key,sep,value=line.partition('=')
            if not sep or not key.replace('_','').isalnum():raise RuntimeError('Invalid .env assignment')
            values[key]=value.strip().strip('\"\'')
    return {**values,**os.environ}

def env_for(component='api',web_port=None,api_port=None,maps_port=None):
    env=load_env();RUNTIME.mkdir(exist_ok=True)
    web=int(web_port or env.get('PROFILEKIT_WEB_PORT',3002));api=int(api_port or env.get('PROFILEKIT_API_PORT',8002));maps=int(maps_port or env.get('PROFILEKIT_MAPS_PORT',8003))
    for port in [web,api,maps]:
        if not 1024<=port<=65535:raise RuntimeError('Ports must be between 1024 and 65535')
    if len({web,api,maps})!=3:raise RuntimeError('App ports must be distinct')
    env.update({'PROFILEKIT_WEB_PORT':str(web),'PROFILEKIT_API_PORT':str(api),'PROFILEKIT_MAPS_PORT':str(maps),
       'PROFILEKIT_DATA_DIR':str(ROOT/'data'),'PYTHONPATH':str(ROOT/'backend'),'DATABASE_URL':'','REDIS_URL':'',
       'MAPS_BASE_URL':f'http://127.0.0.1:{maps}','API_BASE_URL':f'http://127.0.0.1:{api}',
       'ENABLE_BROWSER':'true','BROWSER_CHANNEL':'chrome','BROWSER_BASE_URL':'',
       'GOSOM_BINARY':str(RUNTIME/'bin/google-maps-scraper'),'PLAYWRIGHT_DRIVER_PATH':str(RUNTIME/'maps-driver'),
       'PLAYWRIGHT_BROWSERS_PATH':str(RUNTIME/'browsers/maps'),
       'NEXT_TELEMETRY_DISABLED':'1','DISABLE_TELEMETRY':'1','ANONYMIZED_TELEMETRY':'false','CRAWL4AI_TELEMETRY_ENABLED':'false','CRAWL4AI_NO_CLOUD_NOTICE':'1',
       'CRAWL4AI_BASE_DIRECTORY':str(RUNTIME/'crawl4ai'),'PIP_CACHE_DIR':str(RUNTIME/'cache/pip'),'npm_config_cache':str(RUNTIME/'cache/npm')})
    env.setdefault('OLLAMA_BASE_URL','http://127.0.0.1:11434')
    return env

def request_json(url,method='GET',body=None,timeout=5):
    request=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=timeout) as response:return json.load(response)

def wait_json(url,timeout=60,process=None):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if process and process.poll() is not None:raise RuntimeError(f'Service exited ({process.returncode}); see .runtime/*.log')
        try:return request_json(url,timeout=2)
        except (OSError,ValueError):time.sleep(.25)
    raise RuntimeError(f'Service did not become ready: {url}')

def port_free(port):
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1',port))==0:return False
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(('127.0.0.1',port));return True
        except OSError:return False

def run(args,**kwargs):
    print('+ '+' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True,**kwargs)

def ensure_ollama(pull=False):
    env=env_for();base=env['OLLAMA_BASE_URL'].rstrip('/')
    try:version=request_json(base+'/api/version')
    except OSError:
        binary=shutil.which('ollama')
        if not binary:raise RuntimeError('Install Ollama 0.40.16 from https://github.com/ollama/ollama/releases/tag/v0.40.16, then rerun setup.')
        if base!='http://127.0.0.1:11434':raise RuntimeError('Start the configured Ollama endpoint before continuing.')
        server_env={**env,'OLLAMA_HOST':'127.0.0.1:11434','OLLAMA_MODELS':str(RUNTIME/'models'),'OLLAMA_KEEP_ALIVE':'10m','OLLAMA_NUM_PARALLEL':'1','OLLAMA_MAX_LOADED_MODELS':'1','OLLAMA_NO_CLOUD':'1'}
        with (RUNTIME/'ollama.log').open('ab') as log:
            process=subprocess.Popen([binary,'serve'],env=server_env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (RUNTIME/'ollama-owned.json').write_text(json.dumps({'pid':process.pid,'note':'Independent model service, kept running when the app stops.'}))
        version=wait_json(base+'/api/version',process=process)
    if version.get('version')!=OLLAMA_VERSION:raise RuntimeError(f'Ollama version is {version.get("version")}; this snapshot is verified with {OLLAMA_VERSION}. No software was replaced.')
    models=request_json(base+'/api/tags').get('models',[])
    found=next((m for m in models if m.get('name')==MODEL),None)
    if not found and pull:
        binary=shutil.which('ollama')
        if not binary:raise RuntimeError('The Ollama CLI is required to download the missing model.')
        run([binary,'pull',MODEL],env={**env,'OLLAMA_HOST':base})
        found=next((m for m in request_json(base+'/api/tags').get('models',[]) if m.get('name')==MODEL),None)
    if not found:raise RuntimeError('qwen3:4b is not installed. Run scripts/setup.py to download it.')
    if found.get('digest')!=MODEL_DIGEST:raise RuntimeError('qwen3:4b digest differs from the verified snapshot; no model was overwritten. See docs/installation.md.')
    return {'version':version['version'],'model':MODEL,'digest':found['digest']}

def seed_settings():
    run([PYTHON,ROOT/'scripts/seed_settings.py'],cwd=ROOT,env=env_for())
