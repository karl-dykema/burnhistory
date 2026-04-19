#!/usr/bin/env python3
"""
Parse raw WRCC daily text files for one station, compute percentile bands
by day-of-year, and emit a static JSON file for the BurnHistory viewer.

Output JSON shape:
{
  "station": { "id": "azAHAV", "name": "Havasu", "state": "Arizona", "lat": ..., "lon": ... },
  "metric": "temp_max_f",
  "label": "Daily Max Temperature (°F)",
  "baseline_years": [1995, 2024],
  "percentiles": {
    "1":  [p10, p10, ...],   // 365 values, index 0 = Jan 1
    "7":  [...],             // 7-day smoothed
    "p10": [...], "p25": [...], "p50": [...], "p75": [...], "p90": [...],
  },
  "current_year": {
    "year": 2025,
    "values": [null, null, 68.0, ...]  // 365 values, null = missing/future
  },
  "all_years": {
    "1995": [v0, v1, ...],   // for record high/low lines (optional)
    ...
  }
}
"""

import os
import re
import json
import glob
import math
from collections import defaultdict
from datetime import date

MISSING = -9999

def is_leap(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def doy_to_index(doy, year):
    """Convert 1-based day-of-year to 0-based index in a 365-slot array.
    Leap day (Feb 29 = doy 60) is mapped to the same slot as Mar 1 (index 59).
    Days after Feb 29 in a leap year are shifted back by 1."""
    if is_leap(year) and doy >= 60:
        return doy - 2   # skip leap day slot
    return doy - 1

def parse_wrcc_file(filepath):
    """Parse one WRCC daily summary text file. Returns list of dicts."""
    records = []
    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            line = line.rstrip()
            # Data lines look like: " 01/01/2000  2000     1     1   274  ..."
            m = re.match(r'^\s+(\d{2}/\d{2}/\d{4})\s+(\d{4})\s+(\d+)\s+(\d+)\s+(.*)', line)
            if not m:
                continue
            date_str, year_str, doy_str, run_str, rest = m.groups()
            year = int(year_str)
            doy  = int(doy_str)

            # Split the remaining fixed-width fields by whitespace
            fields = rest.split()
            # Expected order: solar_rad, wind_ave, wind_dir, wind_gust,
            #   temp_ave, temp_max, temp_min,
            #   fuel_ave, fuel_max, fuel_min,
            #   rh_ave,   rh_max,   rh_min,
            #   precip
            if len(fields) < 14:
                continue

            def fval(s):
                v = float(s)
                return None if v == MISSING else v

            records.append({
                'date':      date_str,
                'year':      year,
                'doy':       doy,
                'solar_rad': fval(fields[0]),
                'wind_ave':  fval(fields[1]),
                'wind_dir':  fval(fields[2]),
                'wind_gust': fval(fields[3]),
                'temp_ave':  fval(fields[4]),
                'temp_max':  fval(fields[5]),
                'temp_min':  fval(fields[6]),
                'fuel_ave':  fval(fields[7]),
                'fuel_max':  fval(fields[8]),
                'fuel_min':  fval(fields[9]),
                'rh_ave':    fval(fields[10]),
                'rh_max':    fval(fields[11]),
                'rh_min':    fval(fields[12]),
                'precip':    fval(fields[13]),
            })
    return records


def percentile(values, p):
    """Compute pth percentile of a sorted list (0-100 scale)."""
    if not values:
        return None
    k = (len(values) - 1) * p / 100.0
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - k) + values[hi] * (k - lo)


def smooth(values, window=7):
    """Apply circular day-of-year smoothing with given window half-width."""
    n = len(values)
    result = []
    half = window // 2
    for i in range(n):
        neighborhood = []
        for j in range(i - half, i + half + 1):
            v = values[j % n]
            if v is not None:
                neighborhood.append(v)
        result.append(round(sum(neighborhood) / len(neighborhood), 1) if neighborhood else None)
    return result


