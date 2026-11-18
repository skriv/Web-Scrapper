# Troubleshooting

- **Port already in use:** the launcher refuses to stop somebody else's app. Use the documented three port overrides, or stop your own previous instance.
- **Wrong Python/Node:** install Python 3.13 and Node 22; setup never replaces system tools. Use setup's `--python` argument if Python is not on PATH.
- **Chrome missing:** install Google Chrome at its standard OS location. The launcher deliberately preserves channel `chrome` rather than silently changing to another browser.
- **Apple Silicon/Gosom architecture:** upstream v1.18.2 supplies a macOS Intel binary. Rosetta 2 is a prerequisite; the Go/Node driver and browser are installed in the project's runtime directory.
- **Ollama version/model mismatch:** install the specified version or start the matching existing server. Model weights are verified by digest. No other model is automatically substituted. If Ollama was already running, its model folder remains wherever that server was configured.
- **No automatic browser/Gosom attempt:** inspect the saved settings first. Automatic fallbacks are intentionally OFF in the shipped snapshot. Health-ready is not the same as automatic activation.
- **crw/dataset provider unavailable:** expected without their external server/data. They were not provisioned in the original native environment and are not activated by setup.
- **Interrupted setup/download:** rerun setup. npm/pip use project-local caches; model files remain managed by Ollama. The existing settings record is not reset.
- **Browser OS libraries on Linux:** use setup's explicit `--install-system-deps` option, or install the documented Playwright dependencies yourself. Do not claim an untested OS verified.
- **Application problem:** inspect .runtime/launcher.log, api.log, maps.log and web.log. `manage.py check` writes .runtime/health.json. Do not publish logs without checking for URLs/data you do not wish to share.

Stop the app before moving the folder. Install/runtime paths are computed from the repository location; copied virtual environments and node_modules are not portable and should be regenerated through setup. Ollama is independent and intentionally survives an app stop, as in the original setup.
