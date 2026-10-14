import io
import re
import json
import zipfile
from contextlib import contextmanager

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

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


# ═══════════════════════════════════════════════════════════════════════════════
# GENERAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def df_elementwise(df, func):
    """DataFrame.map (pandas >= 2.1) with fallback to the old applymap."""
    return df.map(func) if hasattr(df, "map") else df.applymap(func)


def style_elementwise(styler, func, subset=None):
    """Styler.map (pandas >= 2.1) with fallback to the old applymap."""
    return styler.map(func, subset=subset) if hasattr(styler, "map") else styler.applymap(func, subset=subset)


def show_chart(fig):
    """Full-width plotly chart on both new and older Streamlit versions."""
    try:
        st.plotly_chart(fig, width="stretch")
    except TypeError:
        st.plotly_chart(fig, use_container_width=True)


def show_df(data, **kwargs):
    """Full-width dataframe on both new and older Streamlit versions."""
    try:
        st.dataframe(data, width="stretch", **kwargs)
    except TypeError:
        st.dataframe(data, use_container_width=True, **kwargs)


@contextmanager
def section_guard(name):
    """If one section fails, show the real reason and keep the rest of the page running."""
    try:
        yield
    except Exception as e:  # noqa: BLE001
        st.error(f"⚠️ {name} could not be displayed — {type(e).__name__}: {e}")


def to_num(series):
    """Convert a column to numbers, accepting '1,234', ' 12 ', Arabic digits/separators."""
    if pd.api.types.is_numeric_dtype(series):
        return series
    s = series.astype(str).str.strip()
    s = s.str.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.str.replace(",", "", regex=False).str.replace("٬", "", regex=False).str.replace("٫", ".", regex=False)
    s = s.str.replace("%", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


def clean_code(x):
    """
    Normalise an HS code to a digit string.
    Fixes the common Excel problems: 252329.0 → '252329', 2523.29.00 → '25232900',
    and lost leading zeros (odd length → padded with one zero).
    """
    if x is None:
        return ""
    if isinstance(x, float):
        if pd.isna(x):
            return ""
        x = f"{x:.0f}"
    s = str(x).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    s = re.sub(r"\D", "", s)
    if s and len(s) % 2 == 1:
        s = "0" + s
    return s


_AR_DIAC = re.compile(r"[ً-ْٰـ]")


def norm_ar(text):
    """Normalise Arabic/English text so spelling variants compare equal."""
    s = _AR_DIAC.sub("", str(text)).strip().lower()
    s = re.sub("[أإآٱ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", s)


def get_hs4_codes(text):
    if pd.isna(text):
        return []
    nums = "".join(re.findall(r"\d+", str(text)))
    return [nums[i:i + 4] for i in range(0, len(nums), 4) if len(nums[i:i + 4]) == 4]


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNORATE MATCHING (density map)
# ═══════════════════════════════════════════════════════════════════════════════

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
    'الوسطى': {'lat': 19.9844, 'lon': 56.2736},
}

# Spelling variants, English names, wilayats and industrial areas → governorate.
GOV_ALIASES = {
    'مسقط': ['muscat', 'masqat', 'مطرح', 'بوشر', 'السيب', 'العامرات', 'قريات', 'الرسيل', 'rusayl', 'seeb', 'muttrah', 'bausher', 'amerat', 'quriyat'],
    'ظفار': ['dhofar', 'dhufar', 'zufar', 'صلاله', 'طاقه', 'مرباط', 'رخيوت', 'ثمريت', 'ضلكوت', 'المزيونه', 'مقشن', 'سدح', 'شليم وجزر الحلانيات', 'ريسوت', 'salalah', 'raysut', 'mirbat', 'thumrait'],
    'مسندم': ['musandam', 'خصب', 'بخا', 'دبا', 'مدحاء', 'khasab', 'dibba', 'bukha'],
    'البريمي': ['buraimi', 'buraymi', 'محضه', 'السنينه', 'mahdah'],
    'الداخلية': ['dakhiliyah', 'dakhiliya', 'dakhliyah', 'dakhiliyya', 'interior', 'نزوي', 'بهلاء', 'بهلا', 'منح', 'الحمراء', 'ادم', 'ازكي', 'سمائل', 'بدبد', 'الجبل الاخضر', 'nizwa', 'bahla', 'izki', 'samail', 'sumail', 'bidbid', 'adam', 'manah'],
    'شمال الباطنة': ['north batinah', 'batinah north', 'north batina', 'صحار', 'شناص', 'لوي', 'صحم', 'الخابوره', 'السويق', 'sohar', 'suhar', 'shinas', 'liwa', 'saham', 'khaburah', 'suwaiq', 'suwayq'],
    'جنوب الباطنة': ['south batinah', 'batinah south', 'south batina', 'الرستاق', 'العوابي', 'نخل', 'وادي المعاول', 'بركاء', 'المصنعه', 'rustaq', 'barka', 'musannah', 'nakhal', 'awabi'],
    'شمال الشرقية': ['north sharqiyah', 'north sharqiya', 'sharqiyah north', 'north sharqiyyah', 'ابراء', 'المضيبي', 'بديه', 'وادي بني خالد', 'دماء والطائيين', 'القابل', 'سناو', 'ibra', 'mudhaibi', 'mudaybi', 'bidiyah', 'sinaw'],
    'جنوب الشرقية': ['south sharqiyah', 'south sharqiya', 'sharqiyah south', 'south sharqiyyah', 'صور', 'الكامل والوافي', 'جعلان بني بو حسن', 'جعلان بني بو علي', 'مصيره', 'sur', 'masirah', 'jalan bani bu ali', 'jalan bani bu hassan'],
    'الظاهرة': ['dhahirah', 'dhahira', 'dhahirah', 'zahirah', 'dhahiryah', 'عبري', 'ينقل', 'ضنك', 'ibri', 'yanqul', 'dank'],
    'الوسطى': ['wusta', 'wosta', 'هيماء', 'هيما', 'محوت', 'الدقم', 'الجازر', 'duqm', 'haima', 'haima', 'mahout', 'jazir'],
}

_EN_ARTICLES = re.compile(r"\b(al|ad|adh|ash|as|az|ar|an|at|ath|el)\b")
_GOV_NOISE = re.compile(r"محافظه|governorate|province|wilayat|wilayah|ولايه|المنطقه الصناعيه|industrial estate|industrial area|منطقه|region")


def gov_tokens(text):
    s = norm_ar(text)
    s = _GOV_NOISE.sub(" ", s)
    s = re.sub(r"[\-_/,\.()]", " ", s)
    s = _EN_ARTICLES.sub(" ", s)
    return tuple(sorted(t for t in s.split() if t))


def _build_gov_lookup():
    lookup = {}
    for canon in GOV_COORDS:
        lookup[gov_tokens(canon)] = canon
        for alias in GOV_ALIASES.get(canon, []):
            lookup[gov_tokens(alias)] = canon
    # longest aliases first so "north batinah" wins over shorter matches
    ordered = sorted(lookup.items(), key=lambda kv: (-len(kv[0]), -sum(len(t) for t in kv[0])))
    return lookup, ordered


GOV_LOOKUP, GOV_LOOKUP_ORDERED = _build_gov_lookup()