def build_station_json(raw_dir, station_id, station_name, station_state,
                       metric_key, metric_label,
                       lat=None, lon=None,
                       baseline_start=None, baseline_end=None,
                       current_year=None,
                       records=None):
    # Load all yearly files (or use pre-computed records)
    if records is not None:
        all_records = records
    else:
        files = sorted(glob.glob(os.path.join(raw_dir, '*.txt')))
        all_records = []
        for f in files:
            all_records.extend(parse_wrcc_file(f))

    if not all_records:
        raise ValueError(f"No records parsed from {raw_dir}")

    years = sorted(set(r['year'] for r in all_records))
    print(f"  Loaded {len(all_records)} records across years {years[0]}–{years[-1]}")

    # Filter to baseline period
    if baseline_start is None:
        baseline_start = years[0]
    if baseline_end is None:
        baseline_end = years[-2]  # exclude current partial year
    if current_year is None:
        current_year = years[-1]

    baseline_records = [r for r in all_records
                        if baseline_start <= r['year'] <= baseline_end]
    print(f"  Baseline: {baseline_start}–{baseline_end} ({len(baseline_records)} records)")

    # Build per-DOY bins (365 slots)
    doy_bins = defaultdict(list)  # slot_index -> list of values
    for r in baseline_records:
        v = r.get(metric_key)
        if v is None:
            continue
        idx = doy_to_index(r['doy'], r['year'])
        if 0 <= idx < 365:
            doy_bins[idx].append(v)

    # Data quality: count years with at least one valid value per DOY
    baseline_years_with_data = sorted(set(
        r['year'] for r in baseline_records if r.get(metric_key) is not None
    ))
    n_years = len(baseline_years_with_data)
    min_bin = min((len(doy_bins[i]) for i in range(365)), default=0)
    print(f"  Data quality: {n_years} years with data, min DOY bin size = {min_bin}")

    # Compute raw percentile arrays
    p_levels = [10, 25, 50, 75, 90]
    raw_percentiles = {}
    for p in p_levels:
        arr = []
        for i in range(365):
            vals = sorted(doy_bins[i])
            arr.append(round(percentile(vals, p), 1) if vals else None)
        raw_percentiles[p] = arr

    # Smooth each band with 7-day window
    smoothed = {p: smooth(raw_percentiles[p], window=7) for p in p_levels}

    # Current-year values
    cy_records = {r['doy']: r for r in all_records if r['year'] == current_year}
    current_values = []
    for i in range(365):
        doy = i + 1  # 1-based (non-leap mapping)
        r = cy_records.get(doy)
        if r:
            v = r.get(metric_key)
            current_values.append(round(v, 1) if v is not None else None)
        else:
            current_values.append(None)

    # Today's DOY for marker
    today = date.today()
    today_doy = today.timetuple().tm_yday
    today_idx = doy_to_index(today_doy, today.year)

    # Compute today's percentile rank
    today_val = None
    today_pct = None
    if 0 <= today_idx < 365:
        today_val = current_values[today_idx]
        if today_val is not None:
            historical = sorted(doy_bins[today_idx])
            if historical:
                n_below = sum(1 for v in historical if v <= today_val)
                today_pct = round(100 * n_below / len(historical))

    # Recent prior years (last 4 before current_year)
    prior_years = sorted(y for y in years if y != current_year)[-4:]
    recent_years_out = {}
    for yr in prior_years:
        yr_recs = {r['doy']: r for r in all_records if r['year'] == yr}
        vals = []
        for i in range(365):
            doy = i + 1
            r = yr_recs.get(doy)
            if r:
                v = r.get(metric_key)
                vals.append(round(v, 1) if v is not None else None)
            else:
                vals.append(None)
        recent_years_out[str(yr)] = vals

    out = {
        "data_quality": {
            "years_with_data": n_years,
            "min_doy_bin":     min_bin,
            "sufficient":      n_years >= 15 and min_bin >= 10,
        },
        "station": {
            "id":    station_id,
            "name":  station_name,
            "state": station_state,
            "lat":   lat,
            "lon":   lon,
        },
        "metric":         metric_key,
        "label":          metric_label,
        "units":          "°F",
        "baseline_years": [baseline_start, baseline_end],
        "today": {
            "doy":        today_doy,
            "index":      today_idx,
            "value":      today_val,
            "percentile": today_pct,
        },
        "percentiles": {
            str(p): smoothed[p] for p in p_levels
        },
        "current_year": {
            "year":   current_year,
            "values": current_values,
        },
        "recent_years": recent_years_out,
    }

    return out


