import streamlit as st
import pandas as pd
import requests
import re
import numpy as np
import json
import gspread

st.set_page_config(layout="wide")
st.title("🧪 研究室用 統合型モル計算＆化合物検索プラットフォーム")

# --- ヘルパー関数 ---
def to_float(val):
    if pd.isna(val) or val == "" or val is None or str(val).strip() in ["", "計算不能", "不明"]:
        return None
    try: return float(val)
    except: return None

def format_val(val, fmt):
    if val is None or pd.isna(val) or val == "": return None
    if isinstance(val, str): return val
    try: return fmt.format(val)
    except: return str(val)

def extract_temp(text):
    match_c = re.search(r'([-+]?\d*\.?\d+)\s*(?:°|deg)?\s*C', str(text), re.IGNORECASE)
    if match_c: return float(match_c.group(1))
    match = re.search(r'[-+]?\d*\.\d+|\d+', str(text))
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

# 最新手入力回収用関数
def get_current_df(df_key, editor_key):
    df = st.session_state[df_key].copy()
    if editor_key in st.session_state:
        ed = st.session_state[editor_key]
        for idx, changes in ed.get("edited_rows", {}).items():
            for col, val in changes.items():
                df.at[int(idx), col] = val
        added = ed.get("added_rows", [])
        if added:
            df = pd.concat([df, pd.DataFrame(added)], ignore_index=True)
        deleted = ed.get("deleted_rows", [])
        if deleted:
            df = df.drop(deleted).reset_index(drop=True)
    return df

# --- GSheets接続ヘルパー ---
def get_gsheets_client():
    creds_dict = dict(st.secrets["connections"]["gsheets"])
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    url = creds_dict.pop("spreadsheet", None)
    client = gspread.service_account_from_dict(creds_dict)
    return client, url

# --- 1. セッション状態の初期化 ---
if 'df_rsolid' not in st.session_state:
    st.session_state.df_rsolid = pd.DataFrame(columns=["分類", "主原料", "試薬名", "純度(%)", "分子量", "密度(g/mL)", "融点(℃)", "沸点(℃)", "当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"])
if 'df_rliquid' not in st.session_state:
    st.session_state.df_rliquid = pd.DataFrame(columns=["分類", "主原料", "試薬名", "設定濃度(M)", "当量(Eq)", "体積(mL)", "モル数(mmol)"])
if 'df_solvents' not in st.session_state:
    st.session_state.df_solvents = pd.DataFrame(columns=["分類", "試薬名", "分子量", "密度(g/mL)", "融点(℃)", "沸点(℃)", "設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"])
if 'df_products' not in st.session_state:
    st.session_state.df_products = pd.DataFrame(columns=["分類", "試薬名", "分子量", "融点(℃)", "沸点(℃)", "当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"])

# --- 2. マイ辞書の読み込み ---
try:
    gc, sheet_url = get_gsheets_client()
    sheet = gc.open_by_url(sheet_url).sheet1
    df_mydict = pd.DataFrame(sheet.get_all_records())
    if df_mydict.empty: raise ValueError("Empty sheet")
except Exception:
    df_mydict = pd.DataFrame(columns=["試薬名", "略称や通称", "分子量", "密度", "沸点", "融点", "CAS番号", "コメント"])

