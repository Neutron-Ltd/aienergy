import os
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import pandas as pd
import json
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

# IDs to explicitly exclude from consumption reports (Solar and Storage)
EXCLUDED_IDS = [56, 29, 31, 30]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_end_meters(node, end_meter_list=None):
    """Recursively extracts ONLY nodes with no sub_nodes, excluding specific IDs and ID-less nodes."""
    if end_meter_list is None: end_meter_list = []
    
    sub_nodes = node.get("sub_nodes", [])
    node_id = node.get("id")

    if not sub_nodes:
        # Check if node has a valid ID and is not in the exclusion list
        if node_id is not None and node_id not in EXCLUDED_IDS:
            end_meter_list.append({
                "id": node_id,
                "name": node.get("name", f"Node {node_id}"),
                "category": node.get("category", "Unknown")
            })
    else:
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
    return end_meter_list

def generate_date_range(start_date_str, days=7):
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]

def main(start_date, days):
    dates = generate_date_range(start_date, days)
    report_file = os.path.join(OUTPUT_DIR, f"endmeterdata-{start_date}-{days}d.html")
    
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        end_meters = get_end_meters(tree_data.get("root_node", {}))
    except Exception as e:
        print(f"❌ JSON Error: {e}"); return

    if not end_meters:
        print("❌ No valid end meters found after filtering."); return

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"❌ DB Error: {e}"); return

    # Data matrix and aggregates
    results = {m['id']: {d: {"kwh": 0.0, "health": 0.0} for d in dates} for m in end_meters}
    total_factory_kwh = 0.0
    total_logs_act = 0
    total_logs_exp = 0

    for date_str in dates:
        print(f"⌛ Processing 20{date_str}...")
        table = f"site_power_{date_str}"
        for meter in end_meters:
            mid = meter['id']
            # Fetch Ratio
            cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (mid,))
            r_res = cursor.fetchone()
            ratio = r_res['ratio'] if r_res else 1

            query = f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC"
            try:
                cursor.execute(query, (mid,))
                df = pd.DataFrame(cursor.fetchall())
                if not df.empty:
                    df['ts'] = pd.to_datetime(df['ts'])
                    median_int = df['ts'].diff().dt.total_seconds().median() / 60 or 15
                    if median_int <= 0: median_int = 15
                    
                    # Energy Integration Math: Pt snapshot to kWh
                    daily_kwh = (pd.to_numeric(df['Pt']).sum() * ratio) * (median_int / 60.0)
                    exp_count = 1440 // median_int
                    act_count = len(df)
                    
                    results[mid][date_str] = {"kwh": daily_kwh, "health": (act_count/exp_count)*100}
                    total_factory_kwh += daily_kwh
                    total_logs_act += act_count
                    total_logs_exp += exp_count
            except: pass

    conn.close()
    overall_rel = (total_logs_act / total_logs_exp * 100) if total_logs_exp > 0 else 0

    # --- UI GENERATION ---
    date_headers = "".join([f"<th class='p-3 border-b text-center text-[10px] font-bold text-slate-500 bg-slate-50'>{d}</th>" for d in dates])
    rows_html = ""
    for meter in end_meters:
        cells = ""
        for d in dates:
            data = results[meter['id']][d]
            h_color = "text-emerald-600 font-bold" if data['health'] > 95 else "text-amber-600" if data['health'] > 0 else "text-slate-300"
            cells += f"""<td class='p-2 border-b text-center'><div class='text-xs font-bold'>{data['kwh']:,.1f}</div><div class='text-[8px] font-mono {h_color}'>{data['health']:.0f}%</div></td>"""
        rows_html += f"""<tr class='hover:bg-slate-50 transition-colors bg-white'><td class='p-3 border-b font-mono text-[10px] text-slate-400'>ID:{meter['id']}</td><td class='p-3 border-b text-xs font-bold text-slate-700 leading-tight'>{meter['name']}</td><td class='p-3 border-b text-[10px] text-slate-400 uppercase'>{meter['category']}</td>{cells}</tr>"""

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - End Meter Weekly Consumption</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{ @page {{ size: landscape; margin: 10mm; }} body {{ background: white; }} .shadow-sm, .shadow-xl {{ box-shadow: none !important; border: 1px solid #e2e8f0 !important; }} }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1600px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div><h1 class="text-3xl font-black">Machine Consumption Matrix</h1><p class="text-slate-500 font-medium italic mt-1">{days}-Day End-Use Audit | Sources Excluded | <span class="font-mono">20{start_date} - Range</span></p></div>
                <div class="text-right"><div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">End Meters Scanned</div><div class="text-xl font-black text-indigo-600">{len(end_meters)} Machines</div></div>
            </header>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Aggregate Reliability</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{overall_rel:.1f}%</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden"><div class="bg-indigo-500 h-full" style="width: {overall_rel}%"></div></div>
                </div>
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-indigo-300 uppercase tracking-widest text-opacity-80">Total Factory Spending ({days}d)</span>
                    <div class="text-4xl font-black mt-1 text-white">{total_factory_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                </div>
            </div>
            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-900 p-4 text-white flex justify-between items-center"><h3 class="font-bold text-xs uppercase tracking-widest">Daily Consumption Matrix</h3><span class="text-[10px] text-slate-400 font-mono">Formula: Pt Integration (Sources Excluded)</span></div>
                <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse min-w-max">
                    <thead><tr><th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 w-16">Node</th><th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50">Machine Name</th><th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50">Category</th>{date_headers}</tr></thead>
                    <tbody class="divide-y divide-slate-100">{rows_html}</tbody>
                </table>
                </div>
            </section>
        </div>
    </body>
    </html>"""
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Weekly End-Meter Report generated: {report_file}")
    webbrowser.open(Path(report_file).resolve().as_uri())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260201")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(); main(args.start, args.days)