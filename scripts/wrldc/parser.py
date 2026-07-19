"""
WRLDC Daily PSP Report parser. Same report family as NRLDC but West region,
with a few differences handled here:
  - bilingual (Hindi/English) headers,
  - 3(A) tables extract as ~43 sparse columns, but blanks are '-' (not None),
    so null-filtering each row yields clean 11-value sequences,
  - 2(C) has only 8 data columns -- WRLDC does NOT publish per-state MIN demand
    (or the max-requirement block) that NRLDC has,
  - total rows are 'TOTAL<X>' (upper, no space) vs NRLDC 'Total<X>'.

Tables captured (mirroring the NRLDC request):
  Table 1   -> regional availability (evening peak / off-peak / day energy)
  Table 2(C)-> per-state max demand met (+ time, shortage, requirement, ACE)
  Table 3(A)-> per-state per-station generation (state entities only, not 3B)
"""
import re

# Prefix rules, longest/most-specific first. Handles both the new (2024+) and
# old (2019-2023) naming, including the "CHHATISGARH" typo and the pre-merger
# separate entities (Dadra, Daman, ESIL) that later became DNHDDPDCL / AMNSIL.
_STATE_RULES = [
    ("CHHATTISGARH", "CHHATTISGARH"), ("CHHATISGARH", "CHHATTISGARH"),
    ("DNHDDPDCL", "DNHDDPDCL"),
    ("DADRA", "DADRA & NAGAR HAVELI"), ("DAMAN", "DAMAN & DIU"),
    ("AMNSIL", "AMNSIL"), ("ESIL", "ESIL"),
    ("RILJAMNAGAR", "RIL JAMNAGAR"), ("BALCO", "BALCO"),
    ("GOA", "GOA"), ("GUJARAT", "GUJARAT"),
    ("MADHYAPRADESH", "MADHYA PRADESH"), ("MAHARASHTRA", "MAHARASHTRA"),
]


