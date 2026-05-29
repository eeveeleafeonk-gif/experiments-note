import streamlit as st
import pandas as pd
import requests
import re
import numpy as np
import json

st.set_page_config(layout="wide")
st.title("🧪 研究室用 統合型モル計算＆化合物検索プラットフォーム")

# --- ヘルパー関数 ---
def to_float(val):
    if pd.isna(val) or val == "" or val is None or str(val).strip() in ["", "計算不能", "不明"]:
        return None
    try:
        return float(val)
    except:
        return None

def format_val(val, fmt):
    if val is None or pd.isna(val) or val == "":
        return None
    if isinstance(val, str):
        return val
    try:
        return fmt.format(val)
    except:
        return str(val)

def extract_temp(text):
    text = str(text)
    match_c = re.search(r'([-+]?\d*\.?\d+)\s*(?:°|deg)?\s*C', text, re.IGNORECASE)
    if match_c: return float(match_c.group(1))
    match_f = re.search(r'([-+]?\d*\.?\d+)\s*(?:°|deg)?\s*F', text, re.IGNORECASE)
    if match_f: return round((float(match_f.group(1)) - 32) * 5.0 / 9.0, 1)
    match = re.search(r'[-+]?\d*\.\d+|\d+', text)
    return float(match.group()) if match else None

def extract_density(text):
    match = re.search(r'[-+]?\d*\.\d+|\d+', str(text))
    return float(match.group()) if match else None

def df_to_markdown_safe(df, cols):
    sub_df = df[cols].copy()
    if sub_df.empty: return ""
    sub_df = sub_df.fillna("")
    md = "| " + " | ".join(cols) + " |\n"
    md += "|" + "|".join(["---"] * len(cols)) + "|\n"
    for _, row in sub_df.iterrows():
        md += "| " + " | ".join([str(x) for x in row.tolist()]) + " |\n"
    return md

# --- 1. セッション状態の初期化 ---
if 'df_reagents' not in st.session_state:
    st.session_state.df_reagents = pd.DataFrame(columns=[
        "分類", "主原料", "試薬名", "分子量", "密度(g/mL)", "融点(℃)", "沸点(℃)", "当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"
    ])
if 'df_solvents' not in st.session_state:
    st.session_state.df_solvents = pd.DataFrame(columns=[
        "分類", "試薬名", "分子量", "密度(g/mL)", "融点(℃)", "沸点(℃)", "設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"
    ])
if 'df_products' not in st.session_state:
    st.session_state.df_products = pd.DataFrame(columns=[
        "分類", "試薬名", "分子量", "融点(℃)", "沸点(℃)", "当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"
    ])

# --- 💾 サイドバー：データの保存と読み込み ---
with st.sidebar:
    st.header("💾 レシピの保存と読込")
    st.write("現在の表の状態をファイルに保存したり、過去のデータを復元したりできます。")
    
    data_to_save = {
        "reagents": st.session_state.df_reagents.to_dict(orient="records"),
        "solvents": st.session_state.df_solvents.to_dict(orient="records"),
        "products": st.session_state.df_products.to_dict(orient="records")
    }
    json_str = json.dumps(data_to_save, ensure_ascii=False, indent=2)
    st.download_button(
        label="⬇️ 現在の表をファイルに保存 (.json)",
        data=json_str,
        file_name="lab_recipe.json",
        mime="application/json",
        use_container_width=True
    )
    
    st.markdown("---")
    
    uploaded_file = st.file_uploader("📂 保存したレシピを読み込む", type=["json"])
    if uploaded_file is not None:
        if st.button("📥 読み込みを実行 (現在の表は上書きされます)", use_container_width=True, type="primary"):
            try:
                loaded_data = json.load(uploaded_file)
                st.session_state.df_reagents = pd.DataFrame(loaded_data.get("reagents", []), columns=st.session_state.df_reagents.columns)
                st.session_state.df_solvents = pd.DataFrame(loaded_data.get("solvents", []), columns=st.session_state.df_solvents.columns)
                st.session_state.df_products = pd.DataFrame(loaded_data.get("products", []), columns=st.session_state.df_products.columns)
                st.success("レシピを読み込みました！")
                st.rerun()
            except Exception as e:
                st.error("ファイルの読み込みに失敗しました。")

# --- 2. PubChem API 検索窓 ---
st.header("🔍 1. 化合物物性検索 (PubChem API)")
st.write("英語の化合物名（例: ethanol, toluene, acetic acid）を入力して物性を取得できます。")

col_search, col_btn = st.columns([4, 1])
with col_search:
    search_name = st.text_input("化合物名を入力", placeholder="例: benzene", label_visibility="collapsed")
