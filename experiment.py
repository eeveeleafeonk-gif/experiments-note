import streamlit as st
import pandas as pd
import requests
import re
import numpy as np
import json
from streamlit_gsheets import GSheetsConnection

st.set_page_config(layout="wide")
st.title("🧪 研究室用 統合型モル計算＆化合物検索プラットフォーム（独自DB連携版）")

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

# --- 2. Googleスプレッドシート（マイ辞書）の読み込み ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_mydict = conn.read(ttl="0m")
    df_mydict = df_mydict.dropna(subset=["試薬名"])
except Exception as e:
    st.warning("⚠️ Googleスプレッドシートへの接続が設定されていないか、エラーが発生しました。マイ辞書機能はスキップされます。")
    df_mydict = pd.DataFrame(columns=["試薬名", "略称や通称", "分子量", "密度", "沸点", "融点", "CAS番号", "コメント"])

# --- 💾 サイドバー：レシピの保存と読込 ---
with st.sidebar:
    st.header("💾 レシピの保存と読込")
    st.write("現在のモル計算表の状態をファイルに保存・復元できます。")
    data_to_save = {
        "reagents": st.session_state.df_reagents.to_dict(orient="records"),
        "solvents": st.session_state.df_solvents.to_dict(orient="records"),
        "products": st.session_state.df_products.to_dict(orient="records")
    }
    json_str = json.dumps(data_to_save, ensure_ascii=False, indent=2)
    st.download_button(
        label="⬇️ 現在の表をファイルに保存 (.json)",
        data=json_str, file_name="lab_recipe.json", mime="application/json", use_container_width=True
    )
    st.markdown("---")
    uploaded_file = st.file_uploader("📂 保存したレシピを読み込む", type=["json"])
    if uploaded_file is not None:
        if st.button("📥 読み込みを実行", use_container_width=True, type="primary"):
            try:
                loaded_data = json.load(uploaded_file)
                st.session_state.df_reagents = pd.DataFrame(loaded_data.get("reagents", []), columns=st.session_state.df_reagents.columns)
                st.session_state.df_solvents = pd.DataFrame(loaded_data.get("solvents", []), columns=st.session_state.df_solvents.columns)
                st.session_state.df_products = pd.DataFrame(loaded_data.get("products", []), columns=st.session_state.df_products.columns)
                st.success("レシピを読み込みました！")
                st.rerun()
            except: st.error("読み込みに失敗しました。")

# --- 3. 化合物物性検索セクション（マイ辞書 ＆ PubChemの並列出力） ---
st.header("🔍 1. 化合物物性検索")
st.write("英語名・略称・通称を入力してください。研究室の「マイ辞書（スプシ）」と「PubChem API」の両方を同時に検索します。")

col_search, col_btn = st.columns([4, 1])
with col_search:
    search_name = st.text_input("化合物名・略称を入力（例: AcOH, benzene, DCM）", placeholder="例: AcOH", label_visibility="collapsed")
with col_btn:
    search_clicked = col_btn.button("🔍 同時検索を実行", use_container_width=True)

if search_clicked and search_name:
    st.session_state.active_search = search_name.strip()
    if "db_result" in st.session_state: del st.session_state.db_result
    if "api_result" in st.session_state: del st.session_state.api_result
    if "api_cid" in st.session_state: del st.session_state.api_cid

