import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from urllib.error import URLError

from discover import append_websites, extract_websites, fetch_json, make_overpass_query


class DiscoveryTests(unittest.TestCase):
    def test_builds_category_query_for_bounding_box(self):
        query = make_overpass_query(
            (30.0, -98.0, 30.5, -97.5),
            (("craft", "plumber"),),
            25,
        )
        self.assertIn('nwr["craft"="plumber"](30.0,-98.0,30.5,-97.5);', query)
        self.assertTrue(query.endswith("out center 25;"))

    def test_extracts_and_deduplicates_company_websites(self):
        result = {
            "elements": [
                {"tags": {"website": "acme.example"}},
                {"tags": {"contact:website": "https://www.acme.example/contact"}},
                {"tags": {"website": "https://www.facebook.com/acme"}},
                {"tags": {"website": "mailto:info@example.com"}},
                {"tags": {"name": "No website listed"}},
            ]
        }
        self.assertEqual(extract_websites(result, 10), ["https://acme.example/"])

    def test_respects_limit(self):
        result = {"elements": [{"tags": {"website": f"https://site{i}.example"}} for i in range(3)]}
        self.assertEqual(
            extract_websites(result, 2),
            ["https://site0.example/", "https://site1.example/"],
        )

    def test_rejects_malformed_overpass_result(self):
        with self.assertRaises(ValueError):
            extract_websites({}, 10)

    def test_appends_only_new_urls_to_existing_list(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "businesses.txt"
            path.write_text("https://existing.example/\n", encoding="utf-8")

            added = append_websites(
                path,
                ["https://existing.example/", "https://new.example/"],
            )

            self.assertEqual(added, 1)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "https://existing.example/\nhttps://new.example/\n",
            )

    def test_append_skips_existing_domain_with_different_path_or_www(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "businesses.txt"
            path.write_text("https://acme.example/contact\n", encoding="utf-8")

            added = append_websites(path, ["https://www.acme.example/locations"])

            self.assertEqual(added, 0)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "https://acme.example/contact\n",
            )

    def test_appends_newline_when_existing_list_has_no_final_newline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "businesses.txt"
            path.write_text("https://existing.example/", encoding="utf-8")

            append_websites(path, ["https://new.example/"])

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "https://existing.example/\nhttps://new.example/\n",
            )

    @patch("discover.time.sleep")
    @patch("discover.urlopen")
    def test_retries_temporary_connection_errors(self, urlopen, sleep):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        urlopen.side_effect = [URLError("temporary failure"), response]

        self.assertEqual(fetch_json("https://example.test", service="test API"), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("discover.time.sleep")
    @patch("discover.urlopen", side_effect=URLError("offline"))
    def test_reports_service_after_retry_limit(self, _urlopen, sleep):
        with self.assertRaisesRegex(RuntimeError, "test API could not be reached"):
            fetch_json("https://example.test", service="test API")
        self.assertEqual(sleep.call_args_list, [call(2), call(4), call(8)])


if __name__ == "__main__":
    unittest.main()
