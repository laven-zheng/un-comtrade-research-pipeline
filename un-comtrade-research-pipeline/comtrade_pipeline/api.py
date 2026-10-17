"""Bounded HTTP retries and verified local request caches for the official API."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = "https://comtradeapi.un.org/data/v1"
LOG = logging.getLogger(__name__)
RAW_COLUMNS = ["period", "reporterCode", "partnerCode", "cmdCode", "primaryValue",
               "classificationCode", "flowCode"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def validate_scope(frame, endpoint, params):
    if frame.empty:
        return
    from .panel import code
    if not frame.period.astype(str).str.removesuffix(".0").eq(str(params["period"])).all():
        raise ValueError("API response does not match requested period")
    if not endpoint.startswith("get/"):
        return
    if (not frame.flowCode.astype(str).eq(params["flowCode"]).all()
            or not frame.cmdCode.astype(str).str.removesuffix(".0").isin(params["cmdCode"].split(",")).all()):
        raise ValueError("API response does not match requested flow/products")
    for col in ["reporterCode", "partnerCode"]:
        if params[col] is not None and not frame[col].map(code).eq(code(params[col])).all():
            raise ValueError(f"API response does not match {col}")


class Client:
    def __init__(self, key, root, *, max_records=500, interval=1.2, retries=5, session=None):
        if not key:
            raise ValueError("Set UN_COMTRADE_API_KEY in your environment")
        if not 2 <= max_records <= 250_000 or interval < 0 or not 1 <= retries <= 10:
            raise ValueError("Invalid request limit, interval, or retry count")
        self.root = Path(root)
        self.max_records = max_records
        self.interval = interval
        self.retries = retries
        self.session = session or requests.Session()
        # Credentials stay in the header, never in cache keys or request URLs.
        self.session.headers.update({"Ocp-Apim-Subscription-Key": key,
                                     "User-Agent": "comtrade-research-pipeline/0.1"})
        self.last_request = 0.0

    def request(self, endpoint, params):
        for attempt in range(self.retries):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            delay = min(2 ** attempt, 30)
            try:
                response = self.session.get(f"{BASE}/{endpoint}", params=params, timeout=(15, 120))
            except (requests.Timeout, requests.ConnectionError):
                if attempt + 1 == self.retries:
                    raise RuntimeError("Comtrade connection failed after bounded retries") from None
            else:
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        raise RuntimeError("Comtrade returned invalid JSON; nothing cached") from None
                    if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                            or payload.get("error") or payload.get("errorCode") not in (None, 0, "0")):
                        raise RuntimeError("Comtrade returned an error or missing data array; nothing cached")
                    return payload
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise RuntimeError(f"Comtrade HTTP {response.status_code}; check subscription/parameters")
                if response.status_code == 429:
                    try:
                        delay = max(delay, min(float(response.headers.get("Retry-After", 0)), 300))
                    except ValueError:
                        pass
                if attempt + 1 == self.retries:
                    raise RuntimeError(f"Comtrade HTTP {response.status_code} after bounded retries")
            LOG.warning("Transient Comtrade request failure; retry %d/%d", attempt + 1, self.retries)
            time.sleep(delay)
        raise RuntimeError("Unreachable retry state")

    def cached(self, endpoint, params):
        query = {"endpoint": endpoint, "params": params}
        identifier = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:24]
        csv_path = self.root / f"{identifier}.csv"
        meta_path = self.root / f"{identifier}.json"
        if csv_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("query") != query or meta.get("sha256") != digest(csv_path):
                raise ValueError("Cache integrity mismatch; move the affected cache pair before retrying")
            frame = pd.read_csv(csv_path, dtype=str)
            if len(frame) != meta.get("rows") or meta.get("status") != "complete":
                raise ValueError("Incomplete cache metadata")
            validate_scope(frame, endpoint, params)
            return frame
        payload = self.request(endpoint, params)
        frame = pd.DataFrame(payload["data"])
        # A query at the cap is split before any truncated response is cached.
        if endpoint.startswith("get/") and len(frame) >= self.max_records:
            return None
        if endpoint.startswith("get/"):
            if frame.empty:
                frame = pd.DataFrame(columns=RAW_COLUMNS)
            elif not set(RAW_COLUMNS) <= set(frame):
                raise ValueError("Trade response schema is incomplete; nothing cached")
        elif not frame.empty and not {"period", "reporterCode"} <= set(frame):
            raise ValueError("Availability response schema is incomplete; nothing cached")
        if frame.empty and len(frame.columns) == 0:
            frame = pd.DataFrame(columns=["period", "reporterCode"])
        validate_scope(frame, endpoint, params)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = csv_path.with_suffix(".csv.tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(csv_path)
        write_json(meta_path, {"query": query, "rows": len(frame), "status": "complete",
                               "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                               "sha256": digest(csv_path)})
        LOG.info("Cached %s: %d rows", endpoint, len(frame))
        return frame

    def trade(self, year, reporter, partner, products, flow, classification="HS"):
        # Keep initial URLs/code lists small before response-size splitting.
        if len(products) > 10:
            return [frame for start in range(0, len(products), 10)
                    for frame in self.trade(year, reporter, partner, products[start:start + 10], flow, classification)]
        params = {"period": str(year), "reporterCode": reporter, "partnerCode": partner,
                  "cmdCode": ",".join(products), "flowCode": flow, "partner2Code": "0",
                  "customsCode": "C00", "motCode": "0", "maxRecords": self.max_records,
                  "breakdownMode": "classic", "includeDesc": "false"}
        frame = self.cached(f"get/C/A/{classification}", params)
        if frame is not None:
            return [frame]
        if len(products) == 1:
            raise RuntimeError("Single-product request reached the cap; increase the permitted cap or narrow the query")
        middle = len(products) // 2
        return (self.trade(year, reporter, partner, products[:middle], flow, classification)
                + self.trade(year, reporter, partner, products[middle:], flow, classification))

    def availability(self, year, classification="HS"):
        return self.cached(f"getDa/C/A/{classification}", {"period": str(year)})
