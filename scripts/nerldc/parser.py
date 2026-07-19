"""
NERLDC Daily PSP Report parser -- MODERN format (Jul 2019 onward), the same
GRID-INDIA report family as NRLDC/WRLDC/ERLDC/SRLDC. Reuses the SRLDC parser
logic (identical 12-column 2(C) + block-buffered 3(A)) with a Northeast state
list. NOTE: NERLDC truncates the ACE time columns in the PDF (e.g. "08:1"), so
ace_max_time / ace_min_time come back mostly null -- the ACE values are fine.

Tables captured:
  Table 1   -> regional availability (evening peak / off-peak / day energy)
  Table 2(C)-> per-state max demand + max-requirement block + ACE max/min
  Table 3(A)-> per-state per-station generation (header-driven, block-buffered)
"""
import re

# Northeast-region states, exact-match (minus parentheticals) so generation
# sub-rows like "ASSAM_SOLAR(...)" are not misread as the state.
NE_EXACT = {
    "ARUNACHALPRADESH": "ARUNACHAL PRADESH", "ARUNACHAL": "ARUNACHAL PRADESH",
    "ARP": "ARUNACHAL PRADESH",
    "ASSAM": "ASSAM",
    "MANIPUR": "MANIPUR",
    "MEGHALAYA": "MEGHALAYA",
    "MIZORAM": "MIZORAM",
    "NAGALAND": "NAGALAND",
    "TRIPURA": "TRIPURA",
    "REGION": "REGION", "NER": "REGION", "NR": "REGION",
}


def canon_state(raw):
    if raw is None:
        return None
    s = re.sub(r"\(.*", "", str(raw).replace("\n", ""))  # drop parenthetical + trailer
    s = re.sub(r"\s+", "", s).strip().upper()
    return NE_EXACT.get(s)


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


def is_modern(page1_text):
    # Modern GRID-INDIA format is identified by the 2(A)/2(C) section numbering
    # on page 1 (the old POSOCO format lacks these entirely). We deliberately do
    # NOT require "3(A)" -- in some reports the 3(A) generation section starts on
    # page 2, so it isn't in page-1 text -- nor the peak hour ("20:00"), which is
    # 19:00 in winter.
    t = page1_text.replace(" ", "")
    return ("2(A)" in t) and ("2(C)" in t)


# ---- Table 1 -----------------------------------------------------------------
_T1_NUM = re.compile(r"-?\d+\.?\d*")


def parse_table1(rows_filtered):
    # The values row holds exactly 10 numbers (EP demand/shortage/req/freq,
    # OP demand/shortage/req/freq, DE demand/shortage). We tokenize numbers
    # ACROSS the whole row rather than per-cell, because some reports merge the
    # EP "Demand Met" and "Shortage" into a single cell like "2657 203".
    for r in rows_filtered:
        head = _clean(r[0]) if r else ""
        if head.startswith("2(A)"):
            break
        nums = [float(t) for c in r if c for t in _T1_NUM.findall(str(c))]
        if len(nums) == 10:
            return {
                "evening_peak_demand_met_mw": nums[0], "evening_peak_shortage_mw": nums[1],
                "evening_peak_requirement_mw": nums[2], "evening_peak_freq_hz": nums[3],
                "offpeak_demand_met_mw": nums[4], "offpeak_shortage_mw": nums[5],
                "offpeak_requirement_mw": nums[6], "offpeak_freq_hz": nums[7],
                "day_energy_demand_met_mu": nums[8], "day_energy_shortage_mu": nums[9],
            }
    return None


# ---- Table 2(C): per-state max demand + max-req block + ACE (12 columns) -------
# The SRLDC 2(C) sub-header has 12 field cells in a FIXED left-to-right order.
# Labels repeat ("Time" x4, "Shortage..." x2), so we map by SEQUENCE, not by
# label text: the i-th field cell after the state column is field FIELD_ORDER[i].
FIELD_ORDER = [
    "max", "max_time", "shortage_max", "req_max",
    "dm_maxreq", "maxreq_time", "shortage_maxreq", "max_req",
    "ace_max", "ace_max_time", "ace_min", "ace_min_time",
]


