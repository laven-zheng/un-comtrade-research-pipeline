# Data dictionary

| Field | Meaning |
| --- | --- |
| `panel_id` | Exporter–importer–product grid identifier; stable for an unchanged grid |
| `year` | Annual observation period |
| `exporter_iso3`, `importer_iso3` | Research identifiers in the explicit mapping files |
| `hs6` | Six-digit product string, interpreted within a classification edition |
| `export_value_usd` | Direct export value or partner-reported mirror import value, current USD |
| `classification_code` | Classification returned with an actual observation |
| `flow_code` | Original source flow: `X` direct export, `M` partner import |
| `trade_source` | `direct_export`, `mirror_import`, `imputed_zero_direct`, `imputed_zero_mirror`, or `missing_unresolved` |
| `trade_observed` | 1 if an actual direct or mirror record is used; includes reported zeros |
| `mirror_value_used` | 1 for an actual mirror value; 0 for assumed mirror zeros |
| `direct_reporter_year_available` | Direct reporting availability or returned-direct-record evidence |
| `direct_query_completed` | All selected direct-product requests completed successfully |
| `mirror_query_completed` | All selected mirror-product requests completed successfully |
| `mirror_importer_year_available` | Destination reporting availability in the requested period |
| `coverage_method` | `direct`, `mirror`, or `none`: query/availability-based coverage, not proof of a true zero |
| `trade_imputed_zero` | 1 only for a zero explicitly assumed by `legacy-coverage` |
| `zero_policy` | `none` or `legacy-coverage` |
| `cyber`, `cyber_matched` | Optional exporter-year covariate and nonmissing-match flag; supplied examples are invented |

Normalized observation inputs require `year,exporter_iso3,importer_iso3,hs6,export_value_usd,classification_code,flow_code`. Mirrors are already direction-reversed in these inputs. Coverage inputs require `year,iso3,direct_available,importer_available,direct_completed,mirror_completed`; all four flags are 0/1. These flags must describe actual reporting/request evidence when working with real data.
