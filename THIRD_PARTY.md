# Third-party components

The application source is MIT-licensed (see LICENSE). Dependencies and data retain their own licenses. This is not a grant of permission to reuse third-party business data.

| Component | License / purpose | Official source |
|---|---|---|
| Sonagara parser/protocol/model modules | MIT; vendored at commit `53db41b3556e61700c0e78e5a3ce3aff777aac83`. Original copyright/license included under `backend/app/vendor/sonagara/LICENSE`. Network, proxy and consent helpers are not invoked. | https://github.com/sonagara-vashram/google-maps-scraper |
| Gosom v1.18.2 | MIT; browser Maps extraction, separately built CLI | https://github.com/gosom/google-maps-scraper/tree/146aefe83844960bb534f335261a4435783a58cc |
| Scrapling 0.5.1 | BSD-3-Clause; HTTP fetching | https://github.com/D4Vinci/Scrapling |
| Crawl4AI 0.9.5 | Apache-2.0; optional local browser, retain upstream attribution requirements | https://github.com/unclecode/crawl4ai |
| Playwright | Apache-2.0; optional browser automation | https://github.com/microsoft/playwright |
| crw | AGPL-3.0; optional separately deployed service. Its source is not bundled here. | https://github.com/fastcrw/crw |
| Ollama | MIT server; downloaded model licenses are separate | https://github.com/ollama/ollama |
| Redis 8 | Includes an AGPLv3 license option; self-hosted cache | https://github.com/redis/redis |
| PostgreSQL | PostgreSQL license; persistent configuration/history | https://www.postgresql.org/about/licence/ |
| Next.js / React | MIT; frontend | https://github.com/vercel/next.js / https://github.com/facebook/react |
| timezonefinder | MIT; timezone lookup | https://github.com/jannikmi/timezonefinder |
| timezone-boundary-builder data | ODbL; timezone polygons | https://github.com/evansiroky/timezone-boundary-builder |
| phonenumbers | Apache-2.0; telephone normalization | https://github.com/daviddrysdale/python-phonenumbers |
| OpenStreetMap | ODbL; © OpenStreetMap contributors, retain attribution | https://www.openstreetmap.org/copyright |
| Overture / Foursquare open datasets | Retain release-specific licenses and provider attribution with each provisioned regional dataset. No datasets included in this source archive. | https://docs.overturemaps.org/attribution/ / https://docs.foursquare.com/data-products/docs/places-os-data-schema |

The native installer downloads the official Gosom v1.18.2 release binary and verifies its SHA-256. Its source/license is at the linked upstream commit. It is not rebuilt or patched, matching the original native installation. The environment sets DISABLE_TELEMETRY=1; upstream may still perform external-IP discovery before telemetry events are suppressed. Browser libraries and model weights are downloaded separately, never bundled in this repository.

Chromium and its bundled/system libraries retain separate notices. Inspect actual installed distributions with `pip list` and `npm ls`. Optional service prerequisites, source revisions, actual routing and execution status are documented in `docs/providers.md`.