with col_btn:
    search_clicked = col_btn.button("APIから物性を取得", use_container_width=True)

if search_clicked and search_name:
    with st.spinner("PubChemからデータと構造式を取り出しています..."):
        cid_search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastidentity/name/{search_name}/cids/JSON"
        res_cid = requests.get(cid_search_url)
        
        if res_cid.status_code != 200:
             cid_search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{search_name}/cids/JSON"
             res_cid = requests.get(cid_search_url)

        if res_cid.status_code == 200:
            cid = res_cid.json()["IdentifierList"]["CID"][0]
            
            url_prop = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/MolecularWeight,CanonicalSMILES,Title/JSON"
            res_prop = requests.get(url_prop)
            
            mw = None
            title = search_name
            if res_prop.status_code == 200:
                prop_data = res_prop.json()["PropertyTable"]["Properties"][0]
                mw = prop_data.get("MolecularWeight", None)
                title = prop_data.get("Title", search_name)
            
            props = {"density": None, "mp": None, "bp": None, "cid": cid}
            
            url_view = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
            res_view = requests.get(url_view)
            if res_view.status_code == 200:
                view_data = res_view.json()
                
                def parse_section(sections):
                    for sec in sections:
                        heading = sec.get("TOCHeading", "")
                        if heading == "Melting Point":
                            try: props["mp"] = extract_temp(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                            except: pass
                        elif heading == "Boiling Point":
                            try: props["bp"] = extract_temp(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                            except: pass
                        elif heading == "Density":
                            try: props["density"] = extract_density(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                            except: pass
                        if "Section" in sec: parse_section(sec["Section"])
                
                if "Record" in view_data and "Section" in view_data["Record"]:
                    parse_section(view_data["Record"]["Section"])
            
            st.session_state.search_result = {
                "分類": "試薬", "主原料": False, "試薬名": title,
                "分子量": float(mw) if mw else None, "密度(g/mL)": props["density"], "融点(℃)": props["mp"], "沸点(℃)": props["bp"],
                "当量(Eq)": None, "重量(mg)": None, "体積(mL)": None, "モル数(mmol)": None
            }
            st.session_state.search_cid = props["cid"]
        else:
            st.error("PubChemに該当する化合物が見つかりませんでした。英語名や略称を確認してください。")
            if "search_result" in st.session_state: del st.session_state.search_result
            if "search_cid" in st.session_state: del st.session_state.search_cid

if "search_result" in st.session_state:
    res = st.session_state.search_result
    cid = st.session_state.get("search_cid")
    
    st.success(f"🎉 化合物が見つかりました: **{res['試薬名']}**")
    
    col_img, col_info = st.columns([1, 4])
    
    with col_img:
        if cid:
            img_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG?record_type=2d&image_size=large"
            st.image(img_url, use_column_width=True)
        else:
            st.write("構造式なし")
            
    with col_info:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("分子量 (g/mol)", f"{res['分子量']:.2f}" if res['分子量'] is not None else "不明")
        col2.metric("密度 (g/mL)", f"{res['密度(g/mL)']:.2f}" if res['密度(g/mL)'] is not None else "データなし")
        col3.metric("融点 (℃)", f"{res['融点(℃)']:.1f}" if res['融点(℃)'] is not None else "データなし")
        col4.metric("沸点 (℃)", f"{res['沸点(℃)']:.1f}" if res['沸点(℃)'] is not None else "データなし")
        
        st.write("")
        if st.button("➕ この化合物を『試薬』の表に追加する", type="secondary"):
            st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([res])], ignore_index=True)
            st.toast(f"「{res['試薬名']}」を表に追加しました！")
            del st.session_state.search_result
            del st.session_state.search_cid
            st.rerun()

st.markdown("---")

# --- 3. メイン計算シート（3段構成） ---
st.header("📊 2. モル計算シート")

cat_options = ["試薬", "溶媒", "生成物"]

st.subheader("🧪 試薬 (Reagents)")
edited_reagents = st.data_editor(
    st.session_state.df_reagents,
    column_config={
        "分類": st.column_config.SelectboxColumn("分類 (移籍)", options=cat_options, required=True),
        "主原料": st.column_config.CheckboxColumn("主原料"),
        "試薬名": st.column_config.TextColumn("試薬名", required=True),
        "分子量": st.column_config.NumberColumn("分子量", format="%.2f"),
        "密度(g/mL)": st.column_config.NumberColumn("密度(g/mL)", format="%.2f"),
        "融点(℃)": st.column_config.NumberColumn("融点(℃)", format="%.1f"),
        "沸点(℃)": st.column_config.NumberColumn("沸点(℃)", format="%.1f"),
        "当量(Eq)": st.column_config.TextColumn("当量(Eq)"),
        "重量(mg)": st.column_config.TextColumn("重量(mg)"),
        "体積(mL)": st.column_config.TextColumn("体積(mL)"),
        "モル数(mmol)": st.column_config.TextColumn("モル数(mmol)"),
    },
    num_rows="dynamic", key="editor_reagents", use_container_width=True
)

st.subheader("💧 溶媒 (Solvents)")
edited_solvents = st.data_editor(
    st.session_state.df_solvents,
    column_config={
        "分類": st.column_config.SelectboxColumn("分類 (移籍)", options=cat_options, required=True),
        "試薬名": st.column_config.TextColumn("試薬名", required=True),
        "分子量": st.column_config.NumberColumn("分子量", format="%.2f"),
        "密度(g/mL)": st.column_config.NumberColumn("密度(g/mL)", format="%.2f"),
        "融点(℃)": st.column_config.NumberColumn("融点(℃)", format="%.1f"),
        "沸点(℃)": st.column_config.NumberColumn("沸点(℃)", format="%.1f"),
        "設定濃度(M)": st.column_config.TextColumn("設定濃度(M)"),
        "溶媒倍率(v/w)": st.column_config.TextColumn("溶媒倍率(v/w)"),
        "体積(mL)": st.column_config.TextColumn("体積(mL)"),
    },
    num_rows="dynamic", key="editor_solvents", use_container_width=True
)

st.subheader("✨ 生成物 (Products)")
edited_products = st.data_editor(
    st.session_state.df_products,
    column_config={
        "分類": st.column_config.SelectboxColumn("分類 (移籍)", options=cat_options, required=True),
        "試薬名": st.column_config.TextColumn("試薬名", required=True),
        "分子量": st.column_config.NumberColumn("分子量", format="%.2f"),
        "融点(℃)": st.column_config.NumberColumn("融点(℃)", format="%.1f"),
        "沸点(℃)": st.column_config.NumberColumn("沸点(℃)", format="%.1f"),
        "当量(Eq)": st.column_config.TextColumn("当量(Eq)"),
        "理論収量(mg)": st.column_config.TextColumn("理論収量(mg)", disabled=True),
        "実収量(mg)": st.column_config.TextColumn("実収量(mg)"),
        "収率(%)": st.column_config.TextColumn("収率(%)", disabled=True),
    },
    num_rows="dynamic", key="editor_products", use_container_width=True
)

migrated = False

mask_r = edited_reagents["分類"] != "試薬"
if mask_r.any():
    for _, row in edited_reagents[mask_r].iterrows():
        if row["分類"] == "溶媒": st.session_state.df_solvents = pd.concat([st.session_state.df_solvents, pd.DataFrame([row])], ignore_index=True)
        elif row["分類"] == "生成物": st.session_state.df_products = pd.concat([st.session_state.df_products, pd.DataFrame([row])], ignore_index=True)
    st.session_state.df_reagents = edited_reagents[~mask_r].reset_index(drop=True)
    migrated = True

mask_s = edited_solvents["分類"] != "溶媒"
if mask_s.any():
    for _, row in edited_solvents[mask_s].iterrows():
        if row["分類"] == "試薬": st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([row])], ignore_index=True)
        elif row["分類"] == "生成物": st.session_state.df_products = pd.concat([st.session_state.df_products, pd.DataFrame([row])], ignore_index=True)
    st.session_state.df_solvents = edited_solvents[~mask_s].reset_index(drop=True)
    migrated = True

