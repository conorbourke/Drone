# Parts research notes (2026-10-09)

`parts.json` has 53 parts and 69 supplier listings. Every entry passes the backend's own
`validate_spec()` and `PartCreate`. It was checked by running
`scratchpad/build_parts.py`, which generates the file.

## How the data was gathered, and why every part is `verified: false`

Direct page fetching was blocked in the research environment. WebFetch failed DNS, and curl
was refused by the egress proxy with a 403 on CONNECT. The only tool that worked was web
search in "extended" mode. That mode reads the pages behind the results and returns
extracts, often with quoted text.

- Spec sources are manufacturer pages where possible: store.tmotor.com, hobbywing.com,
  holybro.com / docs.holybro.com, docs.cubepilot.org, kstservos.com, savox-servo.com,
  mks-servo.com, radiomasterrc.com, team-blacksheep.com, apcprop.com, genstattu.com /
  tattuworld.com, molicel.com, expresslrs.org and easycomposites.co.uk. The `source` field
  holds that URL. However, I never loaded those pages myself; I only saw search-tool
  extracts. For that reason no part is marked `verified: true`. A reviewer who opens the
  `source` page and confirms the numbers can flip the flag.
- Listing URLs are product pages that came back as search results for that exact product.
  Prices and stock come from the search extracts and may be out of date.
  `in_stock: null` means stock could not be determined.
- T-Motor store pages often mix spec blocks for several KV variants. Each such case is
  flagged in the part's `notes`, together with the values I chose.

## Exchange rate and VAT

- 1 GBP = 1.17 EUR was used for all conversions, with no other rates.
- Where a UK shop showed both prices, the VAT-inclusive (20 %) one was used.
- Easy Composites prices are shown ex-VAT. They were multiplied by 1.2 and then converted.
- 3DXR pages sometimes show prices with a `$` sign (probably geo-currency), so the currency
  is unclear. In those cases `price_eur` is `null`.
- Post-Brexit, a UK shop selling to Ireland should zero-rate UK VAT. Irish VAT (23 %), and
  possibly customs duty for non-UK-origin goods, is then due on import. LiPo and Li-ion
  shipping from the UK to Ireland is often restricted to road/sea couriers. Check before
  ordering.

## Counts

| Category | Parts | Notes |
|---|---|---|
| motor | 11 | 7 prototype: MN3510, MN4012, MN4014, MN5008, X4110S, AT3520, AT4120. 4 for 24 kg: MN801-S KV120/KV150, U8 II KV85, U10 II KV100. Ten have manufacturer thrust tables. |
| propeller | 8 | T-Motor P15x5, P16x5.4. APC 12x8E, 13x8E, 14x8.5E (pusher "EP" versions exist). T-Motor NS26/28/30 (10 mm hole). |
| esc | 4 | T-Motor ALPHA 60A 12S, ALPHA 40A 6S. Hobbywing XRotor Pro 40A, SkyWalker 60A V2. |
| servo | 4 | KST BLS815, Savox SV-1270TG+, MKS HBL599, MKS HV1220. |
| battery | 5 | Gens ace 6S 5000 45C, Tattu Plus 6S 10000. Tattu semi-solid 12S 30000 and 14S 33000. Tattu Pro 14S 22000. |
| cell | 3 | Molicel P45B, P50B, Samsung 50S. |
| autopilot | 3 | Pixhawk 6C, Pixhawk 6X Rev 8, Cube Orange+ (ADS-B set). |
| gps | 3 | Holybro M10, Here4, Matek/UMT M10Q-5883. |
| radio | 2 | TBS Crossfire Nano RX (868), RadioMaster ER8 (ELRS 2.4). |
| telemetry | 3 | RFD868x-EU, Holybro SiK V3 433, ELRS MAVLink (RP3 V2). |
| carbon_tube | 7 | Easy Composites roll-wrapped, 12-25 mm OD. |

## Suppliers found

**UK, with product pages recorded:**
- 3DXR (UK T-Motor and CubePilot distributor; the main source for T-Motor parts)
- Unmanned Tech
- Flying Tech
- HobbyRC
- Easy Composites
- RC World
- Rapid RC Models
- MWM Warbirds
- Kings Lynn Model Shop
- Nexus Models
- West London Models
- Align-Trex UK
- Al's Hobbies
- ProBuild UK
- Appliance Electronics
- Component Shop
- Fogstar (wholesale)
- Nu Battery
- Cell Supply

**Ireland:**
- Radio Controlled Shop (radiocontrolledshop.ie, Rathcoole, Dublin) exists and stocks APC
  props and some Hobbywing ESCs. Only one product listing (APC 12x8E) could be pinned to a
  product URL.
- Model Heli Services (modelheliservices.com, Irish) stocks Gens ace and other LiPos, but I
  found no matching 6S 5000 45C listing there.
- Drone Works Ireland is a DJI dealer only.
- No Irish shop selling T-Motor, Holybro, CubePilot or RFDesign parts was found.

**Not found or not used:**
- "Quadcopters.co.uk" exists (TBS Crossfire category page), but I recorded no product
  listing there.
- No result came back for 4Max.
- "Unmanned Systems Ireland", "Hobby Rack" and "Model Heaven" did not show up as drone-parts
  stockists.
