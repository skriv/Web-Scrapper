# Benchmarks and interpretation

Data: [benchmarks/native-history.json](benchmarks/native-history.json). These historical runs used the existing native app on an Apple Silicon Mac, 64 GiB RAM; Ollama used Metal. Original artifacts were captured during development before this source-only packaging. No cloud, Linux, or CPU-only benchmark is implied.

## Website providers, controlled same-input comparison

Input `https://bevri.com`, two rounds per provider, order rotated, application cache bypass. Each single engine fetches one page. Scrapling: median 1.390 s, P90 1.4636 s, P95 1.4728 s. Crawl4AI: median 4.450 s, P90 4.494 s, P95 4.4995 s. Both succeeded twice and filled 7/9 original business fields. With n=2 these percentiles are descriptive interpolations, not robust estimates.

## Other single-provider diagnostics

Sonagara 1.159 s (6/9 fields); Gosom 17.757 s (7/9); Playwright 4.144 s (7/9); Crawl4AI 5.163 s (7/9). These are separate historical runs, not a matched speed contest. The Gosom observation exceeds the current 10-second Maps timeout and must not be presented as a successful automatic fallback under today's settings. That fallback remains disabled.

## Whole pipeline and AI

Paired Bevri Maps runs, 10 HTML pages, same output and settings apart from the implemented selective-AI behavior: before 15.308 s total / 12.301 s AI; after 5.090 s total / AI skipped. Saved 10.218 s (66.75%) in that pair. A separate browser repeat was 3.175 s; it is not the paired after-run. Controlled Services-only inference from a published description took 1.473 s and returned Dining; the actual site already published services. See the JSON methodology notes.

Core completeness counts the original nine business fields. It does not claim all six contact fields or every possible location was found. First/Last measure discoveries; Total includes source cleanup and any pending AI. A skipped model contributes no inference time. Generated greetings, published navigation and inferred services have separate source methods.

## Reproduce

From a running native installation, explicitly select providers without changing the saved primary/fallback settings:

```sh
.venv/bin/python scripts/benchmark.py https://bevri.com \
  --base http://127.0.0.1:8002 --providers scrapling crawl4ai \
  --allow-browser --repeats 3 --output benchmark-results.json
```

This requests explicit comparison runs, permits browser rendering for those requests, and does not persist a new provider configuration. Running a live comparison contacts the given site. Record hardware, OS, input, model, complete settings, timestamps, cache state, sample count, failures, median and P90/P95 with every result. Use multiple sites before making broad performance claims. OS, DNS, upstream and browser caches are not cleared automatically. Runtime results and raw scraped payloads should stay outside Git by default.
