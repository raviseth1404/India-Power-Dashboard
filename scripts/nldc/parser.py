"""
NLDC daily PSP report parser. Handles both the PDF era (2013-04 -> ~2023-06)
and the Excel era (~2023-04 -> present) and normalizes both into the same
row shape per table (regional / state / generation-outage / sourcewise-gen /
solar-nonsolar-peak).

The PDF report format changed multiple times over 13 years. Rather than
hardcoding date-based "eras", every extraction is content-driven: we locate
each table by keyword (title wording is stable even when the letter prefix
shifts), then locate each field by label regex. Anything not found for a
given report is left as None (NULL), which is correct -- it means that
field genuinely wasn't published yet.
"""
import re
import xlrd

REGIONS = ["NR", "WR", "SR", "ER", "NER", "TOTAL"]

# Canonical state name + region, keyed by every alias observed 2013-2026.
STATE_CANON = {
    "Punjab": ("Punjab", "NR"), "Haryana": ("Haryana", "NR"),
    "Rajasthan": ("Rajasthan", "NR"), "Delhi": ("Delhi", "NR"),
    "UP": ("UP", "NR"), "Uttarakhand": ("Uttarakhand", "NR"),
    "HP": ("HP", "NR"), "Chandigarh": ("Chandigarh", "NR"),
    "J&K": ("J&K / Ladakh", "NR"), "J&K(UT) & Ladakh(UT)": ("J&K / Ladakh", "NR"),
    "Railways_NR ISTS": ("Railways_NR ISTS", "NR"),
    "Bulk Consumer_NR ISTS": ("Bulk Consumer_NR ISTS", "NR"),
    "Chhattisgarh": ("Chhattisgarh", "WR"), "Gujarat": ("Gujarat", "WR"),
    "MP": ("MP", "WR"), "Maharashtra": ("Maharashtra", "WR"), "Goa": ("Goa", "WR"),
    # DD and DNH are reported as SEPARATE rows on the same day in 2013-2020,
    # so they must stay distinct series (they'd collide on one PK otherwise).
    # DNHDDPDCL is the post-2020 administratively-merged UT -- its own series.
    "DD": ("Daman & Diu", "WR"), "DNH": ("Dadra & Nagar Haveli", "WR"),
    "DNHDDPDCL": ("DNHDDPDCL", "WR"),
    "Essar steel": ("AMNSIL (Essar Steel)", "WR"), "AMNSIL": ("AMNSIL (Essar Steel)", "WR"),
    "BALCO": ("BALCO", "WR"), "RIL JAMNAGAR": ("RIL JAMNAGAR", "WR"),
    "Andhra Pradesh": ("Andhra Pradesh", "SR"), "Telangana": ("Telangana", "SR"),
    "Karnataka": ("Karnataka", "SR"), "Kerala": ("Kerala", "SR"),
    "Tamil Nadu": ("Tamil Nadu", "SR"),
    "Pondy": ("Puducherry", "SR"), "Puducherry": ("Puducherry", "SR"),
    "Bihar": ("Bihar", "ER"), "DVC": ("DVC", "ER"), "Jharkhand": ("Jharkhand", "ER"),
    "Odisha": ("Odisha", "ER"), "West Bengal": ("West Bengal", "ER"),
    "Railways_ER ISTS": ("Railways_ER ISTS", "ER"),
    "Sikkim": ("Sikkim", "NER"), "Arunachal Pradesh": ("Arunachal Pradesh", "NER"),
    "Assam": ("Assam", "NER"), "Manipur": ("Manipur", "NER"),
    "Meghalaya": ("Meghalaya", "NER"), "Mizoram": ("Mizoram", "NER"),
    "Nagaland": ("Nagaland", "NER"), "Tripura": ("Tripura", "NER"),
}
# Sort aliases longest-first so e.g. "J&K(UT) & Ladakh(UT)" matches before "J&K".
STATE_ALIASES_SORTED = sorted(STATE_CANON.keys(), key=len, reverse=True)
STATE_LINE_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(a) for a in STATE_ALIASES_SORTED) + r")\s+(.*)$"
)

VALUE_TOKEN = re.compile(r"-?\d+\.?\d*|-{1,}")
TIME_TOKEN = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _num(tok):
    if tok is None:
        return None
    if re.fullmatch(r"-{1,}", tok):
        return None
    try:
        return float(tok)
    except ValueError:
        return None