if "active_search" in st.session_state:
    q = st.session_state.active_search
    
    # ── A. マイ辞書（スプシ）の検索 ──
    if not df_mydict.empty:
        q_low = q.lower()
        db_match = df_mydict[
            df_mydict["試薬名"].astype(str).str.lower().str.contains(q_low, na=False) |
            df_mydict["略称や通称"].astype(str).str.lower().str.contains(q_low, na=False)
        ]
        if not db_match.empty:
            row = db_match.iloc[0]
            st.session_state.db_result = {
                "分類": "試薬", "主原料": False, "試薬名": row["試薬名"],
                "分子量": to_float(row["分子量"]), "密度(g/mL)": to_float(row["密度"]),
                "融点(℃)": to_float(row["融点"]), "沸点(℃)": to_float(row["沸点"]),
                "当量(Eq)": None, "重量(mg)": None, "体積(mL)": None, "モル数(mmol)": None,
                "CAS番号": row.get("CAS番号", ""), "コメント": row.get("コメント", "")
            }

    # ── B. PubChem API の検索 ──
    if "api_result" not in st.session_state:
        with st.spinner("PubChemからデータを取得中..."):
            cid_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastidentity/name/{q}/cids/JSON"
            res_cid = requests.get(cid_url)
            if res_cid.status_code != 200:
                cid_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{q}/cids/JSON"
                res_cid = requests.get(cid_url)

            if res_cid.status_code == 200:
                cid = res_cid.json()["IdentifierList"]["CID"][0]
                res_prop = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/MolecularWeight,CanonicalSMILES,Title/JSON")
                
                mw, title = None, q
                if res_prop.status_code == 200:
                    prop_data = res_prop.json()["PropertyTable"]["Properties"][0]
                    mw = prop_data.get("MolecularWeight", None)
                    title = prop_data.get("Title", q)
                
                # CAS番号の取得 (Synonymsから正規表現で抽出)
                cas_number = None
                res_syn = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON")
                if res_syn.status_code == 200:
                    synonyms = res_syn.json().get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
                    for syn in synonyms:
                        if re.match(r'^\d{2,7}-\d{2}-\d$', str(syn)):
                            cas_number = syn
                            break
                
                props = {"density": None, "mp": None, "bp": None}
                res_view = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON")
                if res_view.status_code == 200:
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
                    parse_section(res_view.json().get("Record", {}).get("Section", []))
                
                st.session_state.api_result = {
                    "分類": "試薬", "主原料": False, "試薬名": title,
                    "分子量": float(mw) if mw else None, "密度(g/mL)": props["density"], "融点(℃)": props["mp"], "沸点(℃)": props["bp"],
                    "当量(Eq)": None, "重量(mg)": None, "体積(mL)": None, "モル数(mmol)": None, "CAS番号": cas_number
                }
                st.session_state.api_cid = cid

    # ── C. どっちがどっちかわかるように2列並列で出力する ──
    col_db_view, col_api_view = st.columns(2)
    
    with col_db_view:
        st.subheader("📕 独自データベース（マイ辞書）の結果")
        if "db_result" in st.session_state:
            db_res = st.session_state.db_result
            st.markdown(f"**試薬名:** {db_res['試薬名']} ｜ **CAS番号:** {db_res.get('CAS番号', '')}  \n**コメント:** {db_res['コメント']}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("分子量", f"{db_res['分子量']:.2f}" if db_res['分子量'] else "データなし")
            c2.metric("密度", f"{db_res['密度(g/mL)']:.2f}" if db_res['密度(g/mL)'] else "データなし")
            c3.metric("融点", f"{db_res['融点(℃)']:.1f} ℃" if db_res['融点(℃)'] else "データなし")
            c4.metric("沸点", f"{db_res['沸点(℃)']:.1f} ℃" if db_res['沸点(℃)'] else "データなし")
            
            if st.button("➕ 独自DBのデータで表を構築する", type="primary", key="add_db_btn"):
                row_to_add = {k: v for k, v in db_res.items() if k not in ["コメント", "CAS番号"]}
                st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([row_to_add])], ignore_index=True)
                st.toast("マイ辞書から試薬を追加しました！")
                st.rerun()
        else:
            st.info("❌ マイ辞書には登録されていません。下部の管理画面から登録できます。")

    with col_api_view:
        st.subheader("🌐 PubChem API の検索結果")
        if "api_result" in st.session_state:
            api_res = st.session_state.api_result
            api_cid = st.session_state.api_cid
            
            col_api_img, col_api_metric = st.columns([1, 2])
            with col_api_img:
                st.image(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{api_cid}/PNG?record_type=2d&image_size=large", use_container_width=True)
            with col_api_metric:
                st.markdown(f"**正式名:** {api_res['試薬名']}  \n**CAS番号:** {api_res.get('CAS番号', '不明')}")
                c1, c2 = st.columns(2)
                c1.metric("分子量", f"{api_res['分子量']:.2f}" if api_res['分子量'] else "不明")
                c2.metric("密度", f"{api_res['密度(g/mL)']:.2f}" if api_res['密度(g/mL)'] else "データなし")
                c3, c4 = st.columns(2)
                c3.metric("融点", f"{api_res['融点(℃)']:.1f} ℃" if api_res['融点(℃)'] else "データなし")
                c4.metric("沸点", f"{api_res['沸点(℃)']:.1f} ℃" if api_res['沸点(℃)'] else "データなし")
            
            if st.button("➕ PubChem APIのデータで表を構築する", key="add_api_btn"):
                api_row_to_add = {k: v for k, v in api_res.items() if k != "CAS番号"}
                st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([api_row_to_add])], ignore_index=True)
                st.toast("APIから試薬を追加しました！")
                st.rerun()
        else:
            st.info("❌ PubChemでも化合物が見つかりませんでした。")

