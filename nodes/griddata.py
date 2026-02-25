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

def generate_date_range(start_date_str, days=7):
    """Generates a list of YYMMDD strings for the target period."""
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]

def main(start_date, days, target_id=1):
    dates = generate_date_range(start_date, days)
    end_date_str = dates[-1]
    report_file = os.path.join(OUTPUT_DIR, f"griddata-{start_date}-{days}d.html")
    display_title = get_node_name_from_json(target_id)
    
    print(f"⚡ Generating {days}-Day Grid Audit for {display_title} (20{start_date} to 20{end_date_str})...")

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Fetch Ratio
        cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (target_id,))
        ratio_res = cursor.fetchone()
        ratio = ratio_res['ratio'] if ratio_res else 2000 

        all_logs = []
        daily_stats = []
        
        # Track overall metrics
        total_actual_logs = 0
        total_expected_logs = 0
        total_import_kwh = 0
        total_export_kwh = 0
        global_median_interval = 15

        # 2. Fetch Data Day by Day
        for date_str in dates:
            table_name = f"site_energy_{date_str}"
            try:
                query = f"""
                    SELECT ts, ImpEP, ExpEP, TotalEP 
                    FROM {table_name} 
                    WHERE meter_number = %s 
                    ORDER BY ts ASC
                """
                cursor.execute(query, (target_id,))
                rows = cursor.fetchall()
                df = pd.DataFrame(rows)
                
                if not df.empty:
                    df['ts'] = pd.to_datetime(df['ts'])
                    df['date_str'] = date_str  # Tag for grouping
                    
                    median_int = int(df['ts'].diff().dt.total_seconds().median() / 60) if len(df) > 1 else 15
                    if median_int == 0 or pd.isna(median_int): median_int = 15
                        
                    exp_count = 1440 // median_int
                    act_count = len(df)
                    rel = min(100.0, (act_count / exp_count) * 100)
                    
                    imp_delta = (df['ImpEP'].iloc[-1] - df['ImpEP'].iloc[0]) * ratio
                    exp_delta = (df['ExpEP'].iloc[-1] - df['ExpEP'].iloc[0]) * ratio
                    
                    total_actual_logs += act_count
                    total_expected_logs += exp_count
                    total_import_kwh += imp_delta
                    total_export_kwh += exp_delta
                    
                    daily_stats.append({
                        "date": f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}",
                        "logs": f"{act_count}/{exp_count}",
                        "health": rel,
                        "import": imp_delta,
                        "export": exp_delta
                    })
                    
                    all_logs.append(df)
                else:
                    daily_stats.append({
                        "date": f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}",
                        "logs": "0/96",
                        "health": 0,
                        "import": 0,
                        "export": 0
                    })
            except Exception as e:
                # Table might not exist
                daily_stats.append({
                    "date": f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}",
                    "logs": "MISSING DB",
                    "health": 0,
                    "import": 0,
                    "export": 0
                })

        conn.close()
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    # --- AGGREGATE LOGIC ---
    if total_expected_logs > 0:
        overall_reliability = min(100.0, (total_actual_logs / total_expected_logs) * 100)
    else:
        overall_reliability = 0

    # --- UI GENERATION: DAILY SUMMARY TABLE ---
    daily_rows_html = ""
    for stat in daily_stats:
        health_color = "text-emerald-600" if stat['health'] >= 95 else "text-amber-600" if stat['health'] >= 50 else "text-rose-600"
        daily_rows_html += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-3 font-mono text-sm text-slate-700 font-bold">{stat['date']}</td>
            <td class="p-3 text-slate-500 font-mono">{stat['logs']}</td>
            <td class="p-3 font-bold {health_color}">{stat['health']:.1f}%</td>
            <td class="p-3 text-right font-bold text-emerald-700">{stat['import']:,.0f} kWh</td>
            <td class="p-3 text-right font-bold text-blue-700">{stat['export']:,.0f} kWh</td>
        </tr>
        """

    # --- UI GENERATION: MASTER LOG TABLE ---
    master_rows_html = ""
    if all_logs:
        master_df = pd.concat(all_logs).sort_values(by='ts', ascending=True)
        
        current_day = None
        for i, r in master_df.iterrows():
            day_str = r['ts'].strftime('%Y-%m-%d')
            
            # Insert a visual separator when the day changes
            if day_str != current_day:
                master_rows_html += f"""
                <tr class="bg-slate-200/50 border-y border-slate-300">
                    <td colspan="5" class="p-2 font-bold text-xs text-slate-600 uppercase tracking-widest text-center">--- {day_str} ---</td>
                </tr>"""
                current_day = day_str

            real_imp = r['ImpEP'] * ratio
            real_exp = r['ExpEP'] * ratio
            
            master_rows_html += f"""
            <tr class="hover:bg-slate-50 border-b border-slate-100 transition-all">
                <td class="p-3 font-mono text-xs text-slate-500">{r['ts'].strftime('%H:%M:%S')}</td>
                <td class="p-3 text-slate-700 font-mono text-right">{r['ImpEP']:,.1f}</td>
                <td class="p-3 text-right font-bold text-emerald-700 bg-emerald-50/30">{real_imp:,.0f} kWh</td>
                <td class="p-3 text-slate-700 font-mono text-right">{r['ExpEP']:,.1f}</td>
                <td class="p-3 text-right font-bold text-blue-700 bg-blue-50/30">{real_exp:,.0f} kWh</td>
            </tr>"""

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>{display_title} - {days} Day Grid Audit</title>
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
                    <p class="text-slate-500 font-medium italic mt-1">{days}-Day Power Grid Incomer Audit | <span class="font-mono text-slate-700">20{start_date} - 20{end_date_str}</span></p>
                </div>
                <div class="text-left md:text-right bg-slate-100 p-3 rounded-lg border border-slate-200">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Master Hardware ID</div>
                    <div class="text-lg font-mono font-bold text-blue-600">ID: {target_id} <span class="text-slate-400 mx-1">|</span> Ratio: {ratio}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Overall Reliability</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{overall_reliability:.1f}%</div>
                    <div class="text-xs text-slate-500 mt-1">{total_actual_logs} / {total_expected_logs} Logs Captured</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden">
                        <div class="bg-indigo-500 h-full transition-all" style="width: {overall_reliability}%"></div>
                    </div>
                </div>
                
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl border border-slate-800 text-white md:col-span-3">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">Aggregate Grid Import ({days}d)</span>
                            <div class="text-4xl font-black mt-1 text-emerald-400">{total_import_kwh:,.0f} <span class="text-lg font-light text-slate-300">kWh</span></div>
                        </div>
                        <div class="text-right">
                            <span class="text-[10px] font-bold text-blue-400 uppercase tracking-widest">Aggregate Grid Export ({days}d)</span>
                            <div class="text-3xl font-black mt-1 text-blue-400">{total_export_kwh:,.0f} <span class="text-base font-light text-slate-300">kWh</span></div>
                        </div>
                    </div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200">
                <div class="bg-indigo-50 p-4 border-b border-indigo-100 rounded-t-2xl flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase text-indigo-800 tracking-widest">Daily Consumption Summary</h3>
                    <span class="text-[10px] font-mono text-indigo-500">Aggregated Deltas</span>
                </div>
                <div class="w-full">
                    <table class="w-full text-sm text-left">
                        <thead class="bg-white text-slate-500 border-b border-slate-200">
                            <tr>
                                <th class="p-4 font-bold">Date</th>
                                <th class="p-4 font-bold">Logs (Act/Exp)</th>
                                <th class="p-4 font-bold">Health</th>
                                <th class="p-4 font-bold text-right text-emerald-700">Daily Import</th>
                                <th class="p-4 font-bold text-right text-blue-700">Daily Export</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {daily_rows_html}
                        </tbody>
                    </table>
                </div>
            </section>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200">
                <div class="bg-slate-50 p-4 border-b border-slate-200 rounded-t-2xl flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase text-slate-700 tracking-widest">Complete Odometer Log History</h3>
                    <span class="text-[10px] font-mono text-slate-400">Targeting {days} Table Shards</span>
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
                            {master_rows_html if master_rows_html else "<tr><td colspan='5' class='p-12 text-center text-slate-400 font-medium'>No cumulative energy data found for this range.</td></tr>"}
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
    parser.add_argument("--start", type=str, default="260201", help="Start date (YYMMDD)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to track")
    args = parser.parse_args()
    main(args.start, args.days)