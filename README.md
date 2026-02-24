# AI Energy — Industrial Energy Management Tools

Tools for analyzing smart meter data from the **Unified Energy Management System** database. The system handles high-frequency telemetry from smart power meters, energy sharding, and energy discrepancy analysis.

---

## Project Structure

```
aienergy/
├── db/                 # Database utilities
│   ├── connect.py      # Connection test
│   ├── schema.py       # Schema export
│   ├── schema.sql      # Dumped schema
│   ├── permissions.py  # Permission helpers
│   └── definitions.md  # Schema definitions
├── meter/              # Single-meter analysis
│   ├── meter.py        # HTML report for one meter
│   └── data/           # Output: meter-{id}-{YYMMDD}.html
├── report/             # Aggregate reports
│   ├── daily.py        # Daily meter frequency & energy
│   ├── weekly.py       # Weekly meter health & energy
│   └── data/           # Output: daily-{YYMMDD}.html, weekly-{YYMMDD}.html
├── solar/              # Solar & storage diagnostics
│   ├── solar.py        # Energy register analysis
│   └── data/           # Output: solar-{YYMMDD}.html
├── grid/               # Grid / source meter analysis
│   ├── data-source.py  # Source energy (import/export)
│   └── data/           # Output: data-source-{id}-{YYMMDD}.html
├── frequency/          # Data quality & rhythm
│   ├── frequency.py    # Reporting rhythm (console)
│   ├── gaps.py         # Gap analysis (CSV)
│   └── data/           # Output: gaps-{YYMMDD}.csv
└── definitions.md      # High-level project definitions
```

**Convention:** All scripts that generate HTML or CSV write output into a `data/` subfolder inside their own parent folder.

---

## Scripts & Features

### `meter/meter.py` — Single Meter Analysis

**Output:** `meter/data/meter-{id}-{YYMMDD}.html` (e.g. `meter-29-260202.html`)

Analyzes one meter for a single day from `site_power_*`:

- Total record count, data span, average interval
- Voltage status (LIVE vs DEAD)
- Bar chart of intervals between packets
- Raw data table: timestamp, delta, voltage, current, power
- Gap highlighting: green (14–16 min), orange (short), red (large gaps)

**Config:** `TARGET_METER_ID`, `TARGET_DATE_SUFFIX` (YYMMDD), `FULL_DATE_STR` (YYYY-MM-DD)

---

### `report/daily.py` — Daily Meter Frequency Report

**Output:** `report/data/daily-{YYMMDD}.html` (e.g. `daily-260201.html`)

Daily report across all meters:

- Summary: total meters, dead, low performance, perfect
- Target: 96 records/day (15‑min intervals)
- Energy discrepancy: supply vs accounted vs unaccounted loss
- Per‑meter table: uptime %, record count, avg interval, first/last seen

Energy sources: grid import, solar (29,31,56), storage discharge. Accounted: facility load, storage charge, grid export.

**Config:** `TARGET_DATE` (YYMMDD), `EXCLUDED_METER_IDS`, `GRID_METER_ID`, `SOLAR_METER_IDS`, `STORAGE_METER_ID`

---

### `report/weekly.py` — Weekly Meter Health Report

**Output:** `report/data/weekly-{YYMMDD}.html` (e.g. `weekly-260201.html`)

Weekly rollup (7 days from `START_DATE`):

- Energy discrepancy (weekly totals)
- Summary: meters monitored, network avg uptime, 100% reliable, dead
- Per‑meter table: weekly uptime, total records, 7‑day heatmap (Mon→Sun)
- Color coding: green (≥95%), yellow (≥50%), red (below), grey (no table/data)

**Config:** `START_DATE_STR` (YYYY-MM-DD), `DAYS_TO_ANALYZE`, `EXPECTED_DAILY_COUNT`

---

### `solar/solar.py` — Solar & Storage Energy Diagnostic

**Output:** `solar/data/solar-{YYMMDD}.html` (e.g. `solar-260201.html`)

Checks energy registers for meters 29, 30, 31, 56 over a date range:

- Delta (Max − Min) per day for ImpEP, ExpEP, TotalEP
- Helps verify which registers the hardware uses
- Meter roles: 29/31/56 = PV phases, 30 = energy storage

**Config:** `START_DATE_STR`, `DAYS_TO_ANALYZE`, `METER_IDS`, `METER_LABELS`

---

### `grid/data-source.py` — Source Energy Analysis

**Output:** `grid/data/data-source-{id}-{YYMMDD}.html` (e.g. `data-source-1-260203.html`)

Analysis of the utility source meter (default ID 1):

- Total import (kWh) for the day
- Meter reading range (start → end)
- Sample of billing columns: TotalEP, ImpEP, ExpEP, TotalEQ

Uses `site_energy_*` (cumulative energy) tables.

**Config:** `TARGET_ID`, `TARGET_DATE` (YYMMDD)

---

### `frequency/frequency.py` — Reporting Rhythm (Console)

**Output:** Console only (no file)

Finds an active meter and reports its interval statistics:

- Most common interval (mode)
- Min/max/mean gaps
- Suggests `EXPECTED_FREQ` (e.g. 5min, 15min, 60min) or adaptive logic

**Config:** `TARGET_DATE` (YYMMDD)

---

### `frequency/gaps.py` — Gap Analysis (CSV)

**Output:** `frequency/data/gaps-{YYMMDD}.csv` (e.g. `gaps-260202.csv`)

Data continuity per meter for one day:

- Status: PERFECT, HAS GAPS, or DEAD
- Gap count vs expected 5‑min intervals
- CSV columns: meter_id, name, status, gap_count

**Config:** `TARGET_DATE`, `EXPECTED_FREQ`

---

### `db/connect.py` — Database Connection Test

Connects to the `unified` database and prints a sample query.

---

### `db/schema.py` — Schema Export

**Output:** `full_schema_python.sql` (in `db/`)

Exports the full MySQL schema (tables and views) as SQL `CREATE` statements.

---

## Output Naming Convention

| Script       | Output File Pattern        | Example              |
|-------------|----------------------------|----------------------|
| meter       | `meter-{id}-{YYMMDD}.html` | `meter-29-260202.html` |
| daily       | `daily-{YYMMDD}.html`      | `daily-260201.html`    |
| weekly      | `weekly-{YYMMDD}.html`     | `weekly-260201.html`   |
| solar       | `solar-{YYMMDD}.html`      | `solar-260201.html`    |
| data-source | `data-source-{id}-{YYMMDD}.html` | `data-source-1-260203.html` |
| gaps        | `gaps-{YYMMDD}.csv`        | `gaps-260202.csv`      |

---

## Requirements

- Python 3.x
- `mysql-connector-python`
- `pandas`
- `matplotlib` (for meter interval charts)

---

## Database

- **Host:** localhost
- **Port:** 3307
- **Database:** `unified`
- **Tables:** `site_power_YYMMDD` (power), `site_energy_YYMMDD` (energy), `meter_info` (registry)