def match_governorate(value):
    """Return the canonical governorate for any spelling / English name / wilayat, or None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    toks = gov_tokens(value)
    if not toks:
        return None
    if toks in GOV_LOOKUP:
        return GOV_LOOKUP[toks]
    tok_set = set(toks)
    for key, canon in GOV_LOOKUP_ORDERED:
        if key and set(key).issubset(tok_set):
            return canon
    return None


def make_density_map(df, **kwargs):
    """px.scatter_map (plotly >= 5.24) with fallback to the old scatter_mapbox."""
    if hasattr(px, "scatter_map"):
        return px.scatter_map(df, map_style="carto-positron", **kwargs)
    return px.scatter_mapbox(df, mapbox_style="carto-positron", **kwargs)


def build_geo_table(df, gov_col='المحافظة', company_col='إسم الشركة'):
    """
    Aggregate records per governorate.
    Returns (matched_table, unmatched_counts) so nothing is dropped silently.
    """
    work = df.copy()
    work['_gov'] = work[gov_col].map(match_governorate) if gov_col in work.columns else None
    unmatched = (
        work[work['_gov'].isna()][gov_col].fillna('(blank)').astype(str).value_counts()
        if gov_col in work.columns else pd.Series(dtype=int)
    )
    matched = work.dropna(subset=['_gov'])
    if matched.empty:
        return pd.DataFrame(), unmatched

    def _company_list(names):
        uniq = sorted(set(str(n) for n in names if pd.notna(n) and str(n).strip()))
        shown = uniq[:25]
        extra = len(uniq) - len(shown)
        return "<br>".join(shown) + (f"<br>… and {extra} more" if extra > 0 else "")

    has_company = company_col in matched.columns
    agg = matched.groupby('_gov').agg(
        Records=('_gov', 'size'),
        Companies=(company_col, 'nunique') if has_company else ('_gov', 'size'),
        Company_List=(company_col, _company_list) if has_company else ('_gov', lambda x: ''),
    ).reset_index().rename(columns={'_gov': 'Governorate'})
    agg['lat'] = agg['Governorate'].map(lambda g: GOV_COORDS[g]['lat'])
    agg['lon'] = agg['Governorate'].map(lambda g: GOV_COORDS[g]['lon'])
    return agg.sort_values('Companies', ascending=False).reset_index(drop=True), unmatched


# ═══════════════════════════════════════════════════════════════════════════════
# FILE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _read_csv_bytes(raw):
    last_err = None
    for enc in ("utf-8-sig", "cp1256", "utf-16", "latin1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err


def _clean_frame(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df_elementwise(df, lambda x: x.strip() if isinstance(x, str) else x)


@st.cache_data(show_spinner=False)
def load_zip(zip_bytes: bytes):
    """
    Read every CSV/Excel file in a ZIP.
    - skips macOS '__MACOSX/._x.xlsx' junk and Excel '~$' lock files (these used to
      be picked up instead of the real file)
    - reads ALL sheets of an Excel file, not only the first one
    Returns (dict of DataFrames, list of notes about what was loaded/skipped).
    """
    data, notes = {}, []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for f_name in z.namelist():
            base = f_name.split("/")[-1]
            low = base.lower()
            if f_name.startswith("__MACOSX") or base.startswith((".", "~$")) or not low.endswith((".csv", ".xlsx", ".xls", ".xlsm")):
                continue
            raw = z.read(f_name)
            try:
                if low.endswith(".csv"):
                    sheets = {None: _read_csv_bytes(raw)}
                else:
                    sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None)
            except Exception as e:  # noqa: BLE001
                notes.append(f"❌ Could not read {f_name}: {e}")
                continue

            frames = [(name, _clean_frame(df)) for name, df in sheets.items() if df is not None and not df.dropna(how="all").empty]
            if not frames:
                notes.append(f"⚠️ {f_name} is empty")
                continue

            same_cols = len(frames) > 1 and all(list(df.columns) == list(frames[0][1].columns) for _, df in frames)
            if same_cols:
                data[f_name] = pd.concat([df for _, df in frames], ignore_index=True)
                notes.append(f"✅ {f_name}: {len(data[f_name]):,} rows ({len(frames)} sheets combined)")
            else:
                data[f_name] = frames[0][1]
                notes.append(f"✅ {f_name}: {len(frames[0][1]):,} rows")
                for name, df in frames[1:]:
                    data[f"{f_name} - {name}"] = df
                    notes.append(f"✅ {f_name} [sheet: {name}]: {len(df):,} rows")
    return data, notes


def _direction(name):
    n = norm_ar(name)
    if "import" in n or "واردات" in n:
        return "imp"
    if "export" in n or "صادرات" in n:
        return "exp"
    return None


def prepare_trade(t_dfs):
    """Find the 4 trade files by their columns and clean codes + numbers once."""
    roles = {"hs6_exp": None, "hs6_imp": None, "mo_exp": None, "mo_imp": None}
    for k, df in t_dfs.items():
        d = _direction(k)
        if d is None:
            continue
        if "HS_6DG" in df.columns:
            roles.setdefault(f"hs6_{d}", None)
            if roles[f"hs6_{d}"] is None:
                roles[f"hs6_{d}"] = df.copy()
        elif "رمز النظام المنسق" in df.columns:
            if roles[f"mo_{d}"] is None:
                roles[f"mo_{d}"] = df.copy()

    for key in ("hs6_exp", "hs6_imp"):
        df = roles[key]
        if df is None:
            continue
        df['HS_6DG'] = df['HS_6DG'].map(clean_code).str.zfill(6)
        for c in df.columns:
            if "Value in OMR" in c or "Weight in KG" in c:
                df[c] = to_num(df[c]).fillna(0)
        if 'COUNTRY' in df.columns:
            df['COUNTRY'] = df['COUNTRY'].astype(str).str.strip()

    for key in ("mo_exp", "mo_imp"):
        df = roles[key]
        if df is None:
            continue
        df['رمز النظام المنسق'] = df['رمز النظام المنسق'].map(clean_code)
        if 'القيمة (ر.ع)' in df.columns:
            df['القيمة (ر.ع)'] = to_num(df['القيمة (ر.ع)']).fillna(0)
        if 'الشهر' in df.columns:
            parsed = pd.to_datetime(df['الشهر'], errors='coerce')
            df['_month'] = parsed if parsed.notna().any() else df['الشهر']
    return roles


def prepare_market(m_dfs):
    """Clean the pricing file once: numeric prices, consistent years, trimmed governorates."""
    out = dict(m_dfs)
    for k, df in m_dfs.items():
        if "final_pricing_data_dashboard" in k:
            df = df.copy()
            if 'سعر المبيع' in df.columns:
                df['سعر المبيع'] = to_num(df['سعر المبيع'])
            if 'السنة' in df.columns:
                yrs = to_num(df['السنة'])
                if yrs.notna().any():
                    df['السنة'] = yrs.astype('Int64')
            for c in ('المحافظة', 'المنتج', 'الوصف', 'الوصف التفصيلي', 'إسم الشركة'):
                if c in df.columns:
                    df[c] = df[c].astype(str).str.strip().replace({'nan': pd.NA, '': pd.NA})
            out[k] = df
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# TARIFF HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def norm_rate(v):
    """Turn 5, 0.05, '5', '5 %', 'Free', 'معفاة' … into '5%' / '0%'."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (int, float)):
        num = float(v) * 100 if 0 < float(v) < 1 else float(v)
        return f"{num:g}%"
    s = str(v).strip()
    if not s:
        return ""
    if re.search(r"معف|free|exempt", s, re.IGNORECASE):
        return "0%"
    m = re.search(r"\d+(?:\.\d+)?", s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    return f"{float(m.group()):g}%" if m else s


def rate_to_num(rate):
    m = re.search(r"\d+(?:\.\d+)?", str(rate))
    return float(m.group()) if m else None


def normalize_tariff_lookup(raw):
    """
    Accept any reasonable JSON layout and return { 'HS8': [ {hs12, rate, en_desc, ar_desc}, … ] }.
    Keys like '2523.29.00', '25232900.0', 2523290000 or 12-digit codes all end up as 8-digit strings.
    """
    if isinstance(raw, dict) and len(raw) == 1 and isinstance(next(iter(raw.values())), (dict, list)) \
            and not clean_code(next(iter(raw.keys()))):
        raw = next(iter(raw.values()))      # unwrap {"data": {...}} style files

    if isinstance(raw, list):
        items = []
        for r in raw:
            if isinstance(r, dict):
                key = next((r[c] for c in ("hs8", "hs12", "hs_code", "code", "HS Code", "hs") if c in r and r[c] not in (None, "")), None)
                items.append((key, r))
    elif isinstance(raw, dict):
        items = list(raw.items())
    else:
        items = []

    out = {}
    for key, val in items:
        code = clean_code(key)
        if len(code) < 6:
            continue
        rows = val if isinstance(val, list) else [val]
        for r in rows:
            if isinstance(r, dict):
                rate = next((r[c] for c in ("rate", "duty", "duty_rate", "Duty Rate", "customs_duty", "الرسم") if c in r), "")
                rec = {
                    "hs12": clean_code(r.get("hs12", code)) or code,
                    "rate": norm_rate(rate),
                    "en_desc": str(r.get("en_desc", r.get("description", "")) or ""),
                    "ar_desc": str(r.get("ar_desc", r.get("arabic_description", "")) or ""),
                }
            else:
                rec = {"hs12": code, "rate": norm_rate(r), "en_desc": "", "ar_desc": ""}
            out.setdefault(code[:8].ljust(8, "0"), []).append(rec)
    return out


@st.cache_data(show_spinner=False)
def load_tariff_json(json_bytes: bytes) -> dict:
    raw = json.loads(json_bytes.decode("utf-8-sig"))
    return normalize_tariff_lookup(raw)


@st.cache_data(show_spinner=False)
def build_tariff_lookup_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse tariff PDF into { hs8: [{hs12, rate, en_desc, ar_desc}] }.
    The rate is taken from the SAME line as the HS code only, so neighbouring
    entries cannot bleed their rates into the current code.
    """
    import pypdf

    hs_patterns = [
        re.compile(r'(\d{2} \d{2} \d{2} \d{2} \d{2} \d{2})'),
        re.compile(r'(\d{4}\.\d{2}\.\d{2}(?:\.\d{2}){0,2})'),
        re.compile(r'(?<!\d)(\d{8}(?:\d{2}){0,2})(?!\d)'),
    ]
    rate_pattern = re.compile(r'(\d+(?:\.\d+)?)\s*%')
    free_pattern = re.compile(r'معف|\bfree\b|\bexempt', re.IGNORECASE)
    lookup = {}

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages:
        try:
            text_lines = (page.extract_text() or "").splitlines()
        except Exception:  # noqa: BLE001
            continue
        for line in text_lines:
            m = None
            for pat in hs_patterns:
                m = pat.search(line)
                if m:
                    break
            if not m:
                continue
            hs12 = re.sub(r"\D", "", m.group(1))
            hs8 = hs12[:8]
            rest = line.replace(m.group(1), " ")

            rm = rate_pattern.search(rest)
            rate = f"{float(rm.group(1)):g}%" if rm else ("0%" if free_pattern.search(rest) else "")

            en = ""
            ec = re.findall(r'[A-Za-z][A-Za-z\s\(\),\.\-/]{4,}', rest)
            if ec:
                c = max(ec, key=len).strip()
                if len(c) > 5 and not re.match(r'^[A-Z]\s+[A-Z]', c):
                    en = c

            ar = ""
            ac = re.findall(r'[؀-ۿ][؀-ۿ\s‫‬\(\),\.\-/]{3,}', rest)
            if ac:
                ar = max(ac, key=len).strip().replace("‫", "").replace("‬", "")

            lookup.setdefault(hs8, []).append({"hs12": hs12, "rate": rate, "en_desc": en, "ar_desc": ar})
    return lookup


def summarize_tariff(rows):
    """Pick the rate/description for an HS8 from all its HS12 sub-lines."""
    rates = [r.get("rate", "") for r in rows if r.get("rate")]
    uniq = sorted(set(rates), key=lambda x: (rate_to_num(x) is None, rate_to_num(x) or 0))
    if not uniq:
        display, num = "—", None
    elif len(uniq) == 1:
        display, num = uniq[0], rate_to_num(uniq[0])
    else:
        display = " / ".join(uniq)
        num = rate_to_num(max(set(rates), key=rates.count))   # most common rate
    en = next((r.get("en_desc") for r in rows if r.get("en_desc")), "")
    ar = next((r.get("ar_desc") for r in rows if r.get("ar_desc")), "")
    return display, num, en, ar


def show_tariff_info(tariff_lookup: dict, hs8_codes: list):
    """Compact tariff card for each selected 8-digit code."""
    if not tariff_lookup or not hs8_codes:
        return
    missing = []
    for code in hs8_codes:
        norm = clean_code(code)[:8]
        rows = tariff_lookup.get(norm, [])
        if not rows:
            missing.append(norm)
            continue
        rate, _, en, ar = summarize_tariff(rows)
        st.markdown(
            f"""
            <div class="tariff-card">
                <div class="tariff-title">📋 Oman Tariff 2026 — HS8: {norm}</div>
                <div class="tariff-row">🇬🇧 {en or '—'}</div>
                <div class="tariff-row">🇸🇦 {ar or '—'}</div>
                <div class="tariff-row">Customs Duty Rate: <span class="tariff-rate">{rate}</span>
                &nbsp;&nbsp;|&nbsp;&nbsp; Sub-codes (HS12): {len(rows)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if missing:
        st.info(f"No tariff entry found for: {', '.join(missing)}")


def _color_rate(val):
    n = rate_to_num(val)
    if n is None:
        return 'color: #94a3b8'
    if n == 0:
        return 'color: #22c55e; font-weight: 700'
    if n <= 5:
        return 'color: #3b82f6; font-weight: 700'
    return 'color: #ef4444; font-weight: 700'


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE & SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
for _k in ("m_dfs", "t_roles", "macro_dfs", "tariff_lkp", "tariff_src", "load_notes"):
    if _k not in st.session_state:
        st.session_state[_k] = None

st.sidebar.title("🛠️ Dashboard Control")
page = st.sidebar.radio("Go to Page:", ["Market Monitoring", "Macroeconomic Overview", "Market Structure Maps"])
uploaded_market_zip = st.sidebar.file_uploader("1. Upload Registry & Prices ZIP", type=["zip"])
uploaded_trade_zip  = st.sidebar.file_uploader("2. Upload Trade Data ZIP", type=["zip"])
uploaded_macro_zip  = st.sidebar.file_uploader("3. Upload Macroeconomic Data ZIP", type=["zip"])

st.sidebar.markdown("**4. Oman Tariff 2026**")
uploaded_tariff_json = st.sidebar.file_uploader(
    "Upload tariff_lookup.json ⚡ (recommended)", type=["json"],
    help="Pre-built lookup file — loads instantly.")
uploaded_tariff_pdf = st.sidebar.file_uploader(
    "Or upload Tariff PDF (slower, ~60s)", type=["pdf"],
    help="Fallback if you don't have the JSON file yet.")

# Tariff loads as soon as it is uploaded (no need to press Launch again).
_tariff_file = uploaded_tariff_json or uploaded_tariff_pdf
if _tariff_file is not None:
    _src = (_tariff_file.name, _tariff_file.size)
    if st.session_state.tariff_src != _src:
        try:
            with st.sidebar:
                with st.spinner("Loading tariff…" if uploaded_tariff_json else "Parsing tariff PDF… (~60s, one-time only)"):
                    if uploaded_tariff_json:
                        st.session_state.tariff_lkp = load_tariff_json(uploaded_tariff_json.getvalue())
                    else:
                        st.session_state.tariff_lkp = build_tariff_lookup_from_pdf(uploaded_tariff_pdf.getvalue())
            st.session_state.tariff_src = _src
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(f"Tariff file could not be read: {e}")
if st.session_state.tariff_lkp:
    _n_rated = sum(1 for rows in st.session_state.tariff_lkp.values() if any(r.get('rate') for r in rows))
    st.sidebar.success(f"✅ Tariff: {len(st.session_state.tariff_lkp):,} HS8 codes ({_n_rated:,} with a rate)")
elif st.session_state.tariff_src:
    st.sidebar.warning("Tariff file loaded but no HS codes were found in it.")

launch_btn = st.sidebar.button("🚀 Launch Dashboard")

if launch_btn:
    notes = []
    with st.spinner("Loading data…"):
        if uploaded_market_zip:
            d, n = load_zip(uploaded_market_zip.getvalue())
            st.session_state.m_dfs = prepare_market(d)
            notes += ["**Registry & Prices**"] + n
        if uploaded_trade_zip:
            d, n = load_zip(uploaded_trade_zip.getvalue())
            st.session_state.t_roles = prepare_trade(d)
            notes += ["**Trade**"] + n
        if uploaded_macro_zip:
            d, n = load_zip(uploaded_macro_zip.getvalue())
            st.session_state.macro_dfs = d
            notes += ["**Macroeconomic**"] + n
    st.session_state.load_notes = notes

if st.session_state.load_notes:
    with st.sidebar.expander("📂 Data check — files loaded"):
        for line in st.session_state.load_notes:
            st.markdown(line)
        roles = st.session_state.t_roles
        if roles:
            st.markdown("**Trade files detected**")
            for label, key in [("HS6 exports", "hs6_exp"), ("HS6 imports", "hs6_imp"), ("Monthly exports", "mo_exp"), ("Monthly imports", "mo_imp")]:
                df = roles.get(key)
                st.markdown(f"{'✅' if df is not None else '❌'} {label}: {len(df):,} rows" if df is not None else f"❌ {label}: not found")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1: MARKET MONITORING
# ═══════════════════════════════════════════════════════════════════════════════
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
            "الكابلات": "قطاع الكابلات",
        }
        # Several spellings per sector, compared after Arabic normalisation
        # (e.g. "الإسمنت" and "الاسمنت" both match).
        pricing_terms = {
            "الأسمنت ومنتجات الاسمنت": ["اسمنت"],
            "الاصباغ": ["دهان", "اصباغ", "طلاء"],
            "البلاط والسيراميك والرخام": ["بلاط", "سيراميك", "رخام"],
            "الحديد والصلب": ["حديد", "صلب"],
            "الكابلات": ["كوابل", "كابلات", "اسلاك"],
            "الألمنيوم": ["المنيوم", "الومنيوم"],
            "الأنابيب": ["انابيب"],
        }

        st.title("Oman BM Market Monitoring Dashboard")
        selected_sector = st.selectbox("Select Product Sector", list(sector_mapping.keys()))
        sector_key = norm_ar(sector_mapping[selected_sector])
        df_isic  = next((v for k, v in all_dfs.items() if sector_key in norm_ar(k)), None)
        df_price = next((v for k, v in all_dfs.items() if "final_pricing_data_dashboard" in k), None)
        final_hs4_list = []

        # ── Registry ─────────────────────────────────────────────────────────
        with section_guard("Industrial registry"):
            if df_isic is not None:
                isic_col = next((c for c in df_isic.columns if 'ISIC Code' in c), None)
                hs_col   = next((c for c in df_isic.columns if 'relevant hs code' in c.lower()), None)
                isics    = sorted({"".join(re.findall(r'\d+', str(x))) for x in df_isic[isic_col].dropna().unique()}) if isic_col else []
                raw_hss  = df_isic[hs_col].dropna().unique() if hs_col else []
                final_hs4_list = sorted({h for rh in raw_hss for h in get_hs4_codes(rh)})

                k1, k2 = st.columns(2)
                with k1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">ISIC Codes</div><div class="kpi-value">{" / ".join(isics)}</div></div>', unsafe_allow_html=True)
                with k2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">HS4 Codes</div><div class="kpi-value">{" / ".join(final_hs4_list)}</div></div>', unsafe_allow_html=True)

                reg_cols = ['Company Name (Ar)الاسم باللغة العربية', 'CR Number رقم السجل', 'location عنوان المنشأة - رمز الولاية', 'المدينة الصناعية (مدائن)industrial estate', 'التنظيم الاقتصادي للمنشأة Economic organization', 'الشكل القانوني للمنشأة legal organization']
                st.subheader("Industrial Registry Directory")
                show_df(df_isic[[c for c in reg_cols if c in df_isic.columns]], height=200)
            else:
                st.warning(f"No registry file found for sector '{sector_mapping[selected_sector]}' in the ZIP.")

        st.divider()

        # ── Pricing + density map ────────────────────────────────────────────
        with section_guard("Pricing section"):
            if df_price is not None:
                terms = [norm_ar(t) for t in pricing_terms.get(selected_sector, [selected_sector])]
                prod_norm = df_price['المنتج'].fillna('').map(norm_ar)
                p_base = df_price[prod_norm.apply(lambda p: any(t in p for t in terms))]
                if p_base.empty:
                    st.info("No pricing records found for this sector.")
                else:
                    f1, f2, f3 = st.columns(3)
                    with f1:
                        val_desc = st.selectbox("Category", sorted(p_base['الوصف'].dropna().astype(str).unique()))
                    with f2:
                        sub_opts = p_base[p_base['الوصف'] == val_desc]['الوصف التفصيلي'].dropna().astype(str).unique()
                        val_sub  = st.selectbox("Specification", ["Show All"] + sorted(sub_opts))
                    with f3:
                        years     = sorted(p_base['السنة'].dropna().unique())
                        sel_years = st.multiselect("Time Period", years, default=years[-1:] if years else [])

                    mask = (df_price['الوصف'] == val_desc) & (df_price['السنة'].isin(sel_years))
                    if val_sub != "Show All":
                        mask &= (df_price['الوصف التفصيلي'] == val_sub)
                    df_viz = df_price[mask & df_price['سعر المبيع'].notna()].copy()

                    if df_viz.empty:
                        st.info("No price records match these filters.")
                    else:
                        st.markdown(f"### Monitoring: {val_desc} Price Dispersion")
                        q1, q3 = df_viz['سعر المبيع'].quantile([0.25, 0.75])
                        mean_v = df_viz['سعر المبيع'].mean()
                        iqr = q3 - q1
                        up_f, lo_f = q3 + 1.5 * iqr, q1 - 1.5 * iqr

                        def get_cat(val):
                            if val > up_f or val < lo_f: return 'Outlier (Purple)'
                            if val > q3: return 'High Price (>Q3 Red)'
                            if val < q1: return 'Low Price (<Q1 Green)'
                            return 'Market Range (Blue)'
                        df_viz['Price Category'] = df_viz['سعر المبيع'].apply(get_cat)
                        c_map = {'Outlier (Purple)': '#a855f7', 'High Price (>Q3 Red)': '#ef4444', 'Low Price (<Q1 Green)': '#22c55e', 'Market Range (Blue)': '#3b82f6'}

                        hover_cols = [c for c in ['إسم الشركة', 'المحافظة', 'الوصف التفصيلي', 'السنة'] if c in df_viz.columns]
                        fig_scatter = px.scatter(df_viz, x='إسم الشركة', y='سعر المبيع', color='Price Category', color_discrete_map=c_map, hover_data=hover_cols, height=500)
                        fig_scatter.add_hline(y=mean_v, line_dash="solid", line_color="black", annotation_text="Mean")
                        fig_scatter.add_hline(y=q3, line_dash="dash", line_color="#ef4444", annotation_text="Q3")
                        fig_scatter.add_hline(y=q1, line_dash="dash", line_color="#22c55e", annotation_text="Q1")
                        fig_scatter.update_xaxes(showticklabels=False)
                        show_chart(fig_scatter)

                        c1, c2 = st.columns(2)
                        with c1:
                            trend = df_price[df_price['الوصف'] == val_desc].groupby('السنة')['سعر المبيع'].mean().reset_index()
                            fig_trend = px.line(trend, x='السنة', y='سعر المبيع', markers=True, title="Average Price Trend")
                            fig_trend.update_xaxes(dtick=1, tickformat="d")
                            show_chart(fig_trend)
                        with c2:
                            gov_p = df_viz.groupby('المحافظة')['سعر المبيع'].mean().reset_index().sort_values('سعر المبيع')
                            show_chart(px.bar(gov_p, x='المحافظة', y='سعر المبيع', color='سعر المبيع', color_continuous_scale='Reds', title="Pricing by Governorate"))

                    # ── Density map ──────────────────────────────────────────
                    st.divider()
                    st.subheader("📍 Regional Density & Concentration")
                    map_scope = st.radio(
                        "Map coverage",
                        ["Current filters", "All years & specifications (this category)", "Whole sector (all categories)"],
                        horizontal=True, key="map_scope",
                        help="The map used to show only the filtered records. Widen it here to see every company.",
                    )
                    if map_scope == "Current filters":
                        df_geo = df_price[mask]
                    elif map_scope.startswith("All years"):
                        df_geo = df_price[df_price['الوصف'] == val_desc]
                    else:
                        df_geo = p_base

                    geo_agg, unmatched = build_geo_table(df_geo)
                    total_records = len(df_geo)
                    placed = int(geo_agg['Records'].sum()) if not geo_agg.empty else 0

                    if geo_agg.empty:
                        st.info("No records with a recognisable governorate for this selection.")
                    else:
                        m1_, m2_, m3_ = st.columns(3)
                        with m1_: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Governorates</div><div class="kpi-value">{len(geo_agg)}</div></div>', unsafe_allow_html=True)
                        with m2_: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Companies</div><div class="kpi-value">{df_geo["إسم الشركة"].nunique() if "إسم الشركة" in df_geo.columns else "—"}</div></div>', unsafe_allow_html=True)
                        with m3_: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Records on map</div><div class="kpi-value">{placed:,} / {total_records:,}</div></div>', unsafe_allow_html=True)

                        fig_map = make_density_map(
                            geo_agg,
                            lat="lat", lon="lon",
                            size="Companies", color="Companies",
                            color_continuous_scale=px.colors.sequential.YlOrRd,
                            size_max=45, zoom=4.9,
                            center={"lat": 21.4, "lon": 56.8},     # whole of Oman incl. Dhofar & Musandam
                            hover_name="Governorate",
                            hover_data={"lat": False, "lon": False, "Companies": True, "Records": True, "Company_List": True},
                            labels={"Company_List": "Companies list"},
                            height=620,
                        )
                        fig_map.update_layout(margin=dict(l=0, r=0, t=10, b=0))
                        show_chart(fig_map)

                        with st.expander("📋 Governorate breakdown (all companies)"):
                            full = (df_geo.assign(Governorate=df_geo['المحافظة'].map(match_governorate))
                                    .dropna(subset=['Governorate'])
                                    .groupby('Governorate')['إسم الشركة']
                                    .apply(lambda s: "، ".join(sorted(set(s.dropna().astype(str)))))
                                    .reset_index().rename(columns={'إسم الشركة': 'Companies'}))
                            show_df(geo_agg[['Governorate', 'Companies', 'Records']].rename(columns={'Companies': 'No. of companies'})
                                    .merge(full, on='Governorate', how='left'), hide_index=True)

                    if len(unmatched):
                        st.warning(
                            f"{int(unmatched.sum()):,} record(s) could not be placed on the map because their governorate "
                            f"was not recognised: " + ", ".join(f"'{k}' ({v})" for k, v in unmatched.head(15).items())
                            + ". Add these spellings to GOV_ALIASES in the code to include them."
                        )
            else:
                st.info("Pricing file (final_pricing_data_dashboard) not found in the ZIP.")

        # ── Trade section ────────────────────────────────────────────────────
        roles = st.session_state.t_roles
        if roles:
            st.divider()
            st.header("🌍 Trade Performance (Imports & Exports)")
            m_exp, m_imp = roles.get("hs6_exp"), roles.get("hs6_imp")
            mo_exp, mo_imp = roles.get("mo_exp"), roles.get("mo_imp")

            if m_exp is None or m_imp is None:
                st.warning("Trade ZIP loaded, but the HS6 exports/imports files (with an 'HS_6DG' column) were not found. Open '📂 Data check' in the sidebar to see what was loaded.")
            elif not final_hs4_list:
                st.info("No HS4 codes found for this sector in the registry, so trade data cannot be filtered.")
            else:
                desc_src = pd.concat([m_exp[['HS_6DG'] + (['ENG_DESC'] if 'ENG_DESC' in m_exp.columns else [])],
                                      m_imp[['HS_6DG'] + (['ENG_DESC'] if 'ENG_DESC' in m_imp.columns else [])]])
                rel = desc_src[desc_src['HS_6DG'].str.startswith(tuple(final_hs4_list))].drop_duplicates('HS_6DG').sort_values('HS_6DG')
                hs6_options = {r['HS_6DG']: f"{r['HS_6DG']} - {r.get('ENG_DESC', '')}" for _, r in rel.iterrows()}

                if not hs6_options:
                    st.info(f"No HS6 codes in the trade data start with this sector's HS4 codes ({', '.join(final_hs4_list)}).")
                else:
                    sel_hs6 = st.multiselect("Select HS6 Product Categories", list(hs6_options.keys()),
                                             format_func=lambda x: hs6_options[x], default=list(hs6_options.keys())[:1])

                    # ── TARIFF TABLE ─────────────────────────────────────────
                    with section_guard("Tariff table"):
                        st.markdown("#### 🏷️ Customs Tariff Rates — HS8 Codes")
                        lkp = st.session_state.tariff_lkp
                        if not lkp:
                            st.info("Upload the tariff JSON (or PDF) in the sidebar to see customs duty rates here.")
                        elif not sel_hs6:
                            st.info("Select at least one HS6 code to see its tariff rates.")
                        else:
                            # HS8 codes come from the tariff itself (so they show even without the
                            # monthly files) plus any extra codes that appear in the monthly trade data.
                            trade_hs8, trade_desc = set(), {}
                            for src in (mo_exp, mo_imp):
                                if src is not None:
                                    hit = src[src['رمز النظام المنسق'].str[:6].isin(sel_hs6)]
                                    for code, desc in zip(hit['رمز النظام المنسق'],
                                                          hit.get('الوصف الإحصائي لرمز النظام المنسق باللغة العربية', pd.Series([''] * len(hit), index=hit.index))):
                                        if len(code) >= 8:
                                            trade_hs8.add(code[:8])
                                            trade_desc.setdefault(code[:8], str(desc) if pd.notna(desc) else '')
                            tariff_hs8 = {k for k in lkp if k[:6] in sel_hs6}

                            tf_col1, tf_col2, _ = st.columns([1.4, 1.2, 2])
                            with tf_col1:
                                rate_filter = st.radio("Filter by Rate", ["All", "0%", "≤ 5%", "> 5%"], horizontal=True, key="tariff_rate_filter")
                            with tf_col2:
                                show_only_found = st.checkbox("Only codes with tariff data", value=True, key="tariff_only_found")

                            codes = sorted(tariff_hs8 | trade_hs8) if not show_only_found else sorted(tariff_hs8 | {c for c in trade_hs8 if c in lkp})
                            tariff_rows = []
                            for hs8 in codes:
                                rows = lkp.get(hs8, [])
                                rate, rate_num, en, ar = summarize_tariff(rows) if rows else ("—", None, "", trade_desc.get(hs8, ""))
                                tariff_rows.append({
                                    'HS8 Code': hs8, 'Duty Rate': rate,
                                    'English Desc': en[:90], 'Arabic Desc': (ar or trade_desc.get(hs8, ''))[:90],
                                    'Sub-codes (HS12)': len(rows), 'In tariff': '✅' if rows else '❌',
                                    '_rate_num': rate_num,
                                })

                            if not tariff_rows:
                                sample = ", ".join(list(lkp.keys())[:5])
                                st.warning(f"No tariff entries found for HS6 {', '.join(sel_hs6)}. "
                                           f"The tariff file has {len(lkp):,} HS8 codes (e.g. {sample}). "
                                           "If those don't look like 8-digit codes, the tariff file format needs checking.")
                            else:
                                tariff_df = pd.DataFrame(tariff_rows)
                                rn = tariff_df['_rate_num']
                                b1, b2, b3, b4, b5 = st.columns(5)
                                with b1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total HS8 Codes</div><div class="kpi-value">{len(tariff_df)}</div></div>', unsafe_allow_html=True)
                                with b2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Zero Duty (0%)</div><div class="kpi-value" style="color:#22c55e;">{int((rn == 0).sum())}</div></div>', unsafe_allow_html=True)
                                with b3: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Low Duty (≤5%)</div><div class="kpi-value" style="color:#3b82f6;">{int((rn.notna() & (rn <= 5)).sum())}</div></div>', unsafe_allow_html=True)
                                with b4: st.markdown(f'<div class="kpi-card"><div class="kpi-label">High Duty (>5%)</div><div class="kpi-value" style="color:#ef4444;">{int((rn.notna() & (rn > 5)).sum())}</div></div>', unsafe_allow_html=True)
                                with b5: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Rate Unknown</div><div class="kpi-value" style="color:#94a3b8;">{int(rn.isna().sum())}</div></div>', unsafe_allow_html=True)
                                st.markdown("<br>", unsafe_allow_html=True)

                                disp = tariff_df
                                if rate_filter == "0%":
                                    disp = disp[disp['_rate_num'] == 0]
                                elif rate_filter == "≤ 5%":
                                    disp = disp[disp['_rate_num'].notna() & (disp['_rate_num'] <= 5)]
                                elif rate_filter == "> 5%":
                                    disp = disp[disp['_rate_num'].notna() & (disp['_rate_num'] > 5)]
                                disp = disp[['HS8 Code', 'Duty Rate', 'English Desc', 'Arabic Desc', 'Sub-codes (HS12)', 'In tariff']].reset_index(drop=True)

                                if disp.empty:
                                    st.info(f"No HS8 codes match the selected filter: **{rate_filter}**")
                                else:
                                    st.caption(f"Showing {len(disp)} HS8 code(s) — 🟢 0% duty | 🔵 ≤5% | 🔴 >5%")
                                    show_df(style_elementwise(disp.style, _color_rate, subset=['Duty Rate']),
                                            height=min(40 + len(disp) * 35, 420), hide_index=True)
                        st.markdown("---")

                    t1, t2, t3 = st.tabs(["Country Distribution (HS6)", "Monthly Trade Trends (HS8+)", "📊 Trade Balance Analysis (HS6)"])

                    def _value_years(df):
                        ys = set()
                        for c in df.columns:
                            m = re.search(r'(\d{4})', str(c))
                            if m and "Value in OMR" in c:
                                ys.add(int(m.group(1)))
                        return sorted(ys)

                    with t1, section_guard("Country distribution"):
                        years_t1 = sorted(set(_value_years(m_exp)) | set(_value_years(m_imp)))
                        if not years_t1:
                            st.info("No '(Value in OMR)' year columns found in the HS6 files.")
                        else:
                            sel_year = st.selectbox("Select Year", years_t1, index=len(years_t1) - 1)
                            target = f"{sel_year} (Value in OMR)"

                            def _by_country(df):
                                if target not in df.columns:
                                    return pd.DataFrame(columns=['COUNTRY', target])
                                out = df[df['HS_6DG'].isin(sel_hs6)].groupby('COUNTRY')[target].sum().reset_index()
                                return out[out[target] > 0].sort_values(target)

                            c_exp, c_imp = _by_country(m_exp), _by_country(m_imp)
                            col_v1, col_v2 = st.columns(2)
                            with col_v1:
                                show_chart(px.bar(c_exp, x=target, y='COUNTRY', orientation='h', title=f"Exports by Destination ({sel_year})",
                                                  color_discrete_sequence=['#1e3a8a'], height=max(400, 22 * len(c_exp))))
                            with col_v2:
                                show_chart(px.bar(c_imp, x=target, y='COUNTRY', orientation='h', title=f"Imports by Source ({sel_year})",
                                                  color_discrete_sequence=['#ef4444'], height=max(400, 22 * len(c_imp))))

                    with t2, section_guard("Monthly trade trends"):
                        if mo_exp is None and mo_imp is None:
                            st.info("Monthly exports/imports files (with a 'رمز النظام المنسق' column) were not found in the Trade ZIP.")
                        else:
                            desc_col = 'الوصف الإحصائي لرمز النظام المنسق باللغة العربية'
                            empty = pd.DataFrame(columns=['رمز النظام المنسق', desc_col, '_month', 'القيمة (ر.ع)'])
                            rel_mo_exp = mo_exp[mo_exp['رمز النظام المنسق'].str.startswith(tuple(sel_hs6))] if (mo_exp is not None and sel_hs6) else empty
                            rel_mo_imp = mo_imp[mo_imp['رمز النظام المنسق'].str.startswith(tuple(sel_hs6))] if (mo_imp is not None and sel_hs6) else empty
                            frames = [f[['رمز النظام المنسق'] + ([desc_col] if desc_col in f.columns else [])] for f in (rel_mo_exp, rel_mo_imp)]
                            hs8_map = pd.concat(frames).drop_duplicates('رمز النظام المنسق').sort_values('رمز النظام المنسق')
                            hs8_options = {r['رمز النظام المنسق']: f"{r['رمز النظام المنسق']} - {r.get(desc_col, '')}" for _, r in hs8_map.iterrows()}

                            if not hs8_options:
                                st.info("No 8-digit codes found in the monthly data for the selected HS6 categories.")
                            else:
                                sel_hs8 = st.multiselect("Filter by 8-Digit Code", list(hs8_options.keys()),
                                                         format_func=lambda x: hs8_options[x], default=list(hs8_options.keys())[:1])
                                if st.session_state.tariff_lkp and sel_hs8:
                                    show_tariff_info(st.session_state.tariff_lkp, sel_hs8)

                                def _trend(df):
                                    if df.empty or '_month' not in df.columns:
                                        return pd.DataFrame(columns=['_month', 'القيمة (ر.ع)'])
                                    return df[df['رمز النظام المنسق'].isin(sel_hs8)].groupby('_month')['القيمة (ر.ع)'].sum().reset_index().sort_values('_month')

                                df_trend_exp, df_trend_imp = _trend(rel_mo_exp), _trend(rel_mo_imp)
                                fig_trade = go.Figure()
                                fig_trade.add_trace(go.Scatter(x=df_trend_exp['_month'], y=df_trend_exp['القيمة (ر.ع)'], name='Exports', line=dict(color='#1e3a8a', width=3)))
                                fig_trade.add_trace(go.Scatter(x=df_trend_imp['_month'], y=df_trend_imp['القيمة (ر.ع)'], name='Imports', line=dict(color='#ef4444', width=3)))
                                fig_trade.update_layout(title="Trade Value Trend", xaxis_title="Timeline", yaxis_title="Value (OMR)", hovermode="x unified")
                                show_chart(fig_trade)

                    with t3, section_guard("Trade balance analysis"):
                        if not sel_hs6:
                            st.info("Select at least one HS6 code above to view the trade balance.")
                        else:
                            avail_years_tb = sorted(set(_value_years(m_exp)) | set(_value_years(m_imp)))
                            sel_years_tb = st.multiselect("Select Years", avail_years_tb, default=avail_years_tb, key="tb_years")
                            if not sel_years_tb:
                                st.warning("Select at least one year.")
                            else:
                                def _agg_annual(df, codes, yrs):
                                    sub = df[df['HS_6DG'].isin(codes)]
                                    return pd.DataFrame([{
                                        'Year': y,
                                        'Value_OMR': sub[f"{y} (Value in OMR)"].sum() if f"{y} (Value in OMR)" in sub.columns else 0,
                                        'Weight_KG': sub[f"{y} (Weight in KG)"].sum() if f"{y} (Weight in KG)" in sub.columns else 0,
                                    } for y in sorted(yrs)])

                                ea = _agg_annual(m_exp, sel_hs6, sel_years_tb)
                                ia = _agg_annual(m_imp, sel_hs6, sel_years_tb)
                                tb = pd.merge(
                                    ea.rename(columns={'Value_OMR': 'Exp_OMR', 'Weight_KG': 'Exp_KG'}),
                                    ia.rename(columns={'Value_OMR': 'Imp_OMR', 'Weight_KG': 'Imp_KG'}),
                                    on='Year', how='outer'
                                ).fillna(0).sort_values('Year')
                                tb['Year'] = tb['Year'].astype(int)
                                tb['Balance_OMR'] = tb['Exp_OMR'] - tb['Imp_OMR']
                                tb['Coverage_Pct'] = (tb['Exp_OMR'] / tb['Imp_OMR'].replace(0, float('nan')) * 100).round(1)
                                tb['YoY_Exp'] = (tb['Exp_OMR'].pct_change(fill_method=None) * 100).round(1)
                                tb['YoY_Imp'] = (tb['Imp_OMR'].pct_change(fill_method=None) * 100).round(1)
                                tb[['YoY_Exp', 'YoY_Imp']] = tb[['YoY_Exp', 'YoY_Imp']].replace([float('inf'), float('-inf')], float('nan'))

                                total_exp, total_imp = tb['Exp_OMR'].sum(), tb['Imp_OMR'].sum()
                                total_bal = total_exp - total_imp
                                avg_cov = (total_exp / total_imp * 100) if total_imp else 0
                                dep_flag = "⚠️ Import Dependent" if avg_cov < 50 else ("✅ Export Oriented" if avg_cov > 100 else "🔄 Balanced Trade")

                                v1, v2, v3, v4 = st.columns(4)
                                with v1: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total Exports</div><div class="kpi-value">{total_exp:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v2: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Total Imports</div><div class="kpi-value">{total_imp:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v3: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Trade Balance</div><div class="kpi-value">{"+" if total_bal >= 0 else ""}{total_bal:,.0f} OMR</div></div>', unsafe_allow_html=True)
                                with v4: st.markdown(f'<div class="kpi-card"><div class="kpi-label">Status</div><div class="kpi-value" style="font-size:13px;">{dep_flag}<br><span style="font-size:11px;opacity:0.8;">Coverage {avg_cov:.1f}%</span></div></div>', unsafe_allow_html=True)
                                st.markdown("<br>", unsafe_allow_html=True)

                                fig_tb = go.Figure()
                                fig_tb.add_trace(go.Bar(x=tb['Year'], y=tb['Exp_OMR'], name='Exports (OMR)', marker_color='#1e3a8a'))
                                fig_tb.add_trace(go.Bar(x=tb['Year'], y=tb['Imp_OMR'], name='Imports (OMR)', marker_color='#ef4444'))
                                fig_tb.add_trace(go.Scatter(x=tb['Year'], y=tb['Balance_OMR'], name='Balance (OMR)', mode='lines+markers',
                                                            line=dict(color='#22c55e', width=2.5, dash='dash'), marker=dict(size=7)))
                                fig_tb.update_layout(title="Annual Trade Balance — Exports vs Imports (OMR)", barmode='group', hovermode='x unified',
                                                     xaxis=dict(dtick=1, tickformat='d'), yaxis=dict(title='Value (OMR)'), legend=dict(orientation='h', y=1.12))
                                show_chart(fig_tb)

                                fig_cov = go.Figure()
                                fig_cov.add_trace(go.Scatter(
                                    x=tb['Year'], y=tb['Coverage_Pct'], mode='lines+markers+text',
                                    line=dict(color='#f97316', width=2.5), marker=dict(size=8),
                                    text=tb['Coverage_Pct'].apply(lambda v: f"{v:.0f}%" if pd.notna(v) else ""),
                                    textposition='top center', name='Coverage Ratio (%)'))
                                fig_cov.add_hline(y=100, line_dash='dash', line_color='#94a3b8', annotation_text='Break-even (100%)', annotation_position='bottom right')
                                fig_cov.update_layout(title="Export Coverage Ratio (Exports / Imports × 100)", xaxis=dict(dtick=1, tickformat='d'),
                                                      yaxis=dict(title='Coverage (%)'), hovermode='x unified')
                                show_chart(fig_cov)

                                tb_yoy = tb.dropna(subset=['YoY_Exp', 'YoY_Imp'], how='all')
                                if not tb_yoy.empty:
                                    fig_yoy = go.Figure()
                                    fig_yoy.add_trace(go.Bar(x=tb_yoy['Year'], y=tb_yoy['YoY_Exp'], name='Export Growth %', marker_color='#1e3a8a'))
                                    fig_yoy.add_trace(go.Bar(x=tb_yoy['Year'], y=tb_yoy['YoY_Imp'], name='Import Growth %', marker_color='#ef4444'))
                                    fig_yoy.add_hline(y=0, line_color='#64748b', line_width=1)
                                    fig_yoy.update_layout(title="Year-over-Year Growth Rate (%)", barmode='group', hovermode='x unified',
                                                          xaxis=dict(dtick=1, tickformat='d'), yaxis=dict(title='Growth (%)'), legend=dict(orientation='h', y=1.12))
                                    show_chart(fig_yoy)

                                st.subheader("🌍 Top Trading Partners")

                                def _top_countries(df, codes, yrs, n=5):
                                    sub = df[df['HS_6DG'].isin(codes)]
                                    vcols = [f"{y} (Value in OMR)" for y in yrs if f"{y} (Value in OMR)" in sub.columns]
                                    if not vcols or 'COUNTRY' not in sub.columns:
                                        return pd.DataFrame()
                                    sub = sub.assign(Total=sub[vcols].sum(axis=1))
                                    return sub.groupby('COUNTRY')['Total'].sum().reset_index().sort_values('Total', ascending=False).head(n)

                                tp1, tp2 = st.columns(2)
                                with tp1:
                                    top_exp = _top_countries(m_exp, sel_hs6, sel_years_tb)
                                    if not top_exp.empty:
                                        show_chart(px.bar(top_exp, x='Total', y='COUNTRY', orientation='h', title="Top 5 Export Destinations (OMR)",
                                                          color_discrete_sequence=['#1e3a8a'], labels={'Total': 'Value (OMR)', 'COUNTRY': ''}))
                                with tp2:
                                    top_imp = _top_countries(m_imp, sel_hs6, sel_years_tb)
                                    if not top_imp.empty:
                                        show_chart(px.bar(top_imp, x='Total', y='COUNTRY', orientation='h', title="Top 5 Import Origins (OMR)",
                                                          color_discrete_sequence=['#ef4444'], labels={'Total': 'Value (OMR)', 'COUNTRY': ''}))

                                st.subheader("📋 Annual Summary Table")
                                tb_disp = tb.copy()
                                for col in ['Exp_OMR', 'Imp_OMR', 'Balance_OMR']:
                                    tb_disp[col] = tb_disp[col].apply(lambda v: f"{v:,.0f}")
                                tb_disp['Coverage_Pct'] = tb_disp['Coverage_Pct'].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "—")
                                tb_disp['YoY_Exp'] = tb_disp['YoY_Exp'].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
                                tb_disp['YoY_Imp'] = tb_disp['YoY_Imp'].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
                                tb_disp = tb_disp.rename(columns={
                                    'Exp_OMR': 'Exports (OMR)', 'Imp_OMR': 'Imports (OMR)', 'Balance_OMR': 'Balance (OMR)',
                                    'Coverage_Pct': 'Coverage %', 'YoY_Exp': 'Export YoY', 'YoY_Imp': 'Import YoY'
                                })[['Year', 'Exports (OMR)', 'Imports (OMR)', 'Balance (OMR)', 'Coverage %', 'Export YoY', 'Import YoY']]
                                show_df(tb_disp, hide_index=True)
    else:
        st.info("Dashboard initialized. Please upload data and click 'Launch' to begin.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2: MACROECONOMIC OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Macroeconomic Overview":
    st.title("🇴🇲 Macroeconomic Overview")
    if st.session_state.macro_dfs:
        dfs = st.session_state.macro_dfs

        st.header("1. Economic Growth & Structure")
        m1, m2 = st.columns(2)
        with m1, section_guard("GDP growth"):
            f_gdp = next((v for k, v in dfs.items() if "GDP growth" in k), None)
            if f_gdp is not None:
                df_gdp = f_gdp.iloc[1:].copy().iloc[:, :2]
                df_gdp.columns = ['Year', 'Growth Rate']
                df_gdp['Growth Rate'] = to_num(df_gdp['Growth Rate'])
                show_chart(px.line(df_gdp, x='Year', y='Growth Rate', markers=True, title="GDP Annual Growth Rate (%)"))
        with m2, section_guard("Oil vs non-oil"):
            f_struct = next((v for k, v in dfs.items() if "Percentage of oil and non-oil" in k), None)
            if f_struct is not None:
                df_struct = f_struct.iloc[1:].copy().iloc[:, :3]
                df_struct.columns = ['Year', 'Oil Activities', 'Non-Oil Activities']
                df_struct = df_struct.melt(id_vars='Year', var_name='Sector', value_name='Contribution')
                df_struct['Contribution'] = to_num(df_struct['Contribution']) * 100
                show_chart(px.area(df_struct, x='Year', y='Contribution', color='Sector', title="Oil vs Non-Oil % GDP"))

        st.header("2. External Sector & Trade Dynamics")
        e1, e2 = st.columns(2)
        with e1, section_guard("Trade indicators"):
            f_ti = next((v for k, v in dfs.items() if "Foreign trade indicators" in k), None)
            if f_ti is not None:
                years = f_ti.iloc[0, 1:].values; bm_e = f_ti.iloc[1, 1:].values; tot_e = f_ti.iloc[2, 1:].values
                df_ti = pd.DataFrame({'Year': years, 'BM Exports': bm_e, 'Total Exports': tot_e}).melt(id_vars='Year')
                df_ti['value'] = to_num(df_ti['value'])
                show_chart(px.line(df_ti, x='Year', y='value', color='variable', title="Trade Indicators (Value in OMR)"))
        with e2, section_guard("BM exports by sector"):
            f_bm = next((v for k, v in dfs.items() if "building materials exports" in k), None)
            if f_bm is not None:
                years = f_bm.iloc[0, 1:].values; df_bm = f_bm.iloc[2:].copy(); df_bm.columns = ['Sector'] + list(years)
                df_bm = df_bm.melt(id_vars='Sector', var_name='Year', value_name='Value')
                df_bm['Value'] = to_num(df_bm['Value'])
                show_chart(px.bar(df_bm, x='Year', y='Value', color='Sector', title="BM Exports by Sector (2010-2024)"))

        st.header("3. Investment & Business Environment")
        i1, i2 = st.columns(2)
        with i1, section_guard("FDI inflows"):
            f_fdi = next((v for k, v in dfs.items() if "FDI.xlsx" in k), None)
            if f_fdi is not None:
                df_fdi_raw = f_fdi.iloc[1:].copy()
                df_fdi = df_fdi_raw.iloc[:, :2].copy()
                df_fdi.columns = ['Year', 'FDI Value (Million OMR)']
                df_fdi['FDI Value (Million OMR)'] = to_num(df_fdi['FDI Value (Million OMR)'])
                if df_fdi_raw.shape[1] >= 3:
                    df_fdi['FDI % of GDP'] = to_num(df_fdi_raw.iloc[:, 2])
                    fig_fdi = go.Figure()
                    fig_fdi.add_trace(go.Bar(x=df_fdi['Year'], y=df_fdi['FDI Value (Million OMR)'], name='FDI Value (Million OMR)', marker_color='#1e3a8a', yaxis='y1'))
                    fig_fdi.add_trace(go.Scatter(x=df_fdi['Year'], y=df_fdi['FDI % of GDP'], name='FDI % of GDP', mode='lines+markers', line=dict(color='#ef4444', width=2), yaxis='y2'))
                    fig_fdi.update_layout(title="Total FDI Inflows & % of GDP",
                                          yaxis=dict(title='FDI Value (Million OMR)', side='left'),
                                          yaxis2=dict(title='FDI % of GDP', side='right', overlaying='y', showgrid=False),
                                          legend=dict(x=0, y=1.1, orientation='h'), hovermode='x unified')
                    show_chart(fig_fdi)
                else:
                    show_chart(px.line(df_fdi, x='Year', y='FDI Value (Million OMR)', title="Total FDI Inflow (Million OMR)", markers=True))
        with i2, section_guard("FDI by sector"):
            f_fdis = next((v for k, v in dfs.items() if "FDI per sector" in k), None)
            if f_fdis is not None:
                df_fdis = f_fdis.iloc[1:].copy().iloc[:, :2]
                df_fdis.columns = ['Sector', 'Value']
                df_fdis['Value'] = to_num(df_fdis['Value'])
                show_chart(px.pie(df_fdis, names='Sector', values='Value', title="FDI Distribution by Sector (2024)"))

        i3, i4 = st.columns(2)
        with i3, section_guard("Banking credit"):
            f_bank = next((v for k, v in dfs.items() if "banking credit by sector" in k), None)
            if f_bank is not None:
                df_bank = f_bank.iloc[1:].copy().iloc[:, :2]
                df_bank.columns = ['Sector', 'Credit']
                df_bank['Credit'] = to_num(df_bank['Credit'])
                show_chart(px.bar(df_bank, x='Sector', y='Credit', title="Banking Credit by Sector (2024)", color='Credit'))
        with i4, section_guard("SMEs"):
            f_sme = next((v for k, v in dfs.items() if "SME and percentage" in k), None)
            if f_sme is not None:
                df_sme = f_sme.iloc[1:].copy().iloc[:, :2]
                df_sme.columns = ['Sector', 'Count']
                df_sme['Count'] = to_num(df_sme['Count'])
                show_chart(px.bar(df_sme, x='Sector', y='Count', title="SMEs Distribution by Sector", color_discrete_sequence=['#22c55e']))

        i5, i6 = st.columns(2)
        with i5, section_guard("Riyada card holders"):
            f_riyada = next((v for k, v in dfs.items() if "riyada card holders" in k), None)
            if f_riyada is not None:
                df_rc = f_riyada.iloc[1:].copy().iloc[:, :2]
                df_rc.columns = ['Sector', 'Holders']
                df_rc['Holders'] = to_num(df_rc['Holders'])
                if df_rc['Holders'].dropna().max() <= 1:
                    df_rc['Holders'] = df_rc['Holders'] * 100
                df_rc['Label'] = df_rc['Holders'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "")
                show_chart(px.pie(df_rc, names='Sector', values='Holders', title="Riyada Card Holders (Entrepreneurship Proxy)",
                                  hover_data={'Label': True}).update_traces(texttemplate='%{label}<br>%{value:.1f}%', textposition='inside'))
        with i6, section_guard("Free zones agreements"):
            f_fz_lic = next((v for k, v in dfs.items() if "free zones agreements" in k), None)
            if f_fz_lic is not None:
                # load_zip reads with header=0, so df row 0 = real header row, rows 1+ = data
                raw = f_fz_lic.copy()
                header_row = raw.iloc[0]
                region_names = [str(v).strip() for v in header_row.values[2:] if pd.notna(v) and str(v).strip() != 'nan']
                data = raw.iloc[1:].copy().reset_index(drop=True)
                indicator_col = data.iloc[:, 0].ffill()
                year_col = data.iloc[:, 1]
                value_cols = data.iloc[:, 2: 2 + len(region_names)]

                records = []
                for idx in range(len(data)):
                    indicator = str(indicator_col.iloc[idx]).strip()
                    year_val = str(year_col.iloc[idx]).strip()
                    if year_val.endswith(".0"):
                        year_val = year_val[:-2]
                    if not re.match(r'^\d{4}$', year_val):
                        continue
                    for r_idx, region in enumerate(region_names):
                        try:
                            val = float(value_cols.iloc[idx, r_idx])
                        except (ValueError, TypeError):
                            val = None
                        records.append({'Indicator': indicator, 'Year': year_val, 'Region': region, 'Value': val})

                df_fzl_long = pd.DataFrame(records)
                if not df_fzl_long.empty:
                    df_fzl_long = df_fzl_long.dropna(subset=['Value'])
                if not df_fzl_long.empty:
                    all_indicators = sorted(df_fzl_long['Indicator'].unique())
                    all_years = sorted(df_fzl_long['Year'].unique())
                    all_regions = sorted(df_fzl_long['Region'].unique())
                    fc1, fc2, fc3 = st.columns(3)
                    with fc1: sel_fz_ind = st.selectbox("Indicator", all_indicators, key="fz_lic_ind")
                    with fc2: sel_fz_year = st.selectbox("Year", all_years, index=len(all_years) - 1, key="fz_lic_year")
                    with fc3: sel_fz_region = st.multiselect("Region", all_regions, default=all_regions, key="fz_lic_region")
                    df_fzl_plot = df_fzl_long[(df_fzl_long['Indicator'] == sel_fz_ind) & (df_fzl_long['Year'] == sel_fz_year)
                                              & (df_fzl_long['Region'].isin(sel_fz_region or all_regions))]
                    show_chart(px.bar(df_fzl_plot, x='Region', y='Value', color='Region', title=f"Free Zones - {sel_fz_ind} ({sel_fz_year})",
                                      labels={'Value': 'Count', 'Region': 'Free Zone'}))

        i7, i8 = st.columns(2)
        with i7, section_guard("Free zones investment"):
            f_fz_inv = next((v for k, v in dfs.items() if "investments free zones" in k), None)
            if f_fz_inv is not None:
                df_fzi = f_fz_inv.iloc[1:].copy().iloc[:, :2]
                df_fzi.columns = ['Zone', 'Investment']
                df_fzi['Investment'] = to_num(df_fzi['Investment'])
                show_chart(px.bar(df_fzi, x='Zone', y='Investment', title="Free Zones - Investment Levels (Million OMR) (2024)", color='Investment'))
        with i8, section_guard("Free zones space"):
            f_fz_space = next((v for k, v in dfs.items() if "free zones space" in k), None)
            if f_fz_space is not None:
                df_fzs = f_fz_space.iloc[1:].copy().iloc[:, :2]
                df_fzs.columns = ['Zone', 'Utilized Space']
                df_fzs['Utilized Space'] = to_num(df_fzs['Utilized Space'])
                show_chart(px.pie(df_fzs, names='Zone', values='Utilized Space', title="Free Zones - Space Utilization"))

        st.header("4. Labor Market & Employment")
        l1, l2 = st.columns(2)
        with l1, section_guard("Free zones workers"):
            f_fz_work = next((v for k, v in dfs.items() if "workers free zones" in k), None)
            if f_fz_work is not None:
                df_fzw = f_fz_work.iloc[1:].copy().iloc[:, :3]
                df_fzw.columns = ['Zone', 'National', 'Expat']
                df_fzw = df_fzw[~df_fzw['Zone'].astype(str).str.contains('الإجمالي|Total', na=False)]
                df_fzw = df_fzw.melt(id_vars='Zone', var_name='Type', value_name='Count')
                df_fzw['Count'] = to_num(df_fzw['Count'])
                show_chart(px.bar(df_fzw, x='Zone', y='Count', color='Type', barmode='group', title="Workers in Free Zones by Type (2024)"))
        with l2, section_guard("Omanization rate"):
            f_oman = next((v for k, v in dfs.items() if "Omanization rate" in k), None)
            if f_oman is not None:
                df_om = f_oman.iloc[2:].copy().iloc[:, [0, 1, 2, 4]]
                df_om.columns = ['Zone', 'National', 'Expat', 'Rate']
                df_om['Rate'] = to_num(df_om['Rate'].astype(str).str.replace(',', '.', regex=False))
                df_om.loc[df_om['Rate'] < 1, 'Rate'] *= 100
                show_chart(px.bar(df_om.dropna(subset=['Rate']), x='Zone', y='Rate', title="Omanization Rate in Free Zones (%) (2024)",
                                  color='Rate', color_continuous_scale='Blues'))
    else:
        st.info("Dashboard initialized. Please upload data and click 'Launch' to begin.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3: MARKET STRUCTURE MAPS (Kumu Embed)
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
    selected_map = st.selectbox("🔍 Select Sector", list(KUMU_MAPS.keys()), index=0, key="kumu_map_select")
    embed_url = KUMU_MAPS[selected_map]
    components.html(
        f"""
        <style>
          * {{ margin: 0; padding: 0; box-sizing: border-box; }}
          body {{ background: transparent; overflow: hidden; }}
          iframe {{ width: 100%; height: 800px; border: none; border-radius: 8px; display: block; }}
        </style>
        <iframe src="{embed_url}" allowfullscreen></iframe>
        """,
        height=810,
        scrolling=False,
    )
