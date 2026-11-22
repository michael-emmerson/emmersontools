"""The spreadsheet's formulas (columns I, L, M, N, O), ported from the iPhone app's Formulas.swift."""

DEFAULT_AVERAGE_COST = 8.5
DEFAULT_COGS_ADDER = 2.0

ROAST_LEVELS = ["Light", "Medium Light", "Medium", "Medium Dark", "Dark", "French"]

# Column I: =IF(H2="Medium Dark","#E3CFF0",IF(H2="Light","#EFF0BA",...,"#"))
_LABEL_COLORS = {
    "medium dark": "#E3CFF0",
    "light": "#EFF0BA",
    "medium light": "#F2E0A1",
    "medium": "#F0CAE4",
    "dark": "#CAE0F0",
    "french": "#9AA4F2",
}


def label_color_hex(roast_level):
    return _LABEL_COLORS.get((roast_level or "").strip().lower(), "#")


def diff_to_average(cost, average=DEFAULT_AVERAGE_COST):
    """Column L: =K2-8.5"""
    return (cost or 0) - average


def calculated_msrp(diff):
    """Column M: =IF(L2>=1.5, 24.99,IF(L2>=1, 20.99,IF(L2>=0,19.99,IF(L2<0,18.99,0))))

    The diff is rounded to 6 decimals first so floating-point noise (9.95 - 8.45 = 1.4999999999999991)
    lands on the tier Excel shows.
    """
    scaled = diff * 1_000_000
    rounded = float(int(abs(scaled) + 0.5)) * (1 if scaled >= 0 else -1)
    d = rounded / 1_000_000
    if d >= 1.5:
        return 24.99
    if d >= 1:
        return 20.99
    if d >= 0:
        return 19.99
    return 18.99


def pricing(cost, msrp_override=None, average=DEFAULT_AVERAGE_COST, adder=DEFAULT_COGS_ADDER):
    """Columns L, M, N, O for one row. msrp_override is a typed-in MSRP (like the Emerald Tolima row)."""
    cost = cost or 0
    diff = diff_to_average(cost, average)
    msrp = msrp_override if msrp_override is not None else calculated_msrp(diff)
    cogs = cost + adder  # Column N: =K2+2
    return {"diff": diff, "msrp": msrp, "cogs": cogs, "margin": msrp - cogs}  # Column O: =M2-N2
