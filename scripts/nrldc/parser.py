"""
NRLDC Daily PSP Report parser. The PDF has ruled tables, so pdfplumber's
line-based extract_tables() gives clean cells -- far more reliable than text
parsing. We pull three tables the user asked for:

  Table 1  -> regional availability/demand (evening peak / off-peak / day energy)
  Table 2(C) -> per-state max & min demand met (+ times, ACE, requirement)
  Table 3(A) -> per-state, per-station generation (state entities only, not 3B)

Report date comes from the document title 'dailyDDMMYY' (data date), which the
report body confirms as "...For DD-Mon-YYYY".
"""
import re

# ---- state-name normalization -------------------------------------------------
# Raw labels vary: linebreaks, abbreviations, and mid-word truncation with '..'
# in the narrow 2(C) columns ('UP', 'HP', 'UTTARAKHA ..', 'J&K(UT)&Lad ..').
# Matched against the space-collapsed, upper-cased label via prefix/exact rules
# (order matters: UTTARAK before UTTARP so "UTTARAKHAND" doesn't fall to UP).
def canon_state(raw):
    if raw is None:
        return None
    s = re.sub(r"\s+", "", str(raw).replace("\n", "")).strip().upper().replace("..", "")
    if s.startswith("PUNJAB"):
        return "PUNJAB"
    if s.startswith("HARYANA"):
        return "HARYANA"
    if s.startswith("RAJASTHAN"):
        return "RAJASTHAN"
    if s.startswith("DELHI"):
        return "DELHI"
    if s.startswith("UTTARAKH"):
        return "UTTARAKHAND"
    if s.startswith("UTTARP") or s == "UP":
        return "UTTAR PRADESH"
    if s.startswith("HIMACHAL") or s == "HP":
        return "HIMACHAL PRADESH"
    if s.startswith("J&K"):
        return "J&K & LADAKH"
    if s.startswith("CHANDIGARH"):
        return "CHANDIGARH"
    if s.startswith("RAILWAYS"):
        return "RAILWAYS_NR ISTS"
    if s.startswith("BULK"):
        return "BULK CONSUMER_NR ISTS"
    if s == "NR" or s.startswith("REGION"):
        return "NR"
    return None


KNOWN_STATE_HEADERS = {
    "CHANDIGARH", "DELHI", "HARYANA", "HIMACHALPRADESH", "J&K(UT)&LADAKH(UT)",
    "PUNJAB", "RAJASTHAN", "UTTARPRADESH", "UTTARAKHAND",
}


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
    if s in ("", "-", "--"):
        return None
    return s if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", s) else None


def _filt(row):
    return [c for c in row if c not in (None, "")]


# ---- Table 1: Regional availability ------------------------------------------
def parse_table1(rows_filtered):
    """rows_filtered: list of null-filtered cell lists from page 1's table.
    The values row has exactly 10 numerics right after the DemandMet header."""
    for i, r in enumerate(rows_filtered):
        if r and str(r[0]).replace("\n", "").startswith("DemandMet"):
            # next row holds the 10 values
            for j in range(i + 1, min(i + 3, len(rows_filtered))):
                vals = rows_filtered[j]
                nums = [_num(x) for x in vals]
                if len(nums) == 10 and all(n is not None for n in nums):
                    return {
                        "evening_peak_demand_met_mw": nums[0],
                        "evening_peak_shortage_mw": nums[1],
                        "evening_peak_requirement_mw": nums[2],
                        "evening_peak_freq_hz": nums[3],
                        "offpeak_demand_met_mw": nums[4],
                        "offpeak_shortage_mw": nums[5],
                        "offpeak_requirement_mw": nums[6],
                        "offpeak_freq_hz": nums[7],
                        "day_energy_demand_met_mu": nums[8],
                        "day_energy_shortage_mu": nums[9],
                    }
    return None


# ---- Table 2(C): per-state max/min demand ------------------------------------
def parse_table2c(rows_filtered):
    out = []
    started = False
    for r in rows_filtered:
        head = str(r[0]).replace("\n", "") if r else ""
        if head.startswith("2(C)"):
            started = True
            continue
        if not started:
            continue
        if head.startswith("3(A)") or head.startswith("3("):
            break
        # a data row: state + 14 values
        if len(r) == 15:
            canon = canon_state(r[0])
            if canon is None:
                continue
            out.append({
                "state_raw": re.sub(r"\s+", " ", str(r[0]).replace("\n", " ")).strip(),
                "state_canonical": canon,
                "max_demand_met_mw": _num(r[1]),
                "max_demand_time": _time(r[2]),
                "shortage_at_max_demand_mw": _num(r[3]),
                "requirement_at_max_demand_mw": _num(r[4]),
                "max_requirement_mw": _num(r[5]),
                "max_requirement_time": _time(r[6]),
                "shortage_at_max_requirement_mw": _num(r[7]),
                "demand_met_at_max_requirement_mw": _num(r[8]),
                "min_demand_met_mw": _num(r[9]),
                "min_demand_time": _time(r[10]),
                "ace_max": _num(r[11]),
                "ace_max_time": _time(r[12]),
                "ace_min": _num(r[13]),
                "ace_min_time": _time(r[14]),
            })
    return out


