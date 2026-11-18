# Native Gosom adapter

Private FastAPI wrapper around the official Gosom v1.18.2 CLI, loopback port 8003. It supports GET /health, POST /extract for a specific Maps URL and POST /search for a company query. Adapter source is unchanged from the working app. One CLI job runs at a time; subprocess groups and temporary output are cleaned up on cancellation/timeout.

The installer downloads the official macOS/Linux Intel release and checks its SHA-256. macOS Apple Silicon uses Rosetta. Playwright-Go v0.6100.0 expects driver 1.61.1; the matching playwright-core npm package, Node and Chromium are wired through PLAYWRIGHT_DRIVER_PATH and PLAYWRIGHT_BROWSERS_PATH. No bundled Go executable, browser, virtual environment or model is committed.

The native binary is the upstream release, matching the original native installation; it is not rebuilt with a different telemetry patch. DISABLE_TELEMETRY=1 remains set. The original upstream binary can still perform its external-IP lookup before event reporting is disabled. See upstream notices and setup.py for exact asset hashes.

Gosom is installed and available, but the saved automatic Maps fallback remains OFF. Main Maps provider is Sonagara. Manual provider requests obey the current 10-second Maps timeout. Do not infer current automatic behavior from historical longer Gosom benchmark runs.
