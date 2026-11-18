"""Cancellable local jobs, replayable SSE and real sequential provider comparisons."""
import asyncio
import copy
import json
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from statistics import median

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .configuration import ScrapingSettings
from .engine import ScrapeRequest,execute,load_settings,save_settings,percentile,detect_input
from .providers import REGISTRY
from .storage import store,cache_key

router=APIRouter(prefix='/api/scraping')
slots=asyncio.Semaphore(2)

@dataclass
class Job:
    id: str
    request: ScrapeRequest
    config: ScrapingSettings
    key: str
    created: float=field(default_factory=time.monotonic)
    status: str='queued'
    finished: float | None=None
    events: list=field(default_factory=list)
    seq: int=0
    result: dict | None=None
    error: str | None=None
    task: asyncio.Task | None=None
    changed: asyncio.Event=field(default_factory=asyncio.Event)
    def emit(self,event,data):
        self.seq+=1
        self.events.append({'id':self.seq,'event':event,'data':copy.deepcopy(data)})
        self.events=self.events[-80:]
        self.changed.set()
    def payload(self):
        return {'id':self.id,'status':self.status,'result':self.result,'error':self.error,'eventsUrl':f'/api/scraping/jobs/{self.id}/events'}

jobs:dict[str,Job]={}

async def shutdown_jobs():
    pending=[job.task for job in jobs.values() if job.task and not job.task.done()]
    for task in pending: task.cancel()
    if pending: await asyncio.gather(*pending,return_exceptions=True)

async def run(job):
    try:
        async with slots:
            job.status='running'; job.emit('status',{'status':job.status})
            async def progress(snapshot): job.emit('progress',snapshot)
            async with asyncio.timeout(1200 if job.request.mode=='compare' else 245):
                job.result=await compare(job,progress) if job.request.mode=='compare' else await execute(job.request,progress,job.config)
            job.status='complete'; job.emit('complete',job.result)
    except asyncio.CancelledError:
        job.status='cancelled'; job.emit('cancelled',{'detail':'Cancelled by the user.'})
    except Exception as exc:
        job.status='error'; job.error=str(exc)[:500] or 'Extraction timed out.'
        job.emit('error',{'detail':job.error})
    finally:
        job.finished=time.monotonic()
        asyncio.get_running_loop().call_later(600,lambda job_id=job.id: jobs.pop(job_id,None))

async def compare(job,progress):
    request=job.request
    providers=request.providers
    if not 2<=len(providers)<=4 or len(set(providers))!=len(providers): raise ValueError('Select 2–4 distinct providers.')
    kind,_=detect_input(request.input)
    for provider in providers:
        capabilities=REGISTRY[provider].capabilities
        if kind=='website' and 'website' not in capabilities: raise ValueError('For a website comparison, select website engines.')
        if kind!='website' and not ('maps' in capabilities or 'discovery' in capabilities): raise ValueError('For company/Maps comparison, select compatible Maps/discovery engines.')
    runs={provider:[] for provider in providers}
    # Rotate ordering each round to reduce systematic order bias. No automated mass runs.
    for index in range(request.repeats):
        order=providers[index%len(providers):]+providers[:index%len(providers)]
        for provider in order:
            single=request.model_copy(update={'mode':'single','provider':provider,'providers':[],'bypassCache':True})
            start=time.monotonic()
            try:
                result=await execute(single,None,job.config)
                record={'run':index+1,'success':result['metrics']['success'],'metrics':result['metrics'],'result':result}
            except Exception as exc:
                record={'run':index+1,'success':False,'error':str(exc)[:400],'metrics':{'totalMs':round((time.monotonic()-start)*1000),'completeness':0,'cache':'bypass'}}
            runs[provider].append(record)
            job.emit('comparison_run',{'provider':provider,**record})
    summary=[]
    for provider,records in runs.items():
        durations=[r['metrics']['totalMs'] for r in records]
        summary.append({'provider':provider,'runs':len(records),'medianMs':median(durations),'p90Ms':percentile(durations,.9),'p95Ms':percentile(durations,.95),'successRate':sum(r['success'] for r in records)/len(records),'averageCompleteness':sum(r['metrics'].get('completeness',0) for r in records)/len(records)})
    return {'input':request.input,'runs':runs,'summary':summary,'cachePolicy':'Application cache bypassed for all single-engine comparisons. OS/DNS/upstream/browser caches are not flushed; first observed round is not a guaranteed cold machine.','methodology':'Real sequential requests, rotated order. Percentiles use linear interpolation, include failed-run durations. Small samples are descriptive, not evidence of an engine speed ranking.','settings':job.config.model_dump()}

