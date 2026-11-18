# SKRIV Scrapper: naming and compatibility

[Back to README](../README.md)

The project name is **SKRIV Scrapper**, spelled S-K-R-I-V. It replaces ProfileKit and the visible v2 badge in the main form and settings page. Browser page titles, API documentation titles, native startup messages and package names use the new branding. Technical semantic version 2.0.0 remains release metadata, not part of the product name.

The rename does not change extraction logic, providers, fallbacks, model settings or stored data. Existing compatibility identifiers remain intentional:

- `PROFILEKIT_*` environment variables and the native launcher's port variables.
- `data/profilekit.sqlite3`, SQLite tables and the `profilekit:v2:` optional Redis cache prefix.
- The logger identifier and HTTP/robots user agent `ProfileKit`, so robots matching and source-access behavior remain unchanged.
- Historical benchmark/verification records, existing workspace folder names and development test filenames.

Use the documented existing variables and database path; renaming them manually would require a migration. New downloadable source archives use `skriv-scrapper.zip` with a `skriv-scrapper/` root folder. A checkout can be placed in any directory; native scripts resolve paths from their own project root.

The [current checksums](../config/source-integrity.json) cover the renamed release. [Pre-branding checksums](../config/pre-branding-source-integrity.json) retain the previous source baseline. Installation benchmarks and full-suite results in [verification](verification.md) were captured before this presentation-only rename; its follow-up checks are recorded separately there.
