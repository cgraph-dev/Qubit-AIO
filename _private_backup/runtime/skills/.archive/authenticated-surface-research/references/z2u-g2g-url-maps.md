# Authenticated URL maps — Z2U & G2G (captured 2026-08-08)

Clean-room route inventories for implementation reference (RankValut). These are
behavioral observations of route structure + UI states, not copies of the sites.

## Z2U (z2u.com)

### User-center accordion (exact routes)
| Section | Item | Route |
|---|---|---|
| Buy | Purchase Orders | `/buyOrder/index` |
| Buy | Purchase Order Comment | `/userComment/send` |
| Buy | Credits Balance | `/UserCenter/creditsLog` |
| Buy | Funding Details | `/UserCenter/fundList` |
| Buy | My Favorites | `/UserCenter/myFavorite` |
| Dispute Center | Disputes | `/DisputeCenter/index` |
| Dispute Center | Dispute Statistics | `/DisputeCenter/disputeStats` |
| Service Ticket | All / Pending / Awaiting Reply / Closed | `/faq/serviceList` , `/faq/serviceList/1`, `/2`, `/3` |
| Announcement | All / Unread / Read / Starred | `/userCenter/userAnnouncement` + `?is_read=0|1`, `?is_mark=1` |
| Coupon | Shopping / Platform (red packet) | `/userCoupon/index`, `/userCoupon/redPacket` |
| Affiliate | Manage / My Affiliates | `/Distribution/index` (+`?myaffiliates=1`) |
| Affiliate | Video records / Alliance Bill / Creative Assets | `/distribution/videoRecord`, `/distribution/allianceBill`, `/distribution/promoteMaterial` |
| Settings | Account / Security / Certification / Delete | `/userCenter/index`, `/userCenter/account`, `/UserVerify/index`, `/userCenter/deleteAccount` |

Dashboard entry: `/My/index.html` (Overview Account, also surfaces Invoice item).
Chat: `/Chat` (Welcome page, search, read-filter radios, "User Management").

### Buyer order state machine (URL pattern `/buyOrder/index/order_status/{STATUS}`)
`ALL` → `WAIT_PAY` (Unpaid) → `WAIT_DELIVERY` (Waiting for delivery) →
`DELIVERYED` (Delivered) → `WAIT_COMMENT` (Not Commented) → `COMPLETED` →
`REFUNDED`. Search filters: order_number, start_time, end_time.

### Seller registration wizard (6 steps, `/sell/index.html`)
1 Terms of service → 2 Seller Details → 3 Phone Verification → 4 Identity
Verification → 5 Service Application → 6 Platform Audit.
- Step 1: type selector Personal|Company + rules table (violation→punishment;
  permanent suspension + 180-day payout clearance for serious violations).
- Step 2 form fields: profile/shop name (locked once set, renaming costs money;
  English letters/numbers, only `-` and `_` allowed), country code + mobile,
  IM select (QQ, Microsoft Teams, Whatsapp, Telegram), terms consent.

### Dispute table columns
`Case Id | Order number | Reason | Status | Amount | Last updated on | Action`.
Dispute stats: `# | Game | category | Number of disputes | Game Proportion |
Dispute rate | Refund Resolution Rate`. Status pills: Response Required / Under
Review / Awaiting other party's response.

### Messages center tabs
All / To-do / Announcement / Order Reminder / Dispute Reminder / Platform
Service Reminder / Merchant Reminder. Bulk: Delete, Mark as read, Mark all as
read, Tagged as (star). Search by title.

### Security & verification
Security rows: Email (Verified), Password (Edit), Mobile (Verify), Privacy
Password (Set Up), MFA (Set Up), Promotional Email (toggle).
Certification ladder: email → mobile → identity → payment auth.

## G2G (g2g.com) — SPA, direct deep-URL loads can 404

### Sidebar route inventory
| Item | Route |
|---|---|
| Overview | `/account/overview` |
| Purchase Orders | `/g2g-user/purchase` (all types) and `/order/buyOrder/index` (digital products only, `?filt_opt=all|current|completed`) |
| Create Offer | `/offers/sell` |
| Manage Offer | `/offers/list` |
| Sold Orders | `/g2g-user/sale` |
| Payment | `/payment/index` |
| API Integration | `/offers/api` |
| Affiliate | `/affiliate` |
| Account | `/g2g-user/account` |
| Social Connect | `/g2g-user/account/connects` |
| Security | `/g2g-user/account/security` |
| Verification | `/g2g-user/account/verification` |
| Store Credit | `/store-credit/statement` |
| G2G Points | `/g2gpoints` |
| Sell-order cancellations | `/order/sellOrder?status=7` |
| Chat | `/chat/#/` |

### Buyer order status tabs
To Pay | Verify payment | Preparing | Delivering | Completed | Cancelled |
Resolution.

### Seller tools
- Create Offer: type selector Digital Products | Rent Time Services → category
  tree: Gaming (Top Up & Gift Cards, Skins, Accounts, Items, Boosting, Coins),
  Software & Apps (AI & Tools, eBooks, Video & Music, Live Streaming & Social,
  Others), Retail (eCommerce, Dining & Food, Fashion, Travel & Transport,
  Education, Others), Payment & Stable Coins (eWallet & Prepaid), Telco (Mobile
  Credit, eSIM). Rules: no fake codes, ticket to request new brand, OpenAPI link.
- Manage Offer table: `Title | Stock | Currency | Unit price | Sale | Action`.
- Payment funds table: `Currency | Floating Funds pending for clearance |
  Revenue Reserve Funds set aside | Available` (3-bucket payout model).
- Sold Orders: Download Report (CSV export).

### Verification ladder
mobile → name → billing address → DOB → eKYC (Gov ID/driving license/passport
+ selfie) → face scan (light background, no glasses/hat).

### Account settings form
First/Last name, National identity number, Gender (M/F/Rather not say), DOB,
Instant messenger, Billing address (Address/City/State-Zip/Country), TIN.

### Affiliate
20% recurring commission, reflink flow (login → create reflink → share →
harvest), leaderboard, payout FAQ.

## Session-specific gotchas that cost real time
- z2u session cookies do NOT survive browser restart → always `cookies` backup
  right after user logs in.
- g2g `/user/wallet` 404s on direct goto; boot at `/user` or `/user/wallet`
  then click sidebar links client-side (deep_capture.py pattern).
- Cloudflare challenge on z2u appeared after ~20 rapid navigations; a human
  checkbox click in the raised browser window cleared it. Pace gotos.
- Framework buttons (Quasar q-btn, Svelte h-button) ignore JS `.click()` —
  use the launcher's real `click` action with `text=` / CSS selectors.
