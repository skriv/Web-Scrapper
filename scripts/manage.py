#!/usr/bin/env python3
"""Run the same native web/API/Gosom processes with project-owned logs and PIDs."""
import argparse,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
from common import ROOT,RUNTIME,PYTHON,env_for,request_json,wait_json,port_free,ensure_ollama,seed_settings
STATE=RUNTIME/'app-process.json'

def read_state():
    try:return json.loads(STATE.read_text())
    except (FileNotFoundError,ValueError):return {}

def owned_alive(state):
    pid=state.get('pid');token=state.get('token')
    if not isinstance(pid,int) or not token:return False
    try:
        os.kill(pid,0)
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
        return str(Path(__file__).resolve()) in command and token in command
    except (OSError,subprocess.CalledProcessError):return False

def write_state(state):
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(state,indent=2));temporary.replace(STATE)

def ports(args):
    env=env_for(web_port=args.web_port,api_port=args.api_port,maps_port=args.maps_port)
    return env,{key:int(env['PROFILEKIT_'+key.upper()+'_PORT']) for key in ['web','api','maps']}

def endpoints(values):return {'web':f'http://127.0.0.1:{values["web"]}','api':f'http://127.0.0.1:{values["api"]}','maps':f'http://127.0.0.1:{values["maps"]}'}

def serve(args):
    if not PYTHON.exists() or not (RUNTIME/'installation.json').exists():raise RuntimeError('Run python3 scripts/setup.py first.')
    previous=read_state()
    if owned_alive(previous) and previous['pid']!=os.getpid():raise RuntimeError('This project is already running. Use status or stop.')
    env,values=ports(args)
    for name,port in values.items():
        if not port_free(port):raise RuntimeError(f'{name} port {port} is in use. Existing processes were not stopped. Choose other ports or stop the previous app.')
    ensure_ollama();seed_settings()
    token=args.instance or uuid.uuid4().hex
    state={'pid':os.getpid(),'token':token,'ports':values,'children':{},'status':'starting'}
    if args.instance:write_state(state)
    running=True;children={}
    def stop_signal(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop_signal);signal.signal(signal.SIGINT,stop_signal)
    commands={
       'maps':([str(PYTHON),'-m','uvicorn','adapter:app','--host','127.0.0.1','--port',str(values['maps'])],ROOT/'maps'),
       'api':([str(PYTHON),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(values['api'])],ROOT/'backend'),
       'web':(['node',str(ROOT/'frontend/node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port',str(values['web'])],ROOT/'frontend')}
    try:
        for name,(command,cwd) in commands.items():
            child_env=env_for(name,**{key+'_port':value for key,value in values.items()})
            with (RUNTIME/(name+'.log')).open('ab') as log:
                child=subprocess.Popen(command,cwd=cwd,env=child_env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            children[name]=child;state['children'][name]=child.pid
        if args.instance:write_state(state)
        urls=endpoints(values)
        wait_json(urls['maps']+'/health',process=children['maps']);wait_json(urls['api']+'/api/health',process=children['api'])
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if children['web'].poll() is not None:raise RuntimeError('Frontend exited; see .runtime/web.log')
            try:
                import urllib.request
                with urllib.request.urlopen(urls['web'],timeout=5) as response:
                    if response.status==200:break
            except OSError:time.sleep(.25)
        else:raise RuntimeError('Frontend did not become ready.')
        state['status']='running'
        if args.instance:write_state(state)
        print(f'SKRIV Scrapper ready: {urls["web"]}\nCtrl+C stops app processes. Ollama stays available.',flush=True)
        while running:
            for name,child in children.items():
                if child.poll() is not None:raise RuntimeError(f'{name} exited ({child.returncode}); see .runtime/{name}.log')
            time.sleep(.25)
    finally:
        for child in children.values():
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
        for child in children.values():
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        if read_state().get('token')==token:STATE.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['run','start','stop','status','check']);parser.add_argument('--web-port',type=int);parser.add_argument('--api-port',type=int);parser.add_argument('--maps-port',type=int);parser.add_argument('--instance',help=argparse.SUPPRESS);parser.add_argument('--exact-settings',action='store_true')
    args=parser.parse_args();RUNTIME.mkdir(exist_ok=True)
    if args.command=='run':return serve(args)
    state=read_state()
    if args.command=='stop':
        if not owned_alive(state):print('No owned background app is running. No process was killed.');return
        os.kill(state['pid'],signal.SIGTERM)
        for _ in range(120):
            if not owned_alive(state):print('App stopped. Settings/data and the independent Ollama server were preserved.');return
            time.sleep(.25)
        raise RuntimeError('Shutdown still in progress; inspect .runtime/launcher.log.')
    if args.command=='start':
        if owned_alive(state):print('Already running: '+endpoints(state['ports'])['web']);return
        _,values=ports(args)
        for name,port in values.items():
            if not port_free(port):raise RuntimeError(f'{name} port {port} is in use; no existing process was stopped.')
        token=uuid.uuid4().hex;cmd=[str(PYTHON),str(Path(__file__).resolve()),'run','--instance',token]
        for name,port in values.items():cmd += ['--'+name+'-port',str(port)]
        with (RUNTIME/'launcher.log').open('ab') as log:process=subprocess.Popen(cmd,cwd=ROOT,env=env_for(),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        wait_json(endpoints(values)['api']+'/api/health',timeout=75,process=process)
        for _ in range(240):
            if process.poll() is not None:raise RuntimeError('Startup failed; inspect .runtime/launcher.log.')
            if read_state().get('status')=='running':print('SKRIV Scrapper ready: '+endpoints(values)['web']);return
            time.sleep(.25)
        raise RuntimeError('Startup is still in progress; inspect .runtime/launcher.log.')
    _,default_ports=ports(args);values=state.get('ports',default_ports);urls=endpoints(values)
    if args.command=='status':print(json.dumps({'backgroundRunning':owned_alive(state),'urls':urls,'logs':str(RUNTIME)},indent=2));return
    health=request_json(urls['api']+'/api/health')
    actual=request_json(urls['api']+'/api/scraping/settings');expected=json.loads((ROOT/'config/settings.json').read_text())
    diff={key:{'snapshot':value,'actual':actual.get(key)} for key,value in expected.items() if actual.get(key)!=value}
    providers=request_json(urls['api']+'/api/scraping/providers/health',timeout=20)
    checks={'health':health,'settingsMatchSnapshot':not diff,'settingsDifferences':diff,'ollama':ensure_ollama(),'mapsAdapter':request_json(urls['maps']+'/health'),'providers':providers,'urls':urls}
    (RUNTIME/'health.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
    available={row['id']:row['status'] for row in providers['providers']}
    for name in ['sonagara','scrapling','gosom','crawl4ai','playwright','ollama']:
        if available.get(name)!='ready':raise RuntimeError(f'Provider {name} is not ready; inspect the health report.')
    if args.exact_settings and diff:raise RuntimeError('Settings differ from the shipped snapshot; no settings were changed.')
if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,subprocess.CalledProcessError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
