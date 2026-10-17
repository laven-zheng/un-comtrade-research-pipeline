"""Pure data transformations; no network access or implicit zero filling."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd

KEY = ["year", "exporter_iso3", "importer_iso3", "hs6"]
OBS_COLUMNS = KEY + ["export_value_usd", "classification_code", "flow_code"]


def code(value, width=3):
    """Normalize numeric identifiers without inventing codes from arbitrary text."""
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.0)?", text):
        raise ValueError(f"Invalid numeric identifier: {text!r}")
    return text.removesuffix(".0").zfill(width)


def validate_mapping(frame):
    required = {"iso3", "comtrade_code"}
    if not required <= set(frame):
        raise ValueError("Mapping requires iso3 and comtrade_code columns")
    frame = frame.copy()
    frame["iso3"] = frame.iso3.astype("string").str.strip().str.upper()
    frame["comtrade_code"] = frame.comtrade_code.map(code)
    if frame.empty or not frame.iso3.str.fullmatch(r"[A-Z]{3}").fillna(False).all():
        raise ValueError("Mapping contains empty or invalid ISO3 identifiers")
    if frame.iso3.duplicated().any() or frame.comtrade_code.duplicated().any():
        raise ValueError("Mapping must be one-to-one; resolve historical codes explicitly")
    if frame.comtrade_code.eq("000").any():
        raise ValueError("World aggregate cannot be a sample country")
    return frame


def standardize(raw, exporters, importers, products, flow):
    """Keep monetary bilateral observations; reverse reporter/partner for imports.

    Only aggregate customs/transport/second-partner dimensions are admissible.
    No summing across classifications, repeated requests, or extended dimensions.
    """
    if raw.empty:
        return pd.DataFrame(columns=OBS_COLUMNS)
    required = {"period", "reporterCode", "partnerCode", "cmdCode", "primaryValue",
                "classificationCode", "flowCode"}
    if not required <= set(raw):
        raise ValueError(f"Missing raw columns: {sorted(required - set(raw))}")
    if not raw.flowCode.astype(str).eq(flow).all():
        raise ValueError(f"Unexpected flow in {flow} input")
    raw = raw.copy()
    for name, expected in [("partner2Code", "0"), ("motCode", "0"), ("customsCode", "C00")]:
        if name in raw:
            # Exclude detailed dimensions, which overlap aggregate totals.
            values = raw[name].astype(str).str.removesuffix(".0")
            raw = raw.loc[values.eq(expected)]
    if raw.empty:
        return pd.DataFrame(columns=OBS_COLUMNS)
    out = pd.DataFrame(index=raw.index)
    years = pd.to_numeric(raw.period, errors="raise")
    if (years % 1).ne(0).any():
        raise ValueError("Annual period must be an integer year")
    out["year"] = years.astype(int)
    source_col, target_col = ("reporterCode", "partnerCode") if flow == "X" else ("partnerCode", "reporterCode")
    e = dict(zip(exporters.comtrade_code, exporters.iso3))
    i = dict(zip(importers.comtrade_code, importers.iso3))
    out["exporter_iso3"] = raw[source_col].map(code).map(e)
    out["importer_iso3"] = raw[target_col].map(code).map(i)
    product_codes = raw.cmdCode.astype(str).str.removesuffix(".0")
    out["hs6"] = product_codes.where(product_codes.str.fullmatch(r"\d{6}"))
    out = out.loc[out.exporter_iso3.notna() & out.importer_iso3.notna()
                  & out.hs6.isin(products) & out.exporter_iso3.ne(out.importer_iso3)].copy()
    values = pd.to_numeric(raw.loc[out.index, "primaryValue"], errors="raise")
    if values.isna().any() or not np.isfinite(values).all() or values.lt(0).any():
        raise ValueError("Trade values must be finite nonnegative USD amounts; never substitute weight")
    out["export_value_usd"] = values.astype(float)
    out["classification_code"] = raw.loc[out.index, "classificationCode"].astype("string")
    if out.classification_code.isna().any():
        raise ValueError("Missing classification provenance")
    out["flow_code"] = flow
    return out[OBS_COLUMNS]


def unique_observations(frames):
    """Deduplicate identical responses; stop on conflicting values or revisions."""
    for frame in frames:
        if not set(OBS_COLUMNS) <= set(frame):
            raise ValueError("Normalized observations have incomplete columns")
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=OBS_COLUMNS)
    out = pd.concat(frames, ignore_index=True).drop_duplicates(OBS_COLUMNS)
    if out[KEY].isna().any().any():
        raise ValueError("Observation keys cannot be missing")
    values = pd.to_numeric(out.export_value_usd, errors="raise")
    if values.isna().any() or not np.isfinite(values).all() or values.lt(0).any():
        raise ValueError("Normalized trade values must be finite nonnegative USD")
    out["export_value_usd"] = values.astype(float)
    periods = pd.to_numeric(out.year, errors="raise")
    if not np.isfinite(periods).all() or (periods % 1).ne(0).any():
        raise ValueError("Observation years must be finite integers")
    out["year"] = periods.astype(int)
    for col in ["exporter_iso3", "importer_iso3"]:
        if not out[col].astype(str).str.fullmatch(r"[A-Z]{3}").all():
            raise ValueError("Observation country identifiers must be uppercase ISO3")
    if not out.hs6.astype(str).str.fullmatch(r"\d{6}").all():
        raise ValueError("Observation products must be six-digit strings")
    if out.classification_code.isna().any() or out.flow_code.isna().any():
        raise ValueError("Observation provenance cannot be missing")
    if out.duplicated(KEY).any():
        raise ValueError("Conflicting observations at panel key; audit revisions/dimensions before aggregation")
    return out.sort_values(KEY).reset_index(drop=True)


def build_panel(exporters, importers, products, year, direct, mirror,
                direct_available=(), importer_available=(), direct_completed=(),
                mirror_completed=(), zero_policy="none", cyber=None):
    """Construct one year's grid. Mirrors only enter wholly uncovered exporter-years.

    legacy-coverage is an explicit historical assumption, not evidence of true zero.
    It additionally requires the relevant request to have completed successfully.
    """
    if zero_policy not in {"none", "legacy-coverage"}:
        raise ValueError("Unknown zero policy")
    if (not len(exporters) or not len(importers) or not products
            or len(set(exporters)) != len(exporters) or len(set(importers)) != len(importers)
            or len(set(products)) != len(products)
            or any(not isinstance(x, str) or not re.fullmatch(r"\d{6}", x) for x in products)):
        raise ValueError("Nonempty sample and distinct six-digit product strings required")
    grid = pd.MultiIndex.from_product(
        [sorted(exporters), sorted(importers), sorted(products)],
        names=KEY[1:]).to_frame(index=False)
    grid = grid.loc[grid.exporter_iso3.ne(grid.importer_iso3)].reset_index(drop=True)
    grid.insert(0, "panel_id", np.arange(1, len(grid) + 1))
    grid.insert(1, "year", int(year))
    d = unique_observations([direct.loc[direct.year.eq(year)]])
    m = unique_observations([mirror.loc[mirror.year.eq(year)]])
    if not d.flow_code.eq("X").all() or not m.flow_code.eq("M").all():
        raise ValueError("Direct observations require X and mirrors require M")
    # Even one direct observation establishes direct coverage and blocks mirroring
    # for that whole exporter-year, matching the historical completion rule.
    available = set(direct_available) | set(d.exporter_iso3)
    m = m.loc[~m.exporter_iso3.isin(available)].copy()
    d["trade_source"] = "direct_export"
    m["trade_source"] = "mirror_import"
    nonempty = [f for f in [d, m] if not f.empty]
    observations = pd.concat(nonempty, ignore_index=True) if nonempty else d.copy()
    for col, allowed in [("exporter_iso3", exporters), ("importer_iso3", importers), ("hs6", products)]:
        if not observations[col].isin(allowed).all():
            raise ValueError(f"Observation outside configured {col} sample")
    panel = grid.merge(observations, how="left", on=KEY, validate="one_to_one")
    panel["export_value_usd"] = pd.to_numeric(panel.export_value_usd, errors="raise").astype(float)
    panel["trade_observed"] = panel.trade_source.notna().astype("int8")
    panel["direct_reporter_year_available"] = panel.exporter_iso3.isin(available).astype("int8")
    panel["direct_query_completed"] = panel.exporter_iso3.isin(direct_completed).astype("int8")
    panel["mirror_query_completed"] = panel.exporter_iso3.isin(mirror_completed).astype("int8")
    panel["mirror_importer_year_available"] = panel.importer_iso3.isin(importer_available).astype("int8")
    panel["coverage_method"] = "none"
    direct_mask = panel.direct_reporter_year_available.eq(1) & panel.direct_query_completed.eq(1)
    mirror_mask = (panel.direct_reporter_year_available.eq(0) & panel.mirror_query_completed.eq(1)
                   & panel.mirror_importer_year_available.eq(1))
    panel.loc[direct_mask, "coverage_method"] = "direct"
    panel.loc[mirror_mask, "coverage_method"] = "mirror"
    panel["trade_imputed_zero"] = 0
    if zero_policy == "legacy-coverage":
        fill = panel.export_value_usd.isna() & panel.coverage_method.ne("none")
        panel.loc[fill, "export_value_usd"] = 0.0
        panel.loc[fill, "trade_imputed_zero"] = 1
        panel.loc[fill, "trade_source"] = "imputed_zero_" + panel.loc[fill, "coverage_method"]
    panel["trade_source"] = panel.trade_source.fillna("missing_unresolved")
    panel["mirror_value_used"] = panel.trade_source.eq("mirror_import").astype("int8")
    panel["zero_policy"] = zero_policy
    if cyber is not None:
        required = {"exporter_iso3", "year", "cyber"}
        if not required <= set(cyber) or cyber.duplicated(["exporter_iso3", "year"]).any():
            raise ValueError("Cyber table requires unique exporter_iso3/year keys and cyber")
        c = cyber[list(sorted(required))].copy()
        c["cyber"] = pd.to_numeric(c.cyber, errors="raise")
        if not np.isfinite(c.cyber.dropna()).all():
            raise ValueError("Cyber values must be finite or missing")
        panel = panel.merge(c, on=["exporter_iso3", "year"], how="left", validate="many_to_one")
        panel["cyber_matched"] = panel.cyber.notna().astype("int8")
    return panel


def summarize(panel):
    return {"year": int(panel.year.iloc[0]), "rows": len(panel),
            "observed": int(panel.trade_observed.sum()),
            "mirror_observed": int(panel.mirror_value_used.sum()),
            "imputed_zero": int(panel.trade_imputed_zero.sum()),
            "missing": int(panel.export_value_usd.isna().sum()),
            "duplicate_keys": int(panel.duplicated(KEY).sum()),
            "trade_sources": {str(k): int(v) for k, v in panel.trade_source.value_counts().items()},
            "classifications": sorted(panel.classification_code.dropna().unique().tolist()),
            "zero_policy": panel.zero_policy.iloc[0]}