st.markdown("---")

# --- 4. メイン計算シート（3段構成） ---
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
for src_df, cat_name in [(edited_reagents, "試薬"), (edited_solvents, "溶媒"), (edited_products, "生成物")]:
    mask = src_df["分類"] != cat_name
    if mask.any():
        for _, row in src_df[mask].iterrows():
            if row["分類"] == "試薬": st.session_state.df_reagents = pd.concat([st.session_state.df_reagents, pd.DataFrame([row])], ignore_index=True)
            elif row["分類"] == "溶媒": st.session_state.df_solvents = pd.concat([st.session_state.df_solvents, pd.DataFrame([row])], ignore_index=True)
            elif row["分類"] == "生成物": st.session_state.df_products = pd.concat([st.session_state.df_products, pd.DataFrame([row])], ignore_index=True)
        if cat_name == "試薬": st.session_state.df_reagents = src_df[~mask].reset_index(drop=True)
        elif cat_name == "溶媒": st.session_state.df_solvents = src_df[~mask].reset_index(drop=True)
        elif cat_name == "生成物": st.session_state.df_products = src_df[~mask].reset_index(drop=True)
        migrated = True
if migrated: st.rerun()

# --- 計算・リセットボタン ---
st.markdown("---")
col_calc, col_clear, _ = st.columns([1.5, 1, 4])
calc_triggered = col_calc.button("⚙️ 計算実行 (空きマスを埋める)", type="primary", use_container_width=True)
if col_clear.button("🔄 シートをすべてクリア", use_container_width=True):
    st.session_state.df_reagents = st.session_state.df_reagents.iloc[0:0]
    st.session_state.df_solvents = st.session_state.df_solvents.iloc[0:0]
    st.session_state.df_products = st.session_state.df_products.iloc[0:0]
    st.rerun()

