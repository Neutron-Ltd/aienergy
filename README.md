
# AI Energy — Industrial Energy management tools

Tools for processing and reporting smart meter and energy register data from the Unified Energy Management System.

---

## Project Layout (current)

```
aienergy/
├── db/                 # Database utilities and schema exports
│   ├── connect.py      # Connection test
│   ├── info.py         # DB info helpers
│   ├── permissions.py  # Permission helpers
│   ├── schema.py       # Schema export
│   └── data/           # Any DB-related outputs
├── nodes/              # Processing nodes (scripts that produce reports)
│   ├── column.py
│   ├── endmeters.py
│   ├── endmetersdata.py
│   ├── endmetersping.py
│   ├── grid.py
│   ├── griddata.py
│   ├── meterinfo.py
│   ├── meterping.py
│   ├── report.py
│   ├── solar.py
│   ├── solardata.py
│   └── data/           # Generated HTML/CSV outputs from node scripts
├── report/             # Saved report bundles (e.g. FEB-W1)
├── column.json
├── relation.html
├── relation.json
└── README.md
```

**Convention:** Node scripts write their generated HTML/CSV files into the `nodes/data/` folder (or into a `data/` sibling inside the node's folder).

---

## Key scripts (nodes)

- `nodes/endmeters.py` / `nodes/endmetersdata.py` — end-meter rollups and HTML data outputs.
- `nodes/endmetersping.py` / `nodes/meterping.py` — ping/availability checks and continuity reports.
- `nodes/grid.py` / `nodes/griddata.py` — utility/grid meter read and daily totals.
- `nodes/solar.py` / `nodes/solardata.py` — solar/register diagnostics and delta checks.
- `nodes/meterinfo.py` — meter registry / metadata helpers.
- `nodes/report.py` — orchestration scripts for composing aggregated reports.
- `nodes/column.py` — column definitions / JSON export helpers.

Run a script directly with Python, for example:

```bash
python nodes/report.py
```

Check the top of each node script for configuration constants (date targets, meter IDs) or adapt them to accept CLI/ENV args.

---

## db utilities

- `db/connect.py` — quick connection test to the MySQL `unified` database.
- `db/info.py` — helpers for reading registry and schema information.
- `db/schema.py` — exports SQL CREATE statements for tables/views.

---

## Output naming

Most generated files follow the YYMMDD/date-based naming convention; examples you may see in `nodes/data/`:

- `meter-{id}-{YYMMDD}.html`
- `daily-{YYMMDD}.html`
- `weekly-{YYMMDD}.html`
- `solar-{YYMMDD}.html`
- `data-source-{id}-{YYMMDD}.html`
- `gaps-{YYMMDD}.csv`

---

## Requirements

- Python 3.8+
- `mysql-connector-python`
- `pandas`
- `matplotlib` (optional for charts)

Install with pip:

```bash
pip install -r requirements.txt
```

If `requirements.txt` is not present, install packages individually.

---

## Database (example)

- Host: localhost
- Port: 3307
- Database: `unified`
- Typical tables: `site_power_YYMMDD` (power), `site_energy_YYMMDD` (energy), `meter_info`

Adjust connection settings in `db/connect.py` or via environment variables before running node scripts.

---

## Next steps

- Verify any scripts that expect previous folder layout (e.g., `meter/` vs `nodes/`) and update imports or paths as needed.