@router.get('/settings')
async def get_settings(): return (await load_settings()).model_dump()
@router.put('/settings')
async def put_settings(body:ScrapingSettings): return (await save_settings(body)).model_dump()

@router.get('/providers')
@router.get('/providers/health')
async def providers():
    cfg=await load_settings()
    async def health(provider):
        try:
            async with asyncio.timeout(4): result=await provider.health_check()
        except Exception: result={'status':'unavailable','detail':'Health check timed out or failed.'}
        return {'id':provider.id,'name':provider.name,'capabilities':provider.capabilities,'enabled':provider.id in cfg.enabledProviders,**result}
    rows=await asyncio.gather(*(health(p) for p in REGISTRY.values()))
    return {'providers':rows,'infrastructure':await store.health()}

@router.post('/extract',status_code=202)
@router.post('/test',status_code=202)
@router.post('/compare',status_code=202)
async def start(body:ScrapeRequest,request:Request):
    if request.url.path.endswith('/compare'): body=body.model_copy(update={'mode':'compare'})
    try: detect_input(body.input)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    cfg=await load_settings()
    key=cache_key({'request':body.model_dump(),'config':cfg.model_dump()})
    # Evict finished jobs after ten minutes. Maps payloads never enter durable history/cache.
    now=time.monotonic()
    for job_id in list(jobs):
        job=jobs[job_id]
        if job.status not in {'queued','running'} and now-(job.finished or job.created)>600: del jobs[job_id]
    for job in jobs.values():
        if job.key==key and job.status in {'queued','running'}: return {**job.payload(),'deduplicated':True}
    if sum(job.status in {'queued','running'} for job in jobs.values())>=8: raise HTTPException(429,'The local queue is full. Cancel a job or wait.')
    if len(jobs)>=40:
        oldest=next((key for key,j in jobs.items() if j.status not in {'queued','running'}),None)
        if oldest: del jobs[oldest]
    job=Job(uuid.uuid4().hex,body,cfg,key); jobs[job.id]=job
    job.emit('status',{'status':'queued'})
    job.task=asyncio.create_task(run(job))
    return job.payload()

def find(job_id):
    job=jobs.get(job_id)
    if job and job.finished and time.monotonic()-job.finished>600:
        jobs.pop(job_id,None);job=None
    if not job: raise HTTPException(404,'Job not found or expired. Run the test again.')
    return job

@router.get('/jobs/{job_id}')
async def get_job(job_id:str): return find(job_id).payload()
@router.delete('/jobs/{job_id}')
async def cancel_job(job_id:str):
    job=find(job_id)
    if job.task and not job.task.done():
        job.task.cancel()
        with suppress(asyncio.CancelledError): await job.task
    return job.payload()

@router.get('/jobs/{job_id}/events')
async def job_events(job_id:str,request:Request,after:int=Query(default=0,ge=0)):
    job=find(job_id)
    try: cursor=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError: cursor=after
    async def stream():
        nonlocal cursor
        while True:
            job.changed.clear()
            for event in list(job.events):
                if event['id']<=cursor: continue
                cursor=event['id']
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {json.dumps(event['data'],ensure_ascii=False)}\n\n"
            if job.status not in {'queued','running'}: break
            if await request.is_disconnected(): break
            try: await asyncio.wait_for(job.changed.wait(),10)
            except TimeoutError: yield ': heartbeat\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no'})

@router.get('/history')
async def history():
    return {'history':await store.history(),'retention':'Up to 100 website-only profiles, 7 days. Maps results are transient (10 minutes in local jobs), never written to the profile database or Redis.'}
@router.get('/metrics')
async def recent_metrics():
    recent=[{'id':j.id,'status':j.status,'metrics':j.result.get('metrics') if j.result else None} for j in list(jobs.values())[-20:]]
    return {'recent':recent,'infrastructure':await store.health()}
@router.delete('/cache')
async def clear_cache():
    try: await store.clear_cache()
    except Exception as exc: raise HTTPException(503,'Cache clear failed; the cache service is unavailable.') from exc
    return {'status':'cleared'}