mask_p = edited_products["分類"] != "生成物"
if mask_p.any():
    for _, row in edited_products[mask_p].iterrows():
        if row["分類"] == "試薬": st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([row])], ignore_index=True)
        elif row["分類"] == "溶媒": st.session_state.df_solvents = pd.concat([st.session_state.df_solvents, pd.DataFrame([row])], ignore_index=True)
    st.session_state.df_products = edited_products[~mask_p].reset_index(drop=True)
    migrated = True

if migrated:
    st.rerun()

# --- 計算・リセットボタン ---
st.markdown("---")
col_calc, col_clear, _ = st.columns([1.5, 1, 4])
calc_triggered = col_calc.button("⚙️ 計算実行 (空きマスを埋める)", type="primary", use_container_width=True)
if col_clear.button("🔄 シートをすべてクリア", use_container_width=True):
    st.session_state.df_reagents = st.session_state.df_reagents.iloc[0:0]
    st.session_state.df_solvents = st.session_state.df_solvents.iloc[0:0]
    st.session_state.df_products = st.session_state.df_products.iloc[0:0]
    st.rerun()

# --- 4. 計算ロジック ---
if calc_triggered:
    df_calc_r = edited_reagents.copy()
    df_calc_s = edited_solvents.copy()
    df_calc_p = edited_products.copy()

    base_mask = df_calc_r["主原料"].fillna(False).astype(bool)
    base_mmol = None
    base_w_g = None
    
    if not base_mask.any() and len(df_calc_r) > 0:
        st.error("⚠️ 『試薬』表で『主原料』にチェックを入れてください。")
    elif len(df_calc_r) > 0:
        base_idx = df_calc_r[base_mask].index[0]
        
        base_mw = to_float(df_calc_r.loc[base_idx, "分子量"])
        base_d = to_float(df_calc_r.loc[base_idx, "密度(g/mL)"])
        base_w = to_float(df_calc_r.loc[base_idx, "重量(mg)"])
        base_v = to_float(df_calc_r.loc[base_idx, "体積(mL)"])
        base_mmol_input = to_float(df_calc_r.loc[base_idx, "モル数(mmol)"])
        
        if base_mmol_input is not None: base_mmol = base_mmol_input
        elif base_w is not None and base_mw is not None and base_mw > 0: base_mmol = base_w / base_mw
        elif base_v is not None and base_d is not None and base_mw is not None and base_mw > 0: base_mmol = base_v * base_d * 1000 / base_mw
            
        if base_mmol is None:
            st.error("⚠️ 主原料のモル数が計算できません。「重量」「体積」「モル数」のいずれかと「分子量」を入力してください。")
        else:
            df_calc_r.at[base_idx, "当量(Eq)"] = format_val(1.0, "{:.2f}")
            df_calc_r.at[base_idx, "モル数(mmol)"] = format_val(base_mmol, "{:.3f}")
            
            calc_base_w = base_mmol * base_mw if base_mw else "計算不能"
            df_calc_r.at[base_idx, "重量(mg)"] = format_val(calc_base_w, "{:.1f}")
            if calc_base_w != "計算不能": base_w_g = calc_base_w / 1000.0
            
            if calc_base_w != "計算不能" and base_d is not None and base_d > 0:
                df_calc_r.at[base_idx, "体積(mL)"] = format_val(calc_base_w / (base_d * 1000), "{:.3f}")
            else:
                df_calc_r.at[base_idx, "体積(mL)"] = "計算不能" if base_v is None else format_val(base_v, "{:.3f}")

            for idx, row in df_calc_r.iterrows():
                if idx == base_idx: continue
                mw = to_float(row["分子量"])
                d = to_float(row["密度(g/mL)"])
                eq = to_float(row["当量(Eq)"])
                w = to_float(row["重量(mg)"])
                v = to_float(row["体積(mL)"])
                mmol_input = to_float(row["モル数(mmol)"])
                
                calc_mmol = None
                if mmol_input is not None: calc_mmol = mmol_input
                elif eq is not None: calc_mmol = base_mmol * eq
                elif w is not None and mw is not None and mw > 0: calc_mmol = w / mw
                elif v is not None and d is not None and mw is not None and mw > 0: calc_mmol = v * d * 1000 / mw
                
                if not any(x is not None for x in [eq, w, v, mmol_input]): continue
                
                if calc_mmol is not None:
                    calc_eq = calc_mmol / base_mmol if base_mmol > 0 else "計算不能"
                    calc_w = calc_mmol * mw if mw is not None else "計算不能"
                    calc_v = calc_w / (d * 1000) if (calc_w != "計算不能" and d is not None and d > 0) else "計算不能"
                    df_calc_r.at[idx, "当量(Eq)"] = format_val(calc_eq, "{:.2f}")
                    df_calc_r.at[idx, "重量(mg)"] = format_val(calc_w, "{:.1f}")
                    df_calc_r.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                    df_calc_r.at[idx, "モル数(mmol)"] = format_val(calc_mmol, "{:.3f}")

    if base_mmol is not None:
        for idx, row in df_calc_s.iterrows():
            conc = to_float(row.get("設定濃度(M)"))
            ratio = to_float(row.get("溶媒倍率(v/w)"))
            v = to_float(row.get("体積(mL)"))
            
            if conc is not None and conc > 0:
                calc_v = base_mmol / conc
                df_calc_s.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                df_calc_s.at[idx, "溶媒倍率(v/w)"] = format_val(calc_v / base_w_g if base_w_g else "計算不能", "{:.2f}")
                df_calc_s.at[idx, "設定濃度(M)"] = format_val(conc, "{:.2f}")
            elif ratio is not None and ratio > 0:
                if base_w_g:
                    calc_v = base_w_g * ratio
                    df_calc_s.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                    df_calc_s.at[idx, "設定濃度(M)"] = format_val(base_mmol / calc_v if calc_v > 0 else "計算不能", "{:.2f}")
                    df_calc_s.at[idx, "溶媒倍率(v/w)"] = format_val(ratio, "{:.2f}")
            elif v is not None and v > 0:
                df_calc_s.at[idx, "設定濃度(M)"] = format_val(base_mmol / v, "{:.2f}")
                df_calc_s.at[idx, "溶媒倍率(v/w)"] = format_val(v / base_w_g if base_w_g else "計算不能", "{:.2f}")
                df_calc_s.at[idx, "体積(mL)"] = format_val(v, "{:.3f}")

    if base_mmol is not None:
        for idx, row in df_calc_p.iterrows():
            mw = to_float(row.get("分子量"))
            eq = to_float(row.get("当量(Eq)"))
            act_w = to_float(row.get("実収量(mg)"))
            
            if eq is None:
                eq = 1.0
                df_calc_p.at[idx, "当量(Eq)"] = "1.00"
                
            theo_w = base_mmol * eq * mw if mw is not None else "計算不能"
            df_calc_p.at[idx, "理論収量(mg)"] = format_val(theo_w, "{:.1f}")
            
            if act_w is not None:
                if isinstance(theo_w, float) and theo_w > 0:
                    df_calc_p.at[idx, "収率(%)"] = format_val((act_w / theo_w) * 100, "{:.1f}")
                else:
                    df_calc_p.at[idx, "収率(%)"] = "計算不能"

    st.session_state.df_reagents = df_calc_r
    st.session_state.df_solvents = df_calc_s
    st.session_state.df_products = df_calc_p
    st.rerun()

