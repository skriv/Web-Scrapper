# SKRIV Scrapper native package verification — 2026-11-18

The installer was exercised in a new project folder with a new Python 3.13 virtual environment, fresh frontend npm dependencies, downloaded official Gosom v1.18.2 (checksum matched the original native binary), locked Playwright-core 1.61.1, and downloaded Chromium/Headless Shell revision 1228. At the initial packaging check, 78 source/test files matched the running app byte-for-byte. SKRIV Scrapper was subsequently applied as a presentation-only rename; extraction and provider logic remain unchanged. The current source checksums reflect that renamed release, and pre-branding checksums retain the previous baseline. All 32 current saved settings matched, including cacheStrategy=refresh and all disabled automatic fallbacks.

## Executed checks

- Setup completed, then repeated successfully without resetting existing settings.
- Python dependency consistency check passed.
- Backend 247 passed, 3 skipped (normal unit/fixture suite).
- Frontend 27 passed; typecheck and production build passed.
- Six packaging tests passed: source parity, settings snapshot, first-run/idempotent settings seed, safe PID ownership, active/stopped port detection, native environment/storage/channel.
- Opt-in real Google Chrome JavaScript fixture: 1 passed.
- Pinned Gosom driver launched downloaded Chromium 149.0.7827.55 successfully.
- Isolated app started on 3012/8012/8013 while the original ports were left intact.
- Bevri Maps search through the new frontend API proxy: completed, 10 HTML pages, total 4.021 s; business name, phone and eat@bevri.com verified.
- Newo website through the new frontend API proxy: completed, 10 HTML pages, total 6.989 s; all 8 concrete services and 8 industry solutions verified. Its Maps query status is retained in the JSON rather than treated as a successful Maps test; Bevri was the Maps end-to-end check.
- Stop and immediate restart succeeded twice; every saved setting remained equal. A TIME_WAIT false-positive in the new port preflight was found, fixed and regression-tested; application/provider code was untouched.
- Controlled real qwen3:4b inference from the new environment: Dining from a published Bevri description excerpt, 0.970 s, labeled ollama-inferred. This is not a whole-site benchmark.

Existing local Ollama 0.40.16 and qwen3:4b were reused and the model digest checked. Model download was not repeated. The app launcher deliberately leaves that independent server running, as in the original native setup.

Executed platform: macOS Apple Silicon, Rosetta, installed Google Chrome. Linux x86-64, WSL2 and Intel macOS installation instructions are provided but were not executed here. Timings are observations on this machine and network, not promises for other hardware.

Machine-readable evidence: [native-verification.json](native-verification.json). Runtime logs/raw live responses stay ignored under .runtime or external work files, not in the source archive.

## SKRIV Scrapper rename checks

The follow-up check covers the documentation index, local links, exact branding-only source changes against the prior baseline, frontend tests/typecheck, native packaging checks and live page titles. Detailed outcomes: [native-verification.json](native-verification.json), under `brandingVerification`. The earlier full-suite and live extraction measurements above were not repeated for this rename.
