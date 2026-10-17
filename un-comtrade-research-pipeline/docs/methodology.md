# Methodology and limits

## Sample and identifiers

The configuration specifies exporter and importer mappings, years, products, and the requested classification. Exporter and importer sets may differ. The grid excludes self-trade and aggregate partners such as World. Country mappings must be one-to-one. They should be audited by reporting area and period before a substantive analysis; a fixed mapping file does not resolve historical territorial changes.

## Monetary observations

`primaryValue` supplies the monetary value. Missing, negative, nonnumeric, and infinite values stop processing for selected records. Weight is never used as a substitute for trade value. Raw records outside the selected countries/products are excluded. When supplied, only total second-partner (`0`), total transport (`0`), and total customs (`C00`) records are retained, avoiding sums of totals and their components.

The normalizer does not infer missing fields from filenames, silently replace invalid monetary values, or sum conflicting observations. Identical normalized records are deduplicated; different values or classifications at the same panel key require an explicit audit. `classification_code` and `flow_code` remain attached to actual observations. Missing or assumed-zero cells have no invented classification provenance.

## Coverage, mirrors, and missingness

Direct coverage means either presence in the data-availability response or evidence of returned direct records. The collection routine queries every configured exporter-year; successful completion is recorded separately from availability. For mirrors, original importer = reporting country and original exporter = partner country. Only exporter-years without direct coverage are eligible. This policy preserves direct-source priority at the exporter-year level.

Default policy `none` preserves all unobserved cells as missing, including when a country has other available records. Reported zeros remain observed values. `legacy-coverage` additionally assumes absent cells to be zero when direct coverage and query completion both hold, or when a mirror query completed and the destination has reporting availability. This policy does not establish validity of an HS code in a particular edition, reporting completeness, lack of confidentiality, or a true economic zero. It is retained for explicit sensitivity comparisons.

An actual mirror observation can exist even where generic importer availability is absent; the observation is retained. Generic availability is needed only for assumed mirror zeros. Mirrors are partner-reported imports, not exporter declarations. Valuation and other reporting differences remain; see [UN Comtrade on bilateral asymmetries](https://uncomtrade.org/docs/exports-of-a-country-not-coincide-with-imports-of-its-partner/).

## Classification and reproducibility

Native `HS` data may include multiple reported HS editions. The pipeline retains native provenance and does not supply a product concordance. Selecting an explicit HS edition changes the request; users must verify available converted data and intended product meanings. The product list in the public example is illustrative and does not define the universe of digital trade or services trade.

Source-data revisions can change later downloads. Each cached response has an exact query signature, UTC timestamp, row count, and SHA-256 checksum. Metadata is written after the CSV using atomic replacement of individual files. A mismatched checksum stops reuse. Cached raw values, checksums, and query metadata remain local under `outputs/`; the public repository carries only synthetic examples.