def excel_time(v):
    """Normalize an Excel time cell to 'HH:MM'. xlrd returns time-formatted
    cells as a number: a fraction of a day (0.709 -> 17:01) or, when the cell
    carries a date too, a full serial (45362.451 -> take the .451 part). Cells
    already stored as text ('14:57') pass straight through."""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        s = v.strip()
        if ":" in s:
            return s
        try:
            v = float(s)
        except ValueError:
            return s or None
    try:
        frac = float(v)
    except (ValueError, TypeError):
        return None
    frac = frac - int(frac)  # keep only the time-of-day portion
    total_min = round(frac * 24 * 60)
    h = (total_min // 60) % 24
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


def _clean_annotations(seg):
    """Strip label annotations that contain stray digits, e.g. "(at 20:00 hrs;
    from RLDCs)" or "(From NLDC SCADA-1 min Instantaneous data)". In some report
    years pdfplumber's reading order interleaves the real data values *inside*
    an apparently-unclosed "(at ... RLDCs)" span (the annotation wraps across
    the same line as the numbers), so the "hrs"/"at"/"from RLDCs" fragments are
    stripped independently rather than as one contiguous "(...)" phrase, before
    falling back to generic paired-parenthesis stripping for the simple cases."""
    seg = re.sub(r"\d{1,2}:?\d{2}\s*hrs\b", " ", seg)
    # Strip the "(" together with "at" and the ")" together with "from RLDCs"
    # -- if left orphaned, those two paren characters re-pair around whatever
    # sits between them (which may be the real data values) and get wiped out
    # by the generic paired-parenthesis pass below.
    seg = re.sub(r"\(?\s*\bat\b", " ", seg)
    seg = re.sub(r"\bfrom\s+RLDCs\b\s*\)?", " ", seg)
    # Same interleaving problem for the "Share of Non-fossil fuel (Hydro,
    # Nuclear and RES) in total generation(%)" label in Table G.
    seg = re.sub(r"\(?\s*Hydro,?\s*Nuclear\s+and\b", " ", seg)
    seg = re.sub(r"\bRES\)", " ", seg)
    seg = re.sub(r"\([^)]*\)", " ", seg)
    return seg


def _values_after(text, label_pattern, n, start=0):
    """Find label_pattern in text (from start), return next n numeric tokens after it."""
    m = re.search(label_pattern, text[start:])
    if not m:
        return None, None
    seg_start = start + m.end()
    seg = text[seg_start:seg_start + 400]
    seg_clean = _clean_annotations(seg)
    toks = VALUE_TOKEN.findall(seg_clean)[:n]
    if len(toks) < n:
        return None, seg_start
    return [_num(t) for t in toks], seg_start


def _times_after(text, pos, n):
    seg = text[pos:pos + 200]
    toks = TIME_TOKEN.findall(seg)[:n]
    if len(toks) < n:
        return [None] * n
    return [f"{h}:{m}" for h, m in toks]


def split_sections(text):
    """Split report text into named sections by keyword, regardless of letter prefix."""
    header_re = re.compile(r"\n([A-I])\.\s*([^\n]{0,90})")
    headers = [(m.start(), m.group(1), m.group(2)) for m in header_re.finditer("\n" + text)]
    sections = {}
    for i, (pos, letter, title) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text) + 1
        body = text[pos:end]
        tl = title.lower()
        if "maximum demand" in tl or "power supply position at all india" in tl:
            sections.setdefault("A", body)
        elif "power supply position in states" in tl:
            sections.setdefault("C", body)
        elif "generation outage" in tl:
            sections.setdefault("F", body)
        elif "sourcewise generation" in tl or "source-wise generation" in tl:
            sections.setdefault("G", body)
        elif "peak demand and shortage at solar" in tl or "peak demand and shortage at solar" in tl:
            sections.setdefault("I", body)
        elif "peak demand" in tl and "solar" in tl:
            sections.setdefault("I", body)
    return sections


