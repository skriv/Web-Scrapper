"""One evidence-checked local-model pass at the deadline or earlier scrape completion."""
import asyncio
import copy
import json
import time
from urllib.parse import urlsplit
from .configuration import trace
from .models import Profile
from .providers import ProviderError
from .ai_planner import missing_fields,focused_packet


def source_packet(data,max_chars):
    sources=[];seen=set()
    text_rows=data.get('texts',[])
    def priority(row):
        path=urlsplit(row['url']).path.lower()
        return (0 if 'contact' in path else 1 if path in {'','/'} else 2 if 'llm' in path else 3,row['url'])
    for row in sorted(text_rows,key=priority):
        if row['url'] in seen or not row['text'].strip():continue
        seen.add(row['url']);sources.append({'url':row['url'],'text':row['text']})
    for row in data.get('raw',[]):
        if not 200 <= row.get('status',200) < 300:continue
        structured=row.get('structuredData') or row.get('data')
        if structured:
            sources.append({'url':row.get('sourceUrl',''),'text':json.dumps(structured,ensure_ascii=False)})
    sources=sources[:8]
    parts=[];included=[];remaining=max_chars
    for index,source in enumerate(sources):
        header=f"SOURCE {index+1}: {source['url']}\n"
        budget=min(remaining-len(header)-2,max(256,max_chars//max(1,len(sources))))
        if budget<=0:break
        excerpt=source['text'][:budget]
        part=header+excerpt+'\n\n';parts.append(part);remaining-=len(part)
        included.append({'url':source['url'],'text':excerpt})
    return ''.join(parts),included

class TimedAI:
    def __init__(self,cfg,data,started,call_model,notify):
        self.cfg=cfg;self.data=data;self.started=started;self.call_model=call_model;self.notify=notify
        self.done=asyncio.Event();self.source_ready=data.setdefault('sourceReady',asyncio.Event())
        self.profile=Profile();self.provenance={};self.task=None;self.warning=None;self.added=set()
        self.state={'status':'waiting','model':cfg.ollamaModel,'delaySeconds':cfg.ollamaDelaySeconds,'trigger':None,'startedMs':None,'durationMs':None,'fieldsAdded':[]}
        self.state['debug']={'temporary':True,'stage':'waiting','message':'Waiting for the configured delay or earlier scraping completion.','phaseStartedMs':0,'phaseElapsedMs':0,'history':[{'stage':'waiting','elapsedMs':0,'message':'Waiting for the configured delay or earlier scraping completion.'}]}
        self.latest={};self.requested=[];self.model_task=None;self.cancelled_for_completion=False
        self.data['ai']=self.state

    def observe(self,snapshot):
        # Keep only parser-supported state; model output must never cancel its own request.
        self.latest={key:copy.deepcopy(value) for key,value in snapshot.get('profile',{}).items()
                     if not any(item.get('method','').startswith('ollama') for item in snapshot.get('provenance',{}).get(key,[]))}
        if self.model_task is not None and not self.model_task.done() and self.requested and all(self.latest.get(key) for key in self.requested):
            self.cancelled_for_completion=True
            self.model_task.cancel()

    def start(self):self.task=asyncio.create_task(self.run())
    def scraping_complete(self):
        self.done.set();self.source_ready.set();self.state['scrapingComplete']=True
    async def finish(self):
        if self.task:await self.task
    async def close(self):
        if self.task and not self.task.done():self.task.cancel()
        if self.task:await asyncio.gather(self.task,return_exceptions=True)

    async def stage(self,stage,message,**details):
        at=round((time.monotonic()-self.started)*1000)
        debug=self.state['debug']
        debug.update(stage=stage,message=message,phaseStartedMs=at,phaseElapsedMs=0,**details)
        debug['history'].append({'stage':stage,'elapsedMs':at,'message':message})
        trace('ai_debug_stage',stage=stage)
        await self.notify()

    async def heartbeat(self):
        while self.state['status']=='running':
            await asyncio.sleep(1)
            if self.state['status']!='running':break
            debug=self.state['debug']
            debug['phaseElapsedMs']=max(0,round((time.monotonic()-self.started)*1000)-debug['phaseStartedMs'])
            await self.notify()

    async def run(self):
        heartbeat=None
        self.data['aiProgress']=self.stage
        try:
            delay=max(0,self.started+self.cfg.ollamaDelaySeconds-time.monotonic())
            try:
                if self.data.get('aiAfterScraping'):await self.done.wait()
                else:await asyncio.wait_for(self.done.wait(),delay)
                self.state['trigger']='scraping-complete'
            except TimeoutError:self.state['trigger']='delay'
            self.requested=missing_fields(self.latest,self.data)
            self.state['requestedFields']=list(self.requested)
            if not self.requested:
                self.state.update(status='skipped',reason='parser-complete')
                await self.stage('skipped','Parser already filled all eligible fields; no model request needed.')
                return
            await self.stage('preparing-input','Preparing a bounded snapshot of website and Maps source data.')
            packet,sources=focused_packet(self.data,self.cfg.ollamaMaxInput,self.requested)
            while not packet and not self.done.is_set():
                self.state['status']='waiting-for-data'
                await self.stage('waiting-for-data','Waiting for the first successful source data.')
                self.source_ready.clear()
                # Recheck after clearing so a received source cannot be missed.
                packet,sources=focused_packet(self.data,self.cfg.ollamaMaxInput,self.requested)
                if packet:break
                await self.source_ready.wait()
                packet,sources=focused_packet(self.data,self.cfg.ollamaMaxInput,self.requested)
            if not packet:
                self.state['status']='skipped';self.state['detail']='No successful source text was available.'
                trace('ai_skipped',reason='no_source_text');await self.stage('skipped','No successful source text; model request skipped.');return
            # Re-evaluate after waiting for source data: parsing may already have filled the gaps.
            self.requested=missing_fields(self.latest,self.data)
            self.state['requestedFields']=list(self.requested)
            if not self.requested:
                self.state.update(status='skipped',reason='parser-complete')
                await self.stage('skipped','Parser filled all eligible fields before the request was sent.');return
            packet,sources=focused_packet(self.data,self.cfg.ollamaMaxInput,self.requested)
            self.data['aiRequestedFields']=tuple(self.requested)
            self.data['aiInferServices']='services' in self.requested
            self.state['inferredFields']=['services'] if self.data['aiInferServices'] else []
            self.state.update(status='running',startedMs=round((time.monotonic()-self.started)*1000),inputCharacters=len(packet))
            trace('ai_scheduled_start',trigger=self.state['trigger'],inputCharacters=len(packet),model=self.cfg.ollamaModel)
            await self.notify()
            ai_started=time.monotonic()
            heartbeat=asyncio.create_task(self.heartbeat())
            try:
                await self.stage('requesting-model','Sending source data to the local model.',inputCharacters=len(packet),sourceCount=len(sources))
                if self.latest and all(self.latest.get(key) for key in self.requested):
                    self.state.update(status='skipped',reason='parser-complete')
                    await self.stage('skipped','Parser filled the requested fields before dispatch; no model request sent.');return
                self.model_task=asyncio.create_task(self.call_model('ollama',packet))
                candidate=await self.model_task
                await self.stage('checking-sources','Checking English content and matching quotes to source pages.')
                from .english import english_content
                from .models import Extraction
                checked=Extraction(profile=candidate)
                await english_content(checked)
                candidate=checked.profile
                if checked.warnings:self.warning=' '.join(checked.warnings)
                evidence=self.data.get('aiFieldEvidence',{})
                # Associate every accepted value with the source containing its quote.
                for field,value in candidate.model_dump().items():
                    if not value:continue
                    if field not in self.requested:
                        setattr(candidate,field,[] if isinstance(value,list) else '');continue
                    inferred=field=='services' and self.data.get('aiInferServices')
                    quotes=evidence.get(field,[])
                    supported=[item for item in quotes if any(item['quote'] in source['text'] and (inferred or item['value'] in item['quote']) for source in sources)]
                    allowed={item['value'] for item in supported}
                    accepted=[item for item in value if item in allowed] if isinstance(value,list) else value if value in allowed else ''
                    setattr(candidate,field,accepted)
                    actual=[source for source in sources if any(item['quote'] in source['text'] for item in supported)]
                    if not actual:
                        setattr(candidate,field,[] if isinstance(value,list) else '')
                    else:self.provenance[field]=[{'url':source['url'],'method':'ollama-inferred' if inferred else 'ollama'} for source in actual]
                self.profile=candidate
                self.state['acceptedFields']=sum(bool(v) for v in candidate.model_dump().values())
                await self.stage('applying-fields','Applying supported values only to fields that are still empty.')
                self.state['status']='complete'
                await self.stage('complete','Model processing finished. Scraped and manually edited values are preserved.')
                trace('ai_complete',acceptedFields=self.state['acceptedFields'])
            except asyncio.CancelledError:
                if not self.cancelled_for_completion:raise
                self.state.update(status='cancelled',reason='parser-filled-requested-fields')
                await self.stage('cancelled-satisfied','Parser filled every requested field; the pending model request was cancelled.')
                trace('ai_cancelled_satisfied',fields=self.requested)
            except (ProviderError,ValueError,KeyError) as exc:
                self.warning=str(exc) or 'Local model extraction failed.'
                self.state.update(status='unavailable' if 'unavailable' in self.warning.lower() or 'connection' in self.warning.lower() else 'error',detail=self.warning)
                trace('ai_failed',error=type(exc).__name__)
                await self.stage('error','Model processing failed; successful scraping results are preserved.')
            finally:
                self.state['durationMs']=round((time.monotonic()-ai_started)*1000)
                self.data['ollamaMs']=max(self.data.get('ollamaMs',0),self.state['durationMs'])
            await self.notify()
        except asyncio.CancelledError:
            self.state['status']='cancelled'
            self.state['debug'].update(stage='cancelled',message='Model request cancelled.')
            raise
        finally:
            if heartbeat is not None:
                heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
            self.data.pop('aiProgress',None)
            if self.model_task is not None and not self.model_task.done():
                self.model_task.cancel();await asyncio.gather(self.model_task,return_exceptions=True)

    def merge(self,snapshot):
        changed=[]
        for field,value in self.profile.model_dump().items():
            if value and not snapshot['profile'].get(field):
                snapshot['profile'][field]=copy.deepcopy(value)
                snapshot.setdefault('provenance',{})[field]=self.provenance.get(field,[])
                if field not in self.added:changed.append(field);self.added.add(field)
        if changed:
            at=round((time.monotonic()-self.started)*1000)
            self.data['aiLastFoundMs']=at
            if snapshot.get('firstFoundMs') is None:snapshot['firstFoundMs']=at
        if self.data.get('aiLastFoundMs') is not None and self.added:
            snapshot['lastFoundMs']=max(snapshot.get('lastFoundMs') or 0,self.data['aiLastFoundMs'])
            if snapshot.get('firstFoundMs') is not None:snapshot['discoverySpanMs']=snapshot['lastFoundMs']-snapshot['firstFoundMs']
        self.state['fieldsAdded']=sorted(self.added)
        snapshot['ai']=copy.deepcopy(self.state)
        if self.warning:
            snapshot['warnings']=list(dict.fromkeys(snapshot.get('warnings',[])+[self.warning]))
        snapshot['missingFields']=[field for field,value in snapshot['profile'].items() if not value]
        return snapshot
