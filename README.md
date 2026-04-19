# BurnHistory

**Fire weather vs. historical climatology — station-level, on your phone, in two seconds.**

Live demo → **[karl-dykema.github.io/burnhistory](https://karl-dykema.github.io/burnhistory/)**

![dark chart showing ERC percentile bands and current-year line]

BurnHistory answers one question: *"Is today's fire weather unusual for this time of year at this station?"*

It plots the current ERC against a 30-year percentile envelope — like a reservoir storage chart, but for fire danger. At a glance, a manager can see whether conditions are sitting at the 90th percentile for July at their station, or just a normal dry spell.

---

## Background and motivation

GACC predictive services publish static PNG climatology charts manually, PSA-level, buried in agency websites. FireFamilyPlus computes station-level climatology but requires a desktop install and an NFDRS dataset few managers have ready. WRCC and WIMS have the raw data behind clunky interfaces.

BurnHistory sits between them: interactive, station-level, zero install, accessible from a phone in the field. It's designed as a companion to [BurnWindow](https://burnwindow.app) — when a manager asks *"yeah but is this actually high for April?"*, one link gets them here.

---

## How it works

### Architecture

The entire app is a single `index.html` with vanilla JavaScript and no build step. Pre-computed JSON files for each station (one per fuel model, ~12 KB each) are fetched on demand. There is no server, no API call at runtime, and no framework. The app works offline once the JSON is cached.

```
data/stations/
  havasu_az/
    erc_A.json   ← one file per fuel model (A–U, 19 models)
    erc_G.json
    …
  chester_ca/
  humptullips_wa/
index.html         ← the entire app
```

### Data pipeline

Historical data is pulled from the **Western Regional Climate Center (WRCC)** daily RAWS archive — one text file per year per station. The pipeline (`scripts/build_percentiles.py`) runs offline and bakes the results into static JSON files that are committed to the repo.

```
WRCC raw text  →  parse_wrcc_file()  →  compute_erc_series()  →  percentile bands  →  JSON
```

### NFDRS ERC computation (`scripts/nfdrs.py`)

ERC (Energy Release Component) is computed from scratch using the original NFDRS equations (Bradshaw et al. 1984), adapted from the NCAR/fire-indices NCL scripts. It is **not** pulled from FAMWEB or FireFamilyPlus — it is independently recalculated from raw met observations so the pipeline can run on any RAWS record.

**Running state variables** — ERC requires a multi-day moisture history, so the computation is stateful:

| State var | Initial value | Update rule |
|-----------|--------------|-------------|
| `fm100` | 10% | Daily exponential lag toward boundary moisture |
| `fm1000` | 25% | Daily exponential lag, slower time constant |

**Step-by-step per day:**

1. **Equilibrium moisture content (EMC)** — computed from daily max/min temperature and RH using the piecewise Nelson equations (three RH regimes: `H < 10`, `10 ≤ H ≤ 50`, `H > 50`).

2. **Daylight hours** — estimated from day-of-year and station latitude using a solar declination approximation (`23.45° × sin((284 + doy) × 360/365)`), then fed into the hour-angle formula.

3. **100-hr and 1000-hr fuel moisture** — updated using the boundary-moisture approach: a weighted average of daytime (dry) and nighttime/precipitation EMC sets the daily boundary, and the running state is pulled toward it by a fixed fractional rate (`FR100 ≈ 0.316`, `FR1000 ≈ 0.307`). Precipitation duration is estimated from daily total via a lookup table.

4. **1-hr and 10-hr fuel moisture** — computed directly from daily max/min temperature and RH (no running state needed).

5. **Live fuel moisture** — simplified NFDRS-1978 approach: herbaceous and woody moisture scale with the 1000-hr state as a proxy for phenological greenup. (A full GSI/GDD model is on the roadmap.)

6. **ERC** — computed from all five fuel moisture classes plus the fuel model constants (loads, SAV ratios, heat content, bed depth). The calculation follows the Rothermel reaction intensity framework:

   - Surface-area-weighted characteristic SAV ratios for dead and live fractions
   - Moisture damping coefficients for each fraction
   - Packing ratio and optimum reaction velocity
   - Reaction intensity `Iᵣ` from fuel loads × heat content × mineral and moisture damping
   - Residence time `τ = 384 / σ̄`
   - `ERC = 0.04 × Iᵣ × τ`

   The first 60 days of each record are discarded as spinup before any ERC value is output.

**Fuel models** — all 20 standard NFDRS models (A–U, no M) are supported. Constants are sourced from the NCAR `retrieve_constants.ncl` table. Each model has its own pre-computed JSON, so the viewer can switch models instantly without recomputation.

### Percentile bands (`scripts/build_percentiles.py`)

1. All historical records are binned into 365 day-of-year slots (leap days are folded: Feb 29 maps to the Mar 1 slot; all subsequent days in leap years shift back by one index).

2. For each DOY slot, the 10th, 25th, 50th, 75th, and 90th percentiles are computed using linear interpolation between sorted values.

3. A 7-day **circular smoothing window** is applied to each percentile array — circular so that Dec 31 and Jan 1 are treated as adjacent. This removes day-to-day noise from sparse bins while preserving seasonal shape.

4. **Today's percentile** is a rank-based calculation against the historical bin for today's DOY: `rank = count(historical_values ≤ today_value) / total × 100`.

5. **Data quality flag** — if fewer than 15 years of data are available, or any DOY bin has fewer than 10 observations, the JSON is flagged and the viewer shows a warning banner.

---

## Pilot stations

| Station | RAWS ID | Location | Baseline |
|---------|---------|----------|----------|
| Havasu | `azAHAV` | Lake Havasu City, AZ | 1995–2025 |
| Humptullips | `waHUMP` | Grays Harbor County, WA | ~1995–2025 |
| Chester | `caXCHE` | Plumas County, CA | ~1995–2025 |

These three were chosen for geographic spread (Southwest, PNW coast, Sierra) and long records. Suggestions for additional stations welcome — see below.

---

## Data sources

| Source | Role |
|--------|------|
| [WRCC RAWS Daily Summaries](https://wrcc.dri.edu) | Historical daily met observations (primary input) |
| [Synoptic Data API](https://synopticdata.com) | Current-day observations (planned — Phase 2) |
| NFDRS technical documentation (Bradshaw et al. 1984) | ERC equations |
| NCAR/fire-indices (Abatzoglou) | Fuel model constants, equation reference |

Current-year data in the pilot is pulled from the same WRCC archive and re-baked each time the pipeline runs. Live same-day observations via Synoptic are planned for Phase 2.

---

## Roadmap

### Phase 2 — Metrics + Station Search
- Additional metrics: 10-hr, 100-hr, 1000-hr fuel moisture; Burning Index; KBDI; precipitation departure; temperature/RH extremes
- Station search by name, state, agency, or GPS proximity
- Map view — click any RAWS pin
- Multi-year overlay: compare current year to user-selected historical years
- Record high / record low lines
- Notable fire event markers on the timeline

### Phase 3 — Operational & Analytic Features
- **Rx window overlay** — enter your prescription (ERC min/max, FM range), see matching historical days and *"how many April days in 30 years hit your window"*
- **Burn window climatology** — histogram of Rx-window days by month
- **Analog year finder** — *"conditions today most closely resemble April 2012"*
- Percentile trend sparkline (14-day trajectory)
- Season-to-date ranking (*"currently tracking as the 4th driest year of record"*)
- PSA aggregation view

### Phase 4 — Polish, Distribution, Trust
- Export chart as PNG for IAPs and morning briefing packets
- Shareable URL (station + metric + date)
- Offline cache — works without signal once loaded
- Baseline period toggle: 30-year normal (1991–2020) vs. full period of record vs. last 15 years
- Citation snippet for official documents
- Cross-link from BurnWindow

---

## Questions we're working through

- **Baseline period:** 30-year normal (matching NWS climate normals) vs. full period of record? Both toggleable? Climate change makes this choice non-trivial — a 1991–2020 normal already bakes in warming signal.
- **Live fuel moisture:** the current simplified herbaceous model is a rough proxy. A proper GSI/GDD greenup model requires accumulated temperature data. Worth the complexity?
- **Station scale:** how many stations can be pre-computed and served from GitHub Pages before the repo becomes unwieldy? (Rough estimate: thousands of stations × 20 fuel models × ~12 KB = manageable, but worth benchmarking.)
- **NFDRS 2016 vs. legacy '78:** FEDS/NFDRS 2016 produces non-comparable ERC values. The pipeline currently implements 1978 NFDRS. Should we support both?
- **Update cadence:** re-run the pipeline annually, seasonally, or hook it to a CI job?

---

## Suggestions welcome

If you work in fire and have opinions on any of this — station priorities, metrics that matter, UX that doesn't work on your phone in the field, or data sources we're missing — open an issue or start a discussion. This is an early prototype and the most useful feedback right now is *"I would actually use this if it showed X"* or *"I tried it and it confused me because Y"*.

[Open an issue](https://github.com/karl-dykema/burnhistory/issues) · [Start a discussion](https://github.com/karl-dykema/burnhistory/discussions)

---

## Running the pipeline locally

Requirements: Python 3.8+, no external dependencies (pure stdlib).

```bash
# Pull raw WRCC data (edit station IDs and years in pull_wrcc.sh first)
bash scripts/pull_wrcc.sh

# Build all fuel models for a station
for model in A B C D E F G H I J K L N O P Q R S T U; do
  python scripts/build_percentiles.py \
    data/raw/havasu_az \
    data/stations/havasu_az/erc_${model}.json \
    --station-id azAHAV \
    --name Havasu --state Arizona \
    --lat 34.5 \
    --metric erc \
    --fuel-model $model
done
```

---

## License

MIT. Code and data are freely reusable. If you build on this for official agency use, a note back would be appreciated but isn't required.

---

*BurnHistory is an independent project, not affiliated with USFS, NIFC, WRCC, or any government agency. All data is sourced from public archives. Percentile calculations are provided for situational awareness only — not a forecast, not a dispatch trigger.*
