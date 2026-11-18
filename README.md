# Web Scrapper

Run the same Python + Next.js + Ollama application locally. No Docker, paid API keys, bundled dependencies or bundled model weights. This package preserves the reviewed extraction logic and all 32 currently saved settings, including **Sonagara for Google Maps**, **Scrapling for websites**, and disabled automatic fallbacks.

The English UI streams an editable profile and JSON, first/last/total timing, field discovery sources, locations, and temporary LLM diagnostics. Inputs can be a business website, Google Maps URL or company search query.



https://github.com/user-attachments/assets/90bb65b2-c7f9-4968-998d-5975aff1a3ce





## Quick start

Install these prerequisites once:

- **Python 3.13** and **Node.js 22** (with npm).
- Native build tools: Apple Command Line Tools on macOS; C compiler/Python development headers on Linux.
- **Google Chrome**, matching the current native `BROWSER_CHANNEL=chrome` configuration.
- **Ollama 0.40.16**: [official release](https://github.com/ollama/ollama/releases/tag/v0.40.16).
- On Apple Silicon: **Rosetta 2** for the pinned Intel-only official Gosom release. The installer checks this and never accepts system license prompts automatically.

Then, from the repository root:

```sh
python3 scripts/setup.py --dev
python3 scripts/manage.py start
```

Open **http://localhost:3002**. `python3` can be your system Python: setup searches for Python 3.13 to create `.venv`. Alternatively use `python3.13 scripts/setup.py --dev` or pass `--python /path/to/python3.13`.

Setup installs Python packages into `.venv`, frontend npm packages into `frontend/node_modules`, the pinned Gosom binary and browser tooling into `.runtime`, and qwen3:4b through Ollama if absent. Initial downloads include several GB of dependencies/browser/model files. Installed weights are reused and checked against the verified model digest. An already running matching Ollama service is reused.

The first install initializes SQLite from **[config/settings.json](config/settings.json)**. Re-running setup preserves existing settings; it does not silently reset them.

For foreground mode:

```sh
./start.sh
# Ctrl+C stops the app processes.
```

For background mode and diagnostics:

```sh
python3 scripts/manage.py status
python3 scripts/manage.py check --exact-settings
python3 scripts/manage.py stop
```

`stop` only signals this project's recorded background supervisor. It preserves data, other projects, and the independent Ollama service. Logs are in `.runtime/*.log`; settings/history/cache are in `data/profilekit.sqlite3`.

## Current providers and fallbacks

| Area | Current provider | Current automatic fallback |
| --- | --- | --- |
| Google Maps | **Sonagara**, inside Python backend | Gosom selected as fallback, **automatic fallback OFF** |
| Website HTTP | **Scrapling**, inside Python backend | crw is not selected; its adapter remains available, no crw server provisioned |
| Browser rendering | `renderJs=true`, native browser capability installed | **Crawl4AI OFF; Playwright OFF** in the automatic chain |
| AI | **Ollama / qwen3:4b**, Q4_K_M, 8,192 request context | No replacement model or cloud inference |
| Storage | **SQLite**, native `data/` directory | No PostgreSQL/Redis service is installed or required |
| Discovery | Overture, OSM, Foursquare adapters retained | All three discovery switches **OFF**; no datasets downloaded |

A provider in `enabledProviders` is allowed for explicit tests; that does not mean it belongs to an enabled automatic chain. Likewise, `renderJs=true` does not override the two disabled browser fallback switches. All installed native capabilities remain available without enabling them automatically.

**Snapshot parameters:** 10 HTML pages, depth 2, website concurrency 3, HTTP timeout 12 s; Maps timeout 10 s, concurrency 1, retries 0; bidirectional website/Maps search ON; official website preference ON. AI starts after 3 s or earlier source completion, timeout 20 s, configured maximum input 12,000 characters, retries 0. The selective planner caps focused input at 6,000 characters. Cache is ON, TTL 3,600 s, strategy **refresh**; Playground cache bypass ON. These are the captured saved values, not the class defaults.

See [configuration](docs/configuration.md) and [provider routing](docs/providers.md) for exact precedence.

## Extraction and output

- Website metadata, JSON-LD/Schema.org, microdata, `llms.txt`/`llm.txt`, and relevant internal pages; 10 HTML pages maximum, with auxiliary robots/text/JSON requests counted separately.
- Maps input can start crawling the published company website; website input searches Maps in parallel.
- Contact keys: `source`, `business_name`, `voice_greeting_phrase`, `full_name`, `phone_number`, `email`. Greeting uses the company name locally. Unknown contact names are not invented. Legacy `businessName`/`phone` aliases stay synchronized. `consent` and `external_logo_id` are absent.
- Address, timezone, hours, services, payment methods, locations, field evidence and chronological discoveries are retained.
- Services include explicit structured/metadata facts and published menu/footer offerings. Named product dropdowns are supported, including Newo's Voice AI Employee and its child services. Target industries are separate `industries` data and scoped service labels such as `Industry solutions for Restaurants`.
- LLM requests only eligible missing fields, skips complete profiles, and cancels an outstanding request when the parser closes every requested gap. Services alone may be inferred from a supporting business description and are labeled **LLM inferred**. Other model values require exact quotes; stronger parser results and user edits are protected.

## Interface controls and LLM rules

| Button | Meaning |
| --- | --- |
| **Check health** | Refreshes provider/infrastructure readiness. Does not scrape a business or download models. |
| **Inspect / Inspect result** | Opens an existing history/comparison result for review, without running extraction again. |
| **Apply to profile** | Transfers the selected result to the editable main form, preserving manually edited fields. |
| **Export diagnostics** | Downloads `scraping-diagnostics.json` with the selected result, timing, evidence and available job/comparison details. |

See the **[complete interface guide](docs/interface.md)** for every button, test mode, result tab, export and timing label.

To extend extraction behavior, use the **[business profile extraction skill](skills/business-profile-extraction/SKILL.md)** and **[LLM rule/code guide](docs/llm-rules.md)**. The skill guides development; Ollama does not read it automatically. The actual runtime system prompt is in `OllamaExtractionProvider.scrape()` in [backend/app/providers.py](backend/app/providers.py). The guide also links the planner, evidence validation and deterministic rules.

## Measured benchmarks

Historical native measurements on an Apple Silicon Mac with 64 GiB RAM; Ollama used Metal. These are **not measurements of a fresh install on every platform**.

| Check | Result | Scope |
| --- | --- | --- |
| Scrapling | 1.390 s median | Two same-input single-page rounds |
| Crawl4AI | 4.450 s median | Same two-round website comparison, manually selected browser |
| Sonagara | 1.159 s | One historical Maps diagnostic |
| Gosom | 17.757 s | One historical manual diagnostic; not enabled automatic routing; exceeds the current 10 s Maps timeout |
| Playwright | 4.144 s | One historical manual browser diagnostic |
| Bevri Maps, before selective AI | 15.308 s Total | 10 pages, cache bypass, 12.301 s AI duration, zero added fields |
| Bevri Maps, selective AI | 5.090 s Total | 10 pages, same output; AI skipped |
| Services-only inference | 1.473 s | Controlled Bevri-description test, not a full website crawl |

See [benchmark methodology and data](docs/benchmarks.md). Small samples, changing websites and network/cache differences do not establish a universal speed ranking.

## Tests

After `setup.py --dev`:

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m unittest discover -s tests -v
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Tests use controlled fixtures; browser/network integration checks are opt-in. For an explicit live test against a running app:

```sh
.venv/bin/python scripts/smoke.py --input 'https://www.google.com/maps/search/?api=1&query=Bevri'
```

This contacts Google/the company website, bypasses application cache, preserves settings, and writes ignored diagnostics under `.runtime/`.

 Fresh native installation and live checks are documented in [verification](docs/verification.md).

## Documentation index

This README is the entry point for all project documentation.

| Document | Contents |
| --- | --- |
| [Installation](docs/installation.md) | Prerequisites, dependency/model downloads, native setup, launch and updates. |
| [Interface guide](docs/interface.md) | All buttons, test modes, result tabs, timing and exports. |
| [Configuration](docs/configuration.md) | Saved settings, environment variables, persistence and routing precedence. |
| [Providers and fallbacks](docs/providers.md) | Provider responsibilities, actual enabled chains and optional integrations. |
| [Google Maps adapter](maps/README.md) | Gosom wrapper, endpoints, native requirements and cancellation. |
| [LLM rules](docs/llm-rules.md) | Runtime prompt, planner, evidence validation and how to extend behavior. |
| [Extraction skill](skills/business-profile-extraction/SKILL.md) | Coding-assistant guidance for adding extraction rules. Not loaded automatically by Ollama. |
| [Benchmarks](docs/benchmarks.md) | Timing methodology, observations and comparison limits. |
| [Benchmark data](docs/benchmarks/native-history.json) | Machine-readable historical measurements. |
| [Installation verification](docs/verification.md) | Tested setup, lifecycle, live requests and platform coverage. |
| [Verification data](docs/native-verification.json) | Machine-readable installation and live-check evidence. |
| [Branding and compatibility](docs/branding.md) | SKRIV Scrapper naming and retained internal identifiers. |
| [Troubleshooting](docs/troubleshooting.md) | Common startup, browser, provider and model issues. |
| [Frontend development instructions](frontend/AGENTS.md) | Local Next.js instructions for contributors and coding assistants. |
| [License](LICENSE) | Project license. |
| [Third-party notices](THIRD_PARTY.md) | Dependency licenses, attribution and upstream links. |

### Configuration and release references

- [First-install settings](config/settings.json)
- [Environment template](.env.example)
- [Current source checksums](config/source-integrity.json)
- [Source checksums before the rename](config/pre-branding-source-integrity.json)
- [Native release metadata](NATIVE_RELEASE.json)

This is a native package for macOS and Linux x86-64; Windows can use x86-64 WSL2. Only the platforms explicitly marked tested in verification are claimed verified. Extraction behavior/logic is unchanged; the project name and visible branding are now SKRIV Scrapper, but CPU/GPU and network differences affect timing.

Do not commit `.env`, `.venv`, `.runtime`, `node_modules`, `.next`, databases, logs or model/browser downloads. The repository contains source, tests, install manifests/locks, settings defaults and documentation. No GitHub upload or repository publication is performed by these scripts.
