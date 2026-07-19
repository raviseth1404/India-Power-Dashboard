"""
ERLDC Daily PSP Report parser -- MODERN format only (2020-04-11 onward), which
is the same GRID-INDIA report family as NRLDC/WRLDC. Older POSOCO-format reports
(2015 - 2020-04-10) are a different layout and are intentionally not parsed here
(they return empty and are skipped by the backfill).

Tables captured:
  Table 1   -> regional availability (evening peak / off-peak / day energy)
  Table 2(C)-> per-state max demand met (+ time, shortage, requirement, ACE max/min)
              NOTE ERLDC 2(C) has no per-state MIN demand and no ACE times.
  Table 3(A)-> per-state per-station generation (header-driven, like WRLDC)
"""
import re

# Exact collapsed forms -> canonical. Matching is EXACT (after stripping
# parenthetical annotations), not prefix, so generation sub-rows such as
# "ODISHA_SOLAR(409.16)" or "BIHAR SUGAR BAGASSE(112.5)" are NOT treated as the
# state ODISHA/BIHAR (which caused primary-key collisions in 2(C)).
EAST_EXACT = {
    "WESTBENGAL": "WEST BENGAL", "BIHAR": "BIHAR", "JHARKHAND": "JHARKHAND",
    "DVC": "DVC", "ODISHA": "ODISHA", "ORISSA": "ODISHA", "ORISHA": "ODISHA",
    "SIKKIM": "SIKKIM", "REGION": "REGION", "ER": "REGION",
}


def canon_state(raw):
    if raw is None:
        return None
    s = re.sub(r"\(.*", "", str(raw).replace("\n", ""))  # drop parenthetical + trailer
    s = re.sub(r"\s+", "", s).strip().upper()
    if s in EAST_EXACT:
        return EAST_EXACT[s]
    if s.startswith("RAILWAYS"):  # Railways_ER ISTS (has ISTS suffix variants)
        return "RAILWAYS_ER ISTS"
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


def is_modern(page1_text):
    # Modern GRID-INDIA format is identified by the 2(A)/2(C) section numbering
    # on page 1 (the old POSOCO format lacks these entirely). We deliberately do
    # NOT require "3(A)" -- in some reports the 3(A) generation section starts on
    # page 2, so it isn't in page-1 text -- nor the peak hour ("20:00"), which is
    # 19:00 in winter.
    t = page1_text.replace(" ", "")
    return ("2(A)" in t) and ("2(C)" in t)


# ---- Table 1 -----------------------------------------------------------------
def parse_table1(rows_filtered):
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


# ---- Table 2(C): per-state max demand (header-driven column map) --------------
def parse_table2c(raw_rows):
    # raw_rows are the pre-split 2(C) section rows (marker already stripped).
    out = []
    colmap = None
    for raw in raw_rows:
        f = _filt(raw)
        head = _clean(f[0]) if f else ""
        if head.startswith("3("):
            break
        # header row carrying the field labels -> build column map
        joined = " ".join(_clean(c) for c in raw if c)
        if "MaximumDemandMet" in joined.replace(" ", "") or "MaximumACE" in joined.replace(" ", ""):
            cm = {}
            for i, c in enumerate(raw):
                if not c:
                    continue
                lab = _clean(c).replace(" ", "")
                if lab.startswith("MaximumDemandMet"):
                    cm["max"] = i
                elif lab == "Time":
                    cm["time"] = i
                elif lab.startswith("Shortage"):
                    cm["shortage"] = i
                elif lab.startswith("Requirement"):
                    cm["req"] = i
                elif lab.startswith("MaximumACE"):
                    cm["ace_max"] = i
                elif lab.startswith("MinimumACE"):
                    cm["ace_min"] = i
            if "max" in cm:
                colmap = cm
            continue
        canon = canon_state(raw[0]) if raw and raw[0] else None
        if canon is None or colmap is None:
            continue

        def g(field, conv):
            i = colmap.get(field)
            return conv(raw[i]) if (i is not None and i < len(raw)) else None
        max_demand = g("max", _num)
        if max_demand is None:
            continue  # skip stray rows (e.g. a generation state-header) with no demand value
        out.append({
            "state_raw": _clean(raw[0]), "state_canonical": canon,
            "max_demand_met_mw": max_demand, "max_demand_time": g("time", _time),
            "shortage_at_max_demand_mw": g("shortage", _num),
            "requirement_at_max_demand_mw": g("req", _num),
            "ace_max": g("ace_max", _num), "ace_min": g("ace_min", _num),
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
    out = []
    seq_by_state = {}
    for rows in tables_3a:
        current = None
        pending = []
        colmap = None
        for ri, raw in enumerate(rows):
            nonempty = [c for c in raw if c not in (None, "")]
            if not nonempty:
                continue
            if any(str(c).lstrip().startswith("3(B)") for c in raw):
                return out
            c0 = _clean(raw[0]) if raw[0] else ""
            key = c0.replace(" ", "").upper()
            if len(nonempty) == 1 and canon_state(c0) is not None:
                current = canon_state(c0)
                pending = []
                continue
            # 3(B) boundary without a marker: once we're inside the state-entity
            # blocks, a lone header that is NOT one of the east states (e.g.
            # "ADANI POWER LTD", "NTPC") marks the start of 3(B) Regional
            # Entities -- stop so those aren't attributed to the last state. Must
            # be a pure text label (no digits once parentheticals are removed);
            # a merged-cell data row like "TotalRES(JHARKHAND)(1*44) 44.15 0" is
            # NOT a boundary.
            if len(nonempty) == 1 and current is not None and canon_state(c0) is None \
                    and not _is_gen_header(raw) \
                    and not re.search(r"\d", re.sub(r"\(.*?\)", "", c0)):
                return out
            if any(cell and ("Inst" in str(cell) or "क्षमता" in str(cell)) for cell in raw):
                sub = rows[ri + 1] if ri + 1 < len(rows) else []
                colmap = _build_colmap(raw, sub)
                continue
            if current is None or colmap is None:
                continue
            if "Station/" in c0 or key.startswith("(MW)") or key == "PEAKMW" \
                    or c0 == "NIL" or "NORECORDS" in key:
                continue
            # drop "TOTAL-" style placeholders (no category, not current state)
            if key.startswith("TOTAL") and _category_from_total(key) is None \
                    and canon_state(re.sub(r"\(.*", "", key[5:])) != current:
                continue
            vals = _extract(raw, colmap)
            if all(v is None for v in vals.values()):
                continue
            seq = seq_by_state.get(current, 0)
            cat_total = _category_from_total(key) if key.startswith("TOTAL") else None
            state_total = key.startswith("TOTAL") and canon_state(re.sub(r"\(.*", "", key[5:])) == current
            if cat_total:
                for st in pending:
                    if st["source_category"] is None:
                        st["source_category"] = cat_total
                pending = []
                out.append({**_base(current, seq, "source_total", cat_total, c0), **vals})
            elif state_total:
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


def _section_of(row):
    """Return the section marker a row starts, else None."""
    if not row or not row[0]:
        return None
    h = _clean(row[0]).replace(" ", "")
    for mk, name in [("1.", "1"), ("2(A)", "2A"), ("2(B)", "2B"),
                     ("2(C)", "2C"), ("3(A)", "3A"), ("3(B)", "3B"),
                     ("4", "4")]:
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
        if "MaximumDemandMet" in joined or ("MaximumDemand" in joined and "Time" in joined):
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
