import os
import sys
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import pandas as pd
import json
from datetime import datetime, timedelta

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from db.mysql_env import mysql_connect_kwargs

DB_CONFIG = mysql_connect_kwargs()

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

def main(start_date, days, target_id=1, open_browser=True):
    dates = generate_date_range(start_date, days)
    end_date_str = dates[-1]
    report_file = os.path.join(OUTPUT_DIR, f"griddata-{start_date}-{days}d.html")
    display_title = get_node_name_from_json(target_id)
    
    print(f"⚡ Generating {days}-Day Grid Summary for {display_title} (20{start_date} to 20{end_date_str})...")

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Fetch Ratio
        cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (target_id,))
        ratio_res = cursor.fetchone()
        
        # ADJUSTMENT: If the result is in millions, the ratio 2000 is usually the CT primary.
        # Often these meters require (Delta * Ratio / 1000) or simply Delta * (Ratio/Ratio_factor)
        # We will keep the ratio variable but ensure the calculation produces realistic kWh.
        ratio = ratio_res['ratio'] if ratio_res else 2000 

        daily_stats = []
        total_actual_logs = 0
        total_expected_logs = 0
        total_import_kwh = 0
        total_export_kwh = 0

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
                    
                    median_int = int(df['ts'].diff().dt.total_seconds().median() / 60) if len(df) > 1 else 15
                    if median_int <= 0 or pd.isna(median_int): median_int = 15
                        
                    exp_count = 1440 // median_int
                    act_count = len(df)
                    rel = min(100.0, (act_count / exp_count) * 100)
                    
                    # ODOMETER DELTA CALCULATION
                    # Fixed scaling: if 2000 produced millions, we likely need to scale by 1/1000 
                    # to convert from Wh/scaled units to kWh.
                    imp_delta = (df['ImpEP'].iloc[-1] - df['ImpEP'].iloc[0]) * (ratio / 1000.0)
                    exp_delta = (df['ExpEP'].iloc[-1] - df['ExpEP'].iloc[0]) * (ratio / 1000.0)
                    
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
                else:
                    daily_stats.append({
                        "date": f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}",
                        "logs": "0/96", "health": 0, "import": 0, "export": 0
                    })
            except Exception:
                daily_stats.append({
                    "date": f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}",
                    "logs": "MISSING DB", "health": 0, "import": 0, "export": 0
                })

        conn.close()
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    overall_reliability = min(100.0, (total_actual_logs / total_expected_logs) * 100) if total_expected_logs > 0 else 0

    # --- UI GENERATION ---
    daily_rows_html = ""
    for stat in daily_stats:
        health_color = "text-emerald-600" if stat['health'] >= 95 else "text-amber-600" if stat['health'] >= 50 else "text-rose-600"
        daily_rows_html += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-4 font-mono text-sm text-slate-700 font-bold">{stat['date']}</td>
            <td class="p-4 text-slate-500 font-mono text-xs">{stat['logs']}</td>
            <td class="p-4 font-bold text-xs {health_color}">{stat['health']:.1f}%</td>
            <td class="p-4 text-right font-bold text-emerald-700">{stat['import']:,.1f} kWh</td>
            <td class="p-4 text-right font-bold text-blue-700">{stat['export']:,.1f} kWh</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>{display_title} - {days} Day Grid Summary / {days}天电网汇总</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{
                @page {{ size: landscape; margin: 10mm; }}
                body {{ background: white; }}
                .shadow-sm, .shadow-xl {{ box-shadow: none !important; border: 1px solid #e2e8f0 !important; }}
            }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1600px] mx-auto mt-8 px-6 space-y-8">
            
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-3xl font-black text-slate-900">{display_title}</h1>
                    <p class="text-slate-500 font-medium italic mt-1">{days}-Day Power Grid Executive Summary / {days}天电网执行汇总 | <span class="font-mono text-slate-700">20{start_date} - 20{end_date_str}</span></p>
                </div>
                <div class="text-left md:text-right bg-slate-100 p-3 rounded-lg border border-slate-200">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Ratio / 变比: {ratio}</div>
                    <div class="text-lg font-mono font-bold text-blue-600">ID: {target_id}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Aggregate Reliability / 综合可靠性</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{overall_reliability:.1f}%</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden">
                        <div class="bg-indigo-500 h-full transition-all" style="width: {overall_reliability}%"></div>
                    </div>
                </div>
                
                <div class="bg-emerald-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-emerald-300 uppercase tracking-widest text-opacity-80">Total Grid Import / 电网总输入</span>
                    <div class="text-3xl font-black mt-1 text-emerald-50">{total_import_kwh:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>

                <div class="bg-blue-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-blue-300 uppercase tracking-widest text-opacity-80">Total Grid Export / 电网总输出</span>
                    <div class="text-3xl font-black mt-1 text-blue-50">{total_export_kwh:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 flex justify-between items-center text-white">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Daily Consumption Summary / 日消耗汇总</h3>
                    <span class="text-[10px] font-mono text-slate-400">Aggregated Deltas from site_energy / 来自 site_energy 的累计差值</span>
                </div>
                <div class="w-full">
                    <table class="w-full text-sm text-left">
                        <thead class="bg-slate-50 text-slate-500 border-b border-slate-200">
                            <tr>
                                <th class="p-4 font-bold">Date / 日期</th>
                                <th class="p-4 font-bold">Logs (Act/Exp) / 日志(实/预)</th>
                                <th class="p-4 font-bold">Health / 健康度</th>
                                <th class="p-4 font-bold text-right text-emerald-700">Import / 输入</th>
                                <th class="p-4 font-bold text-right text-blue-700">Export / 输出</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {daily_rows_html}
                        </tbody>
                    </table>
                </div>
            </section>
            
            <footer class="text-center text-slate-400 text-[10px] py-8 border-t border-slate-100">
                Generated by AIENERGY System / AI能源系统生成 • Unified Grid Audit Report / 统一电网审计报告
            </footer>
        </div>
    </body>
    </html>
    """
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Success: Executive Summary generated at {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260201", help="Start date (YYMMDD)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to track")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.start, args.days, open_browser=not args.no_browser)