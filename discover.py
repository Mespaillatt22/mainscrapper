#!/usr/bin/env python3
"""Discover business websites from OpenStreetMap for a named place."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from scraper import normalize_website, website_key

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "MainScrapper/1.0 (OpenStreetMap business website discovery)"
MAX_RESPONSE_BYTES = 5_000_000
MAX_REQUEST_ATTEMPTS = 4
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
CATEGORY_TAGS = {
    "plumbing": (("craft", "plumber"),),
    "contractor": (("craft", "builder"), ("craft", "contractor")),
    "restaurant": (("amenity", "restaurant"),),
    "home-healthcare": (("healthcare", "home_care"),),
}
NON_BUSINESS_HOSTS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}


def fetch_json(url: str, *, data: bytes | None = None, service: str) -> object:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        request = Request(url, data=data, headers=headers)
        try:
            with urlopen(request, timeout=60) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUSES:
                raise RuntimeError(f"{service} request failed with HTTP {error.code}") from error
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise RuntimeError(
                    f"{service} is still unavailable (HTTP {error.code}) after "
                    f"{MAX_REQUEST_ATTEMPTS} attempts; try again later"
                ) from error
            try:
                retry_after = int(error.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = 0
            delay = min(max(retry_after, 2 ** (attempt + 1)), 60)
            print(
                f"{service} returned HTTP {error.code}; retrying in {delay}s "
                f"({attempt + 2}/{MAX_REQUEST_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)
        except (URLError, TimeoutError) as error:
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise RuntimeError(
                    f"{service} could not be reached after {MAX_REQUEST_ATTEMPTS} attempts; "
                    "check your connection and try again later"
                ) from error
            delay = 2 ** (attempt + 1)
            print(
                f"{service} request failed; retrying in {delay}s "
                f"({attempt + 2}/{MAX_REQUEST_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"API response exceeds {MAX_RESPONSE_BYTES} bytes")
    return json.loads(body)


def geocode_place(place: str) -> tuple[float, float, float, float]:
    url = f"{NOMINATIM_URL}?{urlencode({'q': place, 'format': 'jsonv2', 'limit': 1})}"
    result = fetch_json(url, service="OpenStreetMap place lookup (Nominatim)")
    if not isinstance(result, list) or not result:
        raise ValueError(f"no matching place found for {place!r}")
    try:
        south, north, west, east = map(float, result[0]["boundingbox"])
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError("place lookup returned an invalid bounding box") from error
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("place lookup returned out-of-range coordinates")
    if north - south > 5 or east - west > 5:
        raise ValueError("place matched an area larger than 5 degrees; try a smaller, more specific place")
    return south, west, north, east


def make_overpass_query(
    bbox: tuple[float, float, float, float],
    tags: tuple[tuple[str, str], ...],
    limit: int,
) -> str:
    south, west, north, east = bbox
    bounds = f"{south},{west},{north},{east}"
    selections = "\n".join(
        f'  nwr["{key}"="{value}"]({bounds});' for key, value in tags
    )
    return f"[out:json][timeout:60];\n(\n{selections}\n);\nout center {limit};"


def is_non_business_host(hostname: str) -> bool:
    hostname = hostname.lower().removeprefix("www.")
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in NON_BUSINESS_HOSTS)


def extract_websites(result: object, limit: int) -> list[str]:
    if not isinstance(result, dict) or not isinstance(result.get("elements"), list):
        raise ValueError("Overpass returned an invalid response")
    websites: dict[str, str] = {}
    for element in result["elements"]:
        if not isinstance(element, dict) or not isinstance(element.get("tags"), dict):
            continue
        tags = element["tags"]
        for raw_url in (tags.get("website"), tags.get("contact:website")):
            if not isinstance(raw_url, str):
                continue
            try:
                website = normalize_website(raw_url)
            except ValueError:
                continue
            if is_non_business_host(urlsplit(website).hostname or ""):
                continue
            websites.setdefault(website_key(website), website)
    return list(websites.values())[:limit]


def discover_websites(place: str, category: str, limit: int) -> list[str]:
    bbox = geocode_place(place)
    time.sleep(1)
    query = make_overpass_query(bbox, CATEGORY_TAGS[category], limit)
    result = fetch_json(
        OVERPASS_URL,
        data=urlencode({"data": query}).encode("utf-8"),
        service="OpenStreetMap business search (Overpass)",
    )
    return extract_websites(result, limit)


def append_websites(path: Path, websites: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        existing = {website_key(website) for website in read_existing_websites(path)}
    new_websites = []
    for website in websites:
        key = website_key(website)
        if key not in existing:
            new_websites.append(website)
            existing.add(key)
    if not new_websites:
        return 0
    with path.open("a", encoding="utf-8", newline="") as output:
        if path.stat().st_size and not path.read_text(encoding="utf-8").endswith(("\n", "\r")):
            output.write("\n")
        for website in new_websites:
            output.write(f"{website}\n")
    return len(new_websites)


def read_existing_websites(path: Path) -> list[str]:
    existing = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            existing.append(normalize_website(value))
        except ValueError:
            continue
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", required=True, help='place name, for example "Austin, Texas"')
    parser.add_argument("--category", required=True, choices=sorted(CATEGORY_TAGS))
    parser.add_argument("--limit", type=int, default=100, help="maximum websites to save (default: 100)")
    parser.add_argument("-o", "--output", type=Path, default=Path("businesses.txt"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")

    try:
        websites = discover_websites(args.place, args.category, args.limit)
        added = append_websites(args.output, websites)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Discovery failed: {error}", file=sys.stderr)
        return 1

    print(f"Found {len(websites)} website(s); added {added} new URL(s) to {args.output}")
    print("Data source: © OpenStreetMap contributors (ODbL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
