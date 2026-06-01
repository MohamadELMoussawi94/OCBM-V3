import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import zipfile
import re

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Oman BM Market Monitoring Dashboard", page_icon="🇴🇲")

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .kpi-card { background: #ffffff; padding: 15px; border-radius: 12px; border: 1px solid #e2e8f0; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .kpi-value { font-size: 18px; font-weight: bold; color: #1e3a8a; }
    .kpi-label { font-size: 11px; color: #64748b; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
    .tariff-card { background: #f0f7ff; padding: 14px 18px; border-radius: 10px; border-left: 5px solid #1e3a8a; margin-bottom: 16px; }
    .tariff-title { font-size: 13px; font-weight: 700; color: #1e3a8a; margin-bottom: 8px; }
    .tariff-row { font-size: 12px; color: #334155; margin: 3px 0; }
    .tariff-rate { font-weight: 700; color: #dc2626; }
    </style>
""", unsafe_allow_html=True)

# --- COORDINATES ---
GOV_COORDS = {
    'مسقط': {'lat': 23.5859, 'lon': 58.4059}, 
    'ظفار': {'lat': 17.0151, 'lon': 54.0924}, 
    'مسندم': {'lat': 26.1555, 'lon': 56.2482}, 
    'البريمي': {'lat': 24.2503, 'lon': 55.8348}, 
    'الداخلية': {'lat': 22.8465, 'lon': 57.5349}, 
    'شمال الباطنة': {'lat': 24.1132, 'lon': 56.7118}, 
    'جنوب الباطنة': {'lat': 23.6335, 'lon': 57.5750}, 
    'شمال الشرقية': {'lat': 22.6289, 'lon': 58.4950}, 
    'جنوب الشرقية': {'lat': 22.1585, 'lon': 59.2736}, 
    'الظاهرة': {'lat': 23.2307, 'lon': 56.5167}, 
    'الوسطى': {'lat': 19.9844, 'lon': 56.2736}
}

# --- UTILS ---
@st.cache_data
def load_zip(uploaded_file):
    if not uploaded_file: return None
    data_dict = {}
    with zipfile.ZipFile(uploaded_file) as z:
        for f_name in z.namelist():
            if f_name.endswith(('.csv', '.xlsx')):
                with z.open(f_name) as f:
                    df = pd.read_csv(f) if f_name.endswith('.csv') else pd.read_excel(f)
                    df.columns = df.columns.str.strip()
                    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
                    data_dict[f_name] = df
    return data_dict

def get_hs4_codes(text):
    if pd.isna(text): return []
    nums = "".join(re.findall(r'\d+', str(text)))
    return [nums[i:i+4] for i in range(0, len(nums), 4) if len(nums[i:i+4]) == 4]

# ── TARIFF PDF HELPERS ────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_tariff_json(json_bytes: bytes) -> dict:
    """Load pre-built tariff lookup from JSON — instant (~0.03s)."""
    import json
    return json.loads(json_bytes.decode("utf-8"))

@st.cache_data(show_spinner=False)
def build_tariff_lookup_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse tariff PDF into lookup dict { hs8: [{hs12, rate, en_desc, ar_desc}] }.
    Rate is extracted from the SAME line as the HS code only — searching nearby
    lines causes wrong rates from neighbouring entries to bleed in.
    Install: pip install pypdf
    """
    import pypdf
    import io

    hs_pattern   = re.compile(r'(\d{2} \d{2} \d{2} \d{2} \d{2} \d{2})')
    rate_pattern = re.compile(r'(\d+%)')   # no \b — whitespace before digit, not word boundary
    lookup       = {}

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages:
        try:
            text_lines = (page.extract_text() or "").splitlines()
        except Exception:
            continue
        for line in text_lines:
            m = hs_pattern.search(line)
            if not m:
                continue
            hs12 = m.group(1).replace(" ", "")
            hs8  = hs12[:8]

            # ── Rate: same line only ──────────────────────────────────────────
            # Searching lines above causes wrong rates from neighbouring HS codes
            # to bleed in (e.g. country-specific 42% anti-dumping rates from
            # an adjacent entry being wrongly assigned to the current HS8 code)
            rates = rate_pattern.findall(line)
            rate  = rates[0] if rates else ""

            # ── English description: same line only ───────────────────────────
            en = ""
            ec = re.findall(r'[A-Za-z][A-Za-z\s\(\),\.\-/]{4,}', line)
            if ec:
                c = max(ec, key=len).strip()
                if len(c) > 5 and not re.match(r'^[A-Z]\s+[A-Z]', c):
                    en = c

            # ── Arabic description: same line only ────────────────────────────
            ar = ""
            ac = re.findall(r'[\u0600-\u06FF][\u0600-\u06FF\s\u202b\u202c\(\),\.\-/]{3,}', line)
            if ac:
                ar = max(ac, key=len).strip().replace("\u202b", "").replace("\u202c", "")

            if hs8 not in lookup:
                lookup[hs8] = []
            lookup[hs8].append({"hs12": hs12, "rate": rate, "en_desc": en, "ar_desc": ar})
    return lookup


def show_tariff_info(tariff_lookup: dict, hs8_codes: list):
    """
    Given a list of HS8 codes (strings), display a compact tariff card
    for each code found in the lookup.  Called just above the trade visuals.
    """
    if not tariff_lookup or not hs8_codes:
        return

    found_any = False
    for code in hs8_codes:
        norm = str(code).strip()[:8]
        rows = tariff_lookup.get(norm, [])
        if not rows:
            continue
        found_any = True
        rate = rows[0]['rate'] if rows else 'N/A'
        en   = rows[0]['en_desc'] if rows else ''
        ar   = rows[0]['ar_desc'] if rows else ''
        sub_count = len(rows)

        st.markdown(
            f"""
            <div class="tariff-card">
                <div class="tariff-title">📋 Oman Tariff 2026 — HS8: {norm}</div>
                <div class="tariff-row">🇬🇧 {en}</div>
                <div class="tariff-row">🇸🇦 {ar}</div>
                <div class="tariff-row">Customs Duty Rate: <span class="tariff-rate">{rate}</span>
                &nbsp;&nbsp;|&nbsp;&nbsp; Sub-codes (HS12): {sub_count}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if not found_any:
        st.info(f"No tariff data found for selected HS8 codes: {', '.join(hs8_codes)}")


# --- SESSION STATE ---
if "m_dfs"      not in st.session_state: st.session_state.m_dfs      = None
if "t_dfs"      not in st.session_state: st.session_state.t_dfs      = None
if "macro_dfs"  not in st.session_state: st.session_state.macro_dfs  = None
if "tariff_lkp" not in st.session_state: st.session_state.tariff_lkp = None

# --- SIDEBAR ---
st.sidebar.title("🛠️ Dashboard Control")
page = st.sidebar.radio("Go to Page:", ["Market Monitoring", "Macroeconomic Overview", "Market Structure Maps"])
uploaded_market_zip = st.sidebar.file_uploader("1. Upload Registry & Prices ZIP", type=["zip"])
uploaded_trade_zip  = st.sidebar.file_uploader("2. Upload Trade Data ZIP",        type=["zip"])
uploaded_macro_zip  = st.sidebar.file_uploader("3. Upload Macroeconomic Data ZIP", type=["zip"])

# ── NEW: Tariff lookup — upload JSON (instant) or PDF (slower fallback) ──────
st.sidebar.markdown("**4. Oman Tariff 2026**")
uploaded_tariff_json = st.sidebar.file_uploader(
    "Upload tariff_lookup.json ⚡ (recommended)",
    type=["json"],
    help="Pre-built lookup file — loads instantly. Generate it once using the provided Colab script."
)
uploaded_tariff_pdf = st.sidebar.file_uploader(
    "Or upload Tariff PDF (slower, ~60s)",
    type=["pdf"],
    help="Fallback if you don't have the JSON file yet."
)


launch_btn = st.sidebar.button("🚀 Launch Dashboard")

if launch_btn:
    if uploaded_market_zip: st.session_state.m_dfs     = load_zip(uploaded_market_zip)
    if uploaded_trade_zip:  st.session_state.t_dfs      = load_zip(uploaded_trade_zip)
    if uploaded_macro_zip:  st.session_state.macro_dfs  = load_zip(uploaded_macro_zip)
    if uploaded_tariff_json:
        with st.sidebar:
            with st.spinner("Loading tariff lookup…"):
                st.session_state.tariff_lkp = load_tariff_json(uploaded_tariff_json.read())
        st.sidebar.success(f"✅ Tariff loaded: {len(st.session_state.tariff_lkp):,} HS8 codes")
    elif uploaded_tariff_pdf:
        with st.sidebar:
            with st.spinner("Parsing tariff PDF… (~60s, one-time only)"):
                st.session_state.tariff_lkp = build_tariff_lookup_from_pdf(uploaded_tariff_pdf.read())
        st.sidebar.success(f"✅ Tariff indexed: {len(st.session_state.tariff_lkp):,} HS8 codes")

# --- PAGE 1: MARKET MONITORING ---
if page == "Market Monitoring":
    if st.session_state.m_dfs:
        all_dfs = st.session_state.m_dfs
        sector_mapping = {
            "الأسمنت ومنتجات الاسمنت": "قطاع الأسمنت ومنتجات الاسمنت", 
            "الاصباغ": "قطاع الاصباغ", 
            "الألمنيوم": "قطاع الألمنيوم", 
            "الأنابيب": "قطاع الأنابيب", 
            "البلاط والسيراميك والرخام": "قطاع البلاط والسيراميك والرخام", 
            "الحديد والصلب": "قطاع الحديد والصلب", 
            "الكابلات": "قطاع الكابلات"
        }
        pricing_product_map = {
            "الأسمنت ومنتجات الاسمنت": "الاسمنت", 
            "الاصباغ": "الدهانات", 
            "البلاط والسيراميك والرخام": "البلاط", 
            "الحديد والصلب": "الحديد", 
            "الكابلات": "الكوابل", 
            "الألمنيوم": "الألمنيوم", 
            "الأنابيب": "الأنابيب"
        }
        
        st.title("Oman BM Market Monitoring Dashboard")
        selected_sector = st.selectbox("Select Product Sector", list(sector_mapping.keys()))
        df_isic  = next((v for k, v in all_dfs.items() if sector_mapping[selected_sector] in k), None)
        df_price = next((v for k, v in all_dfs.items() if "final_pricing_data_dashboard" in k), None)
        final_hs4_list = []

        if df_isic is not None:
            isic_col = next((c for c in df_isic.columns if 'ISIC Code' in c), None)
            hs_col   = next((c for c in df_isic.columns if 'Relevant HS code' in c or 'Relevant HS Code' in c), None)
            isics    = sorted(list(set(["".join(re.findall(r'\d+', str(x))) for x in df_isic[isic_col].dropna().unique()])))
            raw_hss  = df_isic[hs_col].dropna().unique() if hs_col else []
            final_hs4_list = sorted(list(set([h for rh in raw_hss for h in get_hs4_codes(rh)])))

            k1, k2 = st.columns(2)
            with k1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">ISIC Codes</div><div class="kpi-value">{" / ".join(isics)}</div></div>', unsafe_allow_html=True)
            with k2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">HS4 Codes</div><div class="kpi-value">{" / ".join(final_hs4_list)}</div></div>', unsafe_allow_html=True)
            
            reg_cols = ['Company Name (Ar)الاسم باللغة العربية', 'CR Number رقم السجل', 'location عنوان المنشأة - رمز الولاية', 'المدينة الصناعية (مدائن)industrial estate', 'التنظيم الاقتصادي للمنشأة Economic organization', 'الشكل القانوني للمنشأة legal organization']
            st.subheader("Industrial Registry Directory")
            st.dataframe(df_isic[[c for c in reg_cols if c in df_isic.columns]], use_container_width=True, height=200)

        st.divider()

        if df_price is not None:
            search_term = pricing_product_map.get(selected_sector, selected_sector)
            p_base = df_price[df_price['المنتج'].str.contains(search_term, na=False, case=False)]
            if not p_base.empty:
                f1, f2, f3 = st.columns(3)
                with f1: val_desc = st.selectbox("Category", sorted(p_base['الوصف'].unique()))
                with f2:
                    sub_opts = p_base[p_base['الوصف'] == val_desc]['الوصف التفصيلي'].unique()
                    val_sub  = st.selectbox("Specification", ["Show All"] + sorted(list(sub_opts)))
                with f3:
                    years     = sorted(df_price['السنة'].unique())
                    sel_years = st.multiselect("Time Period", years, default=[years[-1]])

                mask = (df_price['الوصف'] == val_desc) & (df_price['السنة'].isin(sel_years))
                if val_sub != "Show All": mask &= (df_price['الوصف التفصيلي'] == val_sub)
                df_viz = df_price[mask].copy()

                if not df_viz.empty:
                    st.markdown(f"### Monitoring: {val_desc} Price Dispersion")
                    q1, q3  = df_viz['سعر المبيع'].quantile([0.25, 0.75])
                    mean_v  = df_viz['سعر المبيع'].mean()
                    iqr     = q3 - q1
                    up_f, lo_f = q3 + (1.5 * iqr), q1 - (1.5 * iqr)
                    
                    def get_cat(val): return 'Outlier (Purple)' if (val > up_f or val < lo_f) else ('High Price (>Q3 Red)' if val > q3 else ('Low Price (<Q1 Green)' if val < q1 else 'Market Range (Blue)'))
                    df_viz['Price Category'] = df_viz['سعر المبيع'].apply(get_cat)
                    c_map = {'Outlier (Purple)': '#a855f7', 'High Price (>Q3 Red)': '#ef4444', 'Low Price (<Q1 Green)': '#22c55e', 'Market Range (Blue)': '#3b82f6'}
                    
                    fig_scatter = px.scatter(df_viz, x='إسم الشركة', y='سعر المبيع', color='Price Category', color_discrete_map=c_map, hover_data=['إسم الشركة', 'المحافظة', 'الوصف التفصيلي', 'السنة'], height=500)
                    fig_scatter.add_hline(y=mean_v, line_dash="solid", line_color="black", annotation_text="Mean")
                    fig_scatter.add_hline(y=q3, line_dash="dash", line_color="#ef4444", annotation_text="Q3")
                    fig_scatter.add_hline(y=q1, line_dash="dash", line_color="#22c55e", annotation_text="Q1")
                    fig_scatter.update_xaxes(showticklabels=False)
                    st.plotly_chart(fig_scatter, use_container_width=True)

                    c1, c2 = st.columns(2)
                    with c1:
                        trend    = df_price[df_price['الوصف'] == val_desc].groupby('السنة')['سعر المبيع'].mean().reset_index()
                        fig_trend = px.line(trend, x='السنة', y='سعر المبيع', markers=True, title="Average Price Trend")
                        fig_trend.update_xaxes(dtick=1, tickformat="d")
                        st.plotly_chart(fig_trend, use_container_width=True)
                    with c2:
                        gov_p = df_viz.groupby('المحافظة')['سعر المبيع'].mean().reset_index().sort_values('سعر المبيع')
                        st.plotly_chart(px.bar(gov_p, x='المحافظة', y='سعر المبيع', color='سعر المبيع', color_continuous_scale='Reds', title="Pricing by Governorate"), use_container_width=True)

                    st.divider()
                    st.subheader("📍 Regional Density & Concentration")
                    geo_agg = df_viz.groupby('المحافظة').agg({'إسم الشركة': lambda x: "<br>".join(sorted(list(set(x)))[:15]), 'المنتج': 'count'}).reset_index().rename(columns={'المنتج': 'Count', 'إسم الشركة': 'Companies'})
                    geo_agg['lat'] = geo_agg['المحافظة'].map(lambda x: GOV_COORDS.get(x, {}).get('lat'))
                    geo_agg['lon'] = geo_agg['المحافظة'].map(lambda x: GOV_COORDS.get(x, {}).get('lon'))
                    fig_map = px.scatter_mapbox(geo_agg.dropna(subset=['lat']), lat="lat", lon="lon", size="Count", color="Count", color_continuous_scale=px.colors.sequential.YlOrRd, size_max=40, zoom=5, mapbox_style="carto-positron", hover_name="المحافظة", hover_data={"lat": False, "lon": False, "Count": True, "Companies": True}, height=550)
                    st.plotly_chart(fig_map, use_container_width=True)

        if st.session_state.t_dfs:
            st.divider()
            st.header("🌍 Trade Performance (Imports & Exports)")
            t_dfs  = st.session_state.t_dfs
            m_exp  = next((v for k, v in t_dfs.items() if "Materials Exports" in k), None)
            m_imp  = next((v for k, v in t_dfs.items() if "Materials Imports" in k), None)
            mo_exp = next((v for k, v in t_dfs.items() if "Exports.xlsx" in k or k == "Exports.xlsx - Exports.csv"), None)
            mo_imp = next((v for k, v in t_dfs.items() if "Imports.xlsx" in k or k == "Imports.xlsx - Imports.csv"), None)

            if m_exp is not None and m_imp is not None and final_hs4_list:
                m_exp['HS_6DG'] = m_exp['HS_6DG'].astype(str)
                m_imp['HS_6DG'] = m_imp['HS_6DG'].astype(str)
                rel_hs6_df  = m_exp[m_exp['HS_6DG'].str.startswith(tuple(final_hs4_list))][['HS_6DG', 'ENG_DESC']].drop_duplicates()
                hs6_options = {row['HS_6DG']: f"{row['HS_6DG']} - {row['ENG_DESC']}" for _, row in rel_hs6_df.iterrows()}
                
                if hs6_options:
                    sel_hs6 = st.multiselect("Select HS6 Product Categories", list(hs6_options.keys()), format_func=lambda x: hs6_options[x], default=list(hs6_options.keys())[:1])

                    # ── TARIFF RATE TABLE (between HS6 selector and tabs) ─────────
                    if sel_hs6 and st.session_state.tariff_lkp and (mo_exp is not None or mo_imp is not None):
                        st.markdown("#### 🏷️ Customs Tariff Rates — HS8 Codes")

                        # Collect all HS8 codes from imports/exports that match selected HS6
                        hs8_pool = set()
                        for src_df in [mo_exp, mo_imp]:
                            if src_df is not None:
                                col = 'رمز النظام المنسق'
                                src_df[col] = src_df[col].astype(str)
                                matched = src_df[src_df[col].str[:6].isin(sel_hs6)][col].unique()
                                hs8_pool.update(str(c).strip()[:8] for c in matched)

                        # Build tariff rows
                        tariff_rows = []
                        for hs8 in sorted(hs8_pool):
                            rows = st.session_state.tariff_lkp.get(hs8, [])
                            if not rows:
                                continue
                            rate_str = rows[0].get('rate', '')
                            try:
                                rate_num = float(rate_str.replace('%', '').strip())
                            except (ValueError, AttributeError):
                                rate_num = None
                            tariff_rows.append({
                                'HS8 Code':        hs8,
                                'English Desc':    rows[0].get('en_desc', '')[:80],
                                'Arabic Desc':     rows[0].get('ar_desc', '')[:80],
                                'Duty Rate':       rate_str if rate_str else '—',
                                '_rate_num':       rate_num,
                                'Sub-codes (HS12)': len(rows),
                            })

                        if tariff_rows:
                            tariff_df = pd.DataFrame(tariff_rows)

                            # Filter toggle
                            tf_col1, tf_col2, tf_col3 = st.columns([1.2, 1.2, 2])
                            with tf_col1:
                                rate_filter = st.radio(
                                    "Filter by Rate",
                                    ["All", "≤ 5%", "> 5%", "0%"],
                                    horizontal=True,
                                    key="tariff_rate_filter"
                                )
                            with tf_col2:
                                show_only_found = st.checkbox("Only codes with tariff data", value=True, key="tariff_only_found")
                            with tf_col3:
                                pass  # spacer

                            # Apply filter
                            disp_df = tariff_df.copy()
                            if rate_filter == "≤ 5%":
                                disp_df = disp_df[disp_df['_rate_num'].notna() & (disp_df['_rate_num'] <= 5)]
                            elif rate_filter == "> 5%":
                                disp_df = disp_df[disp_df['_rate_num'].notna() & (disp_df['_rate_num'] > 5)]
                            elif rate_filter == "0%":
                                disp_df = disp_df[disp_df['_rate_num'].notna() & (disp_df['_rate_num'] == 0)]

                            display_cols = ['HS8 Code', 'Duty Rate', 'English Desc', 'Arabic Desc', 'Sub-codes (HS12)']
                            disp_df = disp_df[display_cols].reset_index(drop=True)

                            # Summary badges
                            total_codes  = len(tariff_df)
                            zero_codes   = int((tariff_df['_rate_num'] == 0).sum())
                            low_codes    = int((tariff_df['_rate_num'].notna() & (tariff_df['_rate_num'] <= 5)).sum())
                            high_codes   = int((tariff_df['_rate_num'].notna() & (tariff_df['_rate_num'] > 5)).sum())
                            na_codes     = int(tariff_df['_rate_num'].isna().sum())

                            b1, b2, b3, b4, b5 = st.columns(5)
                            with b1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total HS8 Codes</div><div class="kpi-value">{total_codes}</div></div>', unsafe_allow_html=True)
                            with b2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Zero Duty (0%)</div><div class="kpi-value" style="color:#22c55e;">{zero_codes}</div></div>', unsafe_allow_html=True)
                            with b3: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Low Duty (≤5%)</div><div class="kpi-value" style="color:#3b82f6;">{low_codes}</div></div>', unsafe_allow_html=True)
                            with b4: st.markdown(f'<div class="kpi-card"><div class="kpi-label">High Duty (>5%)</div><div class="kpi-value" style="color:#ef4444;">{high_codes}</div></div>', unsafe_allow_html=True)
                            with b5: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Rate Unknown</div><div class="kpi-value" style="color:#94a3b8;">{na_codes}</div></div>', unsafe_allow_html=True)

                            st.markdown("<br>", unsafe_allow_html=True)

                            # Styled table
                            def _color_rate(val):
                                try:
                                    n = float(str(val).replace('%','').strip())
                                    if n == 0:   return 'color: #22c55e; font-weight: 700'
                                    if n <= 5:   return 'color: #3b82f6; font-weight: 700'
                                    return              'color: #ef4444; font-weight: 700'
                                except (ValueError, AttributeError):
                                    return 'color: #94a3b8'

                            if not disp_df.empty:
                                st.caption(f"Showing {len(disp_df)} HS8 code(s) — 🟢 0% duty &nbsp;|&nbsp; 🔵 ≤5% &nbsp;|&nbsp; 🔴 >5%")
                                st.dataframe(
                                    disp_df.style.applymap(_color_rate, subset=['Duty Rate']),
                                    use_container_width=True,
                                    height=min(40 + len(disp_df) * 35, 420),
                                    hide_index=True,
                                )
                            else:
                                st.info(f"No HS8 codes match the selected filter: **{rate_filter}**")

                            st.markdown("---")

                    t1, t2, t3 = st.tabs(["Country Distribution (HS6)", "Monthly Trade Trends (HS8+)", "📊 Trade Balance Analysis (HS6)"])

                    with t1:
                        v_cols   = [c for c in m_exp.columns if "Value in OMR" in c]
                        years    = sorted([re.search(r'\d{4}', c).group() for c in v_cols])
                        sel_year = st.selectbox("Select Year", years, index=len(years)-1)
                        target   = f"{sel_year} (Value in OMR)"
                        c_exp    = m_exp[m_exp['HS_6DG'].isin(sel_hs6)].groupby('COUNTRY')[target].sum().reset_index()
                        c_imp    = m_imp[m_imp['HS_6DG'].isin(sel_hs6)].groupby('COUNTRY')[target].sum().reset_index()
                        col_v1, col_v2 = st.columns(2)
                        with col_v1: st.plotly_chart(px.bar(c_exp.sort_values(target), x=target, y='COUNTRY', orientation='h', title=f"Exports by Destination ({sel_year})", color_discrete_sequence=['#1e3a8a']), use_container_width=True)
                        with col_v2: st.plotly_chart(px.bar(c_imp.sort_values(target), x=target, y='COUNTRY', orientation='h', title=f"Imports by Source ({sel_year})",      color_discrete_sequence=['#ef4444']), use_container_width=True)

                    with t2:
                        if mo_exp is not None and mo_imp is not None:
                            mo_exp['رمز النظام المنسق'] = mo_exp['رمز النظام المنسق'].astype(str)
                            mo_imp['رمز النظام المنسق'] = mo_imp['رمز النظام المنسق'].astype(str)
                            desc_col    = 'الوصف الإحصائي لرمز النظام المنسق باللغة العربية'
                            rel_mo_exp  = mo_exp[mo_exp['رمز النظام المنسق'].str.startswith(tuple(sel_hs6))]
                            rel_mo_imp  = mo_imp[mo_imp['رمز النظام المنسق'].str.startswith(tuple(sel_hs6))]
                            hs8_map     = pd.concat([rel_mo_exp[['رمز النظام المنسق', desc_col]], rel_mo_imp[['رمز النظام المنسق', desc_col]]]).drop_duplicates()
                            hs8_options = {row['رمز النظام المنسق']: f"{row['رمز النظام المنسق']} - {row[desc_col]}" for _, row in hs8_map.iterrows()}

                            if hs8_options:
                                sel_hs8 = st.multiselect("Filter by 8-Digit Code", list(hs8_options.keys()), format_func=lambda x: hs8_options[x], default=list(hs8_options.keys())[:1])

                                # ── TARIFF INFO CARD (above the chart) ──────────────
                                if st.session_state.tariff_lkp and sel_hs8:
                                    hs8_normalized = [str(c).strip()[:8] for c in sel_hs8]
                                    show_tariff_info(st.session_state.tariff_lkp, hs8_normalized)
                                # ────────────────────────────────────────────────────

                                df_trend_exp = rel_mo_exp[rel_mo_exp['رمز النظام المنسق'].isin(sel_hs8)].groupby('الشهر')['القيمة (ر.ع)'].sum().reset_index()
                                df_trend_imp = rel_mo_imp[rel_mo_imp['رمز النظام المنسق'].isin(sel_hs8)].groupby('الشهر')['القيمة (ر.ع)'].sum().reset_index()
                                fig_trade    = go.Figure()
                                fig_trade.add_trace(go.Scatter(x=pd.to_datetime(df_trend_exp['الشهر']), y=df_trend_exp['القيمة (ر.ع)'], name='Exports', line=dict(color='#1e3a8a', width=3)))
                                fig_trade.add_trace(go.Scatter(x=pd.to_datetime(df_trend_imp['الشهر']), y=df_trend_imp['القيمة (ر.ع)'], name='Imports', line=dict(color='#ef4444', width=3)))
                                fig_trade.update_layout(title="Trade Value Trend (2018-2025)", xaxis_title="Timeline", yaxis_title="Value (OMR)", hovermode="x unified")
                                st.plotly_chart(fig_trade, use_container_width=True)

                    # ── TAB 3: TRADE BALANCE ANALYSIS (HS6 only) ─────────────────
                    with t3:
                        if not sel_hs6:
                            st.info("Select at least one HS6 code above to view the trade balance.")
                        else:
                            # ── helper: extract available years from HS6 column names ──
                            def _hs6_years(df):
                                return sorted(set(
                                    int(re.search(r'\d{4}', c).group())
                                    for c in df.columns
                                    if re.search(r'\d{4}', str(c)) and "Value in OMR" in c
                                ))

                            avail_years_tb = sorted(set(
                                _hs6_years(m_exp) + _hs6_years(m_imp)
                            ))
                            sel_years_tb = st.multiselect(
                                "Select Years", avail_years_tb,
                                default=avail_years_tb,
                                key="tb_years"
                            )
                            if not sel_years_tb:
                                st.warning("Select at least one year.")
                            else:
                                # ── aggregate annual exports & imports ────────────────
                                def _agg_annual(df, codes, years):
                                    sub = df[df['HS_6DG'].isin(codes)]
                                    rows = []
                                    for y in sorted(years):
                                        vc = f"{y} (Value in OMR)"
                                        kc = f"{y} (Weight in KG)"
                                        rows.append({
                                            'Year':      y,
                                            'Value_OMR': sub[vc].sum() if vc in sub.columns else 0,
                                            'Weight_KG': sub[kc].sum() if kc in sub.columns else 0,
                                        })
                                    return pd.DataFrame(rows)

                                ea = _agg_annual(m_exp, sel_hs6, sel_years_tb)
                                ia = _agg_annual(m_imp, sel_hs6, sel_years_tb)

                                tb = pd.merge(
                                    ea.rename(columns={'Value_OMR': 'Exp_OMR', 'Weight_KG': 'Exp_KG'}),
                                    ia.rename(columns={'Value_OMR': 'Imp_OMR', 'Weight_KG': 'Imp_KG'}),
                                    on='Year', how='outer'
                                ).fillna(0).sort_values('Year')
                                tb['Year']        = tb['Year'].astype(int)
                                tb['Balance_OMR'] = tb['Exp_OMR'] - tb['Imp_OMR']
                                tb['Coverage_Pct']= (tb['Exp_OMR'] / tb['Imp_OMR'].replace(0, float('nan')) * 100).round(1)
                                tb['YoY_Exp']     = tb['Exp_OMR'].pct_change().mul(100).round(1)
                                tb['YoY_Imp']     = tb['Imp_OMR'].pct_change().mul(100).round(1)

                                # ── Verdict KPIs ──────────────────────────────────────
                                total_exp = tb['Exp_OMR'].sum()
                                total_imp = tb['Imp_OMR'].sum()
                                total_bal = total_exp - total_imp
                                avg_cov   = (total_exp / total_imp * 100) if total_imp else 0
                                dep_flag  = "⚠️ Import Dependent" if avg_cov < 50 else ("✅ Export Oriented" if avg_cov > 100 else "🔄 Balanced Trade")

                                v1, v2, v3, v4 = st.columns(4)
                                with v1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total Exports</div><div class="kpi-value">{total_exp:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total Imports</div><div class="kpi-value">{total_imp:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v3: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Trade Balance</div><div class="kpi-value">{"+" if total_bal >= 0 else ""}{total_bal:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v4: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Status</div><div class="kpi-value" style="font-size:13px;">{dep_flag}<br><span style="font-size:11px;opacity:0.8;">Coverage {avg_cov:.1f}%</span></div></div>', unsafe_allow_html=True)

                                st.markdown("<br>", unsafe_allow_html=True)

                                # ── Chart 1: Exports vs Imports vs Balance ────────────
                                fig_tb = go.Figure()
                                fig_tb.add_trace(go.Bar(x=tb['Year'], y=tb['Exp_OMR'],    name='Exports (OMR)', marker_color='#1e3a8a'))
                                fig_tb.add_trace(go.Bar(x=tb['Year'], y=tb['Imp_OMR'],    name='Imports (OMR)', marker_color='#ef4444'))
                                fig_tb.add_trace(go.Scatter(x=tb['Year'], y=tb['Balance_OMR'], name='Balance (OMR)',
                                                            mode='lines+markers', line=dict(color='#22c55e', width=2.5, dash='dash'),
                                                            marker=dict(size=7), yaxis='y'))
                                fig_tb.update_layout(
                                    title="Annual Trade Balance — Exports vs Imports (OMR)",
                                    barmode='group', hovermode='x unified',
                                    xaxis=dict(dtick=1, tickformat='d'),
                                    yaxis=dict(title='Value (OMR)'),
                                    legend=dict(orientation='h', y=1.12),
                                )
                                st.plotly_chart(fig_tb, use_container_width=True)

                                # ── Chart 2: Coverage Ratio ───────────────────────────
                                fig_cov = go.Figure()
                                fig_cov.add_trace(go.Scatter(
                                    x=tb['Year'], y=tb['Coverage_Pct'],
                                    mode='lines+markers+text',
                                    line=dict(color='#f97316', width=2.5),
                                    marker=dict(size=8),
                                    text=tb['Coverage_Pct'].apply(lambda v: f"{v:.0f}%" if pd.notna(v) else ""),
                                    textposition='top center',
                                    name='Coverage Ratio (%)'
                                ))
                                fig_cov.add_hline(y=100, line_dash='dash', line_color='#94a3b8',
                                                  annotation_text='Break-even (100%)', annotation_position='bottom right')
                                fig_cov.update_layout(
                                    title="Export Coverage Ratio (Exports / Imports × 100)",
                                    xaxis=dict(dtick=1, tickformat='d'),
                                    yaxis=dict(title='Coverage (%)'),
                                    hovermode='x unified',
                                )
                                st.plotly_chart(fig_cov, use_container_width=True)

                                # ── Chart 3: YoY Growth ───────────────────────────────
                                tb_yoy = tb.dropna(subset=['YoY_Exp', 'YoY_Imp'])
                                if not tb_yoy.empty:
                                    fig_yoy = go.Figure()
                                    fig_yoy.add_trace(go.Bar(x=tb_yoy['Year'], y=tb_yoy['YoY_Exp'], name='Export Growth %', marker_color='#1e3a8a'))
                                    fig_yoy.add_trace(go.Bar(x=tb_yoy['Year'], y=tb_yoy['YoY_Imp'], name='Import Growth %', marker_color='#ef4444'))
                                    fig_yoy.add_hline(y=0, line_color='#64748b', line_width=1)
                                    fig_yoy.update_layout(
                                        title="Year-over-Year Growth Rate (%)",
                                        barmode='group', hovermode='x unified',
                                        xaxis=dict(dtick=1, tickformat='d'),
                                        yaxis=dict(title='Growth (%)'),
                                        legend=dict(orientation='h', y=1.12),
                                    )
                                    st.plotly_chart(fig_yoy, use_container_width=True)

                                # ── Top 5 Trading Partners ────────────────────────────
                                st.subheader("🌍 Top Trading Partners")
                                def _top_countries(df, codes, years, n=5):
                                    sub  = df[df['HS_6DG'].isin(codes)]
                                    vcols = [f"{y} (Value in OMR)" for y in years if f"{y} (Value in OMR)" in sub.columns]
                                    if not vcols: return pd.DataFrame()
                                    sub  = sub.copy()
                                    sub['Total'] = sub[vcols].sum(axis=1)
                                    return sub.groupby('COUNTRY')['Total'].sum().reset_index().sort_values('Total', ascending=False).head(n)

                                tp1, tp2 = st.columns(2)
                                with tp1:
                                    top_exp = _top_countries(m_exp, sel_hs6, sel_years_tb)
                                    if not top_exp.empty:
                                        st.plotly_chart(
                                            px.bar(top_exp, x='Total', y='COUNTRY', orientation='h',
                                                   title="Top 5 Export Destinations (OMR)",
                                                   color_discrete_sequence=['#1e3a8a'],
                                                   labels={'Total': 'Value (OMR)', 'COUNTRY': ''}),
                                            use_container_width=True
                                        )
                                with tp2:
                                    top_imp = _top_countries(m_imp, sel_hs6, sel_years_tb)
                                    if not top_imp.empty:
                                        st.plotly_chart(
                                            px.bar(top_imp, x='Total', y='COUNTRY', orientation='h',
                                                   title="Top 5 Import Origins (OMR)",
                                                   color_discrete_sequence=['#ef4444'],
                                                   labels={'Total': 'Value (OMR)', 'COUNTRY': ''}),
                                            use_container_width=True
                                        )

                                # ── Summary Table ─────────────────────────────────────
                                st.subheader("📋 Annual Summary Table")
                                tb_disp = tb.copy()
                                for col in ['Exp_OMR', 'Imp_OMR', 'Balance_OMR']:
                                    tb_disp[col] = tb_disp[col].apply(lambda v: f"{v:,.0f}")
                                tb_disp['Coverage_Pct'] = tb_disp['Coverage_Pct'].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "—")
                                tb_disp['YoY_Exp'] = tb_disp['YoY_Exp'].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
                                tb_disp['YoY_Imp'] = tb_disp['YoY_Imp'].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
                                tb_disp = tb_disp.rename(columns={
                                    'Year': 'Year', 'Exp_OMR': 'Exports (OMR)',
                                    'Imp_OMR': 'Imports (OMR)', 'Balance_OMR': 'Balance (OMR)',
                                    'Coverage_Pct': 'Coverage %', 'YoY_Exp': 'Export YoY',
                                    'YoY_Imp': 'Import YoY'
                                })[['Year', 'Exports (OMR)', 'Imports (OMR)', 'Balance (OMR)', 'Coverage %', 'Export YoY', 'Import YoY']]
                                st.dataframe(tb_disp, use_container_width=True, hide_index=True)
    else:
        st.info("Dashboard initialized. Please upload data and click 'Launch' to begin.")

# --- PAGE 2: MACROECONOMIC OVERVIEW ---
elif page == "Macroeconomic Overview":
    st.title("🇴🇲 Macroeconomic Overview")
    if st.session_state.macro_dfs:
        dfs = st.session_state.macro_dfs
        
        # Section 1: Economic Growth & Structure
        st.header("1. Economic Growth & Structure")
        m1, m2 = st.columns(2)
        with m1:
            f_gdp = next((v for k, v in dfs.items() if "GDP growth" in k), None)
            if f_gdp is not None:
                df_gdp = f_gdp.iloc[1:].copy().iloc[:, :2]
                df_gdp.columns = ['Year', 'Growth Rate']
                df_gdp['Growth Rate'] = pd.to_numeric(df_gdp['Growth Rate'], errors='coerce')
                st.plotly_chart(px.line(df_gdp, x='Year', y='Growth Rate', markers=True, title="GDP Annual Growth Rate (%)"), use_container_width=True)
        with m2:
            f_struct = next((v for k, v in dfs.items() if "Percentage of oil and non-oil" in k), None)
            if f_struct is not None:
                df_struct = f_struct.iloc[1:].copy().iloc[:, :3]
                df_struct.columns = ['Year', 'Oil Activities', 'Non-Oil Activities']
                df_struct = df_struct.melt(id_vars='Year', var_name='Sector', value_name='Contribution')
                df_struct['Contribution'] = pd.to_numeric(df_struct['Contribution'], errors='coerce') * 100
                # UPDATE 1: renamed title to "Oil vs Non-Oil % GDP"
                st.plotly_chart(px.area(df_struct, x='Year', y='Contribution', color='Sector', title="Oil vs Non-Oil % GDP"), use_container_width=True)

        # Section 2: External Sector & Trade Dynamics
        st.header("2. External Sector & Trade Dynamics")
        e1, e2 = st.columns(2)
        with e1:
            f_ti = next((v for k, v in dfs.items() if "Foreign trade indicators" in k), None)
            if f_ti is not None:
                years = f_ti.iloc[0, 1:].values; bm_e = f_ti.iloc[1, 1:].values; tot_e = f_ti.iloc[2, 1:].values
                df_ti = pd.DataFrame({'Year': years, 'BM Exports': bm_e, 'Total Exports': tot_e}).melt(id_vars='Year')
                df_ti['value'] = pd.to_numeric(df_ti['value'], errors='coerce')
                st.plotly_chart(px.line(df_ti, x='Year', y='value', color='variable', title="Trade Indicators (Value in OMR)"), use_container_width=True)
        with e2:
            f_bm = next((v for k, v in dfs.items() if "building materials exports" in k), None)
            if f_bm is not None:
                years = f_bm.iloc[0, 1:].values; df_bm = f_bm.iloc[2:].copy(); df_bm.columns = ['Sector'] + list(years)
                df_bm = df_bm.melt(id_vars='Sector', var_name='Year', value_name='Value')
                df_bm['Value'] = pd.to_numeric(df_bm['Value'], errors='coerce')
                st.plotly_chart(px.bar(df_bm, x='Year', y='Value', color='Sector', title="BM Exports by Sector (2010-2024)"), use_container_width=True)

        # Section 3: Investment & Business Environment
        st.header("3. Investment & Business Environment")
        i1, i2 = st.columns(2)
        with i1:
            f_fdi = next((v for k, v in dfs.items() if "FDI.xlsx" in k), None)
            if f_fdi is not None:
                # UPDATE 2: also show column 3 (نسبة تدفقات الاستثمار الأجنبي من الناتج المحلي %)
                df_fdi_raw = f_fdi.iloc[1:].copy()
                # Primary axis: FDI Value (col 1)
                df_fdi = df_fdi_raw.iloc[:, :2].copy()
                df_fdi.columns = ['Year', 'FDI Value (Million OMR)']
                df_fdi['FDI Value (Million OMR)'] = pd.to_numeric(df_fdi['FDI Value (Million OMR)'], errors='coerce')
                # Secondary axis: FDI as % of GDP (col 2)
                if df_fdi_raw.shape[1] >= 3:
                    df_fdi['FDI % of GDP'] = pd.to_numeric(df_fdi_raw.iloc[:, 2], errors='coerce')
                    fig_fdi = go.Figure()
                    fig_fdi.add_trace(go.Bar(
                        x=df_fdi['Year'], y=df_fdi['FDI Value (Million OMR)'],
                        name='FDI Value (Million OMR)', marker_color='#1e3a8a', yaxis='y1'
                    ))
                    fig_fdi.add_trace(go.Scatter(
                        x=df_fdi['Year'], y=df_fdi['FDI % of GDP'],
                        name='FDI % of GDP', mode='lines+markers',
                        line=dict(color='#ef4444', width=2), yaxis='y2'
                    ))
                    fig_fdi.update_layout(
                        title="Total FDI Inflows & % of GDP",
                        yaxis=dict(title='FDI Value (Million OMR)', side='left'),
                        yaxis2=dict(title='FDI % of GDP', side='right', overlaying='y', showgrid=False),
                        legend=dict(x=0, y=1.1, orientation='h'),
                        hovermode='x unified'
                    )
                    st.plotly_chart(fig_fdi, use_container_width=True)
                else:
                    # Fallback if file only has 2 columns
                    st.plotly_chart(px.line(df_fdi, x='Year', y='FDI Value (Million OMR)', title="Total FDI Inflow (Million OMR)", markers=True), use_container_width=True)
        with i2:
            f_fdis = next((v for k, v in dfs.items() if "FDI per sector" in k), None)
            if f_fdis is not None:
                df_fdis = f_fdis.iloc[1:].copy().iloc[:, :2]
                df_fdis.columns = ['Sector', 'Value']
                df_fdis['Value'] = pd.to_numeric(df_fdis['Value'], errors='coerce')
                # UPDATE 6: add 2024 to title
                st.plotly_chart(px.pie(df_fdis, names='Sector', values='Value', title="FDI Distribution by Sector (2024)"), use_container_width=True)

        i3, i4 = st.columns(2)
        with i3:
            f_bank = next((v for k, v in dfs.items() if "banking credit by sector" in k), None)
            if f_bank is not None:
                df_bank = f_bank.iloc[1:].copy().iloc[:, :2]
                df_bank.columns = ['Sector', 'Credit']
                df_bank['Credit'] = pd.to_numeric(df_bank['Credit'], errors='coerce')
                # UPDATE 7: add 2024 to title
                st.plotly_chart(px.bar(df_bank, x='Sector', y='Credit', title="Banking Credit by Sector (2024)", color='Credit'), use_container_width=True)
        with i4:
            f_sme = next((v for k, v in dfs.items() if "SME and percentage" in k), None)
            if f_sme is not None:
                df_sme = f_sme.iloc[1:].copy().iloc[:, :2]
                df_sme.columns = ['Sector', 'Count']
                df_sme['Count'] = pd.to_numeric(df_sme['Count'], errors='coerce')
                st.plotly_chart(px.bar(df_sme, x='Sector', y='Count', title="SMEs Distribution by Sector", color_discrete_sequence=['#22c55e']), use_container_width=True)

        i5, i6 = st.columns(2)
        with i5:
            f_riyada = next((v for k, v in dfs.items() if "riyada card holders" in k), None)
            if f_riyada is not None:
                df_rc = f_riyada.iloc[1:].copy().iloc[:, :2]
                df_rc.columns = ['Sector', 'Holders']
                # UPDATE 8: all values are percentages — multiply by 100 if stored as decimals (e.g. 0.25 → 25%)
                df_rc['Holders'] = pd.to_numeric(df_rc['Holders'], errors='coerce')
                if df_rc['Holders'].dropna().max() <= 1:
                    df_rc['Holders'] = df_rc['Holders'] * 100
                df_rc['Label'] = df_rc['Holders'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "")
                st.plotly_chart(
                    px.pie(df_rc, names='Sector', values='Holders',
                           title="Riyada Card Holders (Entrepreneurship Proxy)",
                           hover_data={'Label': True}
                    ).update_traces(texttemplate='%{label}<br>%{value:.1f}%', textposition='inside'),
                    use_container_width=True
                )
        with i6:
            f_fz_lic = next((v for k, v in dfs.items() if "free zones agreements" in k), None)
            if f_fz_lic is not None:
                # ── Parse actual file structure ───────────────────────────────
                # Row 0  : title row  → skip
                # Row 1  : headers   → col0=Indicator, col1=Year, col2..N=Region names
                # Rows 2+: data      → col0 has NaN gaps (forward-fill), col1=year, col2..N=values
                raw = pd.read_excel(
                    next((v for k, v in st.session_state.macro_dfs.items()
                          if "free zones agreements" in k), None)
                    if False else f_fz_lic,   # use already-loaded df directly
                    header=None
                ) if False else f_fz_lic.copy()

                # f_fz_lic was loaded with default header (row 0 becomes header).
                # The actual file has: row0=title, row1=real headers, rows2+=data.
                # load_zip reads with default pd.read_excel (header=0), so:
                #   - columns = title row cells  (col0="اتفاقيات...", col1=NaN, ...)
                #   - row 0 in df  = the real header row (المؤشر, السنة, zone names...)
                #   - rows 1+      = data

                # Extract region names from the real header row (df row 0)
                header_row   = raw.iloc[0]                        # المؤشر, السنة, zone1, zone2...
                region_names = [str(v).strip() for v in header_row.values[2:]
                                if pd.notna(v) and str(v).strip() != 'nan']

                # Data rows: row index 1 onwards
                data         = raw.iloc[1:].copy().reset_index(drop=True)
                indicator_col = data.iloc[:, 0].ffill()           # forward-fill merged indicator
                year_col      = data.iloc[:, 1]
                value_cols    = data.iloc[:, 2: 2 + len(region_names)]

                # Build long-format dataframe
                records = []
                for idx in range(len(data)):
                    indicator = str(indicator_col.iloc[idx]).strip()
                    year_val  = str(year_col.iloc[idx]).strip()
                    if not re.match(r'^\d{4}$', year_val):
                        continue
                    for r_idx, region in enumerate(region_names):
                        try:
                            val = float(value_cols.iloc[idx, r_idx])
                        except (ValueError, TypeError):
                            val = None
                        records.append({
                            'Indicator': indicator,
                            'Year':      year_val,
                            'Region':    region,
                            'Value':     val,
                        })

                df_fzl_long = pd.DataFrame(records).dropna(subset=['Value'])

                if not df_fzl_long.empty:
                    all_indicators = sorted(df_fzl_long['Indicator'].unique())
                    all_years      = sorted(df_fzl_long['Year'].unique())
                    all_regions    = sorted(df_fzl_long['Region'].unique())

                    # ── Filters (3 in a row) ──────────────────────────────────
                    fc1, fc2, fc3 = st.columns(3)
                    with fc1:
                        sel_fz_ind = st.selectbox(
                            "Indicator", all_indicators, key="fz_lic_ind"
                        )
                    with fc2:
                        sel_fz_year = st.selectbox(
                            "Year", all_years,
                            index=len(all_years) - 1, key="fz_lic_year"
                        )
                    with fc3:
                        sel_fz_region = st.multiselect(
                            "Region", all_regions,
                            default=all_regions, key="fz_lic_region"
                        )

                    df_fzl_plot = df_fzl_long[
                        (df_fzl_long['Indicator'] == sel_fz_ind) &
                        (df_fzl_long['Year']      == sel_fz_year) &
                        (df_fzl_long['Region'].isin(sel_fz_region if sel_fz_region else all_regions))
                    ]
                    st.plotly_chart(
                        px.bar(
                            df_fzl_plot, x='Region', y='Value',
                            color='Region',
                            title=f"Free Zones - {sel_fz_ind} ({sel_fz_year})",
                            labels={'Value': 'Count', 'Region': 'Free Zone'},
                        ),
                        use_container_width=True
                    )

        i7, i8 = st.columns(2)
        with i7:
            f_fz_inv = next((v for k, v in dfs.items() if "investments free zones" in k), None)
            if f_fz_inv is not None:
                df_fzi = f_fz_inv.iloc[1:].copy().iloc[:, :2]
                df_fzi.columns = ['Zone', 'Investment']
                df_fzi['Investment'] = pd.to_numeric(df_fzi['Investment'], errors='coerce')
                # UPDATE 4: add 2024 to title
                st.plotly_chart(px.bar(df_fzi, x='Zone', y='Investment', title="Free Zones - Investment Levels (Million OMR) (2024)", color='Investment'), use_container_width=True)
        with i8:
            f_fz_space = next((v for k, v in dfs.items() if "free zones space" in k), None)
            if f_fz_space is not None:
                df_fzs = f_fz_space.iloc[1:].copy().iloc[:, :2]
                df_fzs.columns = ['Zone', 'Utilized Space']
                df_fzs['Utilized Space'] = pd.to_numeric(df_fzs['Utilized Space'], errors='coerce')
                st.plotly_chart(px.pie(df_fzs, names='Zone', values='Utilized Space', title="Free Zones - Space Utilization"), use_container_width=True)

        # Section 4: Labor Market & Employment
        st.header("4. Labor Market & Employment")
        l1, l2 = st.columns(2)
        with l1:
            f_fz_work = next((v for k, v in dfs.items() if "workers free zones" in k), None)
            if f_fz_work is not None:
                df_fzw = f_fz_work.iloc[1:].copy().iloc[:, :3]
                df_fzw.columns = ['Zone', 'National', 'Expat']
                df_fzw = df_fzw[~df_fzw['Zone'].str.contains('الإجمالي|Total', na=False)]
                df_fzw = df_fzw.melt(id_vars='Zone', var_name='Type', value_name='Count')
                df_fzw['Count'] = pd.to_numeric(df_fzw['Count'], errors='coerce')
                st.plotly_chart(px.bar(df_fzw, x='Zone', y='Count', color='Type', barmode='group', title="Workers in Free Zones by Type (2024)"), use_container_width=True)
        
        with l2:
            f_oman = next((v for k, v in dfs.items() if "Omanization rate" in k), None)
            if f_oman is not None:
                df_om = f_oman.iloc[2:].copy().iloc[:, [0, 1, 2, 4]]
                df_om.columns = ['Zone', 'National', 'Expat', 'Rate']
                df_om['Rate'] = df_om['Rate'].astype(str).str.replace('%', '').str.replace(',', '.').pipe(pd.to_numeric, errors='coerce')
                df_om.loc[df_om['Rate'] < 1, 'Rate'] *= 100
                # UPDATE 5: add 2024 to title
                st.plotly_chart(px.bar(df_om.dropna(subset=['Rate']), x='Zone', y='Rate', title="Omanization Rate in Free Zones (%) (2024)", color='Rate', color_continuous_scale='Blues'), use_container_width=True)

    else:
        st.info("Dashboard initialized. Please upload data and click 'Launch' to begin.")


# ═══════════════════════════════════════════════════════════════════════════════
# --- PAGE 3: MARKET STRUCTURE MAPS (Kumu Embed) ---
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Market Structure Maps":
    st.title("🕸️ Market Structure Maps")
    st.caption("Live maps powered by Kumu — any updates made in Kumu reflect here immediately. Use the controls inside the map to filter, search, and click on any node for full company details.")

    KUMU_MAPS = {
        "الإسمنت ومنتجات الإسمنت — Cement":       "https://embed.kumu.io/8d6065c11942c7977f64b08398dd32cd#cement-sector",
        "الألمنيوم ومنتجاته — Aluminium":          "https://embed.kumu.io/155018e90214781ee809287a428f3520#aluminium-sector",
        "الكابلات والأسلاك — Cables":              "https://embed.kumu.io/19480dee8aa7048a1c3f83e4847137f2#cables-sector",
        "السيراميك والبلاط والرخام — Ceramics":    "https://embed.kumu.io/8919a22e2961faa5229044a594820c39#ceramic-sector",
        "الدهانات والطلاء — Paints":               "https://embed.kumu.io/791877c3e46a6982a7d165fe80d3161c#paints-sector",
        "الأنابيب — Pipes":                        "https://embed.kumu.io/dc0b4c22afb68e7ebe57b97e77d3e136#pipes-sector",
        "الحديد والصلب — Steel":                   "https://embed.kumu.io/477ef9de5fe3f4427cccf69e1567452b#steel-sector",
        "جميع القطاعات — Combined All Sectors":    "https://embed.kumu.io/3854a91959b526e92c9971f3e4269d70#oman-building-materials-sectors-systems-mapping",
    }

    selected_map = st.selectbox(
        "🔍 Select Sector",
        list(KUMU_MAPS.keys()),
        index=0,
        key="kumu_map_select"
    )

    embed_url = KUMU_MAPS[selected_map]

    import streamlit.components.v1 as components
    components.html(
        f"""
        <style>
          * {{ margin: 0; padding: 0; box-sizing: border-box; }}
          body {{ background: transparent; overflow: hidden; }}
          iframe {{
            width: 100%;
            height: 800px;
            border: none;
            border-radius: 8px;
            display: block;
          }}
        </style>
        <iframe
          src="{embed_url}"
          allowfullscreen
        ></iframe>
        """,
        height=810,
        scrolling=False,
    )
