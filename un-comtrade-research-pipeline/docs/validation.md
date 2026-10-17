# Validation of the public software version

Validation date: 17 October 2026. Platform: macOS on Apple Silicon, Python 3.13. A fresh temporary virtual environment successfully installed the dependencies in `requirements-optional.txt`.

| Check | Result |
| --- | --- |
| Offline tests | 26 passed |
| Collection integration | Passed with synthetic API responses, including output manifest and changed-option rejection |
| Offline demo | 12 rows; 3 observed (including one reported zero); 9 missing; no assumed zeros |
| CSV / Parquet / Stata output | Generated and read back; identifiers, monetary values, missingness, and source flags checked |
| Offline CSV build | Agrees with the programmatically generated demo |
| Credentials / research files | No real API key, empirical trade dataset, research sample configuration, or manuscript included in the public archive |

API behavior was tested with synthetic HTTP responses for success, empty success, authentication failure, throttling, timeout, malformed payload, response-scope mismatch, response caps, and cache integrity. Tests do not require a key or make network requests. Dependency installation used the network; demo execution did not.

The original local research caches were also used in a private compatibility check of the new observation-cleaning logic. The research records, sample details, counts, and private comparison outputs are intentionally excluded from the public package.

No authenticated live Comtrade collection was performed for this release preparation. Subscription-specific caps, quotas, and access must be checked by the person running a fresh collection. The code also does not establish a validated HS concordance or prove that absent records represent true zero trade. No hosted GitHub Actions execution is claimed before upload.

See `reports/validation_summary.json` for the machine-readable summary and `requirements-tested.txt` for the full dependency snapshot of the clean validation environment. The minimal installation remains `requirements.txt`; the tested snapshot includes optional Parquet support.
