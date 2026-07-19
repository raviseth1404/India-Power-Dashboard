import sys
sys.path.insert(0, ".")
import pdfplumber
from parser import parse_pdf_text, parse_excel

EXPECTED_REGIONAL = {
    # year: {field: NR_value}
    "2013": dict(demand=35588, shortage=1552, energy=767, hydro=160, wind=5,
                 solar=None, eshort=None, maxdem=None, time=None),
    "2015": dict(demand=39108, shortage=1935, energy=983, hydro=323, wind=16,
                 solar=None, eshort=None, maxdem=None, time=None),
    "2017": dict(demand=41840, shortage=466, energy=927, hydro=342, wind=6,
                 solar=2.16, eshort=7.3, maxdem=45559, time=None),
    "2018": dict(demand=47900, shortage=1146, energy=1085, hydro=285, wind=8,
                 solar=14.22, eshort=8.6, maxdem=50121, time="21:16"),
    "2019": dict(demand=60121, shortage=2260, energy=1482, hydro=338, wind=41,
                 solar=28.35, eshort=18.8, maxdem=65574, time="22:23"),
    "2021": dict(demand=67614, shortage=2995, energy=1590, hydro=347, wind=63,
                 solar=54.40, eshort=30.38, maxdem=72370, time="22:21"),
    "2022": dict(demand=55439, shortage=0, energy=1359, hydro=334, wind=67,
                 solar=100.68, eshort=0.60, maxdem=70841, time="00:00"),
    "2023": dict(demand=65241, shortage=0, energy=1441, hydro=396, wind=29,
                 solar=126.42, eshort=0.11, maxdem=66660, time="22:22"),
    "2026": dict(demand=85556, shortage=285, energy=1919, hydro=433, wind=55,
                 solar=239.10, eshort=2.39, maxdem=88906, time="14:57"),
}

EXPECTED_GEN_OUTAGE = {
    "2013": (3667, 4185, 7852), "2015": (3211, 7905, 11116), "2017": (3626, 11035, 14661),
    "2018": (6089, 5835, 11924), "2019": (5070, 5195, 10265), "2021": (4863, 7580, 12443),
    "2022": (3882, 8180, 12062), "2023": (4689, 8445, 13134), "2026": (1587, 5319, 6906),
}

EXPECTED_SOURCEWISE = {
    # year: (coal, lignite, thermal, hydro, nuclear, gas, res, total, res_share)
    "2018": (None, None, 484, 285, 31, 29, 36, 866, 4.19),
    "2019": (652, 19, None, 338, 27, 32, 85, 1152, 7.41),
    "2021": (688, 29, None, 347, 30, 35, 135, 1265, 10.70),
    "2022": (644, 29, None, 336, 29, 21, 184, 1244, 14.78),
    "2023": (630, 26, None, 396, 29, 15, 161, 1258, 12.83),
    "2026": (870, 24, None, 433, 50, 22, 304, 1703, 17.86),
}

EXPECTED_NON_FOSSIL_SHARE = {"2018": 40.73, "2019": 39.05, "2021": 40.50, "2022": 44.14, "2023": 46.63, "2026": 46.20}

EXPECTED_STATE_COUNT_PUNJAB = {
    "2013": 5262, "2015": 9805, "2017": 7005, "2018": 8640, "2019": 12461,
    "2021": 12842, "2022": 13662, "2023": 14139, "2026": 15344,
}

EXPECTED_SOLAR_NONSOLAR = {
    "2023": {"solar": (190366, "15:01", 0), "non_solar": (193125, "22:18", 0)},
    "2026": {"solar": (256966, "15:49", 0), "non_solar": (251470, "22:36", 2603)},
}

fails = 0


def check(label, got, exp):
    global fails
    # None-tolerant equality with float rounding tolerance
    if exp is None:
        ok = got is None
    elif isinstance(exp, str):
        ok = got == exp
    else:
        ok = got is not None and abs(float(got) - float(exp)) < 0.6
    if not ok:
        fails += 1
        print(f"  FAIL {label}: expected={exp} got={got}")


for yr, exp in EXPECTED_REGIONAL.items():
    with pdfplumber.open(f"/tmp/nldc_samples/{yr}.pdf") as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    r = parse_pdf_text(text)
    nr = r["regional"][0]
    print(f"=== {yr} Table A ===")
    check("demand", nr["demand_met_evening_peak_mw"], exp["demand"])
    check("shortage", nr["peak_shortage_mw"], exp["shortage"])
    check("energy", nr["energy_met_mu"], exp["energy"])
    check("hydro", nr["hydro_gen_mu"], exp["hydro"])
    check("wind", nr["wind_gen_mu"], exp["wind"])
    check("solar", nr["solar_gen_mu"], exp["solar"])
    check("eshort", nr["energy_shortage_mu"], exp["eshort"])
    check("maxdem", nr["max_demand_met_mw"], exp["maxdem"])
    check("time", nr["max_demand_time"], exp["time"])

    go = r["gen_outage"][0]
    c, s, t = EXPECTED_GEN_OUTAGE[yr]
    print(f"=== {yr} Table F ===")
    check("central", go["central_sector_mw"], c)
    check("state", go["state_sector_mw"], s)
    check("total", go["total_mw"], t)

    p = next((x for x in r["state"] if x["state_raw"] == "Punjab"), None)
    print(f"=== {yr} Table C (Punjab) ===")
    check("max_demand", p["max_demand_met_mw"] if p else None, EXPECTED_STATE_COUNT_PUNJAB[yr])

    if yr in EXPECTED_SOURCEWISE:
        sg = r["sourcewise_gen"][0]
        coal, lig, therm, hyd, nuc, gas, res, tot, rshare = EXPECTED_SOURCEWISE[yr]
        print(f"=== {yr} Table G ===")
        check("coal", sg["coal_mu"], coal)
        check("lignite", sg["lignite_mu"], lig)
        check("thermal", sg["thermal_combined_mu"], therm)
        check("hydro", sg["hydro_mu"], hyd)
        check("nuclear", sg["nuclear_mu"], nuc)
        check("gas", sg["gas_mu"], gas)
        check("res", sg["res_mu"], res)
        check("total", sg["total_mu"], tot)
        check("res_share", sg["res_share_pct"], rshare)
        check("non_fossil_share", sg["non_fossil_share_pct"], EXPECTED_NON_FOSSIL_SHARE[yr])

    if yr in EXPECTED_SOLAR_NONSOLAR:
        print(f"=== {yr} Table I ===")
        for ht, (mw, tm, sh) in EXPECTED_SOLAR_NONSOLAR[yr].items():
            row = next(x for x in r["solar_nonsolar"] if x["hour_type"] == ht)
            check(f"{ht}_mw", row["max_demand_met_mw"], mw)
            check(f"{ht}_time", row["max_demand_time"], tm)
            check(f"{ht}_shortage", row["shortage_mw"], sh)

print()
print(f"TOTAL FAILURES: {fails}")
