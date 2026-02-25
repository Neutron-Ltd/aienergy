import os
import json
import webbrowser
from pathlib import Path

# --- DIRECTORY CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "meterinfo.html")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# IDs that should NOT be tagged as end meters (Sources/Storage)
SOURCE_IDS = [56, 29, 31, 30]

def get_category_style(category):
    cat = str(category).lower()
    if 'transformer' in cat: return "bg-purple-100 text-purple-700 border-purple-200"
    if 'distribution_hub' in cat: return "bg-blue-100 text-blue-700 border-blue-200"
    if 'pv_generation' in cat: return "bg-emerald-100 text-emerald-700 border-emerald-200"
    if 'safety' in cat: return "bg-rose-100 text-rose-700 border-rose-200"
    if 'lighting' in cat: return "bg-amber-100 text-amber-700 border-amber-200"
    if 'main_load' in cat: return "bg-indigo-100 text-indigo-700 border-indigo-200"
    if 'charging' in cat: return "bg-cyan-100 text-cyan-700 border-cyan-200"
    if 'residential' in cat: return "bg-teal-100 text-teal-700 border-teal-200"
    if 'branch' in cat: return "bg-slate-100 text-slate-700 border-slate-300"
    return "bg-slate-100 text-slate-600 border-slate-200"

def generate_nested_table(node, level=0):
    node_id = node.get('id', 'N/A')
    name = node.get('name', 'Unknown Node')
    category = node.get('category', 'root')
    sub_nodes = node.get('sub_nodes', [])
    
    bg_color = "bg-white" if level == 0 else "bg-slate-50/30"
    border_left = "border-l-4 border-l-blue-500" if level == 0 else "border-l-2 border-l-slate-300"
    cat_style = get_category_style(category)
    
    is_end_meter = len(sub_nodes) == 0 and node_id not in SOURCE_IDS
    meter_badge = "<span class='ml-2 px-1.5 py-0.5 text-[8px] bg-slate-800 text-white rounded uppercase'>End Meter</span>" if is_end_meter else ""
    
    # UI COMPRESSION: Reduced padding (p-1.5) and removed margin-bottom (mb-0)
    html = f"""
    <table class="w-full text-xs text-left border border-slate-200 rounded overflow-hidden mb-0.5 shadow-sm {bg_color}">
        <tbody>
            <tr class="hover:bg-slate-100 transition-colors">
                <td class="p-1.5 w-16 font-mono text-[10px] text-slate-400 {border_left}">ID: {node_id}</td>
                <td class="p-1.5 font-bold text-slate-700">{name} {meter_badge}</td>
                <td class="p-1.5 w-32 text-right">
                    <span class="px-2 py-0.5 text-[9px] uppercase font-bold rounded-full border {cat_style}">
                        {category}
                    </span>
                </td>
            </tr>
    """
    
    if sub_nodes:
        # UI COMPRESSION: Reduced left-indent (pl-6) and vertical padding (py-1)
        html += """
            <tr>
                <td colspan="3" class="p-0 border-t border-slate-100">
                    <div class="pl-6 py-1 pr-1 bg-slate-50 border-l border-slate-200">
        """
        for sub in sub_nodes:
            html += generate_nested_table(sub, level + 1)
        html += """
                    </div>
                </td>
            </tr>
        """
        
    html += "</tbody></table>"
    return html

def main():
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        root_node = data.get("root_node", {})
    except Exception as e:
        print(f"❌ Error loading JSON: {e}"); return

    topology_html = generate_nested_table(root_node, level=0)

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>AIENERGY - Meter Network Hierarchy</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @media print {{
                @page {{ size: portrait; margin: 10mm; }}
                body {{ background: white; }}
                .shadow-sm, .shadow-xl {{ box-shadow: none !important; border: 1px solid #e2e8f0 !important; }}
            }}
            table {{ page-break-inside: avoid; }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1600px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200">
                <h1 class="text-3xl font-black text-slate-900">Meter Network Hierarchy</h1>
                <p class="text-slate-500 font-medium italic mt-1">Filtered Sources | High-Density View</p>
            </header>
            <div class="bg-white p-4 rounded-2xl shadow-sm border border-slate-200">
                {topology_html}
            </div>
        </div>
    </body>
    </html>
    """

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f: f.write(html_content)
    print(f"✅ High-Density Topology Report generated: {OUTPUT_FILE}")
    webbrowser.open(Path(OUTPUT_FILE).resolve().as_uri())

if __name__ == "__main__":
    main()