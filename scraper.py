#!/usr/bin/env python3
"""Collect publicly listed, company-level contact details from supplied websites."""

from __future__ import annotations

import argparse
import csv
import ipaddress
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "BusinessContactCollector/1.0"
MAX_PAGE_BYTES = 1_000_000
MIN_REQUEST_INTERVAL = 2.0
CONTACT_PATHS = ("/", "/contact", "/contact-us", "/about", "/about-us")
GENERIC_EMAIL_NAMES = {
    "admin",
    "bookings",
    "contact",
    "customerservice",
    "enquiries",
    "general",
    "hello",
    "info",
    "inquiry",
    "office",
    "sales",
    "service",
    "support",
}
EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.-])")
PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d().\s-]{6,}\d)(?!\w)")


class ContactPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.site_name = ""
        self.emails: list[str] = []
        self.phones: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta" and attributes.get("property", "").lower() == "og:site_name":
            self.site_name = attributes.get("content", "").strip()
        if tag.lower() == "a":
            href = attributes.get("href", "").strip()
            if href.lower().startswith("mailto:"):
                self.emails.append(unquote(href[7:].split("?", 1)[0]))
            elif href.lower().startswith("tel:"):
                self.phones.append(unquote(href[4:].split("?", 1)[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


class SameHostRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source_host = urlsplit(req.full_url).hostname
        target_host = urlsplit(newurl).hostname
        if not source_host or target_host != source_host:
            raise HTTPError(newurl, code, "Cross-host redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class BusinessContact:
    website: str
    company_name: str = ""
    email: str = ""
    phone: str = ""
    source_url: str = ""
    collected_at: str = ""


def normalize_website(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("website URL cannot be empty")
    if "://" not in value:
        value = f"https://{value}"
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port
    except ValueError as error:
        raise ValueError(f"not a valid public website URL: {value}") from error
    if parts.scheme not in {"http", "https"} or not hostname or parts.username or parts.password:
        raise ValueError(f"not a valid public website URL: {value}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def host_is_public(host: str) -> bool:
    try:
        addresses = {ipaddress.ip_address(host.split("%", 1)[0])}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def generic_email(value: str) -> str:
    email = value.strip().strip(".,;:()[]<>\"'").lower()
    if "@" not in email:
        return ""
    local, _ = email.rsplit("@", 1)
    return email if local.split("+", 1)[0].replace(".", "").replace("_", "").replace("-", "") in GENERIC_EMAIL_NAMES else ""


def normalize_phone(value: str) -> str:
    value = value.strip()
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15:
        return ""
    return ("+" if value.startswith("+") else "") + digits


def parse_contact_page(html: str) -> tuple[str, str, str]:
    parser = ContactPageParser()
    parser.feed(html)
    visible_text = " ".join(parser.text_parts)
    email_candidates = parser.emails + EMAIL_RE.findall(visible_text)
    phone_candidates = parser.phones + PHONE_RE.findall(visible_text)
    title = parser.site_name or " ".join(parser.title_parts).strip()
    email = next((found for value in email_candidates if (found := generic_email(value))), "")
    phone = next((found for value in phone_candidates if (found := normalize_phone(value))), "")
    return title, email, phone


def fetch_html(opener, url: str) -> str | None:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with opener.open(request, timeout=15) as response:
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return None
        data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError(f"page exceeds {MAX_PAGE_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def load_robots(opener, origin: str) -> RobotFileParser | None:
    robots_url = urljoin(origin, "/robots.txt")
    parser = RobotFileParser()
    parser.set_url(robots_url)
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=15) as response:
            parser.parse(response.read(256_000).decode("utf-8", errors="replace").splitlines())
    except HTTPError as error:
        if error.code == 404:
            parser.parse([])
        else:
            print(f"Cannot read robots.txt ({error.code}); skipping {origin}", file=sys.stderr)
            return None
    except (URLError, TimeoutError, OSError) as error:
        print(f"Cannot read robots.txt ({error}); skipping {origin}", file=sys.stderr)
        return None
    return parser


def scrape_website(website: str) -> BusinessContact:
    contact = BusinessContact(
        website=website,
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    parts = urlsplit(website)
    host = parts.hostname or ""
    if not host_is_public(host):
        print(f"Skipping non-public or unresolvable host: {host}", file=sys.stderr)
        return contact

    origin = f"{parts.scheme}://{parts.netloc}"
    opener = build_opener(SameHostRedirectHandler())
    robots = load_robots(opener, origin)
    if robots is None:
        return contact

    last_request = 0.0
    for path in CONTACT_PATHS:
        page_url = urljoin(origin, path)
        if not robots.can_fetch(USER_AGENT, page_url):
            continue
        delay = max(MIN_REQUEST_INTERVAL, robots.crawl_delay(USER_AGENT) or 0)
        wait = delay - (time.monotonic() - last_request)
        if last_request and wait > 0:
            time.sleep(wait)
        try:
            html = fetch_html(opener, page_url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            print(f"Could not fetch {page_url}: {error}", file=sys.stderr)
            last_request = time.monotonic()
            continue
        last_request = time.monotonic()
        if html is None:
            continue
        title, email, phone = parse_contact_page(html)
        if not contact.company_name and title:
            contact.company_name = title
        if not contact.email and email:
            contact.email = email
            contact.source_url = page_url
        if not contact.phone and phone:
            contact.phone = phone
            if not contact.source_url:
                contact.source_url = page_url
    return contact


def read_websites(path: Path) -> list[str]:
    websites = []
    seen = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            website = normalize_website(value)
        except ValueError as error:
            print(f"{path}:{line_number}: {error}", file=sys.stderr)
            continue
        key = website_key(website)
        if key not in seen:
            websites.append(website)
            seen.add(key)
    return websites


def write_csv(path: Path, contacts: list[BusinessContact]) -> int:
    """Append contacts not already present by website domain or company name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(BusinessContact.__dataclass_fields__)
    existing_websites: set[str] = set()
    existing_companies: set[str] = set()
    file_exists = path.exists() and path.stat().st_size > 0
    if file_exists:
        with path.open("r", newline="", encoding="utf-8") as existing_file:
            reader = csv.DictReader(existing_file)
            if reader.fieldnames != fieldnames:
                raise ValueError(f"{path} has an unexpected CSV header; refusing to append")
            for row in reader:
                website = row.get("website", "")
                if website:
                    existing_websites.add(website_key(website))
                company = company_key(row.get("company_name", ""))
                if company:
                    existing_companies.add(company)

    new_contacts = []
    for contact in contacts:
        domain = website_key(contact.website)
        company = company_key(contact.company_name)
        if domain not in existing_websites and (not company or company not in existing_companies):
            new_contacts.append(contact)
            existing_websites.add(domain)
            if company:
                existing_companies.add(company)

    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(contact.__dict__ for contact in new_contacts)
    return len(new_contacts)


def website_key(website: str) -> str:
    parts = urlsplit(normalize_website(website))
    hostname = parts.hostname.lower().removeprefix("www.").rstrip(".") if parts.hostname else ""
    return hostname.encode("idna").decode("ascii")


def company_key(name: str) -> str:
    words = re.sub(r"[^\w]+", " ", name.casefold()).split()
    legal_suffixes = {"co", "company", "corp", "corporation", "inc", "incorporated", "limited", "llc", "llp", "lp", "ltd", "pllc"}
    while words and words[-1] in legal_suffixes:
        words.pop()
    return " ".join(words)


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("websites", type=Path, help="text file with one business website URL per line")
    argument_parser.add_argument("-o", "--output", type=Path, default=Path("business_contacts.csv"))
    args = argument_parser.parse_args()

    try:
        websites = read_websites(args.websites)
    except OSError as error:
        argument_parser.error(str(error))
    if not websites:
        argument_parser.error("no valid website URLs found")

    try:
        existing_keys: set[str] = set()
        if args.output.exists() and args.output.stat().st_size > 0:
            with args.output.open("r", newline="", encoding="utf-8") as existing_file:
                reader = csv.DictReader(existing_file)
                expected_fields = list(BusinessContact.__dataclass_fields__)
                if reader.fieldnames != expected_fields:
                    argument_parser.error(f"{args.output} has an unexpected CSV header; refusing to append")
                existing_keys = {
                    website_key(row["website"])
                    for row in reader
                    if row.get("website")
                }
    except (OSError, ValueError) as error:
        argument_parser.error(str(error))

    websites_to_scrape = [website for website in websites if website_key(website) not in existing_keys]
    contacts = [scrape_website(website) for website in websites_to_scrape]
    try:
        added = write_csv(args.output, contacts)
    except (OSError, ValueError) as error:
        argument_parser.error(str(error))
    print(f"Added {added} new business records to {args.output}; existing records were kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