def parse_table2c(raw_rows):
    # raw_rows are the pre-split 2(C) section rows (marker already stripped).
    out = []
    colmap = None
    for raw in raw_rows:
        f = _filt(raw)
        head = _clean(f[0]) if f else ""
        if head.startswith("3("):
            break
        # sub-header row carrying the field labels -> map columns by order.
        # Match the "MaximumDeman" prefix (not the full "MaximumDemandMet") since
        # some reports split the word "Demand" -> "Deman"+"d" across cells. Guard
        # with a cell-count check so the 3-cell super-header ("Maximum Demand,
        # corr..." / "Maximum requirement..." / "ACE") isn't mistaken for it.
        joined = "".join(_clean(c) for c in raw if c).replace(" ", "")
        if "MaximumDeman" in joined and len(_filt(raw)) >= 6:
            field_cols = [i for i, c in enumerate(raw) if c and i > 0]
            colmap = {FIELD_ORDER[k]: col
                      for k, col in enumerate(field_cols) if k < len(FIELD_ORDER)}
            continue
        canon = canon_state(raw[0]) if raw and raw[0] else None
        if canon is None or colmap is None:
            continue

        def g(field, conv):
            i = colmap.get(field)
            return conv(raw[i]) if (i is not None and i < len(raw)) else None
        max_demand = g("max", _num)
        if max_demand is None:
            continue  # skip stray rows (e.g. a generation state-header)
        out.append({
            "state_raw": _clean(raw[0]), "state_canonical": canon,
            "max_demand_met_mw": max_demand, "max_demand_time": g("max_time", _time),
            "shortage_at_max_demand_mw": g("shortage_max", _num),
            "requirement_at_max_demand_mw": g("req_max", _num),
            "demand_met_at_max_req_mw": g("dm_maxreq", _num),
            "max_req_time": g("maxreq_time", _time),
            "shortage_at_max_req_mw": g("shortage_maxreq", _num),
            "max_requirement_mw": g("max_req", _num),
            "ace_max": g("ace_max", _num), "ace_max_time": g("ace_max_time", _time),
            "ace_min": g("ace_min", _num), "ace_min_time": g("ace_min_time", _time),
        })
    return out


# ---- Table 3(A): per-station generation (header-driven, same as WRLDC) --------
CATEGORY_KEYWORDS = {
    "THERMAL": "THERMAL", "HYDEL": "HYDEL", "HYDRO": "HYDEL", "RES": "RES",
    "SOLAR": "SOLAR", "WIND": "WIND", "GAS": "GAS", "OTHERS": "OTHERS",
    "OTHER": "OTHERS", "CPP_IMPORT": "CPP IMPORT", "CPPIMPORT": "CPP IMPORT",
    "CESC": "CESC", "SMALLHYDRO": "SMALL HYDRO",
}
BARE_SOURCE = {"WIND": "WIND", "SOLAR": "SOLAR", "BIOMASS": "BIOMASS",
               "SMALLHYDRO": "SMALL HYDRO", "RES": "RES"}


def _category_from_total(key):
    """key like 'TOTALTHERMAL' or 'TOTALRES(JHARKHAND)(1*44.15)' -> category."""
    body = key[5:]
    body = re.sub(r"\(.*", "", body)  # drop parenthetical annotations
    return CATEGORY_KEYWORDS.get(body)


def _base(state, seq, row_type, cat, name):
    return {"state_canonical": state, "seq": seq, "row_type": row_type,
            "source_category": cat, "station_name": name}


