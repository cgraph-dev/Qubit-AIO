# Hetzner Price API — Reverse-Engineering Guide

Use this when you need to verify **live, current Hetzner dedicated server pricing** for hosting large models. Hetzner's web pages are JS-rendered (Vue.js) and don't embed prices in static HTML — you need their internal price API.

## API base

```
https://website-price-api.hetzner.com
```

## Available endpoints

| Endpoint | Returns | Purpose |
|---|---|---|
| `GET /api/v1/categories` | List of category IDs with lowest prices | Find price ranges matching the user's budget |
| `GET /api/v1/categories/{id}` | Products in a category | See which products live in a price bracket |
| `GET /api/v1/products/{productId}` | Per-location pricing (monthly, hourly, setup) | Get exact prices for a specific server config |
| `GET /api/v1/server-auction` | Auction prices | Find discounted server deals |
| `GET /api/v1/tax-rates` | Tax rates by country | Add VAT where applicable |
| `GET /` | OpenAPI spec | Self-documenting — lists all available paths |

## Mapping product IDs to server models

Product IDs are opaque strings like `ROBOT_1723`. To map them to server models:

1. **Fetch the matrix page** for the server line you care about:

   ```
   https://www.hetzner.com/dedicated-rootserver/matrix-ax/
   https://www.hetzner.com/dedicated-rootserver/matrix-rx/
   https://www.hetzner.com/dedicated-rootserver/matrix-ex/
   https://www.hetzner.com/dedicated-rootserver/matrix-sx/
   ```

2. **Extract the product-to-name mapping** from product-key attributes in the HTML:

   ```python
   import requests, re

   text = requests.get('https://www.hetzner.com/dedicated-rootserver/matrix-ax/').text

   # Split by product-name divs to isolate each server model
   sections = re.split(r'<div class="d-flex flex-column product-text-name">', text)
   for section in sections[1:]:
       name = re.search(r'^\s*(\w+)', section)
       if not name:
           continue
       keys = set(re.findall(r'product-key="([^"]*)"', section))
       robot_ids = set()
       for k in keys:
           for part in k.split('+'):
               if part.startswith('ROBOT_'):
                   robot_ids.add(part)
       print(f'{name.group(1)}: {sorted(robot_ids)}')
   ```

3. **Query each ROBOT_ID** against the Price API:

   ```python
   r = requests.get(f'https://website-price-api.hetzner.com/api/v1/products/{robot_id}')
   data = r.json()
   for loc in data.get('locations', []):
       dc = loc.get('datacenter', '?')
       prices = loc.get('prices', {})
       monthly = prices.get('monthly', {}).get('EUR', '-')
       hourly = prices.get('hourly', {}).get('EUR', '-')
       setup = prices.get('setup', {}).get('EUR', '-')
       print(f'  {dc}: €{monthly}/mo (€{hourly}/hr, setup €{setup})')
   ```

## Known product ID map (verified live)

### AX line

| Server | Product ID | Monthly | Hourly | Setup | Likely config |
|---|---|---|---|---|---|
| AX41 | ROBOT_1718 | €57.30 | €0.09 | €0 | Base |
| AX42 | ROBOT_1720 | €77.30 | €0.12 | €39 | Base |
| AX42 | ROBOT_1719 | €97.30 | €0.16 | €49 | Max |
| AX102 | ROBOT_1723 | €157.30 | €0.25 | €39 | Base (128 GB) |
| AX102 | ROBOT_1721 | €257.30 | €0.41 | €129 | Max (512 GB) |
| AX162 | ROBOT_1724 | €612.30 | €0.98 | €304 | Max config |
| AX162 | ROBOT_1727 | €317.30 | €0.51 | €39 | Base config |

**ROBOT_1266** (€1.70/mo) is an add-on that appears in almost every product-key — ignore it; it's a small mandatory extra.

## Workflow for pricing research

1. Check the OpenAPI spec (`GET /`) to confirm endpoints are up
2. Fetch the matrix page for the user's target server line
3. Extract ROBOT_ IDs per server model
4. Query each product ID against the Price API
5. Present a table with exact verified prices

## Pitfalls

- **The configurator pages (`/dedicated-rootserver/ax102/configurator/`) are fully JS-rendered** — they're useless for scraping. Always use the matrix listing page instead.
- **Category IDs** returned by `/api/v1/categories` are opaque numeric strings (e.g. `"10003"`, `"69"`). The category name hints (e.g. `"AX Server"`) appear as the value of a `name` field in some responses — use them when present.
- **All prices are excl. VAT** unless you add tax via `/api/v1/tax-rates`.
- **Setup fees** are one-time; some servers have €0 setup, others €39-€349.
- **Datacenter locations** (FSN1=Falkenstein, NBG1=Nuremberg, HEL1=Helsinki) affect price slightly — DE locations are often a few euros more than FI.
- **Prices change** — always verify live; don't rely on cached values from this file.
