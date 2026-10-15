# EUR/USD Fed–ECB OIS Forward Differential — provider contract

## Status and authority

This is **optional production-data ingestion**, not automatic position execution. The
existing Policy Differential weight is **5%**, the US Rates Pressure weight is unchanged
(**15%**), and the regular Epistemic / calendar / EPE fail-closed gates remain authoritative.

Without a sourced, licensed feed for **both** USD SOFR OIS and EUR €STR OIS curves,
the new adapter records `ois_market_feed_not_configured` and emits **zero Evidence**.
It does not substitute FRED Fed target rates, ECB deposit rates, SOFR overnight
fixings, government bond yields, or ZQ=F/futures quotes for market OIS curves.

The ECB's EMMS dataset includes OIS market-implied forward *statistics*, but
the relevant EMMS.H series are published half-yearly, not as live quote snapshots.
A real-time Daily signal requires a separately sourced pair of market curves.

## Preferred provider: LSEG Data Platform (former Refinitiv/Reuters)

**Selected for BriefRooms:** LSEG Instrument Pricing Analytics (IPA) zero-coupon
curves. LSEG publicly documents both USD **SOFR** and EUR **ESTR** under the
same cloud API for OIS discounting:

- LSEG developer: https://developers.lseg.com/en/article-catalog/article/instrument-pricing-analytics-zero-coupon-curves
- LSEG guide: https://community.developers.lseg.com/discussion/72439/rdp-ipa-sonia-curve/p1
- LSEG cloud API quick start: https://developers.lseg.com/en/api-catalog/lseg-data-platform/lseg-data-library-for-python/quick-start

The script `scripts/belief_lseg_ois_market_feed.py` implements the direct
OAuth 2.0 **client-credentials** flow for a licensed service account, then
makes one POST for both indexed curves to the documented IPA endpoint
`/data/quantitative-analytics-curves-and-surfaces/v1/curves/zc-curves`.

### Activate after the LSEG contract is agreed

1. Request an **LSEG Data Platform service account**, not an LSEG Workspace
   Desktop or Bloomberg Terminal-only seat. Confirm *cloud-side, automated,
   non-display* access to USD SOFR and EUR ESTR **IPA OIS ZC curves** at
   market-compatible frequency and verify commercial rights for this
   BriefRooms processing use.
2. Configure these two **GitHub Actions repository secrets** (never expose
   values in chats, commits, issues, or website artifacts):
   - `LSEG_OIS_CLIENT_ID` — OAuth v2 Service ID
   - `LSEG_OIS_CLIENT_SECRET` — OAuth v2 Client Secret
3. The existing `belief-core-shadow-live.yml` workflow passes secrets to
   the runtime. A complete pair takes priority over the legacy optional
   feed URL, uses HTTPS and processes responses **in private runtime memory**.
   It never commits raw vendor OIS prices.
4. Confirm that the licensed IPA response returns TWO separate curve objects
   with `indexName=SOFR, currency=USD` and `indexName=ESTR, currency=EUR`,
   `discountingTenor=OIS`, a single OIS curve, and discount factors at
   3M, 6M and 1Y, plus a market quote **time-of-day with timezone**.
5. Inspect `eurusd_fed_ecb_policy_differential` in the private Belief runtime:
   `access_route=lseg_ipa_oauth_v2` and `ready=true` prove the provider
   pipeline was accepted. Inspect actual Daily `used_beliefs` for
   final production influence, only when OIS slope exceeds 5bp.

**Fail-closed caveat:** LSEG's IPA response may carry only a valuation date
instead of a demonstrably current quote timestamp. A date-only
`valuationDate` or `marketDataDate` does **not** prove intraday data
freshness and is rejected; do not replace it with runtime `now()` or claim
a live feed. Ask LSEG for their price-source timestamp field/entitlement
or authorized timestamped dataset if necessary. Until then the 5% modifier
stays unavailable without affecting the other Daily signals.

LSEG access and fees are contract-specific. A bank's existing LSEG/Bloomberg
license does not automatically authorize redistribution or private-project
server use; obtain express licensing approval first.

## Repository configuration

Set GitHub Actions **repository variable** `BELIEF_EURUSD_OIS_CURVES_URL` to an
HTTPS endpoint that supplies your vendor-authorized JSON data (for example, an
authenticated adapter inside BriefRooms). Set GitHub Actions **repository secret**
`BELIEF_EURUSD_OIS_CURVES_TOKEN` if the source requires Bearer authentication.
Do **not** commit credentials or vendor-restricted quotes to the public repository.