def parse_table_a(section_text):
    rows = []
    demand, _ = _values_after(section_text, r"Demand Met during Evening Peak", 6)
    shortage, _ = _values_after(section_text, r"Peak Shortage \(MW\)", 6)
    energy, _ = _values_after(section_text, r"Energy Met \(MU\)", 6)
    hydro, _ = _values_after(section_text, r"Hydro Gen\s*\(MU\)", 6)
    wind, _ = _values_after(section_text, r"Wind Gen\s*\(MU\)", 6)
    solar, _ = _values_after(section_text, r"Solar Gen\s*\(MU\)\*?", 6)
    eshort, _ = _values_after(section_text, r"Energy Shortage \(MU\)", 6)
    maxdem, maxdem_pos = _values_after(
        section_text, r"Maximum Demand Met [Dd]uring [Tt]he [Dd]ay", 6
    )
    times = [None] * 6
    time_m = re.search(r"Time Of Maximum Demand Met", section_text)
    if time_m:
        times = _times_after(section_text, time_m.end(), 6)
    elif maxdem_pos is not None:
        # combined MW+time on one label, e.g. 2018/2019-era format: MW values
        # then a "(MW) & time (from NLDC SCADA)" line then the HH:MM values.
        cand = _times_after(section_text, maxdem_pos, 6)
        if any(cand):
            times = cand

    for i, region in enumerate(REGIONS):
        rows.append({
            "region": region,
            "demand_met_evening_peak_mw": demand[i] if demand else None,
            "peak_shortage_mw": shortage[i] if shortage else None,
            "energy_met_mu": energy[i] if energy else None,
            "hydro_gen_mu": hydro[i] if hydro else None,
            "wind_gen_mu": wind[i] if wind else None,
            "solar_gen_mu": solar[i] if solar else None,
            "energy_shortage_mu": eshort[i] if eshort else None,
            "max_demand_met_mw": maxdem[i] if maxdem else None,
            "max_demand_time": times[i],
        })
    return rows


def parse_table_c(section_text):
    rows = []
    for line in section_text.split("\n"):
        m = STATE_LINE_RE.match(line.strip())
        if not m:
            continue
        state_raw, rest = m.group(1), m.group(2)
        canon, region = STATE_CANON[state_raw]
        toks = VALUE_TOKEN.findall(rest)
        # handle "728/ -1163" fraction Max-OD format: '/' isn't captured by
        # VALUE_TOKEN so it shows as two adjacent tokens already split correctly
        # by the surrounding whitespace/slash; detect via raw substring.
        frac_m = re.search(r"(-?\d+)\s*/\s*(-?\d+)", rest)
        if len(toks) < 6:
            continue
        max_demand = _num(toks[0])
        shortage = _num(toks[1])
        energy = _num(toks[2])
        drawal = _num(toks[3])
        od_ud = _num(toks[4])
        if frac_m:
            max_od_import = float(frac_m.group(1))
            max_od_export = float(frac_m.group(2))
            # the "728/ -1163" fraction produces TWO tokens (indices 5 and 6),
            # so any trailing energy-shortage value is pushed to index 7.
            eshort = _num(toks[7]) if len(toks) > 7 else None
        else:
            max_od_import = _num(toks[5])
            max_od_export = None
            eshort = _num(toks[6]) if len(toks) > 6 else None
        rows.append({
            "region": region,
            "state_raw": state_raw,
            "state_canonical": canon,
            "max_demand_met_mw": max_demand,
            "shortage_during_max_demand_mw": shortage,
            "energy_met_mu": energy,
            "drawal_schedule_mu": drawal,
            "od_ud_mu": od_ud,
            "max_od_import_mw": max_od_import,
            "max_od_export_mw": max_od_export,
            "energy_shortage_mu": eshort,
        })
    return rows


def parse_table_f(section_text):
    rows = []
    central, _ = _values_after(section_text, r"Central Sector", 6)
    state, _ = _values_after(section_text, r"State Sector", 6)
    # Anchored to start-of-line: "Total" also appears as the last word of the
    # "NR WR SR ER NER Total" column-header row, which would otherwise match
    # first and shift the whole extraction onto the Central Sector row.
    total, _ = _values_after(section_text, r"\nTotal\b", 6)
    for i, region in enumerate(REGIONS):
        rows.append({
            "region": region,
            "central_sector_mw": central[i] if central else None,
            "state_sector_mw": state[i] if state else None,
            "total_mw": total[i] if total else None,
            "pct_share": None,
        })
    return rows