# --- 💾 サイドバー：保存/読込 ---
with st.sidebar:
    st.header("💾 レシピの保存と読込")
    data_to_save = {
        "rsolid": st.session_state.df_rsolid.to_dict(orient="records"),
        "rliquid": st.session_state.df_rliquid.to_dict(orient="records"),
        "solvents": st.session_state.df_solvents.to_dict(orient="records"),
        "products": st.session_state.df_products.to_dict(orient="records")
    }
    st.download_button("⬇️ 現在の表をファイルに保存", data=json.dumps(data_to_save, ensure_ascii=False, indent=2), file_name="lab_recipe.json", mime="application/json", use_container_width=True)
    uploaded_file = st.file_uploader("📂 レシピを読み込む", type=["json"])
    if uploaded_file and st.button("📥 読み込みを実行", use_container_width=True, type="primary"):
        loaded = json.load(uploaded_file)
        st.session_state.df_rsolid = pd.DataFrame(loaded.get("rsolid", []), columns=st.session_state.df_rsolid.columns)
        st.session_state.df_rliquid = pd.DataFrame(loaded.get("rliquid", []), columns=st.session_state.df_rliquid.columns)
        st.session_state.df_solvents = pd.DataFrame(loaded.get("solvents", []), columns=st.session_state.df_solvents.columns)
        st.session_state.df_products = pd.DataFrame(loaded.get("products", []), columns=st.session_state.df_products.columns)
        st.rerun()

# --- 3. 化合物物性検索 ---
st.header("🔍 1. 化合物物性検索")
col_s, col_b = st.columns([4, 1])
search_name = col_s.text_input("化合物名・略称を入力", label_visibility="collapsed")
if col_b.button("🔍 同時検索を実行", use_container_width=True) and search_name:
    st.session_state.active_search = search_name.strip()
    if "db_result" in st.session_state: del st.session_state.db_result
    if "api_result" in st.session_state: del st.session_state.api_result

