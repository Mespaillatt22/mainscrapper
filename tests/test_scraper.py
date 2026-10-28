import unittest
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from scraper import (
    BusinessContact,
    _candidate_pages,
    public_email,
    host_is_public,
    normalize_phone,
    normalize_website,
    parse_contact_page,
    read_websites,
    website_key,
    write_csv,
)


class ScraperHelpersTests(unittest.TestCase):
    def test_normalizes_bare_domain_and_removes_query(self):
        self.assertEqual(normalize_website("example.com/path?tracking=1"), "https://example.com/path")

    def test_website_key_ignores_www_and_path(self):
        self.assertEqual(website_key("https://www.example.com/contact"), "example.com")

    def test_read_websites_deduplicates_domain_variants(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "businesses.txt"
            path.write_text(
                "https://example.com/\nhttps://www.example.com/contact\nhttps://other.example/\n",
                encoding="utf-8",
            )

            self.assertEqual(
                read_websites(path),
                ["https://example.com/", "https://other.example/"],
            )

    def test_rejects_non_http_schemes(self):
        with self.assertRaises(ValueError):
            normalize_website("file:///etc/passwd")

    def test_rejects_invalid_ports(self):
        with self.assertRaises(ValueError):
            normalize_website("example.com:invalid")

    def test_private_hosts_are_not_public(self):
        self.assertFalse(host_is_public("127.0.0.1"))

    def test_accepts_public_generic_and_named_business_emails(self):
        self.assertEqual(public_email("mailto:info@example.com?subject=Hello"), "info@example.com")
        self.assertEqual(public_email(" Info+sales@example.com "), "info+sales@example.com")
        self.assertEqual(public_email("jane.doe@example.com"), "jane.doe@example.com")

    def test_rejects_obvious_automated_email_addresses(self):
        self.assertEqual(public_email("no-reply@example.com"), "")
        self.assertEqual(public_email("donotreply@example.com"), "")
        self.assertEqual(public_email("not-an-email"), "")

    def test_normalizes_business_phone_numbers(self):
        self.assertEqual(normalize_phone("+1 (555) 234-5678"), "+15552345678")
        self.assertEqual(normalize_phone("123"), "")

    def test_extracts_company_and_public_contact_details(self):
        html = """
            <html><head><title>Acme Plumbing</title></head><body>
            <a href="mailto:info@acme.example">Email us</a>
            <a href="tel:+1-555-234-5678">Call us</a>
            <p>Owner: jane@acme.example</p>
            </body></html>
        """
        self.assertEqual(
            parse_contact_page(html),
            ("Acme Plumbing", "info@acme.example", "+15552345678"),
        )

    def test_discovers_contact_links_from_homepage_and_ignores_external_links(self):
        html = """
            <a href="/contact-us">Contact</a>
            <a href="/our-team">Meet the team</a>
            <a href="https://other.example/contact">Contact elsewhere</a>
            <a href="/products">Products</a>
        """
        pages = _candidate_pages("https://example.com", html)
        self.assertEqual(pages[0], "https://example.com/")
        self.assertIn("https://example.com/contact-us", pages)
        self.assertIn("https://example.com/our-team", pages)
        self.assertNotIn("https://other.example/contact", pages)
        self.assertNotIn("https://example.com/products", pages)

    def test_discovered_contact_links_are_deduplicated(self):
        html = """
            <a href="/contact">Contact</a>
            <a href="/contact">Contact us</a>
        """
        pages = _candidate_pages("https://example.com", html)
        self.assertEqual(pages.count("https://example.com/contact"), 1)

    def test_appends_only_new_contacts_and_keeps_existing_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            original = BusinessContact(
                website="https://existing.example/",
                company_name="Existing Co",
                email="info@existing.example",
                phone="",
                source_url="https://existing.example/contact",
                collected_at="2026-01-01T00:00:00+00:00",
            )
            new = BusinessContact(
                website="https://new.example/",
                company_name="New Co",
                email="",
                phone="",
                source_url="",
                collected_at="2026-01-02T00:00:00+00:00",
            )
            self.assertEqual(write_csv(path, [original]), 1)
            self.assertEqual(write_csv(path, [original, new]), 1)
            with path.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))

            self.assertEqual([row["website"] for row in rows], [
                "https://existing.example/",
                "https://new.example/",
            ])

    def test_csv_skips_same_company_even_when_domain_differs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            first = BusinessContact(
                website="https://first.example/",
                company_name="Acme Plumbing LLC",
                email="info@acme.example",
                phone="",
                source_url="",
                collected_at="2026-01-01T00:00:00+00:00",
            )
            duplicate = BusinessContact(
                website="https://www.second.example/contact",
                company_name="ACME Plumbing, Inc.",
                email="contact@acme.example",
                phone="",
                source_url="",
                collected_at="2026-01-02T00:00:00+00:00",
            )

            self.assertEqual(write_csv(path, [first]), 1)
            self.assertEqual(write_csv(path, [duplicate]), 0)

    def test_csv_skips_www_path_duplicate_domain(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            first = BusinessContact(
                website="https://example.test/",
                company_name="Example",
                email="",
                phone="",
                source_url="",
                collected_at="2026-01-01T00:00:00+00:00",
            )
            duplicate_domain = BusinessContact(
                website="https://www.example.test/contact",
                company_name="Different title",
                email="",
                phone="",
                source_url="",
                collected_at="2026-01-02T00:00:00+00:00",
            )
            self.assertEqual(write_csv(path, [first]), 1)
            self.assertEqual(write_csv(path, [duplicate_domain]), 0)


if __name__ == "__main__":
    unittest.main()