def parse_table_g(section_text):
    rows = []
    # "Coal"/"Lignite"/"Total" are anchored to start-of-line since they also
    # appear inside other labels, e.g. "Thermal (Coal & Lignite)" in the
    # 2018-era combined-thermal format, or the column-header "...All India".
    coal, _ = _values_after(section_text, r"\nCoal\b", 6)
    lignite, _ = _values_after(section_text, r"\nLignite\b", 6)
    thermal, _ = _values_after(section_text, r"Thermal \(Coal & Lignite\)", 6)
    hydro, _ = _values_after(section_text, r"Hydro", 6)
    nuclear, _ = _values_after(section_text, r"Nuclear", 6)
    gas, _ = _values_after(section_text, r"Gas, Naptha", 6)
    res, _ = _values_after(section_text, r"RES \(Wind", 6)
    total, _ = _values_after(section_text, r"\nTotal\b", 6)
    res_share, _ = _values_after(section_text, r"Share of RES", 6)
    nonfossil_share, _ = _values_after(section_text, r"Share of Non-fossil", 6)
    regions_g = ["NR", "WR", "SR", "ER", "NER", "ALL_INDIA"]
    for i, region in enumerate(regions_g):
        rows.append({
            "region": region,
            "coal_mu": coal[i] if coal else None,
            "lignite_mu": lignite[i] if lignite else None,
            "thermal_combined_mu": thermal[i] if thermal else None,
            "hydro_mu": hydro[i] if hydro else None,
            "nuclear_mu": nuclear[i] if nuclear else None,
            "gas_mu": gas[i] if gas else None,
            "res_mu": res[i] if res else None,
            "total_mu": total[i] if total else None,
            "res_share_pct": res_share[i] if res_share else None,
            "non_fossil_share_pct": nonfossil_share[i] if nonfossil_share else None,
        })
    return rows


def parse_table_i(section_text):
    rows = []
    for hour_type, label in [("solar", r"Solar hr"), ("non_solar", r"Non-Solar hr")]:
        m = re.search(label, section_text)
        if not m:
            rows.append({"hour_type": hour_type, "max_demand_met_mw": None,
                          "max_demand_time": None, "shortage_mw": None})
            continue
        seg = section_text[m.end():m.end() + 100]
        times = TIME_TOKEN.findall(seg)
        time_val = f"{times[0][0]}:{times[0][1]}" if times else None
        seg_no_time = TIME_TOKEN.sub(" ", seg)  # avoid "15:01" -> stray "15"/"01" tokens
        vals = VALUE_TOKEN.findall(seg_no_time)
        max_demand = _num(vals[0]) if vals else None
        shortage = _num(vals[1]) if len(vals) > 1 else None
        rows.append({"hour_type": hour_type, "max_demand_met_mw": max_demand,
                      "max_demand_time": time_val, "shortage_mw": shortage})
    return rows


def parse_pdf_text(text):
    sections = split_sections(text)
    return {
        "regional": parse_table_a(sections["A"]) if "A" in sections else [],
        "state": parse_table_c(sections["C"]) if "C" in sections else [],
        "gen_outage": parse_table_f(sections["F"]) if "F" in sections else [],
        "sourcewise_gen": parse_table_g(sections["G"]) if "G" in sections else [],
        "solar_nonsolar": parse_table_i(sections["I"]) if "I" in sections else [],
    }