if __name__ == '__main__':
    import argparse, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    METRIC_META = {
        'temp_max':  ('temp_max',  'Daily Max Temperature',        '°F'),
        'temp_min':  ('temp_min',  'Daily Min Temperature',        '°F'),
        'temp_ave':  ('temp_ave',  'Daily Mean Temperature',       '°F'),
        'rh_min':    ('rh_min',    'Daily Min Relative Humidity',  '%'),
        'rh_ave':    ('rh_ave',    'Daily Mean Relative Humidity', '%'),
        'wind_gust': ('wind_gust', 'Daily Max Wind Gust',          'mph'),
        'precip':    ('precip',    'Daily Precipitation',          'in'),
        'erc':       ('erc',       'Energy Release Component (ERC, Fuel Model G)', ''),
        'fm100':     ('fm100',     '100-hr Dead Fuel Moisture',    '%'),
        'fm1000':    ('fm1000',    '1000-hr Dead Fuel Moisture',   '%'),
    }

    parser = argparse.ArgumentParser(description='Build BurnHistory percentile JSON')
    parser.add_argument('raw_dir',   help='Directory of yearly WRCC .txt files')
    parser.add_argument('out_file',  help='Output JSON file path')
    parser.add_argument('--station-id',     default='unknown')
    parser.add_argument('--name',           default='Unknown Station')
    parser.add_argument('--state',          default='')
    parser.add_argument('--lat',            type=float, default=34.5)
    parser.add_argument('--metric',         default='erc', choices=list(METRIC_META))
    parser.add_argument('--fuel-model',     default='G',
                        help='NFDRS fuel model letter (A-U, no M). Only used with --metric erc.')
    parser.add_argument('--baseline-start', type=int, default=None)
    parser.add_argument('--baseline-end',   type=int, default=None)
    parser.add_argument('--current-year',   type=int, default=None)
    args = parser.parse_args()

    key, label, units = METRIC_META[args.metric]

    # ERC and fuel moisture need the NFDRS running-state pipeline
    precomputed_records = None
    if args.metric in ('erc', 'fm100', 'fm1000'):
        from nfdrs import compute_erc_series, FUEL_MODELS, FUEL_MODEL_NAMES

        fm_letter = args.fuel_model.upper()
        if fm_letter not in FUEL_MODELS:
            print(f"Unknown fuel model '{fm_letter}'. Valid: {list(FUEL_MODELS)}")
            sys.exit(1)

        fuel = FUEL_MODELS[fm_letter]
        fm_name = FUEL_MODEL_NAMES.get(fm_letter, fm_letter)
        if args.metric == 'erc':
            label = f'ERC — Model {fm_letter}: {fm_name}'

        files = sorted(glob.glob(os.path.join(args.raw_dir, '*.txt')))
        raw = []
        for f in files:
            raw.extend(parse_wrcc_file(f))
        raw.sort(key=lambda r: (r['year'], r['doy']))

        print(f"  Computing NFDRS series (Fuel Model {fm_letter}) for {len(raw)} records...")
        precomputed_records = compute_erc_series(raw, lat=args.lat, fuel=fuel)

    print(f"Building {label} climatology for {args.name}...")
    data = build_station_json(
        raw_dir        = args.raw_dir,
        station_id     = args.station_id,
        station_name   = args.name,
        station_state  = args.state,
        metric_key     = key,
        metric_label   = label,
        lat            = args.lat,
        baseline_start = args.baseline_start,
        baseline_end   = args.baseline_end,
        current_year   = args.current_year,
        records        = precomputed_records,
    )
    data['units'] = units

    os.makedirs(os.path.dirname(os.path.abspath(args.out_file)), exist_ok=True)
    with open(args.out_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    size_kb = os.path.getsize(args.out_file) / 1024
    print(f"  Written to {args.out_file} ({size_kb:.1f} KB)")
    print(f"  Today ({data['today']['doy']}): value={data['today']['value']}, "
          f"percentile={data['today']['percentile']}")
