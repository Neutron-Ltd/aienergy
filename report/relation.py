import os
import mysql.connector
import json
import warnings
import webbrowser

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
REPORT_FILE = os.path.join(DATA_DIR, "relation.html")

# --- HTML & ECHARTS TEMPLATE (Utility Grid Standard Layout) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>电网计量标准体系表 - 厂区拓扑</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{ background-color: #fcfcfc; }}
        #chart-container {{ width: 100%; height: 85vh; background: #fff; border: 1px solid #d1d5db; }}
        .chart-header {{ border-bottom: 3px solid #006655; }} /* Utility Grid Green Accent */
    </style>
</head>
<body class="p-4 md:p-10">
    <div class="max-w-full mx-auto">
        <header class="chart-header pb-4 mb-6 text-center">
            <h1 class="text-3xl font-serif font-bold text-slate-800 tracking-widest uppercase">
                电网计量标准体系表 (厂区能源在线监测)
            </h1>
            <div class="flex justify-center gap-10 mt-4 text-sm font-medium text-slate-500">
                <span>监测节点总数: <b class="text-slate-900">{total_nodes}</b></span>
                <span>层级深度: <b class="text-slate-900">{levels} 级</b></span>
                <span>导出日期: {date}</span>
            </div>
        </header>

        <div id="chart-container" class="rounded-sm shadow-inner"></div>
        
        <footer class="mt-4 flex justify-between text-[10px] text-slate-400 uppercase tracking-tighter">
            <span>Metrology Standard System Chart - Industrial EMS</span>
            <span>Unclassified / Internal Use Only</span>
        </footer>
    </div>

    <script>
        var chartDom = document.getElementById('chart-container');
        var myChart = echarts.init(chartDom);
        
        const treeData = {tree_json};

        myChart.setOption({{
            tooltip: {{ 
                trigger: 'item', 
                triggerOn: 'mousemove',
                backgroundColor: 'rgba(255, 255, 255, 0.95)',
                borderWidth: 1,
                borderColor: '#006655',
                textStyle: {{ color: '#333', fontSize: 12 }}
            }},
            series: [{{
                type: 'tree',
                data: [treeData],
                top: '10%',
                left: '2%',
                bottom: '15%',
                right: '2%',
                layout: 'orthogonal',
                orient: 'TB', 
                symbol: 'rect',
                symbolSize: [120, 34],
                edgeShape: 'polyline',
                edgeForkPosition: '60%',
                initialTreeDepth: 2,
                
                lineStyle: {{
                    color: '#006655',
                    width: 1.5,
                    opacity: 0.6
                }},
                
                label: {{
                    position: 'inside',
                    align: 'center',
                    verticalAlign: 'middle',
                    fontSize: 10,
                    fontWeight: 'bold',
                    color: '#fff',
                    formatter: function(params) {{
                        let name = params.data.name_only || '';
                        if(name.length > 15) name = name.substring(0,13) + '..';
                        return params.data.value + '\\n' + name;
                    }}
                }},

                leaves: {{
                    label: {{
                        position: 'bottom',
                        rotate: -90, // Vertical stacking for terminal nodes
                        align: 'right',
                        verticalAlign: 'middle',
                        color: '#475569',
                        fontSize: 9,
                        distance: 15,
                        formatter: '{{b}}'
                    }},
                    itemStyle: {{ 
                        color: '#e2e8f0', 
                        borderColor: '#94a3b8',
                        borderWidth: 1
                    }}
                }},

                emphasis: {{
                    focus: 'descendant',
                    itemStyle: {{
                        shadowBlur: 10,
                        shadowColor: 'rgba(0,0,0,0.3)'
                    }}
                }},

                expandAndCollapse: true,
                animationDuration: 600
            }}]
        }});

        window.addEventListener('resize', () => myChart.resize());
    </script>
</body>
</html>
"""

def get_node_style(name, mid):
    """Applies official Utility Grid color coding."""
    # Main Grid (Root) - Deep Utility Grid Green
    if mid == 1: 
        return "#006655" 
    # Primary Transformers & Mains - Corporate Blue
    if any(x in name.lower() for x in ['变压', 'trans', '总', 'main']): 
        return "#004a99" 
    # Green Energy / Solar - Amber/Gold
    if any(x in name.lower() for x in ['光伏', 'solar', '储能']): 
        return "#d68910" 
    # Standard Load - Slate Blue
    return "#334155"

def build_standard_tree():
    try:
        conn = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT meter_number, meter_name, parent_meter_number FROM meter_info")
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")
        return None, 0

    # 1. Map all items
    nodes = {}
    for r in rows:
        mid = int(r['meter_number'])
        pid = int(r['parent_meter_number']) if r['parent_meter_number'] else 0
        name = r['meter_name']
        
        nodes[mid] = {
            "name": f"[{mid}] {name}",
            "name_only": name,
            "value": mid,
            "itemStyle": {"color": get_node_style(name, mid)},
            "children": []
        }

    # 2. Link Parents
    roots = []
    for r in rows:
        mid = int(r['meter_number'])
        pid = int(r['parent_meter_number']) if r['parent_meter_number'] else 0
        
        if pid != 0 and pid in nodes and pid != mid:
            nodes[pid]["children"].append(nodes[mid])
        else:
            roots.append(nodes[mid])

    # Enforce Meter 1 as the absolute Metrology Source
    grid_root = next((r for r in roots if r['value'] == 1), None)
    
    if not grid_root:
        # Create a virtual "Utility Grid Source" if ID 1 isn't top-level
        final_tree = {
            "name": "电网供电点",
            "name_only": "电网",
            "value": "SOURCE",
            "itemStyle": {"color": "#006655"},
            "children": roots
        }
    else:
        # If there are other roots, they are orphans; attach them to Grid root for the chart
        other_roots = [r for r in roots if r['value'] != 1]
        grid_root["children"].extend(other_roots)
        final_tree = grid_root

    return final_tree, len(rows)

def main():
    print("--- Generating Metrology System Chart ---")
    tree_data, count = build_standard_tree()
    if not tree_data: return

    html_out = HTML_TEMPLATE.format(
        tree_json=json.dumps(tree_data, ensure_ascii=False),
        total_nodes=count,
        levels=4,
        date=datetime.now().strftime("%Y-%m-%d")
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html_out)
    
    print(f"✅ Formal System Chart generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    from datetime import datetime
    main()