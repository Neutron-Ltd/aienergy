import os
import argparse
import mysql.connector
import pandas as pd
import json
from datetime import datetime, time, timedelta

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Assumes relation.json is one level up from the script location
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_node_name_from_json(target_id):
    """Searches for relation.json to extract the accurate grid node name."""
    possible_paths = [
        os.path.join(SCRIPT_DIR, "relation.json"),
        os.path.join(SCRIPT_DIR, "..", "relation.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "relation.json")
    ]
    json_path = next((p for p in possible_paths if os.path.exists(p)), None)
    
    if not json_path: return f"Main Grid (Node {target_id})"

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        def find_name(node, tid):
            if node.get("id") == tid: return node.get("name")
            for sub in node.get("sub_nodes", []):
                res = find_name(sub, tid)
                if res: return res
            return None
            
        return find_name(data.get("root_node", {}), target_id) or f"Node {target_id}"
    except:
        return f"Node {target_id}"

def main(date_suffix, target_id=1):
    report_file = os.path.join(OUTPUT_DIR, f"grid-{date_suffix}.html")
    display_title = get_node_name_from_json(target_id)
    print(f"⚡ Generating Main Grid Audit for {display_title} on 20{date_suffix}...")

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Fetch Ratio
        cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (target_id,))
        ratio_res = cursor.fetchone()
        ratio = ratio_res['ratio'] if ratio_res else 2000 # Fallback to known Grid ratio

        # 2. Fetch Cumulative Energy Logs (We grab the whole table shard to catch the midnight crossover)
        query = f"""
            SELECT ts, ImpEP, ExpEP, TotalEP 
            FROM site_energy_{date_suffix} 
            WHERE meter_number = %s 
            ORDER BY ts ASC
        """
        cursor.execute(query, (target_id,))
        df = pd.DataFrame(cursor.fetchall())
        
        conn.close()
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    # --- MATH & CONTINUITY LOGIC ---
    if not df.empty:
        df['ts'] = pd.to_datetime(df['ts'])
        
        # Auto-detect interval
        if len(df) > 1:
            median_interval = int(df['ts'].diff().dt.total_seconds().median() / 60)
        else:
            median_interval = 15
            
        expected_count = 1440 // median_interval
        actual_count = len(df)
        reliability = min(100.0, (actual_count / expected_count) * 100)
        
        # Odometer Math: Calculate actual consumption via deltas
        raw_import_delta = df['ImpEP'].iloc[-1] - df['ImpEP'].iloc[0]
        raw_export_delta = df['ExpEP'].iloc[-1] - df['ExpEP'].iloc[0]
        
        daily_import_kwh = raw_import_delta * ratio
        daily_export_kwh = raw_export_delta * ratio
    else:
        median_interval = expected_count = actual_count = reliability = 0
        daily_import_kwh = daily_export_kwh = 0

    # --- UI GENERATION ---
    rows_html = ""
    if not df.empty:
        for i, r in df.iterrows():
            gap_badge = ""
            if i > 0:
                gap = (r['ts'] - df.iloc[i-1]['ts']).total_seconds() / 60
                if gap > (median_interval * 1.5):
                    gap_badge = f"<span class='ml-2 text-[10px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded border border-red-200'>Gap: {int(gap)}m</span>"
            
            real_imp = r['ImpEP'] * ratio
            real_exp = r['ExpEP'] * ratio
            
            rows_html += f"""
            <tr class="hover:bg-slate-50 border-b border-slate-200 transition-all">
                <td class="p-3 font-mono text-xs text-slate-500">{r['ts'].strftime('%Y-%m-%d %H:%M:%S')} {gap_badge}</td>
                <td class="p-3 text-slate-700 font-mono text-right">{r['ImpEP']:,.1f}</td>
                <td class="p-3 text-right font-bold text-emerald-700 bg-emerald-50/30">{real_imp:,.0f} kWh</td>
                <td class="p-3 text-slate-700 font-mono text-right">{r['ExpEP']:,.1f}</td>
                <td class="p-3 text-right font-bold text-blue-700 bg-blue-50/30">{real_exp:,.0f} kWh</td>
            </tr>"""

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>{display_title} - Grid Audit</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @media print {{
                @page {{ size: landscape; margin: 10mm; }}
                body {{ background: white; }}
                .shadow-sm {{ box-shadow: none; border-color: #e2e8f0; }}
            }}
        </style>
    </head>
    <body class="bg-slate-50 min-h-screen p-4 md:p-8 text-slate-900 font-sans">
        <div class="max-w-6xl mx-auto space-y-8">
            
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-3xl font-black text-slate-900">{display_title}</h1>
                    <p class="text-slate-500 font-medium italic mt-1">Primary Power Grid Incomer Audit | <span class="font-mono text-slate-700">20{date_suffix}</span></p>
                </div>
                <div class="text-left md:text-right bg-slate-100 p-3 rounded-lg border border-slate-200">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Master Hardware ID</div>
                    <div class="text-lg font-mono font-bold text-blue-600">ID: {target_id} <span class="text-slate-400 mx-1">|</span> Ratio: {ratio}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">System Reliability</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{reliability:.1f}%</div>
                    <div class="text-xs text-slate-500 mt-1">{actual_count} / {expected_count} Logs Captured</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden">
                        <div class="bg-indigo-500 h-full transition-all" style="width: {reliability}%"></div>
                    </div>
                </div>
                
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl border border-slate-800 text-white md:col-span-2">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">Total Grid Import (24h)</span>
                            <div class="text-4xl font-black mt-1 text-emerald-400">{daily_import_kwh:,.0f} <span class="text-lg font-light text-slate-300">kWh</span></div>
                            <span class="text-[10px] text-slate-400 block mt-2">Odometer Delta: {(df['ImpEP'].iloc[-1] - df['ImpEP'].iloc[0]) if not df.empty else 0:,.1f}</span>
                        </div>
                        <div class="text-right">
                            <span class="text-[10px] font-bold text-blue-400 uppercase tracking-widest">Total Grid Export (24h)</span>
                            <div class="text-3xl font-black mt-1 text-blue-400">{daily_export_kwh:,.0f} <span class="text-base font-light text-slate-300">kWh</span></div>
                            <span class="text-[10px] text-slate-400 block mt-2">Odometer Delta: {(df['ExpEP'].iloc[-1] - df['ExpEP'].iloc[0]) if not df.empty else 0:,.1f}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Data Gap Interval</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{median_interval} <span class="text-xl text-slate-400 font-normal">min</span></div>
                    <span class="text-[10px] text-slate-400 font-medium">Detection Frequency</span>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200">
                <div class="bg-slate-50 p-4 border-b border-slate-200 rounded-t-2xl flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase text-slate-700 tracking-widest">Odometer Log History</h3>
                    <span class="text-[10px] font-mono text-slate-400">Source Table: site_energy_{date_suffix}</span>
                </div>
                
                <div class="w-full">
                    <table class="w-full text-sm text-left">
                        <thead class="bg-slate-50 text-slate-500 border-b border-slate-200">
                            <tr>
                                <th class="p-4 font-bold">Log Timestamp</th>
                                <th class="p-4 font-bold text-right text-slate-400">Raw ImpEP</th>
                                <th class="p-4 font-bold text-right text-emerald-700">Real Import (kWh)</th>
                                <th class="p-4 font-bold text-right text-slate-400">Raw ExpEP</th>
                                <th class="p-4 font-bold text-right text-blue-700">Real Export (kWh)</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {rows_html if rows_html else "<tr><td colspan='5' class='p-12 text-center text-slate-400 font-medium'>No cumulative energy data found for this date.</td></tr>"}
                        </tbody>
                    </table>
                </div>
            </section>
            
            <footer class="text-center text-slate-400 text-xs py-8">
                Generated by AIENERGY System • Print optimized
            </footer>
        </div>
    </body>
    </html>
    """
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Success: Report generated at {report_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260201", help="Target date (YYMMDD)")
    args = parser.parse_args()
    main(args.date)