def _build_colmap(main, sub):
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
    cm["offpeak_0300_mw"] = idx(sub, lambda s: s.replace(" ", "") == "OffPeakMW")
    daypeak_main = idx(main, lambda s: s.strip() == "DayPeak")
    mingen_main = idx(main, lambda s: "MinGeneration" in s)
    energy_main = idx(main, lambda s: "DayEnergy" in s)
    cm["avg_mw_0618"] = idx(main, lambda s: "AVG" in s)
    mw_subs = all_idx(sub, lambda s: s.strip() == "(MW)")
    hrs_subs = all_idx(sub, lambda s: s.strip() == "Hrs")

    def in_range(i, lo, hi):
        return lo is not None and i >= lo and (hi is None or i < hi)

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
        cm["gross_gen_mu"], cm["net_gen_mu"] = gross, net
    else:
        single = next((i for i in all_idx(sub, lambda s: s.strip() == "(MU)")
                       if in_range(i, energy_main, cm["avg_mw_0618"])), None)
        cm["gross_gen_mu"], cm["net_gen_mu"] = None, single
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
    # SRLDC generation blocks are delimited by their CLOSING "Total <STATE>" row
    # (e.g. "Total ANDHRA PRADESH"), not always a header at the top: the 2020-era
    # reports have a lone state header first, but the 2026-era reports have none
    # -- only the closing total names the state. So we buffer each block and
    # assign its state when we hit the "Total <STATE>" row (falling back to a
    # lone-header hint if a block ends without one).
    out = []
    seq_by_state = {}

    def flush(state, block):
        if state is None or not block:
            return
        seq = seq_by_state.get(state, 0)
        for rec in block:
            rec["state_canonical"] = state
            rec["seq"] = seq
            seq += 1
            out.append(rec)
        seq_by_state[state] = seq

    for rows in tables_3a:
        block, pending_cat, hint, colmap = [], [], None, None
        for ri, raw in enumerate(rows):
            nonempty = [c for c in raw if c not in (None, "")]
            if not nonempty:
                continue
            if any(str(c).lstrip().startswith("3(B)") for c in raw):
                flush(hint, block)
                return out
            c0 = _clean(raw[0]) if raw[0] else ""
            key = c0.replace(" ", "").upper()
            # lone state header (2020 style) -> remember as a fallback hint
            if len(nonempty) == 1 and canon_state(c0) is not None:
                hint = canon_state(c0)
                continue
            # 3(B) boundary: a lone pure-text label that is NOT a state (e.g.
            # "ADANI POWER LTD"), seen BETWEEN state blocks (block empty, at least
            # one state already emitted). Restricting to `out and not block` avoids
            # a mid-block station whose row collapsed into one merged cell (e.g.
            # "VEMAGIRI POWER GENERATION LTD.(GAS)...") being mistaken for 3(B).
            if len(nonempty) == 1 and canon_state(c0) is None and not _is_gen_header(raw) \
                    and not re.search(r"\d", re.sub(r"\(.*?\)", "", c0)) \
                    and out and not block:
                flush(hint, block)
                return out
            if any(cell and ("Inst" in str(cell) or "क्षमता" in str(cell)) for cell in raw):
                sub = rows[ri + 1] if ri + 1 < len(rows) else []
                colmap = _build_colmap(raw, sub)
                continue
            if colmap is None:
                continue
            if "Station/" in c0 or key.startswith("(MW)") or key == "PEAKMW" \
                    or c0 == "NIL" or "NORECORDS" in key:
                continue
            vals = _extract(raw, colmap)
            # Total-row classification happens BEFORE the all-None station check:
            # in older reports the "Total <STATE>" / "Total <CAT>" recap rows sit
            # in a compact column layout that the station colmap can't read, so
            # their values come back all-None -- but we still must recognise them
            # to close/label the block (else the block never closes and its
            # stations get absorbed into the next state).
            state_of_total = (canon_state(re.sub(r"\(.*", "", key[5:]))
                              if key.startswith("TOTAL") else None)
            cat_total = _category_from_total(key) if key.startswith("TOTAL") else None
            if state_of_total is not None:  # "Total <STATE>" -> close the block
                block.append({**_base(None, 0, "state_total", None, c0), **vals})
                flush(state_of_total, block)
                block, pending_cat, hint = [], [], None
            elif cat_total:  # "Total <CATEGORY>" -> label the pending stations
                for st in pending_cat:
                    if st["source_category"] is None:
                        st["source_category"] = cat_total
                pending_cat = []
                block.append({**_base(None, 0, "source_total", cat_total, c0), **vals})
            elif key in BARE_SOURCE:
                block.append({**_base(None, 0, "source_total", BARE_SOURCE[key], c0), **vals})
            elif not all(v is None for v in vals.values()):  # a real station row
                rec = {**_base(None, 0, "station", None, c0), **vals}
                block.append(rec)
                pending_cat.append(rec)
        flush(hint, block)  # end of stream -> flush any open block via hint
    return out