- EU sellers that ship to Ireland are mentioned in notes only, because the schema allows
  only `IE`/`UK` listings. These are nkon.nl (Molicel cells), Mibosport (MKS servos),
  Aeroboticshop (Cube) and Carbon-Composite.com (DE, pultruded tubes).

## What could not be verified, and gaps

- **Motor mount patterns.** None were found in the extracts. T-Motor publishes them only in
  2D drawings. Most motors have `mount_pattern` set to "not published in sources found".
- **U10 II max power** is derived (32.4 A x 48 V = 1555 W). The value on the page was
  truncated.
- **U8 II KV85 shaft** (12 mm) was taken from sibling U8 II Pro/Lite pages.
- **Missing thrust tables.** SunnySky X4110S thrust tables have no throttle % or rpm, so
  they were not imported. The values are quoted in its notes.
- **Hobbywing X8 G2** (100 KV, 12-14S, 30x11 folding prop, 17.5 kg max thrust) is an
  integrated motor+ESC. It has an excellent 12S/14S thrust table but no published winding
  resistance or no-load current, so it was skipped. It is worth adding by hand, since it
  suits a 24 kg quad well.
- **MAD M8C08/M10.** Spec data was partial and the M8C08 is discontinued, so these were
  skipped.
- **Folding props.** T-Motor MF1503, MF1604, FA15.2x5 and FA28.2/FA30.2, plus Mejzlik 22-28 in,
  have weights and sizes but no published hub bore. `hub_bore_mm` is required, so they were
  skipped. Their key data:
  - MF1503: 15x5.4, 24 g, max 3 kg / 7000 rpm. 3DXR GBP 34.90/pair.
  - FA15.2x5: 27.5 g, 6 kg limit. Bionic Eye GBP 79.99.
  - FA28.2x9.2: 128 g. FA30.2x9.9: 153 g.

  This is the biggest propeller gap. Measure a hub or ask T-Motor, then add them.
- **P16x5.4 bore** (6 mm) was inferred from the bundled 06*04 adapter.
- **ESCs.**
  - FLAME 60A 12S was skipped (weight, BEC and telemetry not found).
  - APD 120F3 was skipped (minimum cell count not published; discontinued at Flying Tech).
  - ALPHA 40A continuous rating conflicts between sources (20 A vs 40 A).
  - Hobbywing "telemetry: false" means no telemetry is listed.
- **Matek.** The H743-WING/WLITE flight controllers are marked EOL on mateksys.com, so they
  were not included.
- **Carbon tubes.**
  - No round pultruded tube with a published mass per metre was found at a UK supplier.
    Easy Composites gives 28-40 GPa / 400-500 MPa for its pultrusions but no g/m for the
    10/12 mm round tubes.
  - Weights for the 30/35/40 mm roll-wrapped spars (needed for the 24 kg wing) were not in
    the extracts. Prices are known: 30x27 GBP 32.55, 35x32 GBP 35.90, 40x37 GBP 40.40, all
    ex-VAT per 1 m.
  - Carbon Fibre Tubes Ltd (UK) returned no results.
- **Cells.** The P50B diameter and length are the nominal 21700 envelope. Samsung 50S figures
  come from reseller reproductions of the datasheet.
- **Batteries.** No UK or Irish stockist was found for the Tattu semi-solid or 12S/14S packs.
  Note that the 14S 33000 semi-solid is rated only 1C continuous.

## Regulatory notes (Ireland: EU rules via ComReg; drones under EASA)

### Telemetry and control radios

- **868 MHz SRD (863-870 MHz).** Use is licence-exempt within the ERC Rec 70-03 / EU
  SRD-decision limits. Most sub-bands allow 25 mW ERP with duty-cycle or LBT limits. The
  869.40-869.65 MHz sub-band allows 500 mW ERP at 10 % duty cycle. Use the RFD868x-EU (EU
  firmware) and TBS Crossfire in its 868 CE/LBT mode, and configure frequency and power
  accordingly.
- **2.4 GHz (2400-2483.5 MHz).** The limit is 100 mW EIRP. Use ExpressLRS "LBT" (EU) builds
  and set telemetry and TX power to 100 mW or below, including antenna gain.
- **433 MHz (433.05-434.79 MHz).** This band is SRD at 10 mW ERP only. The Holybro SiK V3
  433 at its default 100 mW (20 dBm) is not legal. Reduce TXPOWER to 10 dBm or below, which
  makes it short-range, or use 868 MHz instead.
- **915 MHz (902-928 MHz)** is a US ISM band and not legal in the EU. Do not buy the 915
  versions of SiK, RFD900 or Crossfire.
- **Higher-power 433/868 "long range" modes** (1 W RFD900/868x non-EU firmware, Crossfire
  "868 non-CE") are not legal.

### Drone category

Drones are regulated under EASA (via the Irish Aviation Authority).
- A 2-3 kg prototype without a class mark flies in Open A3 at most, after operator
  registration and A1/A3 training.
- A 24 kg aircraft sits just under the 25 kg Open-category limit. Above 4 kg without a class
  label it is A3 only, and BVLOS or wing-cruise missions will need the Specific category
  (STS or SORA operational authorisation).
- The range figures in `radio`/`telemetry` are manufacturer line-of-sight claims, not what
  Open-category VLOS rules allow you to fly.
