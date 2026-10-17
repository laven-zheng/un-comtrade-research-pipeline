import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd
import requests

from comtrade_pipeline.api import Client
from comtrade_pipeline.panel import (OBS_COLUMNS, build_panel, standardize,
                                     unique_observations, validate_mapping)
from comtrade_pipeline.__main__ import collect


def obs(exporter="CHN", importer="USA", value=10, flow="X", classification="H5"):
    return pd.DataFrame([[2017, exporter, importer, "490110", value, classification, flow]], columns=OBS_COLUMNS)


def raw(flow="X", value=10):
    return pd.DataFrame([[2017, "156", "842", "490110", value, "H5", flow]],
                        columns=["period", "reporterCode", "partnerCode", "cmdCode", "primaryValue", "classificationCode", "flowCode"])


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.mapping = validate_mapping(pd.DataFrame({"iso3": ["CHN", "USA", "FRA"], "comtrade_code": ["156", "842", "251"]}))
        self.empty = pd.DataFrame(columns=OBS_COLUMNS)

    def panel(self, direct=None, mirror=None, **kwargs):
        return build_panel(self.mapping.iso3, self.mapping.iso3, ["490110", "490199"], 2017,
                           self.empty if direct is None else direct,
                           self.empty if mirror is None else mirror, **kwargs)

    def test_mirror_direction_reversed(self):
        out = standardize(raw("M"), self.mapping, self.mapping, ["490110"], "M")
        self.assertEqual(out.iloc[0].exporter_iso3, "USA")
        self.assertEqual(out.iloc[0].importer_iso3, "CHN")

    def test_net_weight_never_used_as_money(self):
        with self.assertRaises(ValueError):
            standardize(raw().rename(columns={"primaryValue": "netWgt"}), self.mapping, self.mapping, ["490110"], "X")

    def test_invalid_monetary_values_fail(self):
        for value in [-1, None, float("inf"), "unknown"]:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                standardize(raw(value=value), self.mapping, self.mapping, ["490110"], "X")

    def test_extended_dimensions_excluded(self):
        frame = pd.concat([raw().assign(motCode="0"), raw(value=9).assign(motCode="1")])
        out = standardize(frame.reset_index(drop=True), self.mapping, self.mapping, ["490110"], "X")
        self.assertEqual(len(out), 1)
        self.assertEqual(out.export_value_usd.sum(), 10)

    def test_duplicate_responses_do_not_double_count(self):
        out = unique_observations([obs(), obs()])
        self.assertEqual(out.export_value_usd.sum(), 10)

    def test_conflicting_values_or_classifications_stop(self):
        for other in [obs(value=20), obs(classification="H4")]:
            with self.assertRaises(ValueError):
                unique_observations([obs(), other])

    def test_defaults_preserve_missing_and_observed_zero(self):
        p = self.panel(direct=obs(value=0), direct_available={"CHN"}, direct_completed={"CHN"})
        self.assertEqual(len(p), 12)
        self.assertEqual(p.export_value_usd.isna().sum(), 11)
        self.assertEqual(p.trade_imputed_zero.sum(), 0)
        self.assertEqual(p.trade_source.eq("direct_export").sum(), 1)

    def test_direct_reporter_blocks_mirror_for_whole_year(self):
        p = self.panel(direct=obs(), mirror=obs(importer="FRA", value=90, flow="M"))
        self.assertEqual(p.mirror_value_used.sum(), 0)
        self.assertEqual(p.export_value_usd.sum(), 10)

    def test_mirror_can_enter_wholly_uncovered_exporter_year(self):
        p = self.panel(mirror=obs(flow="M"), mirror_completed={"CHN"})
        self.assertEqual(p.mirror_value_used.sum(), 1)
        self.assertEqual(p.export_value_usd.sum(), 10)

    def test_legacy_zero_requires_successful_request(self):
        p = self.panel(direct_available={"CHN"}, zero_policy="legacy-coverage")
        self.assertEqual(p.trade_imputed_zero.sum(), 0)
        q = self.panel(direct_available={"CHN"}, direct_completed={"CHN"}, zero_policy="legacy-coverage")
        self.assertEqual(q.trade_imputed_zero.sum(), 4)

    def test_mirror_zero_requires_importer_coverage(self):
        p = self.panel(mirror_completed={"CHN"}, importer_available={"USA"}, zero_policy="legacy-coverage")
        self.assertEqual(p.trade_imputed_zero.sum(), 2)
        self.assertTrue(p.loc[p.importer_iso3.eq("FRA"), "export_value_usd"].isna().all())

    def test_mapping_ambiguity_fails(self):
        with self.assertRaises(ValueError):
            validate_mapping(pd.DataFrame({"iso3": ["USA", "USA"], "comtrade_code": ["840", "842"]}))

    def test_duplicate_cyber_keys_fail(self):
        cyber = pd.DataFrame({"exporter_iso3": ["CHN", "CHN"], "year": [2017, 2017], "cyber": [1, 2]})
        with self.assertRaises(ValueError):
            self.panel(cyber=cyber)

    def test_panel_ids_stable_across_years(self):
        a = self.panel()
        b = build_panel(self.mapping.iso3, self.mapping.iso3, ["490110", "490199"], 2018, self.empty, self.empty)
        pd.testing.assert_frame_equal(a[["panel_id", "exporter_iso3", "importer_iso3", "hs6"]],
                                      b[["panel_id", "exporter_iso3", "importer_iso3", "hs6"]])

    def test_empty_panel_trade_remains_numeric(self):
        self.assertTrue(pd.api.types.is_float_dtype(self.panel().export_value_usd))

    def test_fractional_year_rejected(self):
        with self.assertRaises(ValueError):
            unique_observations([obs().assign(year=2017.5)])

    def test_reported_zero_retains_observation_flag(self):
        p = self.panel(direct=obs(value=0), zero_policy="legacy-coverage", direct_completed={"CHN"})
        row = p.loc[p.exporter_iso3.eq("CHN") & p.importer_iso3.eq("USA") & p.hs6.eq("490110")].iloc[0]
        self.assertEqual(row.trade_observed, 1)
        self.assertEqual(row.trade_imputed_zero, 0)


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code, self.headers = status, {}
        self.payload = {"data": []} if payload is None else payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, results):
        self.headers, self.results, self.calls = {}, list(results), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def client(self, session):
        return Client("SYNTHETIC_TEST_KEY", self.temp.name, max_records=2, interval=0, session=session)

    def test_none_data_not_cached_as_zero(self):
        client = self.client(Session([Response(payload={"data": None})]))
        with self.assertRaises(RuntimeError):
            client.trade(2017, "156", None, ["490110"], "X")
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])

    def test_unauthorized_fails_without_retries(self):
        s = Session([Response(401)])
        with self.assertRaises(RuntimeError):
            self.client(s).availability(2017)
        self.assertEqual(len(s.calls), 1)

    @patch("comtrade_pipeline.api.time.sleep")
    def test_transient_error_retries(self, _sleep):
        s = Session([Response(429), requests.Timeout(), Response()])
        self.assertTrue(self.client(s).availability(2017).empty)
        self.assertEqual(len(s.calls), 3)

    def test_empty_success_cache_and_credential_exclusion(self):
        s = Session([Response()])
        c = self.client(s)
        c.trade(2017, "156", None, ["490110"], "X")
        c.trade(2017, "156", None, ["490110"], "X")
        self.assertEqual(len(s.calls), 1)
        text = "".join(p.read_text() for p in Path(self.temp.name).iterdir())
        self.assertNotIn("SYNTHETIC_TEST_KEY", text)
        self.assertNotIn("SYNTHETIC_TEST_KEY", s.calls[0][0])

    def test_modified_cache_is_rejected(self):
        c = self.client(Session([Response()]))
        c.trade(2017, "156", None, ["490110"], "X")
        next(Path(self.temp.name).glob("*.csv")).write_text("modified")
        with self.assertRaises(ValueError):
            c.trade(2017, "156", None, ["490110"], "X")

    def test_cap_triggers_product_split(self):
        full = raw().to_dict("records") * 2
        s = Session([Response(payload={"data": full}), Response(), Response()])
        result = self.client(s).trade(2017, "156", None, ["490110", "490199"], "X")
        self.assertEqual(len(result), 2)
        self.assertEqual(len(s.calls), 3)
        self.assertEqual(len(list(Path(self.temp.name).glob("*.json"))), 2)

    def test_unsplittable_cap_stops(self):
        s = Session([Response(payload={"data": raw().to_dict("records") * 2})])
        with self.assertRaises(RuntimeError):
            self.client(s).trade(2017, "156", None, ["490110"], "X")

    def test_wrong_response_scope_rejected(self):
        s = Session([Response(payload={"data": raw().assign(period=2018).to_dict("records")})])
        with self.assertRaises(ValueError):
            self.client(s).trade(2017, "156", None, ["490110"], "X")
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])