# ---- Table 3(A): per-state per-station generation ----------------------------
SOURCE_FROM_TOTAL = {
    "TotalTHERMAL": "THERMAL", "TotalGAS/NAPTHA/DIESEL": "GAS/NAPTHA/DIESEL",
    "TotalHYDEL": "HYDEL", "TotalSMALLHYDRO": "SMALL HYDRO",
}
STANDALONE_SOURCE = {"WIND": "WIND", "SOLAR": "SOLAR", "BIOMASS": "BIOMASS"}


def _clean_label(v):
    return re.sub(r"\s+", " ", str(v).replace("\n", " ")).strip()


def parse_table3a(raw_tables_in_order):
    """raw_tables_in_order: list of (page_index, table_rows) for tables that are
    pure 3(A) blocks (header row col0 == 'Station/Constituents', 11 columns).
    Returns list of station/total rows with source category assigned by the
    Total<CATEGORY> row that closes each group."""
    out = []
    seq_by_state = {}
    for rows in raw_tables_in_order:
        current_state = None
        pending = []  # stations awaiting their category total
        for row in rows:
            r = (row + [None] * 11)[:11]
            c0 = _clean_label(r[0]) if r[0] else ""
            c0nospace = c0.replace(" ", "")
            if c0nospace in KNOWN_STATE_HEADERS and all(x in (None, "") for x in r[1:]):
                current_state = canon_state(c0)
                pending = []
                continue
            if current_state is None:
                continue
            if c0 in ("Station/Constituents", "") or c0nospace.startswith("(MW)") \
                    or c0 == "NIL" or c0nospace == "PeakMW":
                continue

            values = {
                "inst_capacity_mw": _num(r[1]), "peak_2000_mw": _num(r[2]),
                "offpeak_0300_mw": _num(r[3]), "daypeak_mw": _num(r[4]),
                "daypeak_time": _time(r[5]), "mingen_mw": _num(r[6]),
                "mingen_time": _time(r[7]), "gross_gen_mu": _num(r[8]),
                "net_gen_mu": _num(r[9]), "avg_mw_0618": _num(r[10]),
            }

            # category-total row: assign category to the pending stations
            key = c0nospace
            if key in SOURCE_FROM_TOTAL:
                cat = SOURCE_FROM_TOTAL[key]
                for st in pending:
                    st["source_category"] = cat
                pending = []
                seq = seq_by_state.get(current_state, 0)
                out.append({**base(current_state, seq, "source_total", cat, c0), **values})
                seq_by_state[current_state] = seq + 1
                continue
            # standalone source line (WIND/SOLAR/BIOMASS with its own value)
            if key in STANDALONE_SOURCE or key.startswith("SOLAR") or \
                    key.startswith("BIOMASS") or key.startswith("WIND") or key.startswith("SMALLHYDRO"):
                cat = ("SMALL HYDRO" if key.startswith("SMALLHYDRO")
                       else "WIND" if key.startswith("WIND")
                       else "SOLAR" if key.startswith("SOLAR")
                       else "BIOMASS")
                # SMALLHYDRO(x) with sub-stations is closed by TotalSMALLHYDRO;
                # a bare WIND/SOLAR/BIOMASS value line is its own source_total.
                if key.startswith("SMALLHYDRO") and not key.startswith("TotalSMALLHYDRO"):
                    seq = seq_by_state.get(current_state, 0)
                    out.append({**base(current_state, seq, "station", cat, c0), **values})
                    seq_by_state[current_state] = seq + 1
                    pending.append(out[-1])
                    continue
                seq = seq_by_state.get(current_state, 0)
                out.append({**base(current_state, seq, "source_total", cat, c0), **values})
                seq_by_state[current_state] = seq + 1
                continue
            # state total row: TotalDELHI, TotalHARYANA, ...
            if key.startswith("Total") and canon_state(key[5:]) == current_state:
                seq = seq_by_state.get(current_state, 0)
                out.append({**base(current_state, seq, "state_total", None, c0), **values})
                seq_by_state[current_state] = seq + 1
                pending = []
                continue
            # otherwise: a generating station awaiting its category total
            seq = seq_by_state.get(current_state, 0)
            rec = {**base(current_state, seq, "station", None, c0), **values}
            out.append(rec)
            seq_by_state[current_state] = seq + 1
            pending.append(rec)
    return out


def base(state, seq, row_type, category, name):
    return {"state_canonical": state, "seq": seq, "row_type": row_type,
            "source_category": category, "station_name": name}


# ---- top-level ---------------------------------------------------------------
def parse_report(pdf):
    """pdf: an open pdfplumber.PDF. Returns dict with the 3 table datasets."""
    page1_filtered = []
    if pdf.pages:
        t = pdf.pages[0].extract_tables()
        if t:
            page1_filtered = [_filt(row) for row in t[0]]

    table1 = parse_table1(page1_filtered)
    table2c = parse_table2c(page1_filtered)

    # Collect pure-3A tables in document order, stopping the moment we reach
    # the 3(B) Regional Entities section (its tables also carry a
    # 'Station/Constituents' header, so they must be cut off explicitly).
    tables_3a = []
    stop = False
    for p in pdf.pages:
        if stop:
            break
        for tbl in p.extract_tables():
            labels = [_clean_label(c) for row in tbl for c in row if c]
            if any(lbl.startswith("3(B)") for lbl in labels):
                stop = True
                break
            is_3a = any(_clean_label(row[0]) == "Station/Constituents"
                        for row in tbl if row and row[0])
            if is_3a and len(tbl[0]) == 11:
                tables_3a.append(tbl)
    table3a = parse_table3a(tables_3a)

    return {"regional": table1, "state_demand": table2c, "state_generation": table3a}
