"""Validated, server-owned configuration and request-local execution context."""
from contextvars import ContextVar
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

PROVIDERS = ['sonagara', 'gosom', 'crw', 'scrapling', 'crawl4ai', 'playwright', 'ollama', 'overture', 'osm', 'foursquare']

class ScrapingSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabledProviders: list[Literal['sonagara','gosom','crw','scrapling','crawl4ai','playwright','ollama','overture','osm','foursquare']] = Field(default_factory=lambda: list(PROVIDERS))
    bidirectionalSearch: bool = True
    mapsPrimary: Literal['sonagara','gosom'] = 'sonagara'
    mapsFallback: Literal['sonagara','gosom'] = 'gosom'
    mapsFallbackEnabled: bool = True
    mapsTimeout: float = Field(default=25, ge=2, le=115)
    mapsRetries: int = Field(default=0, ge=0, le=2)
    mapsConcurrency: int = Field(default=1, ge=1, le=3)
    websitePrimary: Literal['scrapling','crw'] = 'scrapling'
    crawl4aiFallback: bool = True
    renderJs: bool = False
    playwrightFallback: bool = False
    maxPages: int = Field(default=10, ge=1, le=20)
    requestTimeout: float = Field(default=12, ge=2, le=30)
    websiteConcurrency: int = Field(default=3, ge=1, le=6)
    crawlDepth: int = Field(default=2, ge=0, le=5)
    ollamaEnabled: bool = False
    ollamaSchedule: Literal['after-scraping','after-delay'] = 'after-scraping'
    ollamaDelaySeconds: float = Field(default=3,ge=0.1,le=30)
    ollamaModel: str = Field(default='qwen3:4b', min_length=1, max_length=120, pattern=r'^[\w./:-]+$')
    ollamaTimeout: float = Field(default=45, ge=2, le=120)
    ollamaMaxInput: int = Field(default=12000, ge=500, le=40000)
    ollamaRetries: int = Field(default=0, ge=0, le=2)
    overtureEnabled: bool = False
    osmEnabled: bool = False
    foursquareEnabled: bool = False
    discoveryMaxResults: int = Field(default=10, ge=1, le=30)
    cacheEnabled: bool = True
    cacheTtl: int = Field(default=3600, ge=10, le=86400)
    cacheStrategy: Literal['cache-first','refresh'] = 'cache-first'
    bypassCacheForTesting: bool = True
    preferOfficialWebsite: bool = True

settings_context: ContextVar[ScrapingSettings | None] = ContextVar('scraping_settings', default=None)
trace_context: ContextVar[dict | None] = ContextVar('scraping_trace', default=None)

def settings() -> ScrapingSettings:
    return settings_context.get() or ScrapingSettings()

def trace(event: str, **values):
    data = trace_context.get()
    if data is None: return
    import time
    data['logs'].append({'elapsedMs': round((time.monotonic()-data['started'])*1000), 'event': event, **values})
    if len(data['logs']) > 250: del data['logs'][0]

def count(key: str, amount=1):
    data = trace_context.get()
    if data is not None: data[key] = data.get(key, 0) + amount
