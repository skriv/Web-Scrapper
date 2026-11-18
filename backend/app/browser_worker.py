"""Opt-in, private-network browser worker. No public port is published by Compose."""
import asyncio
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from .fetching import fetch_browser
from .providers import PlaywrightProvider
app=FastAPI(title='SKRIV Scrapper browser worker')
slots=asyncio.Semaphore(1)
class BrowserRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url:str=Field(max_length=4096)
    provider:Literal['crawl4ai','playwright']='crawl4ai'
@app.get('/health')
async def health():return {'status':'ok'}
@app.post('/scrape')
async def scrape(body:BrowserRequest):
    async with slots:
        try:
            async with asyncio.timeout(40):
                page=await fetch_browser(body.url) if body.provider=='crawl4ai' else await PlaywrightProvider().scrape(body.url)
            return {'html':page.html,'url':page.url,'status':page.status,'method':body.provider}
        except (RuntimeError,ValueError,TimeoutError) as exc:raise HTTPException(422,str(exc) or 'Browser timed out.') from exc