def parse_excel(path=None, file_contents=None):
    # file_contents (raw bytes) is preferred in the concurrent backfill so
    # worker threads never share a temp file on disk (that race produced
    # spurious "0 bytes" errors when one thread truncated another's file).
    if file_contents is not None:
        wb = xlrd.open_workbook(file_contents=file_contents)
    else:
        wb = xlrd.open_workbook(path)
    sheet = wb.sheet_by_name("MOP_E")

    def row(r):
        return [sheet.cell_value(r, c) for c in range(sheet.ncols)]

    def f(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def region_vals(r, cols=range(2, 8)):
        vals = row(r)
        return [f(vals[c]) if vals[c] != "-" and vals[c] != "" else None for c in cols]

    demand = region_vals(5)
    shortage = region_vals(6)
    energy = region_vals(7)
    hydro = region_vals(8)
    wind = region_vals(9)
    solar = region_vals(10)
    eshort = region_vals(11)
    maxdem = region_vals(12)
    time_row = row(13)
    times = [excel_time(time_row[c]) for c in range(2, 8)]

    regional = []
    for i, region in enumerate(REGIONS):
        regional.append({
            "region": region,
            "demand_met_evening_peak_mw": demand[i],
            "peak_shortage_mw": shortage[i],
            "energy_met_mu": energy[i],
            "hydro_gen_mu": hydro[i],
            "wind_gen_mu": wind[i],
            "solar_gen_mu": solar[i],
            "energy_shortage_mu": eshort[i],
            "max_demand_met_mw": maxdem[i],
            "max_demand_time": times[i],
        })

    state = []
    r = 20
    while r < sheet.nrows:
        vals = row(r)
        state_raw = str(vals[1]).strip()
        if state_raw == "" and str(vals[0]).strip() == "":
            r += 1
            if r < sheet.nrows and str(row(r)[0]).strip().startswith("D."):
                break
            continue
        if state_raw not in STATE_CANON:
            r += 1
            continue
        canon, region = STATE_CANON[state_raw]
        max_od_raw = str(vals[7]) if len(vals) > 7 else ""
        frac_m = re.search(r"(-?\d+)\s*/\s*(-?\d+)", max_od_raw)
        max_od_import = float(frac_m.group(1)) if frac_m else f(vals[7])
        max_od_export = float(frac_m.group(2)) if frac_m else None
        state.append({
            "region": region,
            "state_raw": state_raw,
            "state_canonical": canon,
            "max_demand_met_mw": f(vals[2]),
            "shortage_during_max_demand_mw": f(vals[3]),
            "energy_met_mu": f(vals[4]),
            "drawal_schedule_mu": f(vals[5]),
            "od_ud_mu": f(vals[6]),
            "max_od_import_mw": max_od_import,
            "max_od_export_mw": max_od_export,
            "energy_shortage_mu": f(vals[8]) if len(vals) > 8 else None,
        })
        r += 1

    gen_outage = []
    sourcewise_gen = []
    solar_nonsolar = [{"hour_type": "solar", "max_demand_met_mw": None,
                        "max_demand_time": None, "shortage_mw": None},
                       {"hour_type": "non_solar", "max_demand_met_mw": None,
                        "max_demand_time": None, "shortage_mw": None}]
    # "Total" appears twice (end of Table F, end of Table G) so a plain
    # label->row dict would let the second occurrence clobber the first;
    # track context sequentially instead as we scan.
    by_label = {}
    prev_label = None
    for rr in range(sheet.nrows):
        vals = row(rr)
        label0 = str(vals[0]).strip()
        if label0 == "Total" and prev_label == "State Sector":
            by_label["_outage_total"] = vals
        elif label0 == "Total" and prev_label and prev_label.startswith("RES"):
            by_label["_sourcewise_total"] = vals
        elif label0:
            by_label.setdefault(label0, vals)
        for ci, cv in enumerate(vals):
            if str(cv).strip() == "Solar hr":
                solar_nonsolar[0] = {
                    "hour_type": "solar", "max_demand_met_mw": f(vals[ci + 1]),
                    "max_demand_time": excel_time(vals[ci + 3]),
                    "shortage_mw": f(vals[ci + 4]),
                }
            elif str(cv).strip() == "Non-Solar hr":
                solar_nonsolar[1] = {
                    "hour_type": "non_solar", "max_demand_met_mw": f(vals[ci + 1]),
                    "max_demand_time": excel_time(vals[ci + 3]),
                    "shortage_mw": f(vals[ci + 4]),
                }
        if label0:
            prev_label = label0

    def region_row(key, cols=range(2, 8)):
        vals = by_label.get(key)
        if not vals:
            return [None] * len(cols)
        return [f(vals[c]) for c in cols]

    central = region_row("Central Sector")
    state_sec = region_row("State Sector")
    outage_total = region_row("_outage_total")
    for i, region in enumerate(REGIONS):
        gen_outage.append({
            "region": region,
            "central_sector_mw": central[i],
            "state_sector_mw": state_sec[i],
            "total_mw": outage_total[i],
            "pct_share": None,
        })

    coal = region_row("Coal")
    lignite = region_row("Lignite")
    thermal = region_row("Thermal (Coal & Lignite)")
    hydro_g = region_row("Hydro")
    nuclear = region_row("Nuclear")
    gas = region_row("Gas, Naptha & Diesel")
    res = next((region_row(k) for k in by_label if k.startswith("RES (Wind")), [None] * 6)
    sourcewise_total = region_row("_sourcewise_total")
    res_share = next((region_row(k) for k in by_label if k.startswith("Share of RES")), [None] * 6)
    nonfossil_share = next((region_row(k) for k in by_label if k.startswith("Share of Non-fossil")), [None] * 6)
    regions_g = ["NR", "WR", "SR", "ER", "NER", "ALL_INDIA"]
    for i, region in enumerate(regions_g):
        sourcewise_gen.append({
            "region": region,
            "coal_mu": coal[i],
            "lignite_mu": lignite[i],
            "thermal_combined_mu": thermal[i],
            "hydro_mu": hydro_g[i],
            "nuclear_mu": nuclear[i],
            "gas_mu": gas[i],
            "res_mu": res[i],
            "total_mu": sourcewise_total[i],
            "res_share_pct": res_share[i],
            "non_fossil_share_pct": nonfossil_share[i],
        })

    return {
        "regional": regional,
        "state": state,
        "gen_outage": gen_outage,
        "sourcewise_gen": sourcewise_gen,
        "solar_nonsolar": solar_nonsolar,
    }
