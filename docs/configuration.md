# Settings snapshot and persistence

`config/settings.json` is the exact saved configuration captured from the working native app on 2026-11-18, including the latest `cacheStrategy=refresh`. It is not a reconstruction from defaults. All 32 values are validated with the unchanged Pydantic settings model on first initialization.

`setup.py` calls `seed_settings.py`: if no current settings exist it saves the snapshot into the project's SQLite database; otherwise it preserves them. Setup, start and health checks never reset user choices. `manage.py check --exact-settings` reports a mismatch and exits nonzero; it does not fix or overwrite it.

Actual settings remain editable in the existing Scraping settings UI. Ports/paths are process environment; algorithm/provider choices are saved application settings. `DATABASE_URL` and `REDIS_URL` are empty in this native launcher, preserving SQLite behavior. PostgreSQL/Redis are not installed as replacements.

Key values:

- Maps primary `sonagara`; fallback `gosom` selected but `mapsFallbackEnabled=false`; timeout 10 seconds; zero retries; concurrency 1.
- Website primary `scrapling`; depth 2; up to 10 HTML pages; concurrency 3; HTTP timeout 12 seconds.
- `renderJs=true`, but both `crawl4aiFallback=false` and `playwrightFallback=false`. The same browser packages/capabilities remain installed for explicit tests.
- Bidirectional lookup and official-site priority enabled.
- Ollama enabled, qwen3:4b, after-delay, 3 seconds, timeout 20 seconds, configured max input 12,000, zero retries. Unchanged selective planner further bounds focused source excerpts to 6,000 characters.
- Cache enabled, TTL 3,600 seconds, strategy refresh, Playground bypass enabled.
- Overture/OSM/Foursquare discovery switches disabled. Their entries in enabledProviders permit explicit selection but do not enable automatic discovery.

A missing personal contact name alone does not force inference. No settings script changes primary providers or activates fallbacks to make an installation check pass.