def _section_of(row):
    """Return the section marker a row starts, else None."""
    if not row or not row[0]:
        return None
    h = _clean(row[0]).replace(" ", "")
    # NB: section 4 must be matched as "4." / "4(" -- a bare "4" would also match
    # a Table 1 demand value like "43521" and steal the values row.
    for mk, name in [("1.", "1"), ("2(A)", "2A"), ("2(B)", "2B"),
                     ("2(C)", "2C"), ("3(A)", "3A"), ("3(B)", "3B"),
                     ("4.", "4"), ("4(", "4")]:
        if h.startswith(mk):
            return name
    return None


def _split_sections(all_rows):
    # Regional availability (section 1) has no "1." row inside the extracted
    # tables -- its header/values sit before the first 2(A) marker -- so start
    # in section "1" and let the first real marker switch us out of it.
    sections = {}
    cur = "1"
    for row in all_rows:
        mk = _section_of(row)
        if mk is not None:
            cur = mk
            sections.setdefault(cur, [])
            continue
        sections.setdefault(cur, []).append(row)
    return sections


def parse_report(pdf):
    page1_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    if not is_modern(page1_text):
        return None  # old POSOCO format -> skip

    # Flatten tables into one ordered row stream. The 2020-2023 reports split
    # page 1 into several tables and run generation across page breaks; a single
    # stream + section markers handles all layouts uniformly. We only need
    # through the 3(A)/3(B) generation section, which always precedes the "4."
    # Inter-Regional Exchanges section -- so stop extracting once a page contains
    # a "4." / inter-regional marker (pages 4-11 are line-detail tables we don't
    # use, and extracting them is the bulk of the per-report cost).
    all_rows = []
    for p in pdf.pages:
        page_text = (p.extract_text() or "")
        for tbl in p.extract_tables():
            all_rows.extend(tbl)
        tnorm = page_text.replace(" ", "").upper()
        if "INTER-REGIONALEXCHANGE" in tnorm or "4.A" in tnorm or "4(A)" in tnorm:
            break

    sections = _split_sections(all_rows)
    table1 = parse_table1([_filt(r) for r in sections.get("1", [])])

    # In 2024+ reports the 2(C) demand table and 3(A) generation are in their
    # own marker-delimited sections. In 2020-2023 there is NO 3(A) marker row,
    # so the generation blocks get lumped into the 2(C) section (before the
    # demand table). Pool both and split by content: the demand table starts at
    # its "Maximum Demand Met / Time / Requirement" header; everything else is
    # generation.
    # Some reports (a scattered ~8%) have NO 2(A)/2(C)/3(A)/3(B) marker rows at
    # all in their extracted tables -- every table lands in section "1". Include
    # section "1" in the pool so those are covered too; the content-based split
    # and the 3(B) boundary stop (in parse_table3a) sort out what's what.
    pool = sections.get("1", []) + sections.get("2C", []) + sections.get("3A", [])
    demand_rows, gen_rows = _separate_2c_3a(pool)
    table2c = parse_table2c(demand_rows)
    table3a = parse_table3a([gen_rows])
    return {"regional": table1, "state_demand": table2c, "state_generation": table3a}


def _is_gen_header(row):
    if not row:
        return False
    if row[0] and "Station/" in _clean(row[0]):
        return True
    return any(c and "Inst.Capacity" in _clean(c) for c in row)


def _separate_2c_3a(pool):
    # locate the 2(C) demand-table field header
    H = None
    for i, row in enumerate(pool):
        joined = "".join(_clean(c).replace(" ", "") for c in row if c)
        # "MaximumDeman" prefix tolerates the split-word "Deman"+"d" header.
        if "MaximumDeman" in joined and (len(_filt(row)) >= 6 or "ACE" in joined):
            H = i
            break
    if H is None:
        return [], pool  # no demand table -> everything is generation
    demand = [pool[H]]
    j = H + 1
    while j < len(pool):
        row = pool[j]
        f = _filt(row)
        c0 = _clean(f[0]) if f else ""
        is_lone_state = len(f) == 1 and canon_state(c0) is not None
        nxt = pool[j + 1] if j + 1 < len(pool) else []
        if _is_gen_header(row) or (is_lone_state and _is_gen_header(nxt)):
            break
        demand.append(row)
        j += 1
    generation = pool[:H] + pool[j:]
    return demand, generation
