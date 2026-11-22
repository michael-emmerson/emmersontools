"""Reads and writes the inventory spreadsheet. The .xlsx file is the app's only database.

Every change is written straight back to the file, keeping the sheet's formulas, formatting and
conditional formatting. A timestamped backup is taken before every save.
"""
import datetime as dt
import io
import os
import re
import shutil
import tempfile
import threading
from copy import copy

import openpyxl
from openpyxl.utils import get_column_letter

from . import formulas

LOCK = threading.RLock()

USAGE_SHEET = "Usage Log"
USAGE_HEADERS = ["Date", "Bean (Varietal)", "SKU", "Lbs Used", "Note"]
SYNC_SHEET = "Shopify Sync Log"
SYNC_HEADERS = ["Date/Time", "Bean", "Change", "From", "To", "Result"]
BACKUPS_KEPT = 50

# Input columns: field name -> column letter
INPUT_COLUMNS = {
    "origin": "A",
    "varietal": "B",
    "provider": "C",
    "arrival_date": "D",
    "cultivar": "E",
    "tasting_notes": "F",
    "cupping_score": "G",
    "roast_level": "H",
    "lbs": "J",
    "cost_per_lb": "K",
    "sku": "P",
    "upc": "Q",
}
NUMBER_FIELDS = {"cupping_score", "lbs", "cost_per_lb"}
TEXT_FIELDS = {"origin", "varietal", "provider", "cultivar", "tasting_notes", "roast_level", "sku", "upc"}
LAST_COLUMN = "S"