# --- 5. 実験ノート用テキスト出力 ---
st.header("📝 3. 実験ノート用出力")
try:
    df_r = st.session_state.df_reagents
    is_base = df_r["主原料"].fillna(False).astype(bool)
    base_rows = df_r[is_base]
    
    if not base_rows.empty:
        base_row = base_rows.iloc[0]
        note = "【実験操作】\n"
        w_val = base_row.get('重量(mg)', '')
        mmol_val = base_row.get('モル数(mmol)', '')
        note += f"反応容器に {base_row.get('試薬名', '')} ({w_val} mg, {mmol_val} mmol) を仕込み、"
        
        for _, r in st.session_state.df_solvents.iterrows():
            v = to_float(r.get('体積(mL)'))
            if v is not None and v > 0:
                note += f"{r.get('試薬名', '')} ({r.get('体積(mL)', '')} mL, {r.get('設定濃度(M)', '')} M) を加えて溶解させた。"
            
        for _, r in df_r[~is_base].iterrows():
            w = to_float(r.get('重量(mg)'))
            if w is not None and w > 0:
                vol_str = f" [{r.get('体積(mL)', '')} mL]" if r.get('体積(mL)') not in [None, "", "計算不能"] else ""
                note += f"そこへ {r.get('試薬名', '')} ({r.get('重量(mg)', '')} mg{vol_str}, {r.get('モル数(mmol)', '')} mmol, {r.get('当量(Eq)', '')} Eq) を加えた。"
                
        for _, r in st.session_state.df_products.iterrows():
            act = to_float(r.get('実収量(mg)'))
            if act is not None and act > 0:
                note += f"\n反応終了後、精製を施すことで {r.get('試薬名', '')} を得た（{r.get('実収量(mg)', '')} mg, 収率: {r.get('収率(%)', '')} %）。"
            
        note += "\n\n【組成表】\n\n--- 試薬 ---\n"
        note += df_to_markdown_safe(df_r, ["主原料", "試薬名", "分子量", "当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"])
        
        if not st.session_state.df_solvents.empty:
            note += "\n--- 溶媒 ---\n"
            note += df_to_markdown_safe(st.session_state.df_solvents, ["試薬名", "設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"])
            
        if not st.session_state.df_products.empty:
            note += "\n--- 生成物 ---\n"
            note += df_to_markdown_safe(st.session_state.df_products, ["試薬名", "分子量", "当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"])
        
        st.text_area("以下のテキストをコピーして電子実験ノート(ELN)等に貼り付けてください：", value=note, height=400)
    else:
        st.info("上の『計算実行』ボタンを押すと、ここに実験ノート用の自動文章が生成されます。")
except Exception as e:
    st.error(f"出力の生成中にエラーが発生しました（原因: {str(e)}）。表の入力状態を確認してください。")