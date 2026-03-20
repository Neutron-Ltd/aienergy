# Energy “difference” in the balance report

This note explains why the **Energy Balance Report** (`nodes/report.py`) shows a large positive **Energy Difference** (e.g. ~**+12,000 kWh** over seven days for **2026-03-01 → 2026-03-07**), and how that maps to data and topology—not “lost” database rows.

## What the report computes

\[
\text{Headline difference} = (\text{Grid import} + \text{Solar}) - \text{Consumption}
\]

| Term | Source | Method |
|------|--------|--------|
| **Grid import** | `site_energy_YYMMDD`, meter **1** | Odometer delta `ImpEP` (last − first) × `ratio / 1000` |
| **Grid export** | Same table | Same for `ExpEP` (shown in report, **not** subtracted from the headline supply) |
| **Solar** | `site_power_YYMMDD` | Σ`Pt` × `ratio` × (`interval_min` / 60) for PV nodes in `relation.json` (categories containing `pv` / `solar`) |
| **Consumption** | `site_power_YYMMDD` | Same integration for **leaf** meters only, excluding IDs **`[56, 29, 31, 30]`** |

Implementation mirrors `nodes/endmetersuse.py` and `nodes/report.py`.

## Excluded meters (important)

| ID | Name (summary) | Role in report |
|----|----------------|----------------|
| **56, 29, 31** | Phase 1 / Phase 3 PV | Counted under **Solar**, **not** under **Consumption** (avoids double-counting generation as load) |
| **30** | Pylontech storage | **Not** in consumption sum; any sustained charge/discharge on this branch is **outside** the end-meter total |

PV exclusions are **by design**. Storage exclusion means ESS-related power that only appears on meter **30** does not appear in “Consumption.”

## Findings from a representative 7-day window (20260301–07)

Numbers align with `missing/difference.py` and the generated `nodes/data/report-260301-7d.html`.

1. **Headline gap** ≈ **+12,019 kWh** — supply (import + solar) minus summed end-meter consumption.

2. **Grid export** ≈ **3,360 kWh**  
   The headline formula uses **Import + Solar** and does **not** subtract export. Energy that left the site via the export register contributes roughly **~28%** of the headline “surplus” for this week. A tighter mental model is:
   \[
   \text{Supply}_{\text{net}} \approx \text{Grid import} + \text{Solar} - \text{Grid export}
   \]
   then compare to consumption.

3. **After netting export**  
   **Gap vs net supply** ≈ **+8,659 kWh** still remains versus end-meter consumption alone.

4. **Energy storage (meter 30)**  
   Signed integration of `Pt` over the same period was on the order of **~96 kWh** — **far smaller** than ~12,000 kWh. So for this interval, **ESS does not explain the bulk of the gap**; it is still correct to treat storage as a separate branch that is excluded from the consumption rollup.

5. **Unmetered topology in `relation.json`**  
   There are **23** named nodes (loads or groupings) **without an `id`**. They never appear in `site_power_*` rollups, so their energy is **not** in the consumption total. Examples include kitchen, canteen/utility bundles, outdoor lighting, elevators, residential feeder, fast charging, parts of production hierarchy, and many fire/backup circuits. **Collectively, unmetered loads are the main hypothesis for the remaining gap**, together with normal losses and any metering outside the leaf set.

## What this is *not*

- It is **not** proof that ~12,000 kWh “vanished” from the database.
- It is **not** mainly explained by ESS for the week analyzed (meter 30 magnitude was small).
- Missing **log rows** (~98% reliability in the report) are a separate, smaller effect than **whole branches with no meter id**.

## Tooling

Run the reconciler from the repo root (after installing deps, e.g. `mysql-connector-python`, `pandas`):

```bash
python missing/difference.py --start 260301 --days 7
```

Optional: `--grid-id 1` for the grid odometer meter.

The script writes an HTML report (same Tailwind look as `nodes/data/report-*.html`) to:

`missing/data/differance-20{YYMMDD}-{days}d.htm`

Example: `missing/data/differance-20260301-7d.htm`. The terminal only prints that file path.

## Suggested next steps (operational)

1. **Assign `id` values** in `relation.json` for unmetered nodes where physical meters exist, and confirm rows in `site_power_*` / `meter_info`.
2. **Re-run** `difference.py` and **`nodes/report.py`** to see the headline gap shrink when those loads enter the leaf consumption set.
3. If you need a **strict site balance**, define whether **export** and **storage net** belong in the same equation as import + onsite solar, and extend the report formula accordingly rather than interpreting the raw headline alone.

---

*Generated for the AIENERGY project; numbers for specific weeks may change when re-run against live DB.*
