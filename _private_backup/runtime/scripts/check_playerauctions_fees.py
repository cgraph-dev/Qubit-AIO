#!/usr/bin/env python3
"""PlayerAuctions seller-fee watchdog.

Fetches the official "PlayerAuctions - Seller Fees" help-center article and
compares the commission table against the known baseline. Prints:
  INFO  - fee structure matches baseline (no change)
  ALERT - fee structure differs from baseline (change detected)
  manual-check reminder - page unreachable (Cloudflare/blocked)

Baseline history:
  pre-2026-08: gift cards 4.5%, items 7.5%, accounts 10%, buyer 0%,
               floor 3.4%, 14-level volume tiers
  2026-08-19+: flat per-category + $0.99/order:
               Currency 9.99% | Item 9.99% | Boosting 9.99% |
               Accounts 12.99% (22 named games +5% -> 17.99%) |
               Top Up 4.99% | buyer 4.99% + $0.29 (card) /
               2.99% + $0.29 (crypto) | OSRS/RS3 gold promo 4.99%
               (individual promos expire and are NOT part of the baseline)
"""
import re
import sys
import urllib.request

ARTICLE_URL = "https://support.playerauctions.com/hc/en-us/articles/60837462717977-PlayerAuctions-Seller-Fees"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Core commission rows that define the permanent structure.
# (percent, label) — promo rows like OSRS/RS3 4.99% are excluded on purpose.
BASELINE = [
    (9.99, "Currency"),
    (9.99, "Item"),
    (9.99, "Boosting"),
    (12.99, "Accounts"),
    (17.99, "Accounts game-specific (+5%)"),
    (4.99, "Top Up"),
]


def fetch_article():
    req = urllib.request.Request(ARTICLE_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "replace")


def extract_rates(html):
    """Return the set of 'N.NN%' percentages mentioned with commission context."""
    # Table cells in the help-center article look like "9.99% + $0.99"
    return set(float(p) for p in re.findall(r"(\d{1,2}\.\d{2})%\s*\+\s*\$0\.99", html))


def main():
    try:
        html = fetch_article()
    except Exception as exc:  # noqa: BLE001
        print(f"MANUAL CHECK NEEDED: PlayerAuctions page unreachable ({exc}). "
              "The page is Cloudflare-blocked; open "
              "https://support.playerauctions.com/hc/en-us/articles/60837462717977 "
              "in a browser and verify fees manually.")
        return 2

    if "Seller Fees" not in html:
        print("MANUAL CHECK NEEDED: article content not recognized; verify manually.")
        return 2

    rates = extract_rates(html)
    expected = {p for p, _ in BASELINE}
    if not expected.issubset(rates):
        missing = sorted(expected - rates)
        unknown = sorted(r for r in rates if r not in expected and r not in (4.99,))
        print("ALERT: PlayerAuctions fee change detected.")
        if missing:
            print(f"  Missing baseline rates: {missing}")
        if unknown:
            print(f"  New rates on page: {unknown}")
        print("  Full fee table: " + ", ".join(
            f"{p}% + $0.99" for p in sorted(rates)))
        print("  Docs to update: Phase 0 Decision Pack §5.2, "
              "Competitor Fee and Protection Benchmark, "
              "Valut System and Fee Specification §1.1")
        return 1

    print("INFO: PlayerAuctions seller-fee structure unchanged "
          "(flat rates + $0.99/order; Accounts 12.99%, 22 games 17.99%).")
    return 0


if __name__ == "__main__":
    sys.exit(main())