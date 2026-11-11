# AI Energy: Industrial Energy Management Tools

Tools for analyzing smart-meter telemetry from an industrial energy management system (EMS): grid import/export, solar generation, end-meter consumption, data-quality checks, and ML-based imputation of missing readings, all behind a bilingual (EN/中文) web dashboard.

## Features

- **Grid report:** daily import/export from cumulative energy registers.
- **Solar report:** PV generation integrated from power readings.
- **End-meter reports:** status matrix, logging continuity (actual vs expected records), and kWh consumption per meter.
- **Energy balance:** (grid import + solar) vs (consumption + grid export), with data-quality statistics.
- **ML imputation:** per-meter scikit-learn models that fill gaps in readings using the metering topology.
- **Dashboard:** run any report for a date range, browse past reports, and train or run models from the browser.

## Requirements

- Python 3.10+
- MySQL with the expected tables (see [Database layout](#database-layout))

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env               # then fill in passwords and DB settings
cp relation.example.json relation.json   # then describe your own meter hierarchy
set -a; source .env; set +a
python dashboard.py
```

The dashboard runs at <http://localhost:5050>. If `AIENERGY_PASSWORD` is not set, a random password is generated and printed at startup.

### Docker

```bash
cp .env.example .env               # FLASK_SECRET_KEY, AIENERGY_PASSWORD and MYSQL_PASSWORD are required
cp relation.example.json relation.json
docker compose up --build
```

To reach a MySQL server on the Docker host, use `MYSQL_HOST=host.docker.internal` (the default in compose).

## Configuration

All settings come from environment variables (see [.env.example](.env.example)):

| Variable | Purpose | Default |
|---|---|---|
| `FLASK_SECRET_KEY` | Session signing key | random per start |
| `AIENERGY_USER` / `AIENERGY_PASSWORD` | Dashboard login | `aienergy` / random |
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` | Database connection | `localhost`, `3306`, `energy_user`, (required), `unified` |
| `TABLE_PREFIX` | Prefix of daily telemetry tables | `site` |
| `ANALYZE_TIMEOUT_SECONDS` | Timeout for report generation | 300 (1200 for ML) |
| `MODEL_ARTIFACT_DIR` | Where trained models are stored | `LinearRegression/` |

## Metering topology

`relation.json` describes the meter hierarchy (grid → transformers → distribution hubs → loads). Each node has an `id` matching `meter_number` in the database, a `name`, a `category`, and optional `sub_nodes`. Categories containing `pv`/`solar` are treated as solar generation. Leaf nodes are treated as end meters. See [relation.example.json](relation.example.json).

The solar/storage meter IDs excluded from consumption (`EXCLUDED_IDS`) and the grid meter ID (`--grid-id`, default `1`) are set at the top of the scripts in `nodes/`.

## Database layout

- `meter_info`: meter registry (`meter_number`, ratio, type, …)
- `{TABLE_PREFIX}_power_YYMMDD`: one table per day of power readings (`ts`, `meter_number`, `Pt`, `Uan`, …)
- `{TABLE_PREFIX}_energy_YYMMDD`: one table per day of cumulative energy registers (`ts`, `meter_number`, `ImpEP`, `ExpEP`, `TotalEP`)

`python db/schema.py` exports the CREATE statements of your own database into `db/data/`.

## Project layout

```
dashboard.py        Flask dashboard (entry point)
templates/          Dashboard HTML templates
nodes/              Report generators; output goes to nodes/data/
model/              Topology engine, DB handler, ML predictor and imputer
db/                 Connection, schema export and permission helpers
relation.example.json   Sample metering topology
```

Report scripts can also be run directly, for example `python nodes/griddata.py --help`.

## Security

See [SECURITY.md](SECURITY.md). Never commit `.env`, `relation.json`, generated reports, or trained models. They are git-ignored by default.

## License

[MIT](LICENSE)
