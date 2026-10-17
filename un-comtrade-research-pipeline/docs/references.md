# Source documentation

Official documentation checked on 17 October 2026:

- [UN Comtrade API](https://uncomtrade.org/docs/un-comtrade-api/): authenticated and preview endpoints, request parameters, access tiers.
- [Official Python API library](https://github.com/uncomtrade/comtradeapicall): functions and endpoint conventions used by the original research scripts. This refactor calls the official HTTP endpoints directly for explicit timeout/error handling.
- [rOpenSci comtradr request implementation](https://github.com/ropensci/comtradr/blob/main/R/ct_build_request.R): primary implementation reference for the subscription-key HTTP header.
- [Data conversion](https://uncomtrade.org/docs/data-conversion/): classification conversion and its limits.
- [Reported and converted data](https://uncomtrade.org/docs/reported-and-converted-data-commodity-conversion-issues/): availability and interpretation of classifications.
- [Data-use policy](https://uncomtrade.org/docs/policy-on-comtrade-data-use/) and [re-dissemination policy](https://uncomtrade.org/docs/re-dissemination-of-data/): consult current source terms before distributing downloaded data.

The supplied example observations and covariate values are invented. No UN Comtrade trade records or research covariate data are redistributed in this public package. Country identifiers are explicit configuration values, not inferred from ordinary ISO numeric codes. The public configuration is a teaching sample, not an empirical research sample.