class CollectionIntegrationTests(unittest.TestCase):
    def test_mocked_collection_builds_panel_and_manifest(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def availability(self, *args):
                return pd.DataFrame({"period": [2017], "reporterCode": ["156"]})

            def trade(self, year, reporter, partner, products, flow, classification):
                if flow == "X" and reporter == "156":
                    return [raw()]
                if flow == "M" and partner == "251":
                    return [raw("M", value=75).assign(partnerCode="251")]
                return [raw().iloc[0:0]]

        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(config=Path("config/example.json"), output=Path(temp) / "run",
                                   cyber=None, zero_policy="none", mirror=True, max_records=500,
                                   interval=0, formats=["csv"])
            with patch("comtrade_pipeline.__main__.Client", FakeClient):
                collect(args)
            p = pd.read_csv(args.output / "final/trade_panel_2017.csv")
            self.assertEqual(len(p), 12)
            self.assertEqual(p.trade_observed.sum(), 2)
            self.assertEqual(p.mirror_value_used.sum(), 1)
            self.assertEqual(p.export_value_usd.isna().sum(), 10)
            self.assertTrue((args.output / "run_manifest.json").exists())
            args.zero_policy = "legacy-coverage"
            with patch("comtrade_pipeline.__main__.Client", FakeClient), self.assertRaises(ValueError):
                collect(args)


if __name__ == "__main__":
    unittest.main()
