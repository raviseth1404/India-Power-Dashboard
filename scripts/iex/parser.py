"""Parse an IEX market-snapshot SSR page (day-ahead or real-time) into 96
fifteen-minute block rows for one day.

The page is a React Server Component render: a single MUI <table> whose first
<tbody> holds exactly 96 rows (one per block). Date / Hour / (RTM) Session ID
cells use rowSpan, so they only appear on the row where they change. The five
numeric values plus the time-block label are ALWAYS the last six <td> cells of
each row, which makes extraction robust regardless of how many leading cells
were collapsed by rowSpan.

DAM columns:  Date, Hour, Time Block, Purchase Bid, Sell Bid, MCV, FSV, MCP
RTM columns:  Date, Hour, Session ID, Time Block, Purchase Bid, Sell Bid, MCV,
              FSV, MCP
"""
import re

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _num(s):
    s = _TAG.sub("", s).strip().replace(",", "")
    if s in ("", "-", "NA", "N/A", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s):
    s = _TAG.sub("", s).strip()
    try:
        return int(s)
    except ValueError:
        return None


def parse_day(html, is_rtm):
    """Return a list of up to 96 block dicts, or [] if the page has no table
    (no trading that day / 404 body)."""
    i = html.find("MuiTableBody-root")
    if i < 0:
        return []
    body = html[i:]
    end = body.find("</tbody>")
    if end > 0:
        body = body[:end]
    out = []
    session = None
    for ri, rowhtml in enumerate(_TR.findall(body)):
        cells = _TD.findall(rowhtml)
        if len(cells) < 6:
            continue
        # last six cells: time_block, purchase, sell, mcv, fsv, mcp
        tail = cells[-6:]
        lead = cells[:-6]  # any of date / hour / session that appear this row
        time_block = _TAG.sub("", tail[0]).strip()
        rec = {
            "block": ri + 1,
            "hour": (ri // 4) + 1,
            "time_block": time_block,
            "purchase_bid_mw": _num(tail[1]),
            "sell_bid_mw": _num(tail[2]),
            "mcv_mw": _num(tail[3]),
            "final_scheduled_volume_mw": _num(tail[4]),
            "mcp_rs_mwh": _num(tail[5]),
        }
        if is_rtm:
            # Session ID, when present this row, is the last lead cell that is a
            # bare small integer (date has dashes, hour <=24). Forward-fill it.
            for c in lead:
                v = _int(c)
                if v is not None and "-" not in _TAG.sub("", c):
                    session = v
            rec["session_id"] = session
        out.append(rec)
    return out
