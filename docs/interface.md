# Interface guide

[Back to README](../README.md)

## Business profile

| Control | What it does |
| --- | --- |
| **Find** | Starts extraction for the entered website, Google Maps URL or business query. Fields and JSON update as results arrive. The label becomes **Searching…** while running. |
| **Scraping settings & tests** | Opens provider configuration, the Playground and History. |
| Profile fields | Edit any value, including during extraction. Manually edited fields are protected from subsequent automatic updates and imports. The greeting follows the company name until you edit the greeting itself. |
| **Unlock edited fields** | Removes this protection for future updates; keeps the current values. Appears after manual edits. |
| Business / branch buttons in Maps matches or Locations | Merge that candidate into the main profile while preserving manually edited fields. Selecting a candidate does not start another extraction. |
| **Copy** | Copies the current editable profile JSON, including locations/candidates when present. This is the main form's export, not the full diagnostic result. |
| **Download** | Downloads the current editable profile JSON as `business-profile.json`. Disabled until fields are populated. |
| **Discovery report** | Expands the chronological field report: when information first arrived, subsequent updates and their sources. |
| **Warnings** | Expands extraction warnings. |
| **LLM stages** | Expands temporary model-stage diagnostics when available. |

### Timing

- **First**: elapsed time until the first field discovery.
- **Last**: elapsed time until the last field discovery.
- **First to last**: difference between those discoveries.
- **Total**: duration of the complete extraction, including remaining scraping/model work after the last new field. During extraction it is a live elapsed timer; the final value uses backend timing.

A later Total than Last can therefore be expected. The field report identifies Web, Maps or LLM provenance; inferred model services are labeled separately.

## Scraping page: shared controls

| Control | What it does |
| --- | --- |
| **← Business profile** | Returns to the editable main form. |
| **Check health** | Refreshes provider readiness and infrastructure status. It checks connectivity/availability; it does not scrape a business, save settings, install dependencies or download models. A ready provider can still fail on a particular site. |
| **Save settings** | Saves changed settings to the server's local SQLite database. `*` marks unsaved changes; the button is disabled when nothing changed. |
| **Playground** | Opens the extraction test runner. |
| **Configuration** | Opens extraction, Maps, AI, discovery and cache options. |
| **Providers** | Shows provider readiness and enabled-provider controls. |
| **History** | Loads retained website-only extraction results. Up to 100 profiles / 7 days; Maps data is excluded from durable history. Live jobs are transient and expire after 10 minutes. |

## Playground

| Control | What it does |
| --- | --- |
| **Run test** | Starts a real extraction with the selected input and test mode. Streams progress/results. |
| **Save & run test** | Appears when configuration has unsaved changes. Saves those settings first, then runs the test. These changes also affect later automatic runs. |
| **Running…** | Disabled state of the run button while a test is active. |
| **Cancel** | Requests cancellation of the active job and closes its live stream. Keeps settings/history and does not stop the independent Ollama service. |
| Mode / provider / repeats controls | Choose the test route, engines and comparison repetitions. See modes below. |
| **Inspect result** | Opens an already completed engine run in the result viewer's Form tab. Does not scrape again or apply it to the main profile. |
| **Apply to profile** | Transfers the selected result into the main editable Business Profile form and navigates there. Preserves fields you manually edited. It does not start a scrape or publish anything. The editable draft is held in this browser tab's memory across navigation; download JSON to retain it across a full reload. |
| **Copy JSON** | Copies the entire selected extraction result, including diagnostics present in it. Unlike main-form Copy, this is not just the editable profile export. |
| **Export diagnostics** | Downloads `scraping-diagnostics.json` containing `jobId`, `status`, the selected `result`, and comparison `runs`. Can include timing, field discoveries, evidence, bounded raw source excerpts, logs, warnings and AI diagnostics. Does not start another request or change the profile/settings. Review source text and business details before sharing. |
| **Form** | Shows normalized result fields for inspection. Use Apply to profile to edit them in the main form. |
| **Timeline** | Shows chronological discoveries and updates with their sources. |
| **JSON** | Shows the selected full result as JSON. |
| **Raw data** | Shows available raw excerpts and Maps/discovery candidates. |
| **Sources** | Shows per-field supporting evidence. |
| **View locations (n)** | Expands locations in the selected result; the count is the number found. This list is read-only. |
| **Warnings (n)** / **Request log** / **Result JSON** | Expand warnings, provider request details, or an individual comparison result. |

When exporting an inspected historical result, `result` is the selected historical data, while wrapper job/status/runs values can describe the current Playground session. They are not a guaranteed reconstruction of the historical job.

### Test modes

| Mode | Behavior |
| --- | --- |
| **Automatic** | Uses saved provider routing and fallback switches. |
| **Single Engine** | Runs the explicitly selected provider. Manual selection does not enable it as an automatic fallback. |
| **Compare Engines** | Runs selected providers with the requested repetitions and rotated order, recording individual results. |
| **Deep Extraction** | Uses the deep-extraction route while honoring saved opt-ins. |
| **Scraping + AI (timed)** | Explicitly enables timed model assistance for this test, without changing the saved AI toggle by itself. |
| **Fast Extraction** | Uses HTTP extraction; disables AI and automatic Maps enrichment for website inputs. |

## Configuration and Providers

| Control | What it does |
| --- | --- |
| Setting inputs, toggles and dropdowns | Change the page's unsaved configuration. Persist with Save settings, or Save & run test. The [configuration guide](configuration.md) explains routing and persistence. |
| **Installed model** | Selects an already installed model for the unsaved configuration. Entering/selecting a name does not download it. |
| **Test Ollama connectivity** | Refreshes provider health and displays Ollama's connectivity detail. Does not run inference, download a model or save settings. |
| **Clear application cache** | Clears the application's extraction cache. Retains saved settings and profile history. |
| **Enable [provider]** checkboxes | Allow a provider for explicit testing after settings are saved. They do not install it or automatically activate its fallback switch. |
| **Configuration and infrastructure** | Expands diagnostic configuration/infrastructure details. |

## History

| Control | What it does |
| --- | --- |
| **Inspect** | Opens a stored result in the Playground. Retains the currently selected result-view tab. No new scrape or automatic profile update. |
| **Apply to profile** | Transfers that stored result to the main form, preserving manual edits, just like the Playground action. |
| **Recent local job metrics** | Expands locally recorded job metrics. |

For extending extraction behavior, see [LLM rules](llm-rules.md) and the [extraction skill](../skills/business-profile-extraction/SKILL.md).
