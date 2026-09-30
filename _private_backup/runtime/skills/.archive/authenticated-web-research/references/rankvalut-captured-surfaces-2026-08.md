# RankValut authenticated-surface captures (2026-08-08)

Session context: user is building RankValut (gaming marketplace, Next.js 16
App Router + NestJS/Prisma, repo ~/Projects/Repos/RankValut, docs/ has parity
blueprints). Task: capture LOGGED-IN customer + seller pages of z2u, g2g,
zeusx, eldorado.gg, g2a. Only z2u + g2g login succeeded — zeusx/eldorado/g2a
blocked by captcha (pending, user to decide approach).

Raw evidence (PERSONAL DATA — never commit): ~/.hermes/rankvalut-research/
  dumps/*.json  — DOM structure dumps
  shots/*.png   — screenshots
  cookies.json  — session cookies (z2u + g2g + partial others)

## z2u (logged in as customer; seller side gated behind registration)
Hidden sidebar lives in collapsed sections `#dashDropbody1/3/4/5/7/8/11`:
- Buy: Purchase Orders `/buyOrder/index` (status tabs: ALL → WAIT_PAY →
  WAIT_DELIVERY → DELIVERYED → WAIT_COMMENT → COMPLETED → REFUNDED), Order
  Comment `/userComment/send`, Credits Balance `/UserCenter/creditsLog`,
  Funding Details `/UserCenter/fundList`, My Favorites `/UserCenter/myFavorite`
- Dispute Center: Disputes `/DisputeCenter/index` (table: Case Id, Order
  number, Reason, Status, Amount, Last updated, Action; filters Response
  Required / Under Review / Awaiting other party's response), Dispute Stats
  `/DisputeCenter/disputeStats` (by game: # disputes, proportion, dispute
  rate, refund-resolution rate)
- Service Ticket: `/faq/serviceList` (+ `/1` Pending, `/2` Awaiting Reply,
  `/3` Closed)
- Announcement: `/userCenter/userAnnouncement` (?is_read=0|1, ?is_mark=1)
- Coupon: `/userCoupon/index`, `/userCoupon/redPacket`
- Affiliate: `/Distribution/index` (+?myaffiliates=1), `/distribution/videoRecord`,
  `/distribution/allianceBill`, `/distribution/promoteMaterial`
- Settings: `/userCenter/index` (Account Details: avatar, binding, personal,
  billing address, preference), `/userCenter/account` (Security: email
  verified, password, mobile, privacy password, MFA, promo email),
  `/UserVerify/index` (Certification: email/mobile/identity/payment auth),
  `/userCenter/deleteAccount`
- Overview: `/My/index.html` (purchase orders table)
- Chat: `/Chat` ("Welcome to Z2U chat", user management, search, read-filter)

Seller registration = 6 steps (Terms → Seller Details → Phone Verification →
Identity Verification → Service Application → Platform Audit). Step 2 form:
store/profile name rules (English letters/numbers, only `-` and `_`; changing
it later costs money), country code + mobile, IM select (QQ/Teams/WhatsApp/
Telegram). Violations/punishments table incl. 180-day payout-clearance
language. `/sellOrder/index` exists but redirects to the registration gate
when account is buyer-only.

## g2g (logged in as itrick, Level 17, ID 1003267207)
SPA: boot at `/user/wallet`, click sidebar links (direct goto 404s).
Sidebar:
- Overview `/account/overview` (purchase + sold orders tables)
- Purchase Orders `/g2g-user/purchase` + `/order/buyOrder/index` (filters All
  Types/Current/Completed; other-categories link)
- Create Offer `/offers/sell` (Digital Products vs Rent Time Services →
  category tree: Gaming: Top Up & Gift Cards/Skins/Accounts/Items/Boosting/
  Coins; Software & Apps: AI & Tools/eBooks/Video & Music/Live Streaming &
  Social/Others; Retail: eCommerce/Dining & Food/Fashion/Travel & Transport/
  Education/Others; Payment & Stable Coins: eWallet & Prepaid; Telco: Mobile
  Credit/eSIM)
- Manage Offer `/offers/list` (table: Title, Stock, Currency, Unit price,
  Sale, Action)
- Sold Orders `/g2g-user/sale` (+ Download Report)
- Payment `/payment/index` (Request Payment; table Currency | Floating Funds |
  Revenue Reserve | Available)
- API Integration `/offers/api` (G2G OpenAPI)
- Affiliate `/affiliate` (20% lifetime commission, reflink, leaderboard)
- Account `/g2g-user/account` (Personal: first/last name, national ID, gender,
  DOB, IM, billing address, country, TIN)
- Social Connect `/g2g-user/account/connects` (Facebook/Google/YouTube/TikTok/
  Twitter/PayPal/Instagram/Weibo/Twitch)
- Security `/g2g-user/account/security` (email, password, mobile, MFA,
  privacy toggles: followers list, chat-from-profile)
- Verification `/g2g-user/account/verification` (mobile, name, billing
  address, DOB, eKYC ID, face scan)
- Store Credit `/store-credit/statement` (monthly statement: Date, Description,
  Currency, Amount, Balance)
- G2G Points `/g2gpoints` (ledger; points per completed order)
Category landing pattern: Total Offers/Sellers stats, Trending Brands chips,
Filter (Brands, Budget, Online Sellers), Sort by, offers with Sold counts /
offer counts / "From" price.
