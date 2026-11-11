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
from db.mysql_env import TABLE_PREFIX, mysql_connect_kwargs

DB_CONFIG = mysql_connect_kwargs()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_renewable_nodes(node, target_list=None):
    """Recursively searches relation.json for Solar PV nodes only."""
    if target_list is None: target_list = []
    category = node.get("category", "").lower()
    if any(k in category for k in ["pv", "solar"]):
        if "id" in node:
            target_list.append({
                "id": node["id"], 
                "name": node.get("name", f"Node {node['id']}"),
                "type": "Solar PV"
            })
    for sub in node.get("sub_nodes", []):
        get_renewable_nodes(sub, target_list)
    return target_list

def generate_date_range(start_date_str, days=7):
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]

def main(start_date, days, open_browser=True):
    dates = generate_date_range(start_date, days)
    end_date_str = dates[-1]
    report_file = os.path.join(OUTPUT_DIR, f"solardata-{start_date}-{days}d.html")
    
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        target_nodes = get_renewable_nodes(tree_data.get("root_node", {}))
    except Exception as e:
        print(f"❌ JSON Error: {e}"); return

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"❌ DB Error: {e}"); return

    # Data Structure and Aggregates
    results = {n['id']: {d: {"yield": 0, "health": 0} for d in dates} for n in target_nodes}
    total_weekly_solar = 0
    total_logs_act = 0
    total_logs_exp = 0

    for date_str in dates:
        print(f"⌛ Processing 20{date_str}...")
        table = f"{TABLE_PREFIX}_power_{date_str}"
        for node in target_nodes:
            nid = node['id']
            cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (nid,))
            r_res = cursor.fetchone()
            ratio = r_res['ratio'] if r_res else 1

            query = f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC"
            try:
                cursor.execute(query, (nid,))
                df = pd.DataFrame(cursor.fetchall())
                if not df.empty:
                    df['ts'] = pd.to_datetime(df['ts'])
                    median_int = df['ts'].diff().dt.total_seconds().median() / 60 or 15
                    if median_int <= 0: median_int = 15
                    
                    # Pt -> kWh Integration
                    daily_kwh = (pd.to_numeric(df['Pt']).sum() * ratio) * (median_int / 60.0)
                    
                    exp_count = 1440 // median_int
                    act_count = len(df)
                    health = (act_count / exp_count) * 100
                    
                    results[nid][date_str] = {"yield": daily_kwh, "health": health}
                    total_weekly_solar += daily_kwh
                    
                    total_logs_act += act_count
                    total_logs_exp += exp_count
            except: pass

    conn.close()
    overall_rel = (total_logs_act / total_logs_exp * 100) if total_logs_exp > 0 else 0

    # --- UI GENERATION ---
    date_headers = "".join([f"<th class='p-3 border-b text-center text-[10px] font-bold text-slate-500 bg-slate-50'>{d}</th>" for d in dates])
    rows_html = ""
    for node in target_nodes:
        cells = ""
        for d in dates:
            data = results[node['id']][d]
            h_color = "text-emerald-600" if data['health'] > 95 else "text-amber-600" if data['health'] > 0 else "text-slate-300"
            cells += f"""<td class='p-2 border-b text-center'><div class='text-xs font-bold'>{data['yield']:,.1f}</div><div class='text-[9px] font-mono {h_color}'>{data['health']:.0f}%</div></td>"""
        rows_html += f"""<tr class='hover:bg-slate-50'><td class='p-3 border-b font-mono text-[10px] text-slate-400'>ID:{node['id']}</td><td class='p-3 border-b text-xs font-bold text-slate-700 leading-tight'>{node['name']}</td>{cells}</tr>"""

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - Solar Generation Summary / 太阳能发电汇总</title>
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
                <div><h1 class="text-3xl font-black">Solar Generation / 太阳能发电</h1><p class="text-slate-500 font-medium italic mt-1">{days}-Day Executive Summary / {days}天执行汇总 | <span class="font-mono">20{start_date} - 20{end_date_str}</span></p></div>
                <div class="bg-slate-100 p-3 rounded-lg border border-slate-200 text-right"><div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Nodes Tracked / 监测节点</div><div class="text-xl font-black text-blue-600">{len(target_nodes)} Devices / 设备</div></div>
            </header>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Aggregate Reliability / 综合可靠性</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{overall_rel:.1f}%</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden"><div class="bg-emerald-500 h-full" style="width: {overall_rel}%"></div></div>
                </div>
                <div class="bg-emerald-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-emerald-300 uppercase tracking-widest text-opacity-80">Total Solar Generation / 太阳能总发电量</span>
                    <div class="text-3xl font-black mt-1 text-emerald-50">{total_weekly_solar:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
            </div>
            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-900 p-4 text-white flex justify-between items-center"><h3 class="font-bold text-xs uppercase tracking-widest">{days}-Day Matrix Summary / {days}天矩阵汇总</h3><span class="text-[10px] text-slate-400">Values: Daily Yield (kWh) / Log % / 值: 日发电量(kWh) / 日志%</span></div>
                <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse min-w-max">
                    <thead><tr><th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 w-16">Node / 节点</th><th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50">Device Name / 设备名称</th>{date_headers}</tr></thead>
                    <tbody class="divide-y divide-slate-100">{rows_html}</tbody>
                </table>
                </div>
            </section>
            <footer class="text-center text-slate-400 text-[10px] py-4">Generated by AIENERGY System / AI能源系统生成 • Solar Generation Report / 太阳能发电报告</footer>
        </div>
    </body>
    </html>"""
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Executive Solar Summary generated: {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260201")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.start, args.days, open_browser=not args.no_browser)