# Native installation

## Supported targets and prerequisites

The supplied process manager uses POSIX process groups: macOS, Linux x86-64, or Windows via x86-64 WSL2. Native Windows and Linux ARM64 are not claimed supported by this release. The Gosom upstream v1.18.2 assets are Intel-only for macOS/Linux; Apple Silicon therefore requires Rosetta 2. Install Rosetta through macOS if prompted; the setup script does not auto-accept any license.

Install Python 3.13, Node.js 22/npm, Google Chrome, and Ollama 0.40.16 using their official installers or your package manager. Some pinned Python dependencies compile native extensions: macOS needs Apple Command Line Tools (`xcode-select --install`); Linux needs a C compiler and Python development headers (e.g. `build-essential` and `python3.13-dev` on Ubuntu). Setup checks build-tool availability and never auto-accepts an OS installation dialog. Setup does not replace system runtimes. Current inference model is qwen3:4b, digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` (Q4_K_M, about 2.5 GB download). If a future qwen3:4b tag points to different weights, setup stops instead of silently switching the model.

On Linux, Chrome and Playwright need system shared libraries. After installing the prerequisites, `python3 scripts/setup.py --dev --install-system-deps` invokes Playwright's OS-dependency installer, which may request sudo. This opt-in is not used on macOS. The package itself never disables the existing system's security protections.

## Setup

```sh
python3 scripts/setup.py --dev
```

If the system `python3` is not 3.13, setup searches for `python3.13`; use `--python` if needed. `--dev` adds the test dependencies. Runtime dependencies always include the same browser-provider packages, even though their automatic fallback switches remain off.

Install locations:

| Files | Location |
| --- | --- |
| Python libraries | `.venv/` |
| Frontend libraries | `frontend/node_modules/` |
| Gosom v1.18.2 | `.runtime/bin/` |
| Gosom Playwright-core 1.61.1 / browser | `.runtime/maps-driver/`, `.runtime/browsers/maps/` |
| Python browser providers | Installed system Google Chrome (`BROWSER_CHANNEL=chrome`) |
| Private caches and logs | `.runtime/` |
| SQLite settings/history/cache | `data/` |
| Model weights when this package starts its own server | `.runtime/models/` |

The Gosom binary is downloaded from its official GitHub release and SHA-256 checked. Its matching driver is installed from locked `playwright-core@1.61.1` on npm; browsers come from Playwright's official installer. No Go compiler or locally bundled executable is needed. The original upstream native release is used, matching the existing native installation; the unused source patch from the old container build is not applied to this binary.

If Ollama is already listening at `http://127.0.0.1:11434`, setup verifies its version and model digest and reuses it without moving or replacing model files. Otherwise it starts the installed Ollama CLI bound to loopback, with cloud disabled and project-local models. Its log is `.runtime/ollama.log`. Missing qwen3:4b weights are explicitly downloaded during setup. Later app starts do not download weights.

## Start / stop

```sh
python3 scripts/manage.py start
python3 scripts/manage.py check --exact-settings
python3 scripts/manage.py status
python3 scripts/manage.py stop
```

Default ports are 3002 (web), 8002 (API) and 8003 (private Gosom adapter). All bind to `127.0.0.1`. To keep another local installation running:

```sh
python3 scripts/manage.py start --web-port 3012 --api-port 8012 --maps-port 8013
```

These port overrides do not change parser/provider settings. `check`, `status` and `stop` use the recorded ports for this project's background instance. Alternatively copy `.env.example` to `.env` and set these three port values. Do not commit `.env`.

`./start.sh` runs in the foreground; use Ctrl+C there. Background `stop` never kills unrelated processes or an independently running Ollama server. The Ollama process is intentionally separate, as in the original native setup.

## Updates and data

Stop the app, keep a backup of `data/profilekit.sqlite3`, update source, rerun setup, then start. Existing settings are preserved. `config/settings.json` is used only when the settings table has no current record. Removing `data/` loses local settings/history; it is not part of normal update instructions.

The installation requires substantial free disk space for packages, browser versions, npm caches and the model. RAM requirements depend on browser concurrency and model execution; allow about 16 GiB for comfortable full-stack testing. That is an engineering starting point, not a measured universal minimum. The original Mac had 64 GiB. Native Ollama can use Metal on Apple Silicon; Linux CPU/GPU performance needs measurement on the target machine.
