# Provider responsibilities and actual fallback routing

| Component | Execution | Role | Current automatic behavior |
| --- | --- | --- | --- |
| Sonagara | Vendored Python modules inside API | Google Maps HTTP parsing | Primary; 10 s timeout, no retries |
| Gosom v1.18.2 | Separate native CLI through loopback adapter | Browser-based Maps extraction/search | Available; selected fallback is OFF |
| Scrapling 0.5.1 | Python API process | HTTP fetching and keep-alive pool | Website primary |
| crw | Retained client for a separately hosted service | Alternative website HTTP engine | Not selected; no crw server installed, as in original environment |
| Crawl4AI 0.9.5 | Native Python + Chrome | JavaScript rendering | Available; automatic fallback OFF |
| Playwright | Native Python + Chrome | Alternative rendering | Available; automatic fallback OFF |
| Ollama 0.40.16 / qwen3:4b | Independent loopback model server | Eligible missing-field extraction, Services inference | ON, after 3 s or earlier completion; skip/cancel when parser closes gaps |
| SQLite | Project `data/` | Saved settings, permitted history and cache | Current native storage |
| Overture / Foursquare | Retained local-file adapters | Regional dataset discovery | OFF, no datasets bundled |
| OSM | Retained Nominatim adapter | Query discovery | OFF |

## Exact routes

Maps builds a chain starting with `mapsPrimary`. It appends the configured `mapsFallback` only when `mapsFallbackEnabled` is true. This snapshot therefore uses Sonagara without an automatic Gosom step. Manually selecting Gosom remains possible; its call still obeys the saved Maps timeout.

Website HTTP uses Scrapling. The implemented crw → Scrapling fallback applies only when crw is selected as website primary; it does not run in this snapshot.

Automatic JavaScript escalation is conditional on the page looking like a JS shell after lightweight sources. Even then, the configured chain only tries providers whose fallback switch is enabled. Both switches are false here. `renderJs=true` is not permission to ignore them. Explicit single-engine browser tests use the installed Chrome capability.

AI receives only missing eligible fields; output schema/input budget is restricted to that request. There is no secondary model. Failure preserves scraped fields. Services inference retains supporting source quotes and the distinct `ollama-inferred` method; normal values require exact quoted facts. User edits remain protected by the existing frontend logic.

## Versions and notices

Python roots are pinned in backend/requirements*.txt and constrained by requirements/constraints-python313.txt, captured from the tested Python 3.13 environment. Frontend and Gosom driver npm trees have package-lock.json files. Gosom release SHA-256 values are in setup.py. Application source checksums are in config/source-integrity.json. Third-party source/license attribution is in THIRD_PARTY.md and vendored license files.