def canon_state(raw):
    if raw is None:
        return None
    s = re.sub(r"\s+", "", str(raw).replace("\n", "")).strip().upper()
    for key, canon in _STATE_RULES:
        if s.startswith(key):
            return canon
    if s == "WR" or s.startswith("REGION"):
        return "WR"
    return None


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _time(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", s) else None


def _clean(v):
    return re.sub(r"\s+", " ", str(v).replace("\n", " ")).strip()


def _filt(row):
    return [c for c in row if c not in (None, "")]


# ---- Table 1 -----------------------------------------------------------------
def parse_table1(rows_filtered):
    for i, r in enumerate(rows_filtered):
        head = _clean(r[0]) if r else ""
        if head.startswith("EveningPeak") or (r and "Demand" in "".join(map(str, r)) and i <= 3):
            pass
        # locate the 10-number values row that follows the demand header
    # simpler: scan for the first row of exactly 10 numeric cells before 2(A)
    for r in rows_filtered:
        head = _clean(r[0]) if r else ""
        if head.startswith("2(A)"):
            break
        nums = [_num(x) for x in r]
        if len(nums) == 10 and all(n is not None for n in nums):
            return {
                "evening_peak_demand_met_mw": nums[0], "evening_peak_shortage_mw": nums[1],
                "evening_peak_requirement_mw": nums[2], "evening_peak_freq_hz": nums[3],
                "offpeak_demand_met_mw": nums[4], "offpeak_shortage_mw": nums[5],
                "offpeak_requirement_mw": nums[6], "offpeak_freq_hz": nums[7],
                "day_energy_demand_met_mu": nums[8], "day_energy_shortage_mu": nums[9],
            }
    return None


# ---- Table 2(C): per-state max demand -----------------------------------------
# WRLDC never publishes per-state MIN demand. The first 4 columns (max demand
# met, time, shortage, requirement) are identical across eras; the last 4 differ
# -- new (2024+) reports carry ACE_MAX/ACE_MIN, old (2019-2023) carry a max-
# requirement block. `has_ace` (from page text) picks the mapping; old-era ACE
# fields stay null.
def parse_table2c(rows_filtered, has_ace):
    out = []
    started = False
    for r in rows_filtered:
        head = _clean(r[0]) if r else ""
        if head.startswith("2(C)"):
            started = True
            continue
        if not started:
            continue
        if head.startswith("3(A)") or head.startswith("3("):
            break
        if len(r) == 9:
            canon = canon_state(r[0])
            if canon is None:
                continue
            row = {
                "state_raw": _clean(r[0]), "state_canonical": canon,
                "max_demand_met_mw": _num(r[1]), "max_demand_time": _time(r[2]),
                "shortage_at_max_demand_mw": _num(r[3]),
                "requirement_at_max_demand_mw": _num(r[4]),
                "ace_max": None, "ace_max_time": None,
                "ace_min": None, "ace_min_time": None,
            }
            if has_ace:
                row.update({
                    "ace_max": _num(r[5]), "ace_max_time": _time(r[6]),
                    "ace_min": _num(r[7]), "ace_min_time": _time(r[8]),
                })
            out.append(row)
    return out


# ---- Table 3(A): per-station generation --------------------------------------
BARE_SOURCE = {"WIND": "WIND", "SOLAR": "SOLAR", "BIOMASS": "BIOMASS",
               "SMALLHYDRO": "SMALL HYDRO"}
TOTAL_CATEGORY = {
    "TOTALTHERMAL": "THERMAL", "TOTALGAS": "GAS", "TOTALHYDEL": "HYDEL",
    "TOTALBIOMASS": "BIOMASS", "TOTALSOLAR": "SOLAR", "TOTALWIND": "WIND",
    "TOTALSMALLHYDRO": "SMALL HYDRO",
}


def _base(state, seq, row_type, cat, name):
    return {"state_canonical": state, "seq": seq, "row_type": row_type,
            "source_category": cat, "station_name": name}


def _build_colmap(main, sub):
    """Given the two raw header rows of a 3(A) block, return field -> column
    index. Robust to the 3 layout eras (single DayEnergy MU / Gross+Net /
    Gross+Net with MinGeneration) because it reads the actual header positions.
    Data values sit at the same column index as their sub-header cell."""
    def idx(row, pred):
        for i, c in enumerate(row):
            if c and pred(str(c).replace("\n", " ")):
                return i
        return None

    def all_idx(row, pred):
        return [i for i, c in enumerate(row) if c and pred(str(c).replace("\n", " "))]

    cm = {}
    cm["inst_capacity_mw"] = idx(main, lambda s: "Inst" in s or "क्षमता" in s)
    cm["peak_2000_mw"] = idx(sub, lambda s: s.strip() == "PeakMW")
    cm["offpeak_0300_mw"] = idx(sub, lambda s: s.strip() == "OffPeakMW")
    daypeak_main = idx(main, lambda s: s.strip() == "DayPeak")
    mingen_main = idx(main, lambda s: "MinGeneration" in s)
    energy_main = idx(main, lambda s: "DayEnergy" in s)
    cm["avg_mw_0618"] = idx(main, lambda s: "AVG" in s)

    mw_subs = all_idx(sub, lambda s: s.strip() == "(MW)")
    hrs_subs = all_idx(sub, lambda s: s.strip() == "Hrs")

    def in_range(i, lo, hi):
        return lo is not None and i >= lo and (hi is None or i < hi)

    # DayPeak block ends where MinGeneration or DayEnergy begins
    dp_hi = mingen_main if mingen_main is not None else energy_main
    cm["daypeak_mw"] = next((i for i in mw_subs if in_range(i, daypeak_main, dp_hi)), None)
    cm["daypeak_time"] = next((i for i in hrs_subs if in_range(i, daypeak_main, dp_hi)), None)
    if mingen_main is not None:
        cm["mingen_mw"] = next((i for i in mw_subs if in_range(i, mingen_main, energy_main)), None)
        cm["mingen_time"] = next((i for i in hrs_subs if in_range(i, mingen_main, energy_main)), None)
    else:
        cm["mingen_mw"] = cm["mingen_time"] = None

    gross = idx(sub, lambda s: "Gross" in s)
    net = idx(sub, lambda s: s.strip().startswith("Net"))
    if gross is not None or net is not None:
        cm["gross_gen_mu"] = gross
        cm["net_gen_mu"] = net
    else:
        # single "(MU)" energy column (2021-era) -> store as net, gross null
        single = next((i for i in all_idx(sub, lambda s: s.strip() == "(MU)")
                       if in_range(i, energy_main, cm["avg_mw_0618"])), None)
        cm["gross_gen_mu"] = None
        cm["net_gen_mu"] = single
    return cm


def _extract(raw, cm):
    def get(field, conv):
        i = cm.get(field)
        return conv(raw[i]) if (i is not None and i < len(raw)) else None
    return {
        "inst_capacity_mw": get("inst_capacity_mw", _num),
        "peak_2000_mw": get("peak_2000_mw", _num),
        "offpeak_0300_mw": get("offpeak_0300_mw", _num),
        "daypeak_mw": get("daypeak_mw", _num),
        "daypeak_time": get("daypeak_time", _time),
        "mingen_mw": get("mingen_mw", _num),
        "mingen_time": get("mingen_time", _time),
        "gross_gen_mu": get("gross_gen_mu", _num),
        "net_gen_mu": get("net_gen_mu", _num),
        "avg_mw_0618": get("avg_mw_0618", _num),
    }


def parse_table3a(tables_3a):
    out = []
    seq_by_state = {}
    for rows in tables_3a:
        current = None
        pending = []
        colmap = None
        for raw in rows:
            nonempty = [c for c in raw if c not in (None, "")]
            if not nonempty:
                continue
            if any(str(c).lstrip().startswith("3(B)") for c in raw):
                return out
            c0 = _clean(raw[0]) if raw[0] else ""
            key = c0.replace(" ", "").upper()
            # state header: single non-empty cell that normalizes to a state
            if len(nonempty) == 1 and canon_state(c0) is not None:
                current = canon_state(c0)
                pending = []
                continue
            # main header row -> pair with next sub-header row to build colmap
            if any(cell and ("Inst" in str(cell) or "क्षमता" in str(cell)) for cell in raw):
                sub = rows[rows.index(raw) + 1] if rows.index(raw) + 1 < len(rows) else []
                colmap = _build_colmap(raw, sub)
                continue
            if current is None or colmap is None:
                continue
            if "Station/" in c0 or key.startswith("(MW)") or key == "PEAKMW" \
                    or c0 == "NIL" or "NORECORDS" in key:
                continue
            # "TOTAL-" placeholder: on days a state has no generation data, the
            # report prints "No Records Found" followed by one or two zeroed
            # "TOTAL-" rows instead of any real total/station row. It doesn't
            # match the category-total or state-total patterns below (the
            # suffix after "TOTAL" is just "-"), so without this check it
            # falls through to "station" with a garbage name -- drop it.
            if key.startswith("TOTAL") and key not in TOTAL_CATEGORY \
                    and canon_state(key[5:]) != current:
                continue
            if canon_state(c0) is not None and len(nonempty) == 1:
                continue
            vals = _extract(raw, colmap)
            if all(v is None for v in vals.values()):
                continue
            seq = seq_by_state.get(current, 0)
            if key in TOTAL_CATEGORY:
                cat = TOTAL_CATEGORY[key]
                for st in pending:
                    if st["source_category"] is None:
                        st["source_category"] = cat
                pending = []
                out.append({**_base(current, seq, "source_total", cat, c0), **vals})
            elif key.startswith("TOTAL") and canon_state(key[5:]) == current:
                out.append({**_base(current, seq, "state_total", None, c0), **vals})
                pending = []
            elif key in BARE_SOURCE:
                out.append({**_base(current, seq, "source_total", BARE_SOURCE[key], c0), **vals})
            else:
                rec = {**_base(current, seq, "station", None, c0), **vals}
                out.append(rec)
                pending.append(rec)
            seq_by_state[current] = seq + 1
    return out


def parse_report(pdf):
    page1 = []
    page1_text = ""
    if pdf.pages:
        page1_text = pdf.pages[0].extract_text() or ""
        t = pdf.pages[0].extract_tables()
        if t:
            page1 = [_filt(row) for row in t[0]]
    table1 = parse_table1(page1)
    has_ace = "ACE_MAX" in page1_text.replace(" ", "") or "ACE" in page1_text
    table2c = parse_table2c(page1, has_ace)

    # Collect every table from the 3(A) marker through the table that first
    # contains 3(B); parse_table3a stops precisely at the 3(B) row (Maharashtra
    # and 3B can live in the same page/table).
    tables_3a = []
    seen_3a = False
    done = False
    for p in pdf.pages:
        if done:
            break
        for tbl in p.extract_tables():
            labels = [_clean(c) for row in tbl for c in row if c]
            if any(lbl.startswith("3(A)") for lbl in labels):
                seen_3a = True
            if seen_3a:
                tables_3a.append(tbl)  # raw rows -- 3A parse needs column positions
            if any(lbl.startswith("3(B)") for lbl in labels):
                done = True
                break
    table3a = parse_table3a(tables_3a)
    return {"regional": table1, "state_demand": table2c, "state_generation": table3a}