The existing `.github/workflows/belief-core-shadow-live.yml` passes these settings
to the live Belief collection run. If either currency curve is missing or the
contract is invalid, no fresh OIS Evidence is created.

## JSON response contract

**The following example uses deliberately synthetic numbers and a non-routable
example domain. It is not market data or a feed you can trade on.**

```json
{
  "schema_version": "briefrooms-eurusd-ois-curves-v1",
  "provider": "Licensed Vendor Name",
  "curves": {
    "USD": {
      "currency": "USD",
      "overnight_index": "SOFR",
      "curve_type": "ois_discount_factors",
      "construction": "bootstrapped_from_ois_swaps",
      "market_observed_at": "2026-10-15T14:04:00Z",
      "source_ref": "https://vendor.example.test/ois/USD",
      "instrument_id": "TEST-USD-SOFR-OIS",
      "discount_factors": [
        {"months": 3, "df": 0.99005},
        {"months": 6, "df": 0.98167},
        {"months": 12, "df": 0.96657}
      ]
    },
    "EUR": {
      "currency": "EUR",
      "overnight_index": "ESTR",
      "curve_type": "ois_discount_factors",
      "construction": "bootstrapped_from_ois_swaps",
      "market_observed_at": "2026-10-15T14:04:00Z",
      "source_ref": "https://vendor.example.test/ois/EUR",
      "instrument_id": "TEST-EUR-ESTR-OIS",
      "discount_factors": [
        {"months": 3, "df": 0.99377},
        {"months": 6, "df": 0.98758},
        {"months": 12, "df": 0.97531}
      ]
    }
  }
}
```

Each curve **must** represent spot-normalized OIS *discount factors* derived
from actual OIS swaps, **not** quoted par swap rates or compounded fixings.
Required matching maturities: 3M, 6M, 12M. Discount factor units are pure
ratios, not percent. Both curves must have UTC or offset-aware
`market_observed_at` timestamps obtained from the actual market quote system
(not collector fetch time).

## Calculation

With `DF_ccy(T)` from the curve, continuous-compounding forward:
`f_ccy(a,b) = -ln[DF_ccy(b)/DF_ccy(a)] / (b-a)`; T expressed in years
(3,6,12 months /12).

- `S_near = f_USD(3M,6M) - f_EUR(3M,6M)`
- `S_far = f_USD(6M,12M) - f_EUR(6M,12M)`
- `delta_spread_bps = (S_far - S_near) * 100` when rates are in percent.
- Negative material delta means the market-implied Fed-side yield advantage
  is declining on the farther horizon: positive EUR/USD Evidence.
- Positive material delta means a widening USD advantage: negative Evidence.
- Absolute delta below 5bp -> **no directional Evidence**.

The source represents the **market-implied OIS overnight rate path** with basis,
liquidity and term risk premia; it is **not** a precise probability distribution
over individual FOMC or ECB meetings. Comparing same windows reduces maturity
mismatch but does not make SOFR and €STR identical benchmark concepts.

## Safety and validation

- Source age <=75 minutes and allowed future skew <=2 minutes.
- USD and EUR observation times must differ by <=10 minutes.
- Source HTTPS, explicit instrument IDs and source references, USD/SOFR and EUR/ESTR.
- No future/stale/partial curves, duplicate maturity, fabricated quote time,
  futures, par swaps or invalid factors are converted to Evidence.
- Market OIS-only namespace in Belief Core: previously ingested historical
  *official-rate-level* Evidence stays in research ledger but cannot vote in
  the new OIS policy signal.
- Qualifying fresh OIS Evidence decays out of the Epistemic weight after
  75 minutes without a genuinely new timestamp.
- Strength capped at 0.22 *before* the already capped 5% epistemic weight.

## Operational verification

Watch Belief run log key `BELIEF_STAGE_TIMING` with
`stage=eurusd_ois_forward_fetch`, and the stored
`eurusd_fed_ecb_policy_differential` state. `ready=true` with
`signal_status=eligible` plus Evidence under the
`fed_ecb_ois_forward` adapter means it can influence the next Epistemic
projection. `ready=true` with `no_material_forward_spread_slope` means
valid curves, but no directional modifier.

For proof of production effectiveness, inspect actual
`data/investments/eurusd_daily_spot.json` ->
`metadata.final_decision.used_beliefs` and verify
`eurusd.policy_differential.supportive` plus current `observed_at`.
**The mere existence of this code or a green CI test is not proof of a live
OIS market feed.**
