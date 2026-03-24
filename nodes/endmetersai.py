import os
import sys
import argparse
import webbrowser
from pathlib import Path
import json
from datetime import datetime

import mysql.connector
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.mysql_env import mysql_connect_kwargs
from model.ml_predictor import NodeAwareMLPredictor
from model.topology_engine import TopologyEngine

# IDs to explicitly exclude from end-meter reports (Solar and Storage)
EXCLUDED_IDS = [56, 29, 31, 30]
DB_CONFIG = mysql_connect_kwargs()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_end_meters(node, end_meter_list=None):
    """Recursively extracts ONLY leaf nodes (no sub_nodes), excluding IDs above."""
    if end_meter_list is None:
        end_meter_list = []

    sub_nodes = node.get("sub_nodes", [])
    node_id = node.get("id")

    if not sub_nodes:
        if node_id is not None and node_id not in EXCLUDED_IDS:
            end_meter_list.append({
                "id": int(node_id),
                "name": node.get("name", f"Node {node_id}"),
                "category": node.get("category", "Unknown"),
            })
    else:
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
    return end_meter_list


def integrate_daily_kwh(df: pd.DataFrame, ratio: float) -> tuple[float, float]:
    """Match endmetersuse integration logic using raw sampling interval."""
    if df.empty:
        return 0.0, 0.0
    tmp = df.copy()
    tmp["ts"] = pd.to_datetime(tmp["ts"])
    median_int = tmp["ts"].diff().dt.total_seconds().median() / 60 or 15
    if median_int <= 0:
        median_int = 15
    daily_kwh = (pd.to_numeric(tmp["Pt"], errors="coerce").fillna(0.0).sum() * ratio) * (median_int / 60.0)
    exp_count = max(1, int(1440 // median_int))
    health = (len(tmp) / exp_count) * 100.0
    return float(daily_kwh), float(health)


def main(date_suffix, model_dir=None, open_browser=True):
    report_file = os.path.join(OUTPUT_DIR, f"endmetersai-{date_suffix}.html")
    print(f"Starting End Meter AI Prediction for 20{date_suffix}...")
    artifacts_dir = Path(model_dir).resolve() if model_dir else (_ROOT / "model_artifacts").resolve()
    os.environ["MODEL_ARTIFACT_DIR"] = str(artifacts_dir)
    print(f"Model artifacts directory: {artifacts_dir}")

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            tree_data = json.load(f)
        end_nodes = get_end_meters(tree_data.get("root_node", {}))
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    if not end_nodes:
        print("❌ No valid end meters found after filtering.")
        return

    try:
        target_day = datetime.strptime(f"20{date_suffix}", "%Y%m%d")
    except ValueError:
        print("❌ Invalid date. Expected YYMMDD.")
        return

    start_dt = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = target_day.replace(hour=23, minute=59, second=59, microsecond=0)
    target_index = pd.date_range(start=start_dt, end=end_dt, freq="15min")

    try:
        topology = TopologyEngine(Path(JSON_FILE))
        predictor = NodeAwareMLPredictor(topology=topology)
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        table = f"site_power_{date_suffix}"

        # Main grid context sampled to 15-minute index.
        cursor.execute(f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC", (1,))
        root_df = pd.DataFrame(cursor.fetchall())
        main_grid_load = None
        if not root_df.empty:
            root_df["ts"] = pd.to_datetime(root_df["ts"])
            root_series = pd.Series(pd.to_numeric(root_df["Pt"], errors="coerce").values, index=root_df["ts"]).sort_index()
            main_grid_load = root_series.reindex(target_index).ffill().fillna(0.0)
    except Exception as e:
        print(f"❌ Initialization Error: {e}")
        return

    rows = []
    total_actual_kwh = 0.0
    total_pred_kwh = 0.0
    loaded_models = 0

    for node in end_nodes:
        nid = node["id"]
        try:
            cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (nid,))
            ratio_row = cursor.fetchone()
            ratio = float(ratio_row["ratio"]) if ratio_row and ratio_row.get("ratio") is not None else 1.0

            cursor.execute(f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC", (nid,))
            raw_df = pd.DataFrame(cursor.fetchall())
            actual_kwh, health = integrate_daily_kwh(raw_df, ratio=ratio)
        except Exception:
            raw_df = pd.DataFrame(columns=["ts", "Pt"])
            actual_kwh = 0.0
            health = 0.0

        pred_kwh = 0.0
        model_state = "missing"
        try:
            if predictor.load_trained_model(nid):
                loaded_models += 1
                pred_series = predictor.predict(
                    node_id=nid,
                    target_index=target_index,
                    main_grid_load=main_grid_load,
                    neighboring_pv=None,
                )
                pred_kwh = float(pd.to_numeric(pred_series, errors="coerce").fillna(0.0).sum() * (15 / 60.0))
                model_state = "ok"
        except Exception:
            pred_kwh = 0.0
            model_state = "error"

        total_actual_kwh += actual_kwh
        total_pred_kwh += pred_kwh
        rows.append({
            "id": nid,
            "name": node["name"],
            "actual_kwh": actual_kwh,
            "pred_kwh": pred_kwh,
            "health": health,
            "model_state": model_state,
        })

    variance_pct = ((total_pred_kwh - total_actual_kwh) / total_actual_kwh * 100.0) if total_actual_kwh > 0 else 0.0
    variance_color = "text-rose-600" if variance_pct > 0 else "text-emerald-600"
    variance_sign = "+" if variance_pct > 0 else ""

    table_rows = ""
    for r in rows:
        model_badge = (
            "<span class='px-2 py-0.5 rounded bg-emerald-50 text-emerald-600 text-[9px] font-bold'>MODEL</span>"
            if r["model_state"] == "ok"
            else "<span class='px-2 py-0.5 rounded bg-slate-100 text-slate-400 text-[9px] font-bold'>NO MODEL</span>"
            if r["model_state"] == "missing"
            else "<span class='px-2 py-0.5 rounded bg-amber-50 text-amber-600 text-[9px] font-bold'>MODEL ERR</span>"
        )
        health_color = "text-emerald-600" if r["health"] >= 95 else "text-amber-600" if r["health"] > 0 else "text-slate-300"
        table_rows += f"""
        <tr class="hover:bg-slate-50 transition-colors bg-white">
            <td class="p-3 border-b text-[10px] text-slate-500 font-mono">ID:{r['id']}</td>
            <td class="p-3 border-b text-xs font-semibold text-slate-700">{r['name']}</td>
            <td class="p-3 border-b text-right text-xs font-bold text-slate-800">{r['actual_kwh']:,.1f}</td>
            <td class="p-3 border-b text-right text-xs font-bold text-indigo-600">{r['pred_kwh']:,.1f}</td>
            <td class="p-3 border-b text-center text-[10px]"><span class="{health_color} font-mono">{r['health']:.0f}%</span></td>
            <td class="p-3 border-b text-center">{model_badge}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - End Meter AI / 末端电表AI预测</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{ @page {{ size: landscape; margin: 10mm; }} body {{ background: white; }} }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1400px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h1 class="text-3xl font-black">End Meter AI Spending / 末端电表AI电量</h1>
                    <p class="text-slate-500 font-medium italic mt-1">Date / 日期: <span class="font-mono">20{date_suffix}</span> • AI Model Comparison / AI模型对比</p>
                </div>
                <div class="text-right">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Models Loaded / 已加载模型</div>
                    <div class="text-xl font-black text-indigo-600">{loaded_models} / {len(rows)}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Actual Total Spending / 实际总用电</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{total_actual_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                </div>
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-indigo-300 uppercase tracking-widest text-opacity-80">AI Predicted Spending / AI预测总用电</span>
                    <div class="text-3xl font-black mt-1">{total_pred_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Prediction Variance / 预测偏差</span>
                    <div class="text-3xl font-black mt-1 {variance_color}">{variance_sign}{variance_pct:.1f}%</div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-900 p-4 text-white flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Actual vs AI by End Meter / 每台设备实际与预测</h3>
                    <span class="text-[10px] text-slate-400 font-mono">Columns: node, machine name, actual use, AI prediction</span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse min-w-max">
                        <thead>
                            <tr>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 w-16">Node / 节点</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50">Machine Name / 设备名称</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-right">Actual Use / 实际用电 (kWh)</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-right">AI Prediction / AI预测 (kWh)</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-center">Data Health / 数据完整度</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-center">Model / 模型</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{table_rows}</tbody>
                    </table>
                </div>
            </section>

            <footer class="text-center text-slate-400 text-[10px] py-4">Generated by AIENERGY System / AI能源系统生成 • End Meter AI Report / 末端电表AI报告</footer>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    try:
        conn.close()
    except Exception:
        pass
    print(f"✅ End Meter AI report generated: {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260301", help="Target date (YYMMDD)")
    parser.add_argument("--model-dir", type=str, default=None, help="Optional model artifacts directory")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.date, model_dir=args.model_dir, open_browser=not args.no_browser)