# --- 5. 計算ロジック ---
if calc_triggered:
    df_calc_r, df_calc_s, df_calc_p = edited_reagents.copy(), edited_solvents.copy(), edited_products.copy()
    base_mask = df_calc_r["主原料"].fillna(False).astype(bool)
    base_mmol, base_w_g = None, None
    
    if not base_mask.any() and len(df_calc_r) > 0:
        st.error("⚠️ 『試薬』表で『主原料』にチェックを入れてください。")
    elif len(df_calc_r) > 0:
        base_idx = df_calc_r[base_mask].index[0]
        base_mw, base_d, base_w, base_v = to_float(df_calc_r.loc[base_idx, "分子量"]), to_float(df_calc_r.loc[base_idx, "密度(g/mL)"]), to_float(df_calc_r.loc[base_idx, "重量(mg)"]), to_float(df_calc_r.loc[base_idx, "体積(mL)"])
        base_mmol_input = to_float(df_calc_r.loc[base_idx, "モル数(mmol)"])
        
        if base_mmol_input is not None: base_mmol = base_mmol_input
        elif base_w is not None and base_mw is not None and base_mw > 0: base_mmol = base_w / base_mw
        elif base_v is not None and base_d is not None and base_mw is not None and base_mw > 0: base_mmol = base_v * base_d * 1000 / base_mw
            
        if base_mmol is not None:
            df_calc_r.at[base_idx, "当量(Eq)"] = format_val(1.0, "{:.2f}")
            df_calc_r.at[base_idx, "モル数(mmol)"] = format_val(base_mmol, "{:.3f}")
            calc_base_w = base_mmol * base_mw if base_mw else "計算不能"
            df_calc_r.at[base_idx, "重量(mg)"] = format_val(calc_base_w, "{:.1f}")
            if calc_base_w != "計算不能": base_w_g = calc_base_w / 1000.0
            df_calc_r.at[base_idx, "体積(mL)"] = format_val(calc_base_w / (base_d * 1000), "{:.3f}") if (calc_base_w != "計算不能" and base_d and base_d > 0) else ( "計算不能" if base_v is None else format_val(base_v, "{:.3f}") )

            for idx, row in df_calc_r.iterrows():
                if idx == base_idx: continue
                mw, d, eq, w, v, mmol_input = to_float(row["分子量"]), to_float(row["密度(g/mL)"]), to_float(row["当量(Eq)"]), to_float(row["重量(mg)"]), to_float(row["体積(mL)"]), to_float(row["モル数(mmol)"])
                calc_mmol = mmol_input if mmol_input is not None else (base_mmol * eq if eq is not None else (w / mw if w and mw else (v * d * 1000 / mw if v and d and mw else None)))
                if not any(x is not None for x in [eq, w, v, mmol_input]): continue
                if calc_mmol is not None:
                    calc_v = (calc_mmol * mw) / (d * 1000) if (mw and d and d > 0) else "計算不能"
                    df_calc_r.at[idx, "当量(Eq)"] = format_val(calc_mmol / base_mmol, "{:.2f}")
                    df_calc_r.at[idx, "重量(mg)"] = format_val(calc_mmol * mw, "{:.1f}") if mw else "計算不能"
                    df_calc_r.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                    df_calc_r.at[idx, "モル数(mmol)"] = format_val(calc_mmol, "{:.3f}")

    if base_mmol is not None:
        for idx, row in df_calc_s.iterrows():
            conc, ratio, v = to_float(row.get("設定濃度(M)")), to_float(row.get("溶媒倍率(v/w)")), to_float(row.get("体積(mL)"))
            if conc and conc > 0:
                calc_v = base_mmol / conc
                df_calc_s.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                df_calc_s.at[idx, "溶媒倍率(v/w)"] = format_val(calc_v / base_w_g if base_w_g else "計算不能", "{:.2f}")
            elif ratio and ratio > 0 and base_w_g:
                calc_v = base_w_g * ratio
                df_calc_s.at[idx, "体積(mL)"] = format_val(calc_v, "{:.3f}")
                df_calc_s.at[idx, "設定濃度(M)"] = format_val(base_mmol / calc_v if calc_v > 0 else "計算不能", "{:.2f}")
            elif v and v > 0:
                df_calc_s.at[idx, "設定濃度(M)"] = format_val(base_mmol / v, "{:.2f}")
                df_calc_s.at[idx, "溶媒倍率(v/w)"] = format_val(v / base_w_g if base_w_g else "計算不能", "{:.2f}")

        for idx, row in df_calc_p.iterrows():
            mw, eq, act_w = to_float(row.get("分子量")), to_float(row.get("当量(Eq)")), to_float(row.get("実収量(mg)"))
            if eq is None: eq = 1.0; df_calc_p.at[idx, "当量(Eq)"] = "1.00"
            theo_w = base_mmol * eq * mw if mw is not None else "計算不能"
            df_calc_p.at[idx, "理論収量(mg)"] = format_val(theo_w, "{:.1f}")
            if act_w is not None and isinstance(theo_w, float) and theo_w > 0:
                df_calc_p.at[idx, "収率(%)"] = format_val((act_w / theo_w) * 100, "{:.1f}")

    st.session_state.df_reagents, st.session_state.df_solvents, st.session_state.df_products = df_calc_r, df_calc_s, df_calc_p
    st.rerun()

