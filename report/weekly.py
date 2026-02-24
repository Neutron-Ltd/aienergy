import os
import argparse
import mysql.connector
import pandas as pd
import warnings
import webbrowser
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
DB_HOST = "localhost"
DB_PORT = 3307
DB_USER = "energy_user"
DB_PASS = "REDACTED"
DB_NAME = "unified"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Smart Categorization
GRID_ID = 1
SOLAR_IDS = [29, 31, 56, 161, 167]
STORAGE_IDS = [30]
TRANSFORMER_IDS = [2, 32, 34] # Explicitly excluded just in case they aren't marked as parents

EXPECTED_DAILY_COUNT = 96

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Comprehensive Weekly Report: {start} to {end}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 0; }}
        h2 {{ color: #2c3e50; margin-top: 40px; padding-bottom: 10px; border-bottom: 2px solid #eee; font-size: 22px; }}
        h3.section-header {{ color: #34495e; font-size: 16px; margin-top: 30px; margin-bottom: 10px; padding-left: 10px; border-left: 4px solid #3498db; }}
        
        .date-range {{ background: #ebf5fb; color: #2980b9; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 20px; }}
        
        /* Analytics Cards */
        .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #e0e6ed; padding: 25px; border-radius: 8px; text-align: center; position: relative; overflow: hidden; }}
        .card h3 {{ margin: 0; font-size: 32px; color: #2c3e50; font-weight: 900; }}
        .card p {{ margin: 5px 0 0; color: #7f8c8d; font-weight: 700; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; }}
        .card.supply {{ border-top: 4px solid #3498db; }}
        .card.solar {{ border-top: 4px solid #f1c40f; }}
        .card.consume {{ border-top: 4px solid #27ae60; }}
        .card.loss {{ border-top: 4px solid #e74c3c; background-color: #fdf2f0; }}

        /* Network Health Cards */
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 20px; }}
        .scard {{ background: #f8f9fa; border: 1px solid #ddd; padding: 15px; border-radius: 6px; text-align: center; }}
        .scard h3 {{ margin: 0; font-size: 28px; color: #2c3e50; }}
        .scard p {{ margin: 5px 0 0; font-size: 12px; color: #95a5a6; font-weight: bold; text-transform: uppercase; }}

        .layout-grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 30px; margin-bottom: 40px; }}
        
        /* Tables */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 20px; }}
        th {{ background: #f8fafc; color: #475569; padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #e2e8f0; position: sticky; top: 0; }}
        td {{ padding: 12px; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: middle; }}
        tr:hover {{ background-color: #f8fafc; }}
        .text-right {{ text-align: right; }}
        .font-bold {{ font-weight: bold; }}
        .text-blue {{ color: #2980b9; }}

        /* Heatmap Grid */
        .week-grid {{ display: flex; gap: 4px; }}
        .day-box {{ width: 30px; height: 30px; border-radius: 3px; font-size: 10px; display: flex; align-items: center; justify-content: center; color: white; cursor: help; font-weight: bold; }}
        .s-perfect {{ background-color: #2ecc71; }} 
        .s-good {{ background-color: #f1c40f; color: #7f8c8d; }} 
        .s-bad {{ background-color: #e74c3c; }} 
        .s-empty {{ background-color: #ecf0f1; border: 1px solid #bdc3c7; color: #bdc3c7; }}

        .badge {{ padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; }}
        .bg-green {{ background: #d5f5e3; color: #1eb866; }}
        .bg-red {{ background: #fadbd8; color: #e74c3c; }}
        .bg-purple {{ background: #e8daef; color: #8e44ad; }}
        .bg-yellow {{ background: #fcf3cf; color: #d68910; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Comprehensive Executive Report</h1>
        <div class="date-range">📅 {start} &nbsp;&mdash;&nbsp; {end} ({days} Days)</div>

        <h2>1. Energy Balance & Discrepancy Analysis</h2>
        <div class="kpi-grid">
            <div class="card supply">
                <h3>{grid_import:,.0f} <span style="font-size:16px">kWh</span></h3>
                <p>Grid Import</p>
            </div>
            <div class="card solar">
                <h3>{solar_kwh:,.0f} <span style="font-size:16px">kWh</span></h3>
                <p>Est. Solar Generation</p>
            </div>
            <div class="card consume">
                <h3>{consume_kwh:,.0f} <span style="font-size:16px">kWh</span></h3>
                <p>Measured Terminal Load</p>
            </div>
            <div class="card loss">
                <h3 style="color: #c0392b;">{loss_kwh:,.0f} <span style="font-size:16px">kWh</span></h3>
                <p>Unaccounted Loss ({loss_pct}%)</p>
            </div>
        </div>

        <div class="layout-grid">
            <div>
                <h3 style="color:#2c3e50; font-size:18px;">🏆 Top 10 Energy Consumers</h3>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Meter Name</th>
                            <th class="text-right">Accounted Load (kWh)</th>
                            <th class="text-right">% of Facility</th>
                        </tr>
                    </thead>
                    <tbody>
                        {top_consumers}
                    </tbody>
                </table>
            </div>
            
            <div>
                <h3 style="color:#2c3e50; font-size:18px;">📊 Balance Breakdown</h3>
                <div style="background:#f8fafc; padding:20px; border-radius:8px; border:1px solid #e2e8f0;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:15px; border-bottom:1px dashed #cbd5e1; padding-bottom:5px;">
                        <span style="color:#64748b; font-weight:bold;">Total Energy Supplied</span>
                        <span style="font-weight:900; color:#334155;">{supply_kwh:,.0f} kWh</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-bottom:15px; padding-left:15px;">
                        <span style="color:#94a3b8;">↳ Utility Grid Import</span>
                        <span style="color:#334155;">{grid_import:,.0f} kWh</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-bottom:20px; padding-left:15px;">
                        <span style="color:#94a3b8;">↳ Solar Arrays</span>
                        <span style="color:#334155;">{solar_kwh:,.0f} kWh</span>
                    </div>
                    
                    <div style="display:flex; justify-content:space-between; margin-bottom:15px; border-bottom:1px dashed #cbd5e1; padding-bottom:5px; border-top:2px solid #e2e8f0; padding-top:15px;">
                        <span style="color:#64748b; font-weight:bold;">Total Accounted Energy</span>
                        <span style="font-weight:900; color:#27ae60;">{accounted_kwh:,.0f} kWh</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-bottom:15px; padding-left:15px;">
                        <span style="color:#94a3b8;">↳ Terminal End-Point Loads</span>
                        <span style="color:#27ae60;">{consume_kwh:,.0f} kWh</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-bottom:20px; padding-left:15px;">
                        <span style="color:#94a3b8;">↳ Solar Exported to Grid</span>
                        <span style="color:#27ae60;">{grid_export:,.0f} kWh</span>
                    </div>

                    <div style="display:flex; justify-content:space-between; margin-top:20px; background:#fdf2f0; padding:10px; border-radius:4px; border-left:4px solid #e74c3c;">
                        <span style="color:#c0392b; font-weight:bold;">Unaccounted Loss</span>
                        <span style="font-weight:900; color:#c0392b;">{loss_kwh:,.0f} kWh</span>
                    </div>
                </div>
            </div>
        </div>

        <h2>2. Device Diagnostics & Network Heatmap</h2>
        <div class="summary">
            <div class="scard"><h3>{total_meters}</h3><p>Meters Monitored</p></div>
            <div class="scard"><h3>{avg_uptime}%</h3><p>Network Avg Uptime</p></div>
            <div class="scard"><h3>{perfect_meters}</h3><p>Perfect Reporting</p></div>
            <div class="scard" style="border-left: 5px solid #e74c3c"><h3>{dead_meters}</h3><p>Offline / Dead</p></div>
        </div>

        <h3 class="section-header" style="border-color: #8e44ad;">🔌 Main Grid Utility</h3>
        <table>
            <thead>
                <tr>
                    <th width="5%">ID</th>
                    <th width="35%">Name</th>
                    <th width="15%">Weekly Uptime</th>
                    <th width="15%">Total Records</th>
                    <th width="30%">Reporting Rhythm Heatmap</th>
                </tr>
            </thead>
            <tbody>{heatmap_grid}</tbody>
        </table>

        <h3 class="section-header" style="border-color: #f1c40f;">☀️ Solar PV & Battery Storage</h3>
        <table>
            <thead>
                <tr>
                    <th width="5%">ID</th>
                    <th width="35%">Name</th>
                    <th width="15%">Weekly Uptime</th>
                    <th width="15%">Total Records</th>
                    <th width="30%">Reporting Rhythm Heatmap</th>
                </tr>
            </thead>
            <tbody>{heatmap_solar}</tbody>
        </table>

        <h3 class="section-header">🏭 Facility Loads & Machinery</h3>
        <table>
            <thead>
                <tr>
                    <th width="5%">ID</th>
                    <th width="35%">Name</th>
                    <th width="15%">Weekly Uptime</th>
                    <th width="15%">Total Records</th>
                    <th width="30%">Reporting Rhythm Heatmap</th>
                </tr>
            </thead>
            <tbody>{heatmap_facility}</tbody>
        </table>
    </div>
</body>
</html>
"""

def main(start_date_str, end_date_str):
    start_dt = datetime.strptime(start_date_str, "%y%m%d")
    end_dt = datetime.strptime(end_date_str, "%y%m%d")
    days = (end_dt - start_dt).days + 1
    
    date_suffixes = [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]
    date_strings = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    
    report_file = os.path.join(DATA_DIR, f"weekly-report-{start_date_str}-to-{end_date_str}.html")
    print(f"--- Running Unified Weekly Report: {start_date_str} to {end_date_str} ({days} Days) ---")

    try:
        conn = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)
        cursor = conn.cursor(dictionary=True)
        
        # --- PARENT / CHILD HIERARCHY FILTER ---
        cursor.execute("SELECT meter_number, meter_name, parent_meter_number FROM meter_info ORDER BY meter_number ASC")
        raw_meters = cursor.fetchall()
        
        meters = []
        parent_ids = set()
        for m in raw_meters:
            mid = int(m['meter_number'])
            pid = int(m.get('parent_meter_number') or 0)
            m['meter_number'] = mid
            m['parent_meter_number'] = pid
            
            # Map out all parent meters so we can exclude them from the sum
            if pid != 0:
                parent_ids.add(pid)
                
            meters.append(m)
            
        meta_dict = {m['meter_number']: m['meter_name'] for m in meters}
        
        daily_counts = {m['meter_number']: {d: 0 for d in date_strings} for m in meters}
        consumer_totals = {m['meter_number']: 0.0 for m in meters}
        grid_import = 0.0
        grid_export = 0.0
        total_solar_kwh = 0.0

        solar_ids_str = ",".join(map(str, SOLAR_IDS))

        for i, ds in enumerate(date_suffixes):
            d_str = date_strings[i]
            print(f"Aggregating data for {d_str}...")

            # --- A. HEATMAP COUNTS (Power Table) ---
            try:
                cursor.execute(f"SELECT meter_number, COUNT(*) as cnt FROM site_power_{ds} GROUP BY meter_number")
                for row in cursor.fetchall():
                    mid = int(row['meter_number'])
                    if mid in daily_counts: 
                        daily_counts[mid][d_str] = int(row['cnt'])
            except: pass

            # --- B. HEATMAP COUNTS (Dynamic Energy Table Fallback) ---
            try:
                cursor.execute(f"SELECT meter_number, COUNT(*) as cnt FROM site_energy_{ds} GROUP BY meter_number")
                for row in cursor.fetchall():
                    mid = int(row['meter_number'])
                    if mid in daily_counts and daily_counts[mid][d_str] == 0:
                        daily_counts[mid][d_str] = int(row['cnt'])
            except: pass

            # --- C. SOLAR GENERATION (Positive Power Integration) ---
            try:
                cursor.execute(f"SELECT meter_number, SUM(Pt) * 0.25 as yield_kwh FROM site_power_{ds} WHERE meter_number IN ({solar_ids_str}) AND Pt > 1 GROUP BY meter_number")
                for row in cursor.fetchall():
                    total_solar_kwh += float(row['yield_kwh'])
            except: pass

            # --- D. FACILITY ENERGY AGGREGATION ---
            try:
                # We use ImpEP consistently for facility loads just like the original script to prevent noise
                query = f"""
                    SELECT 
                        meter_number, 
                        (MAX(ImpEP) - MIN(ImpEP)) as d_imp,
                        (MAX(ExpEP) - MIN(ExpEP)) as d_exp
                    FROM site_energy_{ds} 
                    GROUP BY meter_number
                """
                cursor.execute(query)
                for row in cursor.fetchall():
                    mid = int(row['meter_number'])
                    d_imp = float(row['d_imp']) if row['d_imp'] else 0.0
                    d_exp = float(row['d_exp']) if row['d_exp'] else 0.0
                    
                    if mid == GRID_ID:
                        grid_import += d_imp
                        grid_export += d_exp
                        
                    # CRITICAL FIX: Ensure `mid not in parent_ids` to block Double-Counting!
                    elif mid not in SOLAR_IDS and mid not in STORAGE_IDS and mid not in parent_ids and mid not in TRANSFORMER_IDS:
                        if mid in consumer_totals:
                            consumer_totals[mid] += d_imp
            except: pass

        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return

    # --- BALANCE MATH ---
    total_consumed = sum(consumer_totals.values())
    total_supply = grid_import + total_solar_kwh
    total_accounted = total_consumed + grid_export
    
    unaccounted_loss = total_supply - total_accounted
    loss_pct = (unaccounted_loss / total_supply * 100) if total_supply > 0 else 0

    # --- TOP CONSUMERS HTML ---
    top_consumers_html = ""
    sorted_consumers = sorted(consumer_totals.items(), key=lambda x: x[1], reverse=True)[:10]
    
    for mid, kwh in sorted_consumers:
        if kwh <= 0: continue
        name = meta_dict.get(mid, 'Unknown')
        pct = (kwh / total_consumed * 100) if total_consumed > 0 else 0
        top_consumers_html += f"""
        <tr>
            <td class="font-bold">{mid}</td>
            <td>{name}</td>
            <td class="text-right text-blue font-bold">{kwh:,.1f}</td>
            <td class="text-right">{pct:.1f}%</td>
        </tr>
        """

    # --- HEATMAP HTML GENERATION & CATEGORIZATION ---
    heatmap_grid = ""
    heatmap_solar = ""
    heatmap_facility = ""
    
    target_weekly = EXPECTED_DAILY_COUNT * days
    sum_uptime = 0
    perfect_meters = 0
    dead_meters = 0
    
    for m in meters:
        mid = m['meter_number']
        m_name = m['meter_name']
        
        total_rec = 0
        heatmap_html = "<div class='week-grid'>"
        
        for d_str in date_strings:
            count = daily_counts[mid][d_str]
            total_rec += count
            
            tooltip = f"{d_str}: {count} records"
            if count == 0: css_class = "s-empty"; display_txt = "X"
            elif count >= (EXPECTED_DAILY_COUNT * 0.95): css_class = "s-perfect"; display_txt = str(count)
            elif count >= (EXPECTED_DAILY_COUNT * 0.5): css_class = "s-good"; display_txt = str(count)
            else: css_class = "s-bad"; display_txt = str(count)
            
            heatmap_html += f"<div class='day-box {css_class}' title='{tooltip}'>{display_txt}</div>"
        
        heatmap_html += "</div>"

        uptime = min((total_rec / target_weekly) * 100, 100.0) if target_weekly > 0 else 0
        sum_uptime += uptime

        if uptime >= 98: perfect_meters += 1
        if uptime == 0: dead_meters += 1
        
        if mid == GRID_ID:
            badge = f"<span class='badge bg-purple'>{uptime:.1f}%</span>"
        elif mid in SOLAR_IDS or mid in STORAGE_IDS:
            badge = f"<span class='badge bg-yellow'>{uptime:.1f}%</span>"
        elif uptime > 90:
            badge = f"<span class='badge bg-green'>{uptime:.1f}%</span>"
        else:
            badge = f"<span class='badge bg-red'>{uptime:.1f}%</span>"

        row_html = f"""
        <tr>
            <td><b>{mid}</b></td>
            <td>{m_name}</td>
            <td>{badge}</td>
            <td><b>{total_rec}</b> / {target_weekly}</td>
            <td>{heatmap_html}</td>
        </tr>
        """
        
        if mid == GRID_ID:
            heatmap_grid += row_html
        elif mid in SOLAR_IDS or mid in STORAGE_IDS:
            heatmap_solar += row_html
        else:
            heatmap_facility += row_html

    avg_uptime = round(sum_uptime / len(meters), 1) if len(meters) > 0 else 0

    # --- RENDER HTML ---
    html_out = HTML_TEMPLATE.format(
        start=start_dt.strftime("%b %d, %Y"),
        end=end_dt.strftime("%b %d, %Y"),
        days=days,
        target_weekly=target_weekly,
        grid_import=grid_import,
        grid_export=grid_export,
        solar_kwh=total_solar_kwh,
        accounted_kwh=total_accounted,
        consume_kwh=total_consumed,
        loss_kwh=unaccounted_loss,
        loss_pct=f"{loss_pct:.1f}",
        supply_kwh=total_supply,
        top_consumers=top_consumers_html,
        total_meters=len(meters),
        avg_uptime=avg_uptime,
        perfect_meters=perfect_meters,
        dead_meters=dead_meters,
        heatmap_grid=heatmap_grid,
        heatmap_solar=heatmap_solar,
        heatmap_facility=heatmap_facility
    )

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(f"✅ Success! Report generated: {report_file}")
    
    try:
        webbrowser.open('file://' + os.path.realpath(report_file))
    except:
        pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate comprehensive weekly analytics and heatmap.")
    parser.add_argument("--start", type=str, required=True, help="Start Date (YYMMDD, e.g., 260201)")
    parser.add_argument("--end", type=str, required=True, help="End Date (YYMMDD, e.g., 260207)")
    
    args = parser.parse_args()
    main(args.start, args.end)