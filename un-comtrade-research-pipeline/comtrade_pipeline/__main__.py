"""Run with python -m comtrade_pipeline --help."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import pandas as pd

from .api import Client, digest, write_json
from .panel import (OBS_COLUMNS, build_panel, code, standardize, summarize,
                    unique_observations, validate_mapping)


def read_csv(path):
    return pd.read_csv(path, dtype={"iso3": str, "comtrade_code": str, "hs6": str, "cmdCode": str})


def save_panel(panel, output, formats):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stem = output / f"trade_panel_{int(panel.year.iloc[0])}"
    if "csv" in formats:
        panel.to_csv(stem.with_suffix(".csv"), index=False)
    if "parquet" in formats:
        panel.to_parquet(stem.with_suffix(".parquet"), index=False)
    if "stata" in formats:
        out = panel.copy()
        for col in out.select_dtypes(include=["object", "string"]):
            out[col] = out[col].fillna("").astype(str)
        out.to_stata(stem.with_suffix(".dta"), write_index=False, version=118)
    write_json(stem.with_suffix(".qa.json"), summarize(panel))


def demo(output, formats):
    """Small invented fixture; deliberately includes uncovered trade cells."""
    mapping = validate_mapping(pd.DataFrame({"iso3": ["CHN", "FRA", "USA"],
                                            "comtrade_code": ["156", "251", "842"]}))
    rows = pd.DataFrame([
        [2017, "156", "842", "490110", 120.0, "H5", "X"],
        [2017, "156", "251", "490110", 0.0, "H5", "X"],
    ], columns=["period", "reporterCode", "partnerCode", "cmdCode", "primaryValue", "classificationCode", "flowCode"])
    mirror_raw = pd.DataFrame([[2017, "156", "251", "490110", 75.0, "H5", "M"]], columns=rows.columns)
    products = ["490110", "490199"]
    direct = standardize(rows, mapping, mapping, products, "X")
    mirror = standardize(mirror_raw, mapping, mapping, products, "M")
    cyber = pd.DataFrame({"exporter_iso3": ["CHN", "FRA"], "year": [2017, 2017], "cyber": [0.6, 0.8]})
    panel = build_panel(mapping.iso3, mapping.iso3, products, 2017, direct, mirror,
                        direct_available={"CHN"}, importer_available={"CHN", "USA"},
                        direct_completed={"CHN", "FRA", "USA"}, mirror_completed={"FRA"}, cyber=cyber)
    save_panel(panel, output, formats)
    write_json(Path(output) / "DEMO_NOTICE.json", {"synthetic": True, "description": "Invented values; not research findings"})
    print(json.dumps(summarize(panel), indent=2))


def collect(args):
    cfg_path = args.config.resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent
    exporters_path = base / cfg["exporters"]
    importers_path = base / cfg["importers"]
    exporters = validate_mapping(read_csv(exporters_path))
    importers = validate_mapping(read_csv(importers_path))
    products = cfg["products"]
    if (not products or len(set(products)) != len(products)
            or any(not isinstance(x, str) or len(x) != 6 or not x.isdigit() for x in products)):
        raise ValueError("Products must be distinct six-digit strings")
    years = cfg["years"]
    if not years or len(set(years)) != len(years) or any(type(y) is not int or not 1900 <= y <= 2100 for y in years):
        raise ValueError("Years must be distinct integer annual periods")
    classification = cfg.get("classification", "HS")
    if classification not in {"HS", "H0", "H1", "H2", "H3", "H4", "H5", "H6"}:
        raise ValueError("Unsupported HS classification identifier")
    if classification == "HS":
        logging.warning("HS requests native reported classifications; this does not harmonize product definitions")
    if args.zero_policy != "none":
        logging.warning("legacy-coverage assumes absent cells are zero; product validity/confidentiality are not established")
    cyber = read_csv(args.cyber) if args.cyber else None
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Avoid mixing outputs from different assumptions in the same directory.
    signature = {"config_sha256": digest(cfg_path), "exporters_sha256": digest(exporters_path),
                 "importers_sha256": digest(importers_path), "zero_policy": args.zero_policy,
                 "mirror": args.mirror, "max_records": args.max_records,
                 "cyber_sha256": digest(args.cyber) if args.cyber else None,
                 "formats": args.formats}
    manifest = out / "run_manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != signature:
        raise ValueError("Output belongs to different inputs/options; use a new output directory")
    write_json(manifest, signature)
    client = Client(os.getenv("UN_COMTRADE_API_KEY", "").strip(), out / "cache",
                    max_records=args.max_records, interval=args.interval)
    summaries = []
    # One exporter-year at a time: retry/resume is independent of full panel size.
    for year in years:
        available = client.availability(year, classification)
        available_codes = set(available.reporterCode.map(code))
        direct_available = set(exporters.loc[exporters.comtrade_code.isin(available_codes), "iso3"])
        importer_available = set(importers.loc[importers.comtrade_code.isin(available_codes), "iso3"])
        direct_frames, mirror_frames = [], []
        direct_completed, mirror_completed = set(), set()
        for row in exporters.itertuples():
            raw_frames = client.trade(year, row.comtrade_code, None, products, "X", classification)
            cleaned = [standardize(r, exporters, importers, products, "X") for r in raw_frames]
            direct_frames.extend(cleaned)
            direct_completed.add(row.iso3)
            if any(not r.empty for r in raw_frames):
                direct_available.add(row.iso3)
        direct = unique_observations(direct_frames)
        if args.mirror:
            for row in exporters.loc[~exporters.iso3.isin(direct_available)].itertuples():
                raw_frames = client.trade(year, None, row.comtrade_code, products, "M", classification)
                mirror_frames.extend(standardize(r, exporters, importers, products, "M") for r in raw_frames)
                mirror_completed.add(row.iso3)
        mirror = unique_observations(mirror_frames)
        panel = build_panel(exporters.iso3, importers.iso3, products, year, direct, mirror,
                            direct_available, importer_available, direct_completed, mirror_completed,
                            args.zero_policy, cyber)
        save_panel(panel, out / "final", args.formats)
        summaries.append(summarize(panel))
        write_json(out / "qa_summary.json", summaries)
        logging.info("Completed %d: %d panel rows", year, len(panel))


def build(args):
    """Build offline from normalized observations and explicit coverage evidence."""
    cfg_path = args.config.resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    exporters = validate_mapping(read_csv(cfg_path.parent / cfg["exporters"]))
    importers = validate_mapping(read_csv(cfg_path.parent / cfg["importers"]))
    direct = unique_observations([read_csv(args.direct)])
    mirror = unique_observations([read_csv(args.mirror)]) if args.mirror else pd.DataFrame(columns=OBS_COLUMNS)
    for frame in [direct, mirror]:
        if not frame.year.isin(cfg["years"]).all():
            raise ValueError("Offline observations include years outside the configuration")
    coverage = read_csv(args.coverage) if args.coverage else pd.DataFrame(
        columns=["year", "iso3", "direct_available", "importer_available", "direct_completed", "mirror_completed"])
    required = {"year", "iso3", "direct_available", "importer_available", "direct_completed", "mirror_completed"}
    if not required <= set(coverage) or coverage.duplicated(["year", "iso3"]).any():
        raise ValueError("Coverage needs unique year/iso3 and four availability/completion flags")
    for flag in required - {"year", "iso3"}:
        if not coverage[flag].isin([0, 1]).all():
            raise ValueError("Coverage flags must be 0 or 1")
    cyber = read_csv(args.cyber) if args.cyber else None
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Offline build output must be empty; use a new output directory")
    for year in cfg["years"]:
        c = coverage.loc[coverage.year.eq(year)]
        flags = [set(c.loc[c[f].eq(1), "iso3"]) for f in
                 ["direct_available", "importer_available", "direct_completed", "mirror_completed"]]
        panel = build_panel(exporters.iso3, importers.iso3, cfg["products"], year,
                            direct, mirror, *flags, args.zero_policy, cyber)
        save_panel(panel, args.output, args.formats)


def main():
    parser = argparse.ArgumentParser(description="Collect bilateral Comtrade records or run an offline synthetic demo")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo", help="Offline invented example; no account or API key")
    d.add_argument("--output", type=Path, default=Path("outputs/demo"))
    d.add_argument("--formats", nargs="+", choices=["csv", "parquet", "stata"], default=["csv"])
    c = sub.add_parser("collect", help="Collect fresh data with your own authorized API key")
    c.add_argument("--config", type=Path, default=Path("config/example.json"))
    c.add_argument("--output", type=Path, default=Path("outputs/research"))
    c.add_argument("--cyber", type=Path, help="Optional CSV: exporter_iso3,year,cyber")
    c.add_argument("--mirror", action="store_true", help="Fill wholly uncovered exporter-years with partner-reported imports")
    c.add_argument("--zero-policy", choices=["none", "legacy-coverage"], default="none")
    c.add_argument("--max-records", type=int, default=500, help="Must not exceed your subscription's actual response limit")
    c.add_argument("--interval", type=float, default=1.2, help="Minimum seconds between HTTP requests; adapt to your quota")
    c.add_argument("--formats", nargs="+", choices=["csv", "parquet", "stata"], default=["csv"])
    b = sub.add_parser("build", help="Build offline from normalized CSVs; see examples/")
    b.add_argument("--config", type=Path, default=Path("config/example.json"))
    b.add_argument("--direct", type=Path, required=True)
    b.add_argument("--mirror", type=Path)
    b.add_argument("--coverage", type=Path, help="Explicit year/iso3 reporting and query-completion flags")
    b.add_argument("--cyber", type=Path)
    b.add_argument("--output", type=Path, default=Path("outputs/offline-build"))
    b.add_argument("--zero-policy", choices=["none", "legacy-coverage"], default="none")
    b.add_argument("--formats", nargs="+", choices=["csv", "parquet", "stata"], default=["csv"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command == "demo":
        demo(args.output, args.formats)
    elif args.command == "collect":
        collect(args)
    else:
        build(args)


if __name__ == "__main__":
    main()
