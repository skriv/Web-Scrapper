# Extending LLM extraction rules

[Back to README](../README.md) · [Extraction skill](../skills/business-profile-extraction/SKILL.md)

## Where the rules live

**Ollama's runtime instructions currently live in Python, not in a loaded skill file.** Start with `OllamaExtractionProvider.scrape()` in [backend/app/providers.py](../backend/app/providers.py). The `instructions` string is the system prompt; a conditional addition controls supported Services inference.

The repository also includes [skills/business-profile-extraction/SKILL.md](../skills/business-profile-extraction/SKILL.md): a project skill for a coding assistant extending these rules. You can add guidance there for future development. **Editing that Markdown alone does not change the running parser or Ollama prompt.** To change runtime behavior, implement the rule in the relevant files below and verify it. The application does not automatically discover or load this skill.

| File | Responsibility |
| --- | --- |
| [providers.py](../backend/app/providers.py) | Model system prompt, requested-field output schema, inference options, output validation and exact supporting quotes. |
| [ai_planner.py](../backend/app/ai_planner.py) | Chooses eligible missing fields and relevant source excerpts. `HINTS` selects likely supporting text. Input is bounded. |
| [timed_ai.py](../backend/app/timed_ai.py) | Schedules model requests, skips/cancels covered gaps, checks evidence and English output, and merges only requested missing fields. |
| [navigation_offerings.py](../backend/app/navigation_offerings.py) | Deterministic services/products/industry extraction from navigation and footer groups, including named product dropdowns. These rules apply across sites. |
| [extraction.py](../backend/app/extraction.py) | Structured website parsing, including metadata and Schema.org/JSON-LD. |
| [llms.py](../backend/app/llms.py) | Reads a website's `llms.txt`/`llm.txt` as source evidence. Website content is untrusted data, not instructions for the model. |
| [contact_fields.py](../backend/app/contact_fields.py) | Contact normalization and the local company-name greeting template. A greeting change belongs here, not in the LLM prompt. |
| [config/settings.json](../config/settings.json) | First-install settings snapshot, including model and scheduling. These settings are not extraction instructions; existing installs use saved server settings. |

## Current model contract

- Request only eligible missing fields. Skip inference when none remain; cancel an outstanding request when the parser supplies every requested gap.
- Return English output in the requested JSON schema, with exact supporting quotes from the supplied excerpts. Unknown values remain null/empty.
- Do not invent personal names, contact details, addresses, hours or payment methods. A contact person's name needs explicit source support.
- For **Services only**, broad categories may be inferred from a strongly supporting business description when no explicit service list exists. Supporting quotes are still required; inferred services carry `ollama-inferred` provenance. Dishes/products are not automatically services.
- Retain parser priority and manual-edit protection. Do not fill unrelated fields just because the model returned them.

The prompt is only one layer: schema constraints, planner eligibility and merge validation can reject a value even when the prompt requests it.

## Adding a rule

1. Add the desired behavior and an example to the [skill](../skills/business-profile-extraction/SKILL.md) if it should guide future changes. State what evidence supports the field and what should remain unknown.
2. Choose the implementation layer. Explicit metadata, JSON-LD, menu/footer links and `llms.txt` facts should usually be parsed locally. Use LLM instructions for supported interpretation that needs model reasoning.
3. For a model change, edit `instructions` (and its Services condition if relevant) in `providers.py`. If the required source text never reaches the model, inspect `ai_planner.py` and its excerpt hints rather than only expanding the prompt. Adding a new output field also requires schema/planner/merge/UI work.
4. Add meaningful fixtures: one supported case and one case where missing evidence must leave the field empty. Preserve English output, source quotes, selective calls and Services provenance.
5. Run relevant tests, restart the backend, then compare the new result in Playground using **Timeline**, **Sources** and **Export diagnostics**. Check Total as well as field arrival times. A frontend restart is unnecessary for a backend-only rule change.

For example, to recognize a new menu group such as “What we offer,” extend deterministic navigation parsing with a cross-site fixture first. To infer a broad service category from a published description, adjust the Services prompt/planner and verify the exact supporting quote and inferred label.

From the repository root after development dependencies are installed:

```sh
.venv/bin/python -m pytest backend/tests/test_selective_ai.py backend/tests/test_parallel_ai.py backend/tests/test_navigation_offerings.py backend/tests/test_llms.py -q
```

Broaden testing when the change affects shared parsing or output schemas. Do not change provider choices, fallback switches, model or scheduling as a side effect of adding an extraction rule.
