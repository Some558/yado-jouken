"""Offline regression fixtures. No credentials, API calls, or production writes."""

import contextlib
import copy
import datetime
import hashlib
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
import urllib.error
from unittest import mock

PIPELINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
import fetch
import promote
import rakuten_client
import transform
import validate

AS_OF = "2026-10-23"
SECRET = "fixture-only-sensitive-value"
AUTH_URL = f"https://example.invalid/?accessKey={SECRET}"
ENDPOINT = "Travel/SimpleHotelSearch/20170426"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


class PipelineFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.work = self.root / "data/work"
        self.staged = self.root / "data/staged"
        self.latest = self.root / "data/latest"
        self.areas = [{"slug": slug, "label": slug, "officialName": slug,
                       "middleClassCode": "nagano", "smallClassCode": "karui"}
                      for slug in ("one", "two")]
        config = self.root / "pipeline/config"
        write_json(config / "areas.json", {"areas": self.areas})
        write_json(config / "conditions.json", {"conditions": [{
            "slug": "sauna", "label": "サウナ", "detect": {
                "method": "facility", "fields": ["hotelFacilities"], "values": ["サウナ"],
            },
        }]})
        write_json(self.root / "data/cache/squeeze.json", {"areas": {}})
        for number in range(1, 21):
            write_json(self.root / f"data/facilities/{number}.json", {
                "hotelNo": number, "hotelFacilities": ["サウナ"],
            })
        self.hotels = [{"hotelNo": n, "hotelName": f"hotel {n}",
                        "hotelMinCharge": 10000, "reviewAverage": 4,
                        "hotelInformationUrl": "https://hb.afl.rakuten.co.jp/fixture",
                        "planListUrl": "https://hb.afl.rakuten.co.jp/fixture"}
                       for n in range(1, 21)]
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for module in (fetch, transform):
            for name, value in {"ROOT": self.root, "CONFIG": config, "WORK": self.work,
                                "CACHE": self.root / "data/cache",
                                "FACILITIES": self.root / "data/facilities"}.items():
                stack.enter_context(mock.patch.object(module, name, value))
        stack.enter_context(mock.patch.object(validate, "load_env", return_value={}))
        self.write_daily()
        self.run_transform()
        shutil.copytree(self.staged, self.latest)
        self.before = hashes(self.latest)

    def write_daily(self, hotels=None, total=200):
        for area in self.areas:
            write_json(self.work / f"daily/{area['slug']}.json", {
                "area": area["slug"], "dataAsOf": AS_OF, "recordCount": total,
                "hotels": self.hotels if hotels is None else hotels,
            })
        write_json(self.work / "daily/manifest.json", {
            "dataAsOf": AS_OF, "areas": [a["slug"] for a in self.areas],
        })

    def run_transform(self, out=None):
        with mock.patch.object(sys, "argv", ["transform", str(out or self.staged), "--as-of", AS_OF]):
            with contextlib.redirect_stdout(io.StringIO()):
                transform.main()

    def run_validate(self):
        output = io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["validate", str(self.staged), "--baseline", str(self.latest)]):
            with contextlib.redirect_stdout(output):
                try:
                    validate.main()
                except SystemExit as error:
                    code = error.code
        return code, output.getvalue()

    def seed_stale(self):
        page = json.loads((self.staged / "pages/sauna--one.json").read_text())
        page.update(slug="sauna--old", dataAsOf="2026-07-15")
        page["stats"]["medianPrice"] = 15000
        write_json(self.staged / "pages/sauna--old.json", page)
        for name in ("old", "area--old"):
            write_json(self.staged / f"hubs/{name}.json", {"dataAsOf": "2026-07-15"})

    def assert_latest_preserved(self):
        self.assertEqual(self.before, hashes(self.latest))

    def test_success_replaces_stale_pages_and_both_hub_types(self):
        self.seed_stale()
        self.run_transform()
        self.assertFalse((self.staged / "pages/sauna--old.json").exists())
        self.assertFalse((self.staged / "hubs/old.json").exists())
        self.assertFalse((self.staged / "hubs/area--old.json").exists())
        self.assertEqual(0, self.run_validate()[0])
        self.assert_latest_preserved()

    def test_zero_removes_all_old_outputs_and_still_fails_both_count_gates(self):
        self.seed_stale()
        self.write_daily([], 0)
        self.run_transform()
        self.assertEqual([], list((self.staged / "pages").glob("*.json")))
        self.assertEqual([], list((self.staged / "hubs").glob("*.json")))
        code, log = self.run_validate()
        self.assertEqual(1, code)
        self.assertIn("0 < 300", log)
        self.assertIn("80%", log)
        self.assertNotIn("中央値価格", log)
        self.assert_latest_preserved()

    def test_partial_daily_fetch_cannot_reuse_old_inputs(self):
        response = {"pagingInfo": {"recordCount": 200, "pageCount": 1},
                    "hotels": [{"hotel": [{"hotelBasicInfo": h}]} for h in self.hotels]}
        client = mock.Mock()
        client.search.side_effect = [response, rakuten_client.RakutenTransportError("network_error")]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(rakuten_client.RakutenTransportError):
                fetch.fetch_daily(client, self.areas, datetime.date.fromisoformat(AS_OF))
        self.assertFalse((self.work / "daily/manifest.json").exists())
        self.assertFalse((self.work / "daily/two.json").exists())
        with self.assertRaises(SystemExit):
            self.run_transform()
        self.assertFalse((self.staged / "meta.json").exists())
        self.assert_latest_preserved()

    def test_complete_daily_fetch_publishes_dated_manifest(self):
        client = mock.Mock()
        client.search.return_value = {"pagingInfo": {"recordCount": 0, "pageCount": 0}, "hotels": []}
        with contextlib.redirect_stdout(io.StringIO()):
            fetch.fetch_daily(client, self.areas, datetime.date.fromisoformat(AS_OF))
        self.assertEqual(AS_OF, json.loads((self.work / "daily/manifest.json").read_text())["dataAsOf"])
        self.run_transform()
        self.assertEqual(1, self.run_validate()[0])
        self.assert_latest_preserved()

    def test_wrong_day_or_partial_manifest_rejected(self):
        for manifest in ({"dataAsOf": "2026-07-15", "areas": ["one", "two"]},
                         {"dataAsOf": AS_OF, "areas": ["one"]}):
            with self.subTest(manifest=manifest):
                write_json(self.work / "daily/manifest.json", manifest)
                with self.assertRaises(SystemExit):
                    self.run_transform()
                self.assertFalse((self.staged / "meta.json").exists())
                self.assert_latest_preserved()

    def test_wrong_day_area_rejected(self):
        path = self.work / "daily/two.json"
        daily = json.loads(path.read_text())
        daily["dataAsOf"] = "2026-07-15"
        write_json(path, daily)
        with self.assertRaises(SystemExit):
            self.run_transform()
        self.assertFalse((self.staged / "meta.json").exists())

    def test_partial_transform_leaves_no_completion_marker(self):
        original = pathlib.Path.write_text

        def fail_midway(path, *args, **kwargs):
            if path.name == "sauna--two.json":
                raise OSError("fixture write failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "write_text", fail_midway):
            with self.assertRaises(OSError):
                self.run_transform()
        self.assertTrue((self.staged / "pages/sauna--one.json").exists())
        self.assertFalse((self.staged / "meta.json").exists())
        self.assertNotEqual(0, self.run_validate()[0])
        self.assert_latest_preserved()

    def test_output_cannot_clean_latest_or_follow_output_symlink(self):
        for out in (self.latest, self.latest / "nested", self.latest.parent):
            with self.assertRaises(SystemExit):
                self.run_transform(out)
        shutil.rmtree(self.staged / "pages")
        (self.staged / "pages").symlink_to(self.latest / "pages")
        with self.assertRaises(SystemExit):
            self.run_transform()
        self.assert_latest_preserved()

    def test_validate_rejects_extra_files_and_stale_page_or_hub_dates(self):
        self.seed_stale()
        code, log = self.run_validate()
        self.assertEqual(1, code)
        self.assertIn("生成ページ数", log)
        self.assertIn("取得日不一致 sauna--old.json", log)
        self.assertIn("取得日不一致 hub old.json", log)

    def test_existing_quality_gates_remain_closed(self):
        page_path = self.staged / "pages/sauna--one.json"
        original = json.loads(page_path.read_text())
        for kind in ("median", "price", "schema", "affiliate", "affiliate_id"):
            with self.subTest(kind=kind):
                page = copy.deepcopy(original)
                if kind == "median":
                    page["stats"]["medianPrice"] = 15000
                elif kind == "price":
                    page["hotels"][0]["minCharge"] = 1000001
                elif kind == "schema":
                    page["hotels"][0]["name"] = ""
                elif kind == "affiliate":
                    page["hotels"][0]["infoUrl"] = AUTH_URL
                write_json(page_path, page)
                with mock.patch.object(validate, "load_env", return_value={"RAKUTEN_AFFILIATE_ID": "expected"} if kind == "affiliate_id" else {}):
                    code, log = self.run_validate()
                self.assertEqual(1, code, log)
                self.assertNotIn(SECRET, log)
                self.assertNotIn(AUTH_URL, log)
                self.assert_latest_preserved()

    def test_zero_pages_fails_even_without_baseline(self):
        self.write_daily([], 400)
        self.run_transform()
        shutil.rmtree(self.latest)
        self.assertEqual(1, self.run_validate()[0])

    def prepare_script_fixture(self):
        for source in PIPELINE.glob("*.py"):
            shutil.copy(source, self.root / "pipeline" / source.name)
        shutil.copytree(PIPELINE / "schema", self.root / "pipeline/schema", dirs_exist_ok=True)
        shutil.copytree(PIPELINE.parent / "scripts", self.root / "scripts")
        # Entry scripts prefer their own venv over /opt/homebrew/bin.
        interpreter = self.root / ".venv/bin/python3"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        interpreter.chmod(0o755)

    def run_script(self, weekly=False, skip=False):
        env = {**os.environ, "PATH": str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
        for key in list(env):
            if key.startswith("RAKUTEN_"):
                env.pop(key)
        command = ["bash", str(self.root / f"scripts/{'weekly' if weekly else 'daily'}-refresh.sh"), "--as-of", AS_OF]
        if skip:
            command.append("--skip-fetch")
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)

    def test_shell_zero_validation_failure_preserves_latest(self):
        self.prepare_script_fixture()
        self.write_daily([], 0)
        result = self.run_script(skip=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("公開中止", result.stdout)
        self.assert_latest_preserved()

    def test_both_shell_entrypoints_stop_on_fetch_failure(self):
        self.prepare_script_fixture()
        (self.root / "pipeline/fetch.py").write_text("raise SystemExit('fixture fetch failure')\n")
        for weekly in (False, True):
            with self.subTest(weekly=weekly):
                result = self.run_script(weekly=weekly)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("fixture fetch failure", result.stderr)
                self.assert_latest_preserved()

    def test_promotion_success_replaces_and_copy_failure_preserves_latest(self):
        with mock.patch.object(promote.shutil, "copytree", side_effect=OSError("fixture full disk")):
            with self.assertRaises(OSError):
                promote.promote(self.staged, self.latest)
        self.assert_latest_preserved()
        (self.latest / "obsolete").write_text("old")
        with contextlib.redirect_stdout(io.StringIO()):
            promote.promote(self.staged, self.latest)
        self.assertEqual(hashes(self.staged), hashes(self.latest))

    def test_promotion_rename_failure_restores_latest(self):
        original = pathlib.Path.rename

        def fail_candidate(path, target):
            if path.name == "next":
                raise OSError("fixture rename failure")
            return original(path, target)

        with mock.patch.object(pathlib.Path, "rename", fail_candidate):
            with self.assertRaises(OSError):
                promote.promote(self.staged, self.latest)
        self.assert_latest_preserved()


class ClientFixtures(unittest.TestCase):
    def setUp(self):
        env = {name: SECRET for name in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID")}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(rakuten_client, "load_env", return_value=env))
        self.stack.enter_context(mock.patch.object(rakuten_client.time, "sleep"))
        self.client = rakuten_client.RakutenClient()
        self.stack.enter_context(mock.patch.object(self.client, "_throttle"))
        self.urlopen = self.stack.enter_context(mock.patch.object(rakuten_client.urllib.request, "urlopen"))

    def response(self, data):
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(data).encode()
        response.__enter__.return_value = response
        return response

    def http_error(self, status, code="not_found"):
        body = json.dumps({"error": code, "error_description": SECRET + AUTH_URL}).encode()
        return urllib.error.HTTPError(AUTH_URL, status, SECRET, {}, io.BytesIO(body))

    def call(self):
        output = io.StringIO()
        result, failure = None, None
        with contextlib.redirect_stdout(output):
            try:
                result = self.client.search(ENDPOINT, {"page": 1, "accessKey": SECRET})
            except Exception as error:
                failure = error
                output.write(traceback.format_exc())
        log = output.getvalue()
        self.assertNotIn(SECRET, log)
        self.assertNotIn(AUTH_URL, log)
        self.assertNotIn("applicationId=", log)
        return result, failure, log

    def test_404_not_found_and_200_empty_are_distinguishable(self):
        self.urlopen.side_effect = self.http_error(404)
        result, error, log = self.call()
        self.assertIsNone(result)
        self.assertIsNone(error)
        self.assertIn('"outcome": "not_found"', log)
        self.urlopen.side_effect = None
        self.urlopen.return_value = self.response({"pagingInfo": {"recordCount": 0, "pageCount": 0}, "hotels": []})
        result, error, log = self.call()
        self.assertEqual([], result["hotels"])
        self.assertIsNone(error)
        self.assertIn('"outcome": "ok"', log)
        self.assertIn('"recordCount": 0', log)

    def test_404_body_substring_and_403_are_errors_without_retry(self):
        for status, code in ((404, "description mentions not_found"), (403, "invalid_token")):
            with self.subTest(status=status):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = self.http_error(status, code)
                _, error, log = self.call()
                self.assertIsInstance(error, rakuten_client.RakutenApiError)
                self.assertEqual(1, self.urlopen.call_count)
                self.assertIn('"outcome": "http_error"', log)

    def test_transient_http_errors_retry_with_bounded_safe_logs(self):
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = [self.http_error(status) for _ in range(3)]
                _, error, log = self.call()
                self.assertIsInstance(error, rakuten_client.RakutenApiError)
                self.assertEqual(3, self.urlopen.call_count)
                self.assertIn('"retry": false', log)

    def test_network_timeout_and_invalid_json_never_expose_original_exception(self):
        for failure, category in ((urllib.error.URLError(AUTH_URL), "network_error"),
                                  (ConnectionResetError(AUTH_URL), "network_error"),
                                  (TimeoutError(SECRET), "timeout")):
            with self.subTest(category=category):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = failure
                _, error, log = self.call()
                self.assertIsInstance(error, rakuten_client.RakutenTransportError)
                self.assertEqual(3, self.urlopen.call_count)
                self.assertIn(category, log)
        self.urlopen.side_effect = None
        response = self.response({})
        response.read.return_value = (SECRET + AUTH_URL).encode()
        self.urlopen.return_value = response
        _, error, log = self.call()
        self.assertIsInstance(error, rakuten_client.RakutenTransportError)
        self.assertIn("invalid_json", log)

    def test_retry_can_recover_and_logs_record_and_return_counts(self):
        self.urlopen.side_effect = [self.http_error(503), self.response({
            "pagingInfo": {"recordCount": 100, "pageCount": 4}, "hotels": [{}, {}],
        })]
        result, error, log = self.call()
        self.assertIsNone(error)
        self.assertEqual(2, len(result["hotels"]))
        self.assertIn('"recordCount": 100', log)
        self.assertIn('"returnedHotels": 2', log)
        self.assertIn('"attempt": 2', log)

    def test_unexpected_payload_is_closed_without_echoing_values(self):
        for payload in ([SECRET], {"error": SECRET}, {"error": "not_found"},
                        {"pagingInfo": {"recordCount": SECRET, "pageCount": 1}, "hotels": []}):
            with self.subTest(payload=payload):
                self.urlopen.return_value = self.response(payload)
                _, error, log = self.call()
                self.assertIsInstance(error, rakuten_client.RakutenTransportError)
                self.assertIn("invalid_response", log)

    def test_http_body_read_failure_is_safe_and_not_a_false_empty_result(self):
        error = self.http_error(404)
        error.read = mock.Mock(side_effect=OSError(AUTH_URL))
        self.urlopen.side_effect = error
        _, failure, log = self.call()
        self.assertIsInstance(failure, rakuten_client.RakutenApiError)
        self.assertIn('"code": "unrecognized"', log)


if __name__ == "__main__":
    unittest.main()
