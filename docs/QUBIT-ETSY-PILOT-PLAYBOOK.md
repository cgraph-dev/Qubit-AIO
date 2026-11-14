# Qubit Etsy pilot playbook — digital downloads

**Status:** pilot plan, not a release claim. Source of truth for the machine
fields is `voicebridge/integrations/etsy_playbook.py`; this document is the
human-readable twin. Requirement source: `docs/QUBIT-OFFICE-MASTER-PLAN.md`
§29 and §33.

**Pilot:** one personal Etsy shop selling digital downloads (templates,
printables, planners). No inventory, no shipping, no custom manufacturing.

---

## 1. Offer — Digital downloads only

- Sell digital downloads only: templates, printables and planners.
- No physical inventory, no shipping and no custom manufacturing in the pilot.
- Every product needs a downloadable file, a licence statement and a preview
  image.

## 2. Niche selection

- Use the War Room to compare demand, competition, price and effort.
- Score each niche on demand, competition, price, effort, IP risk and speed to
  first sale.
- Prefer narrow, repeatable evergreen needs over trend-chasing one-offs.
- Record evidence labels (FACT / INFERENCE / ASSUMPTION / UNKNOWN). No
  fabricated research.

## 3. IP and licence checks

- Use only original work or assets with explicit commercial-use rights.
- No copied art, fonts, characters, trademarks or AI-generated images of
  protected IP.
- Keep a licence record for every font, icon and mockup used.
- Reject any listing whose provenance or licence is uncertain.
- **Human approval:** IP/licence record before design work is accepted.

## 4. Listing, SEO and images

- Write a 140-character title led by the buyer's search phrase.
- Use all 13 tags, each 20 characters or fewer.
- Write a description with what is included, file formats, sizes and how to use
  it.
- Provide clear mockups/previews and list every included file.
- Set the digital-download listing type and attach the actual deliverable.
- **Human approval:** first publish of every listing.

## 5. Pricing

- Set a price from the niche comparison, not from hope.
- Account for Etsy fees, payment processing, tax and refunds.
- Start with a simple price; no fake discounts or invented "was" prices.
- **Human approval:** any price change, discount or guarantee.

## 6. Shop policies and taxes

- Publish digital-download shop policies: no shipping, instant delivery.
- State the refund policy and the licence for personal vs commercial use.
- Configure tax settings and personal seller details.
- No policy may promise something the product cannot deliver.
- **Human approval:** shop policies before the first listing goes live.

## 7. Customer support

- Draft replies for download issues, licence questions and product questions.
- Answer within the shop's stated response window.
- Escalate complaints, chargebacks and legal threats to the owner.
- No automated message may commit to custom work or a new price.
- **Human approval:** sensitive replies, complaints and commitments.

## 8. Refunds

- Follow the published refund policy and Etsy seller rules.
- Draft refund decisions; the owner approves every refund above the cap.
- Record the reason and the receipt for every refund.
- No automatic refund and no fake review or testimonial.
- **Human approval:** every refund above the configured cap and any
  irreversible action.

## 9. Analytics and review

- Track visits, conversion, favourites, messages, refunds and revenue.
- Use Etsy Ads only within an owner-approved budget cap.
- Review weekly and improve the weakest listing first.
- Report the metric and its source, not a guess.

## 10. Launch checklist

- [ ] Niche and product brief approved.
- [ ] IP/licence record complete.
- [ ] Digital file, preview and mockups ready.
- [ ] Title, description and 13 tags written.
- [ ] Price and shop policies set.
- [ ] First listing published with human approval.
- [ ] Support/refund wording ready and approval limits configured.
- [ ] Analytics baseline captured.

## 11. Human approval points

- First publish of every listing.
- Claims, pricing, discounts and guarantees.
- Sensitive buyer replies and complaints.
- Any refund or spend (including Etsy Ads).
- Any irreversible action or credential change.

## 12. Personal seller now, entity later

- Start as a personal seller with one personal Etsy shop.
- Keep personal and future company money/data separate from day one.
- Record every sale, fee and refund for bookkeeping.
- Move to a separate legal entity later through an explicit migration.

## 13. Integration contract

- Official Etsy Open API v3 with OAuth 2.0 only. No scraping, no browser
  automation and no logged-in UI driving.
- The adapter defaults to dry run. Publishing, price changes, refunds and buyer
  messages require an approval token and an idempotency key.
- Missing credentials or transport is a typed refusal
  (`adapter_not_configured` / `adapter_unavailable`), never a fabricated
  success.
- Rate limits are enforced before any provider call; a repeated idempotency key
  returns the original result.
- See `voicebridge/integrations/etsy.py` and
  `voicebridge/integrations/platform_policy.py`.