# Row 1 of the first sheet, as the master spreadsheet has it. An uploaded file must match, because the app
# writes formulas into I and L-O and reads/writes the input columns by letter.
EXPECTED_HEADERS = {
    "A": "Origin", "B": "Varietal", "C": "Provider", "D": "Arrival Date", "E": "Cultivar", "F": "Tasting Notes",
    "G": "Cupping Score", "H": "Roast Level", "I": "Label Color by Roast Level", "J": "Lbs", "K": "Cost per lb",
    "L": "Diff to Ave", "M": "MSRP", "N": "COGS", "O": "Margin", "P": "SKU", "Q": "UPC",
}
APP_HEADERS = {"R": "Lbs Used", "S": "Lbs Remaining"}  # added by the app; blank in a fresh sheet
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class SheetError(Exception):
    """A problem the person can fix (shown in the app as a message)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --- Formula templates (identical to the ones already in the sheet) -------------------------------

def _num(value):
    return f"{value:g}" if isinstance(value, float) else str(value)


def formula_i(r):
    return (f'=IF(H{r}="Medium Dark","#E3CFF0",IF(H{r}="Light","#EFF0BA",IF(H{r}="Medium Light","#F2E0A1",'
            f'IF(H{r}="Medium","#F0CAE4",IF(H{r}="Dark","#CAE0F0",IF(H{r}="French","#9AA4F2","#"))))))')


def formula_l(r, average):
    return f"=K{r}-{_num(average)}"


def formula_m(r):
    return f"=IF(L{r}>=1.5, 24.99,IF(L{r}>=1, 20.99,IF(L{r}>=0,19.99,IF(L{r}<0,18.99,0))))"


def formula_n(r, adder):
    return f"=K{r}+{_num(adder)}"


def formula_o(r):
    return f"=M{r}-N{r}"


def formula_r(r):
    return f"=IF(B{r}=\"\",\"\",SUMIF('{USAGE_SHEET}'!$B:$B,B{r},'{USAGE_SHEET}'!$D:$D))"


def formula_s(r):
    return f'=IF(OR(B{r}="",J{r}=""),"",J{r}-R{r})'


def _is_formula(value):
    return isinstance(value, str) and value.startswith("=")


# --- Workbook access ---------------------------------------------------------------------------------

class Inventory:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))

    # Loading -----------------------------------------------------------------------------------------
    def _load(self):
        if not os.path.exists(self.path):
            raise SheetError(f"Can't find the spreadsheet at {self.path}. Set its location in Settings.", 404)
        try:
            return openpyxl.load_workbook(self.path)
        except Exception as exc:  # noqa: BLE001 - surfaced to the person
            raise SheetError(f"Couldn't open the spreadsheet: {exc}") from exc

    @staticmethod
    def _beans_sheet(wb):
        return wb.worksheets[0]

    @staticmethod
    def _settings_from(ws):
        average, adder = formulas.DEFAULT_AVERAGE_COST, formulas.DEFAULT_COGS_ADDER
        for r in range(2, ws.max_row + 1):
            l, n = ws[f"L{r}"].value, ws[f"N{r}"].value
            ml = re.fullmatch(r"=K\d+\s*-\s*([0-9.]+)", str(l or "").strip())
            mn = re.fullmatch(r"=K\d+\s*\+\s*([0-9.]+)", str(n or "").strip())
            if ml and mn:
                return float(ml.group(1)), float(mn.group(1))
        return average, adder

    @staticmethod
    def _row_has_bean(ws, r):
        return any(ws[f"{col}{r}"].value not in (None, "") for f, col in INPUT_COLUMNS.items() if f != "roast_level")

    def _bean_rows(self, ws):
        return [r for r in range(2, ws.max_row + 1) if self._row_has_bean(ws, r)]

    @staticmethod
    def _usage_rows(wb):
        if USAGE_SHEET not in wb.sheetnames:
            return []
        ws = wb[USAGE_SHEET]
        rows = []
        for r in range(2, ws.max_row + 1):
            date, bean, sku, lbs, note = (ws.cell(r, c).value for c in range(1, 6))
            if bean in (None, "") and lbs in (None, ""):
                continue
            rows.append({
                "id": r,
                "date": _date_str(date),
                "varietal": bean or "",
                "sku": sku or "",
                "lbs": _to_float(lbs) or 0.0,
                "note": note or "",
            })
        return rows

    def _bean_from_row(self, ws, r, average, adder, usage):
        bean = {"row": r}
        for field, col in INPUT_COLUMNS.items():
            value = ws[f"{col}{r}"].value
            if field == "arrival_date":
                value = _date_str(value)
            elif field in NUMBER_FIELDS:
                value = _to_float(value)
            elif value is not None:
                value = str(value)
            bean[field] = value if value is not None else ("" if field in TEXT_FIELDS else None)
        m = ws[f"M{r}"].value
        override = None if (m is None or _is_formula(m)) else _to_float(m)
        calc = formulas.pricing(bean["cost_per_lb"], override, average, adder)
        used = sum(u["lbs"] for u in usage if u["varietal"] == bean["varietal"])
        bean.update({
            "label_color": formulas.label_color_hex(bean["roast_level"]),
            "diff_to_average": calc["diff"],
            "msrp": calc["msrp"],
            "msrp_manual": override is not None,
            "calculated_msrp": formulas.calculated_msrp(calc["diff"]),
            "cogs": calc["cogs"],
            "margin": calc["margin"],
            "lbs_used": used,
            "lbs_remaining": (bean["lbs"] - used) if bean["lbs"] is not None else None,
        })
        return bean

    def snapshot(self):
        """Everything the app shows: beans, usage entries and the sheet's settings."""
        with LOCK:
            wb = self._load()
            ws = self._beans_sheet(wb)
            average, adder = self._settings_from(ws)
            usage = self._usage_rows(wb)
            beans = [self._bean_from_row(ws, r, average, adder, usage) for r in self._bean_rows(ws)]
            return {
                "path": self.path,
                "modified": dt.datetime.fromtimestamp(os.path.getmtime(self.path)).isoformat(timespec="seconds"),
                "settings": {"average_cost": average, "cogs_adder": adder},
                "beans": beans,
                "usage": sorted(usage, key=lambda u: (u["date"] or "", u["id"]), reverse=True),
            }

    # Saving ------------------------------------------------------------------------------------------
    def _backup(self):
        folder = os.path.join(os.path.dirname(self.path), "backups")
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(self.path))
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = os.path.join(folder, f"{stem} {stamp}{ext}")
        shutil.copy2(self.path, backup)
        backups = sorted(f for f in os.listdir(folder) if f.startswith(stem + " ") and f.endswith(ext))
        for old in backups[:-BACKUPS_KEPT]:
            os.remove(os.path.join(folder, old))
        return backup

    def _save(self, wb):
        wb.calculation.fullCalcOnLoad = True  # Excel/Numbers recalculate every formula on open
        self._backup()
        folder = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, self.path)
        except PermissionError as exc:
            raise SheetError("The spreadsheet is locked, probably because it's open in Excel. "
                             "Close it there and try again.", 423) from exc
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _prepare(self, wb):
        """Adds the Lbs Used / Lbs Remaining columns and the Usage Log sheet the first time they're needed."""
        ws = self._beans_sheet(wb)
        if ws["R1"].value != "Lbs Used":
            for col, title in (("R", "Lbs Used"), ("S", "Lbs Remaining")):
                ws[f"{col}1"].value = title
                ws[f"{col}1"]._style = copy(ws["Q1"]._style)
                ws.column_dimensions[col].width = 14
            for r in range(2, ws.max_row + 1):
                if _is_formula(ws[f"I{r}"].value):
                    self._write_usage_formulas(ws, r)
        if USAGE_SHEET not in wb.sheetnames:
            us = wb.create_sheet(USAGE_SHEET)
            us.append(USAGE_HEADERS)
            for c in range(1, len(USAGE_HEADERS) + 1):
                us.cell(1, c)._style = copy(ws["A1"]._style)
            for col, width in zip("ABCDE", (12, 36, 18, 10, 40)):
                us.column_dimensions[col].width = width
            us.freeze_panes = "A2"
        return ws

    @staticmethod
    def _write_usage_formulas(ws, r):
        ws[f"R{r}"].value = formula_r(r)
        ws[f"S{r}"].value = formula_s(r)
        for col in "RS":
            ws[f"{col}{r}"].number_format = "0.##"

    def _write_row_formulas(self, ws, r, average, adder, msrp_override=None):
        ws[f"I{r}"].value = formula_i(r)
        ws[f"L{r}"].value = formula_l(r, average)
        ws[f"M{r}"].value = msrp_override if msrp_override is not None else formula_m(r)
        ws[f"N{r}"].value = formula_n(r, adder)
        ws[f"O{r}"].value = formula_o(r)
        self._write_usage_formulas(ws, r)

    @staticmethod
    def _copy_style_without_fill(ws, src_row, dst_row):
        for c in range(1, openpyxl.utils.column_index_from_string(LAST_COLUMN) + 1):
            src, dst = ws.cell(src_row, c), ws.cell(dst_row, c)
            dst.number_format = src.number_format
            dst.font = copy(src.font)
            dst.border = copy(src.border)
            dst.alignment = copy(src.alignment)

    def _validate(self, ws, data, row=None):
        varietal = (data.get("varietal") or "").strip()
        if not varietal:
            raise SheetError("Varietal is required; it's the bean's name in the app and the usage log.")
        for r in self._bean_rows(ws):
            if r != row and str(ws[f"B{r}"].value or "").strip().lower() == varietal.lower():
                raise SheetError(f"There's already a bean called “{varietal}” (row {r}). Use a different varietal name.")
        if data.get("roast_level") and data["roast_level"] not in formulas.ROAST_LEVELS:
            raise SheetError(f"Roast level must be one of: {', '.join(formulas.ROAST_LEVELS)}.")

    def _write_inputs(self, ws, r, data):
        for field, col in INPUT_COLUMNS.items():
            if field not in data:
                continue
            value = data[field]
            if field == "arrival_date":
                value = _parse_date(value)
            elif field in NUMBER_FIELDS:
                value = _parse_number(value, field)
            elif value is not None:
                value = str(value).strip() or None
            ws[f"{col}{r}"].value = value
        if "msrp_manual" in data:
            if data.get("msrp_manual"):
                ws[f"M{r}"].value = _parse_number(data.get("msrp"), "msrp")
                if ws[f"M{r}"].value is None:
                    raise SheetError("Enter the MSRP, or turn off “Set MSRP manually”.")
            else:
                ws[f"M{r}"].value = formula_m(r)

    # Beans ---------------------------------------------------------------------------------------------
    def _check_row(self, ws, row, expect_varietal):
        if row not in self._bean_rows(ws) or str(ws[f"B{row}"].value or "") != (expect_varietal or ""):
            raise SheetError("The spreadsheet changed since this page loaded. Reload and try again.", 409)

    def add_bean(self, data):
        with LOCK:
            wb = self._load()
            ws = self._prepare(wb)
            average, adder = self._settings_from(ws)
            self._validate(ws, data)
            rows = self._bean_rows(ws)
            r = (rows[-1] + 1) if rows else 2
            if not _is_formula(ws[f"I{r}"].value):
                self._copy_style_without_fill(ws, max(r - 1, 2), r)
            self._write_row_formulas(ws, r, average, adder)
            self._write_inputs(ws, r, data)
            self._save(wb)
            return r

    def update_bean(self, row, expect_varietal, data):
        with LOCK:
            wb = self._load()
            ws = self._prepare(wb)
            self._check_row(ws, row, expect_varietal)
            self._validate(ws, {**{"varietal": ws[f"B{row}"].value}, **data}, row)
            old_name = ws[f"B{row}"].value
            self._write_inputs(ws, row, data)
            new_name = ws[f"B{row}"].value
            if new_name != old_name and USAGE_SHEET in wb.sheetnames:  # keep usage attached to the bean
                us = wb[USAGE_SHEET]
                for r in range(2, us.max_row + 1):
                    if us.cell(r, 2).value == old_name:
                        us.cell(r, 2).value = new_name
            if "sku" in data and USAGE_SHEET in wb.sheetnames:
                us = wb[USAGE_SHEET]
                for r in range(2, us.max_row + 1):
                    if us.cell(r, 2).value == new_name:
                        us.cell(r, 3).value = ws[f"P{row}"].value
            self._save(wb)
            return old_name, new_name

    def delete_bean(self, row, expect_varietal):
        with LOCK:
            wb = self._load()
            ws = self._prepare(wb)
            self._check_row(ws, row, expect_varietal)
            average, adder = self._settings_from(ws)
            name = ws[f"B{row}"].value
            ws.delete_rows(row)
            self._renumber_formulas(ws, average, adder)
            if USAGE_SHEET in wb.sheetnames:
                us = wb[USAGE_SHEET]
                for r in range(us.max_row, 1, -1):
                    if us.cell(r, 2).value == name:
                        us.delete_rows(r)
            self._save(wb)
            return name

    def _renumber_formulas(self, ws, average, adder):
        """Rewrites every row's formulas so they point at their own row again (after a row is removed)."""
        for r in range(2, ws.max_row + 1):
            if _is_formula(ws[f"I{r}"].value) or _is_formula(ws[f"L{r}"].value):
                m = ws[f"M{r}"].value
                self._write_row_formulas(ws, r, average, adder, None if (m is None or _is_formula(m)) else m)

    def update_settings(self, average, adder):
        with LOCK:
            wb = self._load()
            ws = self._prepare(wb)
            for r in range(2, ws.max_row + 1):
                if _is_formula(ws[f"L{r}"].value):
                    ws[f"L{r}"].value = formula_l(r, average)
                if _is_formula(ws[f"N{r}"].value):
                    ws[f"N{r}"].value = formula_n(r, adder)
            self._save(wb)

    # Usage ---------------------------------------------------------------------------------------------
    def log_usage(self, row, expect_varietal, lbs, date, note):
        lbs = _parse_number(lbs, "lbs used")
        if not lbs or lbs <= 0:
            raise SheetError("Enter how many pounds you used (more than 0).")
        with LOCK:
            wb = self._load()
            ws = self._prepare(wb)
            self._check_row(ws, row, expect_varietal)
            us = wb[USAGE_SHEET]
            r = us.max_row + 1
            values = [_parse_date(date) or dt.datetime.combine(dt.date.today(), dt.time()),
                      ws[f"B{row}"].value, ws[f"P{row}"].value, lbs, (note or "").strip() or None]
            for c, v in enumerate(values, 1):
                us.cell(r, c).value = v
            us.cell(r, 1).number_format = "mm-dd-yy"
            us.cell(r, 4).number_format = "0.##"
            self._save(wb)

    def delete_usage(self, entry_row, expect_varietal):
        with LOCK:
            wb = self._load()
            if USAGE_SHEET not in wb.sheetnames:
                raise SheetError("There's no usage log in the spreadsheet yet.", 404)
            us = wb[USAGE_SHEET]
            if entry_row < 2 or entry_row > us.max_row or us.cell(entry_row, 2).value != expect_varietal:
                raise SheetError("The usage log changed since this page loaded. Reload and try again.", 409)
            us.delete_rows(entry_row)
            self._save(wb)

    # Shopify log ---------------------------------------------------------------------------------------
    def log_sync(self, entries):
        """entries: list of (bean, change, from, to, result)."""
        with LOCK:
            wb = self._load()
            self._prepare(wb)
            if SYNC_SHEET not in wb.sheetnames:
                ss = wb.create_sheet(SYNC_SHEET)
                ss.append(SYNC_HEADERS)
                for c in range(1, len(SYNC_HEADERS) + 1):
                    ss.cell(1, c)._style = copy(self._beans_sheet(wb)["A1"]._style)
                for col, width in zip("ABCDEF", (18, 36, 22, 16, 16, 40)):
                    ss.column_dimensions[col].width = width
                ss.freeze_panes = "A2"
            ss = wb[SYNC_SHEET]
            now = dt.datetime.now().replace(microsecond=0)
            for entry in entries:
                r = ss.max_row + 1
                for c, v in enumerate((now, *entry), 1):
                    ss.cell(r, c).value = v
                ss.cell(r, 1).number_format = "yyyy-mm-dd hh:mm"
            self._save(wb)


