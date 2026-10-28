# Business contact scraper

A small, repeatable Python tool for discovering and scraping business websites. It can discover website URLs listed in OpenStreetMap for a named place and category, then checks each site's `robots.txt`, makes requests slowly, and collects only information presented as company contact details:

- Company/site name
- Generic business email addresses (such as `info@` or `sales@`)
- Public business phone numbers
- The page URL where a contact detail was found

It does not guess email addresses or collect named people's email addresses. Only scrape sites you are permitted to access, follow their terms, and use collected contact details responsibly.

## Run

Requires Python 3.10 or newer. There are no third-party dependencies.

1. Discover websites from OpenStreetMap by place and category:

   ```bash
   python discover.py --place "Austin, Texas" --category plumbing
   ```

   Categories are `plumbing`, `contractor`, `restaurant`, and `home-healthcare`. The command saves up to 100 website URLs to `businesses.txt` by default. Change the maximum with `--limit` (up to 500) or the output path with `-o`.

2. Scrape the discovered websites:

   ```bash
   python scraper.py businesses.txt
   ```

The results are written to `business_contacts.csv`. Choose a different output path with `-o`, for example `python scraper.py businesses.txt -o results/contacts.csv`. Each run appends new websites to the existing list and new contact rows to the existing CSV. Duplicate detection treats `www` and non-`www` URLs, paths, and query strings on the same domain as the same site. The contacts CSV also skips a matching normalized company name, including common legal suffix variations such as `LLC` and `Inc.`. Existing rows are kept; the CSV header is written only when the file is new or empty. Schedule the commands with your operating system's task scheduler for periodic collection.

Discovery also appends new website URLs to `businesses.txt` instead of replacing its contents. URLs on a domain already in the file are not added a second time, even if they have a different path or use `www`. The success message reports how many URLs were newly added.

Discovery uses the public Nominatim and Overpass APIs; it requires internet access and may return no results when businesses have not added website URLs to OpenStreetMap. Use small, local searches, and review the current usage policies of both services before repeated use. The tool makes one place lookup and one Overpass query per run, waits between those requests, and attributes the data to OpenStreetMap contributors (ODbL). Temporary network errors and HTTP 429/5xx responses are retried up to four times with increasing delays. If a service remains unavailable, the error names the service and suggests trying later. It outputs URLs only, not a bulk directory of business records.

The scraper checks the home page and common contact/about page paths, follows same-host redirects only, skips private/local network hosts, and waits at least two seconds between page requests to the same site. A site with an unavailable `robots.txt` is skipped (a `404` is treated as no published robots rules). You can still provide your own list of website URLs instead of using discovery.

## Tests

```bash
python -m unittest discover -s tests
```