# --- 6. 実験ノート用テキスト出力 ---
st.header("📝 3. 実験ノート用出力")
try:
    df_r = st.session_state.df_reagents
    is_base = df_r["主原料"].fillna(False).astype(bool)
    if not df_r[is_base].empty:
        base_row = df_r[is_base].iloc[0]
        note = "【実験操作】\n" + f"反応容器に {base_row.get('試薬名', '')} ({base_row.get('重量(mg)', '')} mg, {base_row.get('モル数(mmol)', '')} mmol) を仕込み、"
        for _, r in st.session_state.df_solvents.iterrows():
            if to_float(r.get('体積(mL)')) and to_float(r.get('体積(mL)')) > 0:
                note += f"{r.get('試薬名', '')} ({r.get('体積(mL)', '')} mL, {r.get('設定濃度(M)', '')} M) を加えて溶解させた。"
        for _, r in df_r[~is_base].iterrows():
            if to_float(r.get('重量(mg)')) and to_float(r.get('重量(mg)')) > 0:
                vol_str = f" [{r.get('体積(mL)', '')} mL]" if r.get('体積(mL)') not in [None, "", "計算不能"] else ""
                note += f"そこへ {r.get('試薬名', '')} ({r.get('重量(mg)', '')} mg{vol_str}, {r.get('モル数(mmol)', '')} mmol, {r.get('当量(Eq)', '')} Eq) を加えた。"
        for _, r in st.session_state.df_products.iterrows():
            if to_float(r.get('実収量(mg)')) and to_float(r.get('実収量(mg)')) > 0:
                note += f"\n反応終了後、精製を施すことで {r.get('試薬名', '')} を得た（{r.get('実収量(mg)', '')} mg, 収率: {r.get('収率(%)', '')} %）。"
        note += "\n\n【組成表】\n\n--- 試薬 ---\n" + df_to_markdown_safe(df_r, ["主原料", "試薬名", "分子量", "当量(Eq)", "重量(mg)", "体積(mL)", "モル数(mmol)"])
        if not st.session_state.df_solvents.empty: note += "\n--- 溶媒 ---\n" + df_to_markdown_safe(st.session_state.df_solvents, ["試薬名", "設定濃度(M)", "溶媒倍率(v/w)", "体積(mL)"])
        if not st.session_state.df_products.empty: note += "\n--- 生成物 ---\n" + df_to_markdown_safe(st.session_state.df_products, ["試薬名", "分子量", "当量(Eq)", "理論収量(mg)", "実収量(mg)", "収率(%)"])
        st.text_area("ELN貼り付け用テキスト", value=note, height=300)
    else: st.info("計算実行後にノートが生成されます。")
except Exception as e: st.error(f"出力エラー: {str(e)}")

st.markdown("---")

# --- 7. 📕 マイ辞書（Googleスプレッドシート）の管理画面セクション ---
st.header("📕 3. マイ辞書（独自データベース）の管理・直接編集")
st.write("スプレッドシートの中身をWebから直接編集できます。「試薬名」のみ必須で、その他は自由（任意）です。編集後は必ず一番下の保存ボタンを押してください。")

if not df_mydict.empty or 'conn' in locals():
    edited_mydict = st.data_editor(
        df_mydict,
        column_config={
            "試薬名": st.column_config.TextColumn("試薬名（必須）", required=True),
            "略称や通称": st.column_config.TextColumn("略称や通称 (例: AcOH, DCM)"),
            "分子量": st.column_config.NumberColumn("分子量 (g/mol)", format="%.2f"),
            "密度": st.column_config.NumberColumn("密度 (g/mL)", format="%.2f"),
            "沸点": st.column_config.NumberColumn("沸点 (℃)", format="%.1f"),
            "融点": st.column_config.NumberColumn("融点 (℃)", format="%.1f"),
            "CAS番号": st.column_config.TextColumn("CAS番号 (例: 64-19-7)"),
            "コメント": st.column_config.TextColumn("コメント・注意事項"),
        },
        num_rows="dynamic",
        key="my_dictionary_editor",
        use_container_width=True
    )
    
    if st.button("💾 変更をGoogleスプレッドシートに保存（研究室全員に同期）", type="primary"):
        with st.spinner("クラウド上のスプレッドシートに書き込み中..."):
            try:
                conn.update(data=edited_mydict)
                st.success("🎉 スプレッドシートの更新が完了しました！次回検索時から即座に反映されます。")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 保存に失敗しました。URLの設定やスプレッドシートの共有権限（編集者）を確認してください。エラー詳細: {str(e)}")
else:
    st.info("事前準備（secrets.toml の設定）が完了すると、ここにWeb編集画面が表示されます。")