# --- Uploading a spreadsheet -----------------------------------------------------------------------

def _header(value):
    return " ".join(str(value or "").split()).lower()


def check_upload(data, filename=""):
    """Checks an uploaded .xlsx has the inventory layout the app expects. Returns a short summary, or raises
    SheetError explaining what to fix."""
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext in (".xls", ".numbers", ".csv", ".ods"):
        raise SheetError(f"“{name}” isn't an .xlsx file. Open it in Excel or Numbers and export or save it "
                         "as an Excel workbook (.xlsx), then upload that.")
    if ext and ext not in (".xlsx", ".xlsm"):
        raise SheetError(f"“{name}” isn't an Excel workbook. Upload the inventory spreadsheet as an .xlsx file.")
    if not data:
        raise SheetError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SheetError("That file is too big to be the inventory spreadsheet (over 20 MB).", 413)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - surfaced to the person
        raise SheetError("Couldn't read that file as an Excel workbook. Make sure it's the .xlsx inventory "
                         f"spreadsheet ({exc}).") from exc

    def mismatches(ws):
        return [f"{col}1 should be “{want}” but is “{ws[f'{col}1'].value or ''}”"
                for col, want in EXPECTED_HEADERS.items() if _header(ws[f"{col}1"].value) != _header(want)]

    ws = wb.worksheets[0]
    wrong = mismatches(ws)
    if wrong:
        elsewhere = next((other.title for other in wb.worksheets[1:] if not mismatches(other)), None)
        if elsewhere:
            raise SheetError(f"The inventory is on the “{elsewhere}” sheet, but the app reads the first sheet. "
                             f"Move “{elsewhere}” to be the first sheet and upload it again.")
        shown = "; ".join(wrong[:4]) + (f"; and {len(wrong) - 4} more" if len(wrong) > 4 else "")
        raise SheetError("This doesn't look like the RRR Coffee inventory spreadsheet. The first sheet's header row "
                         f"needs the columns Origin through UPC in A to Q, in the same order. {shown}.")
    for col, want in APP_HEADERS.items():
        have = ws[f"{col}1"].value
        if have not in (None, "") and _header(have) != _header(want):
            raise SheetError(f"Column {col} has “{have}” in it. The app uses columns R and S for Lbs Used and "
                             "Lbs Remaining, so move that column further right and upload it again.")
    if USAGE_SHEET in wb.sheetnames:
        us = wb[USAGE_SHEET]
        if [_header(us.cell(1, c).value) for c in (2, 4)] != [_header(USAGE_HEADERS[1]), _header(USAGE_HEADERS[3])]:
            raise SheetError(f"The “{USAGE_SHEET}” sheet doesn't have the layout the app writes "
                             f"({', '.join(USAGE_HEADERS)}). Rename or remove that sheet and upload it again.")

    rows = [r for r in range(2, ws.max_row + 1) if Inventory._row_has_bean(ws, r)]
    seen, dupes, unnamed = {}, [], []
    for r in rows:
        varietal = str(ws[f"B{r}"].value or "").strip()
        if not varietal:
            unnamed.append(r)
            continue
        key = varietal.lower()
        if key in seen:
            dupes.append(f"“{varietal}” (rows {seen[key]} and {r})")
        seen.setdefault(key, r)
    if dupes:
        raise SheetError("Each bean needs its own Varietal, because the usage log matches on it. Found the same "
                         f"Varietal more than once: {', '.join(dupes)}. Rename them and upload it again.")
    warnings = []
    if unnamed:
        warnings.append(f"{len(unnamed)} row(s) have no Varietal (row {', '.join(map(str, unnamed[:5]))}"
                        f"{'…' if len(unnamed) > 5 else ''}). They show in the app but can't log usage until named.")
    if not rows:
        warnings.append("The spreadsheet has no beans yet. Add them in the app.")
    return {"sheet": ws.title, "beans": len(rows), "usage_entries": len(Inventory._usage_rows(wb)),
            "warnings": warnings}