if "active_search" in st.session_state:
    q = st.session_state.active_search
    # DB Search
    q_low = q.lower()
    db_match = df_mydict[df_mydict["試薬名"].astype(str).str.lower().str.contains(q_low, na=False) | df_mydict["略称や通称"].astype(str).str.lower().str.contains(q_low, na=False)]
    if not db_match.empty:
        row = db_match.iloc[0]
        st.session_state.db_result = {"試薬名": row["試薬名"], "分子量": to_float(row.get("分子量")), "密度(g/mL)": to_float(row.get("密度")), "融点(℃)": to_float(row.get("融点")), "沸点(℃)": to_float(row.get("沸点")), "CAS番号": row.get("CAS番号", ""), "コメント": row.get("コメント", "")}
    
    # API Search
    if "api_result" not in st.session_state:
        with st.spinner("PubChemから取得中..."):
            res_cid = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastidentity/name/{q}/cids/JSON")
            if res_cid.status_code != 200: res_cid = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{q}/cids/JSON")
            if res_cid.status_code == 200:
                cid = res_cid.json()["IdentifierList"]["CID"][0]
                rp = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/MolecularWeight,Title/JSON").json()
                mw = rp["PropertyTable"]["Properties"][0].get("MolecularWeight")
                title = rp["PropertyTable"]["Properties"][0].get("Title", q)
                cas = next((s for s in requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON").json().get("InformationList",{}).get("Information",[{}])[0].get("Synonym",[]) if re.match(r'^\d{2,7}-\d{2}-\d$', str(s))), None)
                props = {"density": None, "mp": None, "bp": None}
                def parse_sec(secs):
                    for sec in secs:
                        h = sec.get("TOCHeading", "")
                        if h == "Melting Point": props["mp"] = extract_temp(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                        elif h == "Boiling Point": props["bp"] = extract_temp(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                        elif h == "Density": props["density"] = extract_density(sec["Information"][0]["Value"]["StringWithMarkup"][0]["String"])
                        if "Section" in sec: parse_sec(sec["Section"])
                parse_sec(requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON").json().get("Record",{}).get("Section",[]))
                st.session_state.api_result = {"試薬名": title, "分子量": float(mw) if mw else None, "密度(g/mL)": props["density"], "融点(℃)": props["mp"], "沸点(℃)": props["bp"], "CAS番号": cas, "cid": cid}

    c_db, c_api = st.columns(2)
    def add_btn_ui(res_dict, key_prefix):
        c1, c2 = st.columns(2)
        if c1.button("➕ 固体・純液体 に追加", key=f"{key_prefix}_solid", use_container_width=True):
            current_df = get_current_df("df_rsolid", "ed_rsolid")
            r = {"分類": "試薬(固体/液体)", "主原料": False, "試薬名": res_dict["試薬名"], "純度(%)": None, "分子量": res_dict["分子量"], "密度(g/mL)": res_dict["密度(g/mL)"], "融点(℃)": res_dict["融点(℃)"], "沸点(℃)": res_dict["沸点(℃)"]}
            st.session_state.df_rsolid = pd.concat([current_df, pd.DataFrame([r])], ignore_index=True)
            st.rerun()
        if c2.button("➕ 溶液試薬 に追加", key=f"{key_prefix}_liq", use_container_width=True):
            current_df = get_current_df("df_rliquid", "ed_rliquid")
            r = {"分類": "試薬(溶液)", "主原料": False, "試薬名": res_dict["試薬名"], "設定濃度(M)": None, "当量(Eq)": None, "体積(mL)": None, "モル数(mmol)": None}
            st.session_state.df_rliquid = pd.concat([current_df, pd.DataFrame([r])], ignore_index=True)
            st.rerun()

    with c_db:
        st.subheader("📕 独自DB (マイ辞書)")
        if "db_result" in st.session_state:
            db_res = st.session_state.db_result
            st.markdown(f"**{db_res['試薬名']}** (CAS: {db_res.get('CAS番号','')}) | ｺﾒﾝﾄ: {db_res['コメント']}")
            st.caption(f"分子量: {db_res['分子量']} | 密度: {db_res['密度(g/mL)']} | 融点: {db_res.get('融点(℃)', '不明')} | 沸点: {db_res.get('沸点(℃)', '不明')}")
            add_btn_ui(db_res, "db")
        else: st.info("マイ辞書に該当なし")

    with c_api:
        st.subheader("🌐 PubChem API")
        if "api_result" in st.session_state:
            api_res = st.session_state.api_result
            st.markdown(f"**{api_res['試薬名']}** (CAS: {api_res.get('CAS番号','')})")
            st.caption(f"分子量: {api_res['分子量']} | 密度: {api_res['密度(g/mL)']} | 融点: {api_res.get('融点(℃)', '不明')} | 沸点: {api_res.get('沸点(℃)', '不明')}")
            add_btn_ui(api_res, "api")
        else: st.info("PubChemに該当なし")

st.markdown("---")

# --- 4. メイン計算シート ---
st.header("📊 2. モル計算シート")
cat_options = ["試薬(固体/液体)", "試薬(溶液)", "反応溶媒", "生成物"]

st.subheader("🪨 試薬 (固体・純液体)")
ed_rsolid = st.data_editor(st.session_state.df_rsolid, column_config={
    "分類": st.column_config.SelectboxColumn("移籍", options=cat_options, required=True), 
    "主原料": st.column_config.CheckboxColumn("主原料"),
    "純度(%)": st.column_config.NumberColumn("純度(%)", format="%.1f"), 
    "分子量": st.column_config.NumberColumn("分子量", format="%.2f"), 
    "密度(g/mL)": st.column_config.NumberColumn("密度(g/mL)", format="%.2f")
}, num_rows="dynamic", use_container_width=True, key="ed_rsolid")

st.subheader("🧪 試薬 (溶液)")
ed_rliquid = st.data_editor(st.session_state.df_rliquid, column_config={
    "分類": st.column_config.SelectboxColumn("移籍", options=cat_options, required=True), 
    "主原料": st.column_config.CheckboxColumn("主原料"),
    "設定濃度(M)": st.column_config.TextColumn("設定濃度(M)")
}, num_rows="dynamic", use_container_width=True, key="ed_rliquid")

st.subheader("💧 反応溶媒 (Solvents)")
ed_solv = st.data_editor(st.session_state.df_solvents, column_config={
    "分類": st.column_config.SelectboxColumn("移籍", options=cat_options, required=True)
}, num_rows="dynamic", use_container_width=True, key="ed_solv")

st.subheader("✨ 生成物 (Products)")
ed_prod = st.data_editor(st.session_state.df_products, column_config={
    "分類": st.column_config.SelectboxColumn("移籍", options=cat_options, required=True),
    "理論収量(mg)": st.column_config.TextColumn("理論収量(mg)", disabled=True), 
    "収率(%)": st.column_config.TextColumn("収率(%)", disabled=True)
}, num_rows="dynamic", use_container_width=True, key="ed_prod")

# --- 移籍処理 ---
migrated = False
frames = [(ed_rsolid, "試薬(固体/液体)"), (ed_rliquid, "試薬(溶液)"), (ed_solv, "反応溶媒"), (ed_prod, "生成物")]
for src_df, cat_name in frames:
    mask = src_df["分類"] != cat_name
    if mask.any():
        for _, r in src_df[mask].iterrows():
            if r["分類"] == "試薬(固体/液体)": st.session_state.df_rsolid = pd.concat([st.session_state.df_rsolid, pd.DataFrame([r.to_dict()])], ignore_index=True)
            elif r["分類"] == "試薬(溶液)": st.session_state.df_rliquid = pd.concat([st.session_state.df_rliquid, pd.DataFrame([r.to_dict()])], ignore_index=True)
            elif r["分類"] == "反応溶媒": st.session_state.df_solvents = pd.concat([st.session_state.df_solvents, pd.DataFrame([r.to_dict()])], ignore_index=True)
            elif r["分類"] == "生成物": st.session_state.df_products = pd.concat([st.session_state.df_products, pd.DataFrame([r.to_dict()])], ignore_index=True)
        if cat_name == "試薬(固体/液体)": st.session_state.df_rsolid = src_df[~mask].reset_index(drop=True)
        elif cat_name == "試薬(溶液)": st.session_state.df_rliquid = src_df[~mask].reset_index(drop=True)
        elif cat_name == "反応溶媒": st.session_state.df_solvents = src_df[~mask].reset_index(drop=True)
        elif cat_name == "生成物": st.session_state.df_products = src_df[~mask].reset_index(drop=True)
        migrated = True
if migrated: st.rerun()

st.markdown("---")
c_calc, c_clear_val, c_clr, _ = st.columns([1.5, 1.5, 1.2, 2.8])

if c_clr.button("🔄 すべてクリア", use_container_width=True):
    st.session_state.df_rsolid = st.session_state.df_rsolid.iloc[0:0]
    st.session_state.df_rliquid = st.session_state.df_rliquid.iloc[0:0]
    st.session_state.df_solvents = st.session_state.df_solvents.iloc[0:0]
    st.session_state.df_products = st.session_state.df_products.iloc[0:0]
    st.rerun()

if c_clear_val.button("🧹 計算値のみクリア", use_container_width=True):
    for col in ["当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"]:
        if col in st.session_state.df_rsolid.columns: st.session_state.df_rsolid[col] = None
    for col in ["当量(Eq)", "体積(mL)", "モル数(mmol)"]:
        if col in st.session_state.df_rliquid.columns: st.session_state.df_rliquid[col] = None
    for col in ["設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"]:
        if col in st.session_state.df_solvents.columns: st.session_state.df_solvents[col] = None
    for col in ["当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"]:
        if col in st.session_state.df_products.columns: st.session_state.df_products[col] = None
    st.rerun()

# --- 計算ロジック ---
if c_calc.button("⚙️ 計算実行 (空きマスを埋める)", type="primary", use_container_width=True):
    d_rs, d_rl, d_sv, d_pr = ed_rsolid.copy(), ed_rliquid.copy(), ed_solv.copy(), ed_prod.copy()
    b_solid_mask = d_rs["主原料"].fillna(False).astype(bool)
    b_liq_mask = d_rl["主原料"].fillna(False).astype(bool)
    b_mmol, b_w_g = None, None

    if not b_solid_mask.any() and not b_liq_mask.any() and (len(d_rs)>0 or len(d_rl)>0):
        st.error("⚠️ 『主原料』にチェックを入れてください。")
    else:
        # 主原料の計算
        if b_solid_mask.any():
            idx = d_rs[b_solid_mask].index[0]
            mw, d, w, v, m_in = to_float(d_rs.loc[idx,"分子量"]), to_float(d_rs.loc[idx,"密度(g/mL)"]), to_float(d_rs.loc[idx,"重量(mg)"]), to_float(d_rs.loc[idx,"体積(mL)"]), to_float(d_rs.loc[idx,"モル数(mmol)"])
            p_raw = to_float(d_rs.loc[idx,"純度(%)"])
            p_fac = p_raw / 100.0 if p_raw and p_raw > 0 else 1.0 # 純度補正
            
            b_mmol = m_in if m_in else (w*p_fac/mw if w and mw else (v*d*1000*p_fac/mw if v and d and mw else None))
            
            if b_mmol:
                d_rs.at[idx,"当量(Eq)"], d_rs.at[idx,"モル数(mmol)"] = "1.00", format_val(b_mmol, "{:.3f}")
                calc_w = b_mmol*mw if mw else "計算不能"
                calc_w_gross = calc_w / p_fac if calc_w != "計算不能" else "計算不能" # 量り取るべき実際の重さ
                d_rs.at[idx,"重量(mg)"] = format_val(calc_w_gross, "{:.1f}")
                b_w_g = calc_w_gross/1000.0 if calc_w_gross != "計算不能" else None
                d_rs.at[idx,"体積(mL)"] = format_val(calc_w_gross/(d*1000), "{:.3f}") if calc_w_gross!="計算不能" and d else ("計算不能" if v is None else format_val(v,"{:.3f}"))
        
        elif b_liq_mask.any():
            idx = d_rl[b_liq_mask].index[0]
            c, v, m_in = to_float(d_rl.loc[idx,"設定濃度(M)"]), to_float(d_rl.loc[idx,"体積(mL)"]), to_float(d_rl.loc[idx,"モル数(mmol)"])
            b_mmol = m_in if m_in else (c*v if c and v else None)
            if b_mmol:
                d_rl.at[idx,"当量(Eq)"], d_rl.at[idx,"モル数(mmol)"] = "1.00", format_val(b_mmol, "{:.3f}")
                if c: d_rl.at[idx,"体積(mL)"] = format_val(b_mmol/c, "{:.3f}")

        # 添加試薬等の計算
        if b_mmol:
            for i, r in d_rs.iterrows():
                if b_solid_mask.any() and i == d_rs[b_solid_mask].index[0]: continue
                mw, d, eq, w, v, m_in = to_float(r["分子量"]), to_float(r["密度(g/mL)"]), to_float(r["当量(Eq)"]), to_float(r["重量(mg)"]), to_float(r["体積(mL)"]), to_float(r["モル数(mmol)"])
                p_raw = to_float(r.get("純度(%)"))
                p_fac = p_raw / 100.0 if p_raw and p_raw > 0 else 1.0
                
                c_m = m_in if m_in is not None else (b_mmol*eq if eq is not None else (w*p_fac/mw if w and mw else (v*d*1000*p_fac/mw if v and d and mw else None)))
                if c_m is not None:
                    calc_w = c_m*mw if mw else "計算不能"
                    calc_w_gross = calc_w / p_fac if calc_w != "計算不能" else "計算不能"
                    d_rs.at[i,"当量(Eq)"] = format_val(c_m/b_mmol, "{:.2f}")
                    d_rs.at[i,"重量(mg)"] = format_val(calc_w_gross, "{:.1f}")
                    d_rs.at[i,"体積(mL)"] = format_val(calc_w_gross/(d*1000), "{:.3f}") if calc_w_gross != "計算不能" and d else "計算不能"
                    d_rs.at[i,"モル数(mmol)"] = format_val(c_m, "{:.3f}")
            
            for i, r in d_rl.iterrows():
                if b_liq_mask.any() and i == d_rl[b_liq_mask].index[0]: continue
                c, eq, v, m_in = to_float(r["設定濃度(M)"]), to_float(r["当量(Eq)"]), to_float(r["体積(mL)"]), to_float(r["モル数(mmol)"])
                c_m = m_in if m_in is not None else (b_mmol*eq if eq is not None else (c*v if c and v else None))
                if c_m is not None:
                    d_rl.at[i,"当量(Eq)"] = format_val(c_m/b_mmol, "{:.2f}")
                    d_rl.at[i,"モル数(mmol)"] = format_val(c_m, "{:.3f}")
                    if c: d_rl.at[i,"体積(mL)"] = format_val(c_m/c, "{:.3f}")

            for i, r in d_sv.iterrows():
                c, rat, v = to_float(r.get("設定濃度(M)")), to_float(r.get("溶媒倍率(v/w)")), to_float(r.get("体積(mL)"))
                if c:
                    d_sv.at[i,"体積(mL)"] = format_val(b_mmol/c, "{:.3f}")
                    d_sv.at[i,"溶媒倍率(v/w)"] = format_val((b_mmol/c)/b_w_g, "{:.2f}") if b_w_g else "計算不能"
                elif rat and b_w_g:
                    d_sv.at[i,"体積(mL)"] = format_val(b_w_g*rat, "{:.3f}")
                    d_sv.at[i,"設定濃度(M)"] = format_val(b_mmol/(b_w_g*rat), "{:.2f}")
                elif v:
                    d_sv.at[i,"設定濃度(M)"] = format_val(b_mmol/v, "{:.2f}")
                    d_sv.at[i,"溶媒倍率(v/w)"] = format_val(v/b_w_g, "{:.2f}") if b_w_g else "計算不能"

            for i, r in d_pr.iterrows():
                mw, eq, act = to_float(r.get("分子量")), to_float(r.get("当量(Eq)")), to_float(r.get("実収量(mg)"))
                eq = eq if eq else 1.0; d_pr.at[i,"当量(Eq)"] = format_val(eq, "{:.2f}")
                theo = b_mmol*eq*mw if mw else "計算不能"
                d_pr.at[i,"理論収量(mg)"] = format_val(theo, "{:.1f}")
                if act and isinstance(theo, float): d_pr.at[i,"収率(%)"] = format_val((act/theo)*100, "{:.1f}")

    st.session_state.df_rsolid, st.session_state.df_rliquid, st.session_state.df_solvents, st.session_state.df_products = d_rs, d_rl, d_sv, d_pr
    st.rerun()

# --- 5. 実験ノート用出力 ---
st.header("📝 3. 実験ノート用出力")
try:
    if not st.session_state.df_rsolid.empty or not st.session_state.df_rliquid.empty:
        # 主原料の文章作成
        b_rs_mask = st.session_state.df_rsolid["主原料"].fillna(False).astype(bool)
        b_rl_mask = st.session_state.df_rliquid["主原料"].fillna(False).astype(bool)
        note = "【実験操作】\n反応容器に "
        
        if b_rs_mask.any():
            br = st.session_state.df_rsolid[b_rs_mask].iloc[0]
            p_raw = to_float(br.get('純度(%)'))
            p_str = f" ({p_raw}%)" if p_raw and p_raw < 100 else ""
            note += f"{br.get('試薬名', '')}{p_str} ({br.get('重量(mg)', '')} mg, {br.get('モル数(mmol)', '')} mmol) を仕込み、"
        elif b_rl_mask.any():
            br = st.session_state.df_rliquid[b_rl_mask].iloc[0]
            note += f"{br.get('試薬名', '')} ({br.get('設定濃度(M)', '')} M, {br.get('体積(mL)', '')} mL, {br.get('モル数(mmol)', '')} mmol) を仕込み、"
        
        for _, r in st.session_state.df_solvents.iterrows():
            v = to_float(r.get('体積(mL)'))
            if v and v > 0: note += f"{r.get('試薬名', '')} ({r.get('体積(mL)', '')} mL, {r.get('設定濃度(M)', '')} M) を加えて溶解させた。"
            
        for _, r in st.session_state.df_rsolid[~b_rs_mask].iterrows():
            w = to_float(r.get('重量(mg)'))
            if w and w > 0:
                p_raw = to_float(r.get('純度(%)'))
                p_str = f" ({p_raw}%)" if p_raw and p_raw < 100 else ""
                v_str = f" [{r.get('体積(mL)', '')} mL]" if r.get('体積(mL)') not in [None, "", "計算不能"] else ""
                note += f"そこへ {r.get('試薬名', '')}{p_str} ({r.get('重量(mg)', '')} mg{v_str}, {r.get('モル数(mmol)', '')} mmol, {r.get('当量(Eq)', '')} Eq) を加えた。"
        
        for _, r in st.session_state.df_rliquid[~b_rl_mask].iterrows():
            v = to_float(r.get('体積(mL)'))
            if v and v > 0:
                note += f"そこへ {r.get('試薬名', '')} ({r.get('設定濃度(M)', '')} M, {r.get('体積(mL)', '')} mL, {r.get('モル数(mmol)', '')} mmol, {r.get('当量(Eq)', '')} Eq) を滴下した。"
                
        for _, r in st.session_state.df_products.iterrows():
            act = to_float(r.get('実収量(mg)'))
            if act and act > 0: note += f"\n反応終了後、精製を施すことで {r.get('試薬名', '')} を得た（{r.get('実収量(mg)', '')} mg, 収率: {r.get('収率(%)', '')} %）。"
            
        note += "\n\n【組成表】\n\n--- 試薬 (固体・純液体) ---\n" + df_to_markdown_safe(st.session_state.df_rsolid, ["主原料", "試薬名", "純度(%)", "分子量", "当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"])
        note += "\n--- 試薬 (溶液) ---\n" + df_to_markdown_safe(st.session_state.df_rliquid, ["主原料", "試薬名", "設定濃度(M)", "当量(Eq)", "体積(mL)", "モル数(mmol)"])
        note += "\n--- 反応溶媒 ---\n" + df_to_markdown_safe(st.session_state.df_solvents, ["試薬名", "設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"])
        note += "\n--- 生成物 ---\n" + df_to_markdown_safe(st.session_state.df_products, ["試薬名", "分子量", "当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"])
        st.text_area("以下のテキストをコピーして電子実験ノート(ELN)等に貼り付けてください：", value=note, height=400)
except Exception as e: st.error(f"出力エラー: {str(e)}")

st.markdown("---")

# --- 6. DB管理 ---
st.header("📕 4. マイ辞書（独自DB）の管理")
ed_db = st.data_editor(df_mydict, num_rows="dynamic", use_container_width=True, key="ed_db")
if st.button("💾 変更をGoogleスプレッドシートに保存", type="primary"):
    with st.spinner("スプレッドシートに書き込み中..."):
        try:
            gc, sheet_url = get_gsheets_client()
            sheet = gc.open_by_url(sheet_url).sheet1
            sheet.clear()
            ed_db_filled = ed_db.fillna("")
            data_to_write = [ed_db_filled.columns.values.tolist()] + ed_db_filled.values.tolist()
            sheet.update(data_to_write)
            st.success("🎉 スプレッドシートの更新が完了しました！")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 保存エラー: {e}")