def install_upload(path, data, filename=""):
    """Validates an uploaded workbook and makes it the spreadsheet at `path`. Any file already there is
    copied to the backups folder first. The upload is written byte for byte, so nothing in it changes."""
    summary = check_upload(data, filename)
    path = os.path.abspath(os.path.expanduser(path))
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        raise SheetError(f"The folder {folder} doesn't exist.", 404)
    with LOCK:
        backup = Inventory(path)._backup() if os.path.exists(path) else None
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except PermissionError as exc:
            raise SheetError("The spreadsheet is locked, probably because it's open in Excel. "
                             "Close it there and try again.", 423) from exc
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return {**summary, "path": path, "backup": backup}


# --- Value helpers -----------------------------------------------------------------------------------

def _to_float(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("lb", "").strip())
    except ValueError:
        return None


def _parse_number(value, label):
    if value in (None, ""):
        return None
    number = _to_float(value)
    if number is None:
        raise SheetError(f"“{value}” isn't a number ({label}).")
    return int(number) if number.is_integer() else number


def _date_str(value):
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value) if value not in (None, "") else ""


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.combine(dt.date.fromisoformat(str(value)[:10]), dt.time())
    except ValueError as exc:
        raise SheetError(f"“{value}” isn't a date.") from exc


__all__ = ["Inventory", "SheetError", "check_upload", "install_upload", "get_column_letter"]
