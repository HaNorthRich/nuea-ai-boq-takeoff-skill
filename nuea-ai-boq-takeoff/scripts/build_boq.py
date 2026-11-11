#!/usr/bin/env python3
"""Build a BOQ workbook (live formulas, summary, chart) from a quantity file.

    python3 build_boq.py <qty.json> --out BOQ.xlsx        # opens in GridCraft, Excel, LibreOffice, Google Sheets
    python3 build_boq.py <qty.json> --jsonl boq.jsonl     # then: gridcraft-cli run --script boq.jsonl --out BOQ.xlsx
    python3 build_boq.py <qty.json> --out T.xlsx --blank  # same rows, no quantities or prices (an empty template)
    python3 build_boq.py --selftest

Prints the totals computed in Python; the workbook's สรุปราคา sheet must show the same numbers.
The quantity file format is in references/qty-format.md. --out needs openpyxl (python3 -m pip install "openpyxl>=3.1,<4").
"""
import json
import math
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

NAVY, GREEN, SAND, MINT, INK, GREY, AMBER, MUTED = "#1F3A5F", "#2E5E4E", "#F2E6CF", "#E3F0E8", "#3B2F1E", "#D0D5DB", "#B45309", "#5B6470"
MONEY = "#,##0.00"
# Thai text needs a Thai font or it sits off the digit baseline in GridCraft
FONT = {"darwin": "Sukhumvit Set", "win32": "Leelawadee UI"}.get(sys.platform, "Noto Sans Thai")
CUSTOMER = "ลูกค้า"   # a row with this in the supplier column is priced but left out of the total
RATES = {"overhead": 0.05, "profit": 0.10, "vat": 0.07}   # sample defaults: set your own in the file's "rates"
RESERVED = ("สรุปราคา", "ข้อมูลโครงการ")
FORMULA = re.compile("[\\s\u200b-\u200f\u2060\ufeff\u00ad\\x00-\\x1f]*(=|\\+|@|-(?=\\S))")   # what a spreadsheet may read as a formula
HEAD = ["ลำดับ", "รายการ", "ปริมาณ", "หน่วย", "ราคาวัสดุ\n(บาท/หน่วย)", "รวมค่าวัสดุ\n(บาท)", "ค่าแรง\n(บาท/หน่วย)",
        "รวมค่าแรง\n(บาท)", "รวมทั้งสิ้น\n(บาท)", "หมายเหตุ · ที่มาของตัวเลข", "ผู้จัดหา"]


def r2(x):
    """Half-up to 2 places, like a spreadsheet's ROUND (Python's round() is banker's on the binary value)."""
    return float(Decimal(str(round(x, 9))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def fmt(q):
    return "#,##0" if float(q).is_integer() else "#,##0.00"


def rows_of(d):
    for sheet in d["sheets"]:
        for sec in sheet["sections"]:
            for row in sec["rows"]:
                yield sheet, sec, (list(row) + [None] * 7)[:7]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _texts(x):
    """Every string anywhere in the file, so none can reach a cell as a formula. A NaN or Infinity is refused here too."""
    if isinstance(x, float) and not math.isfinite(x):
        raise ValueError("ตัวเลขในไฟล์ต้องไม่เป็น NaN หรือ Infinity")
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _texts(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _texts(v)


def check(d):
    """Refuse a quantity file that would build a wrong workbook. Returns the number of item rows."""
    bad = lambda msg: ValueError(msg)
    if not isinstance(d, dict) or not isinstance(d.get("sheets"), list) or len(d["sheets"]) != 4:
        raise bad("ต้องมี 4 หมวดงานใน sheets (สรุปราคามี 4 แถวหมวด)")
    for t in _texts(d):
        if FORMULA.match(t):
            raise bad(f"ข้อความต้องไม่ขึ้นต้นด้วย = + @ หรือ - ตามด้วยตัวอักษร (ชีตจะอ่านเป็นสูตร): {t[:50]!r}")
    if not isinstance(d.get("project") or {}, dict):
        raise bad("project ต้องเป็น object")
    if any(not isinstance(v, (str, int, float)) for v in (d.get("project") or {}).values()):
        raise bad("ค่าใน project ต้องเป็นข้อความหรือตัวเลข")
    if not _num(d.get("floor_area")) or d["floor_area"] <= 0:
        raise bad("floor_area ต้องเป็นตัวเลขมากกว่า 0 (ใช้คิดราคาต่อ ตร.ม.)")
    rates = d.get("rates") or {}
    if not isinstance(rates, dict) or set(rates) - set(RATES) or any(not _num(v) or not 0 <= v < 1 for v in rates.values()):
        raise bad("rates ใส่ได้แค่ overhead profit vat เป็นเศษส่วน 0 ถึงน้อยกว่า 1 (7% = 0.07)")
    if any(not isinstance(t, str) for t in d.get("notes") or []):
        raise bad("notes ต้องเป็นรายการข้อความ")
    for key, width, nums in (("facts", 2, ()), ("key", 4, ()), ("pieces", 6, (2, 4))):
        for row in d.get(key) or []:
            if not isinstance(row, list) or len(row) != width or any(not _num(row[i]) for i in nums) \
                    or any(isinstance(v, (dict, list)) or v is None for v in row):
                raise bad(f"{key}: แต่ละแถวต้องมี {width} ช่อง" + (" ช่องที่ 3 และ 5 เป็นตัวเลข" if nums else "") + f": {row!r}")
    names = []
    for sheet in d["sheets"]:
        if not isinstance(sheet, dict) or not all(isinstance(sheet.get(k), str) and sheet[k].strip() for k in ("name", "title")):
            raise bad("แต่ละหมวดใน sheets ต้องมี name และ title เป็นข้อความ")
        names.append(sheet["name"])
        secs = sheet.get("sections")
        if not isinstance(secs, list) or not secs:
            raise bad(f'หมวดว่าง: {sheet["name"]}')
        for sec in secs:
            if not isinstance(sec, dict) or not all(isinstance(sec.get(k), str) and sec[k].strip() for k in ("code", "name")):
                raise bad(f'หมวดย่อยใน {sheet["name"]} ต้องมี code และ name เป็นข้อความ')
            if not isinstance(sec.get("rows"), list) or not sec["rows"]:
                raise bad(f'หมวดย่อยว่าง: {sec["code"]} {sec["name"]}')   # an empty section makes its SUM wrap
            if any(not isinstance(r, list) or not 3 <= len(r) <= 7 for r in sec["rows"]):
                raise bad(f'แถวใน {sec["code"]} ต้องเป็น [รายการ, ปริมาณ, หน่วย, ราคาวัสดุ, ค่าแรง, หมายเหตุ, ผู้จัดหา]')
    low = [n.strip().casefold() for n in names]
    if len(set(low)) != 4 or any(len(n) > 31 or set(n) & set("[]:*?/\\'") or n.strip() in RESERVED for n in names):
        raise bad(f"ชื่อชีตซ้ำ ยาวเกิน 31 ตัว ใช้ชื่อ {RESERVED} หรือมีอักขระที่ชีตไม่รับ: {names}")
    n = 0
    for sheet, sec, (lab, q, u, e, g, note, sup) in rows_of(d):
        where = f'{sec["code"]} {lab}'
        if not isinstance(lab, str) or not lab.strip():
            raise bad(f"ชื่อรายการต้องเป็นข้อความ: {where}")
        for name, v in (("ปริมาณ", q), ("ราคาวัสดุ", e), ("ค่าแรง", g)):
            if v is not None and (not _num(v) or v < 0):
                raise bad(f"{name} ต้องเป็นตัวเลขไม่ติดลบ หรือ null: {where}")
        if q is not None and q <= 0:
            raise bad(f"ปริมาณต้องมากกว่า 0 (ไม่มีในบ้านหลังนี้ให้ลบแถวออก): {where}")
        if any(t is not None and not isinstance(t, str) for t in (u, note)):
            raise bad(f"หน่วยและหมายเหตุต้องเป็นข้อความหรือ null: {where}")
        if sup not in (None, "", CUSTOMER):
            raise bad(f'ช่องผู้จัดหาใส่ได้แค่ "{CUSTOMER}" หรือเว้นว่าง: {where}')
        if lab.startswith("คอนกรีต") and u == "ลบ.ม." and q is not None and abs(q * 2 - round(q * 2)) > 1e-9:
            raise bad(f"คอนกรีตผสมเสร็จสั่งเป็นขั้น 0.5 ลบ.ม.: {where} = {q}")
        n += 1
    return n


def unpriced(d):
    return [lab for _, _, (lab, q, u, e, g, note, sup) in rows_of(d) if q is not None and e is None and g is None]


def expected(d):
    """The totals a correct workbook must show, computed here without the spreadsheet."""
    rates = {**RATES, **(d.get("rates") or {})}
    out, sub = {"sheets": []}, 0.0
    for sheet in d["sheets"]:
        mat = lab_ = cust = 0.0
        for sec in sheet["sections"]:
            for row in sec["rows"]:
                lab, q, u, e, g, note, sup = (list(row) + [None] * 7)[:7]
                if q is None:
                    continue
                if sup == CUSTOMER:
                    cust += q * (e or 0)
                elif e is not None:
                    mat += r2(q * e)
                if g is not None:
                    lab_ += r2(q * g)
        out["sheets"].append({"name": sheet["name"], "material": r2(mat), "labour": r2(lab_), "customer": r2(cust)})
        sub += r2(mat) + r2(lab_)
    over, profit = r2(sub * rates["overhead"]), r2(sub * rates["profit"])
    pre = r2(sub + over + profit)
    vat = r2(pre * rates["vat"])
    out.update(subtotal=r2(sub), overhead=over, profit=profit, vat=vat, total=r2(pre + vat),
               per_m2=int(Decimal(str((pre + vat) / d["floor_area"])).quantize(Decimal("1"), ROUND_HALF_UP)))
    return out


def ops(d, blank=False):
    """The workbook as a list of (command, params): GridCraft's command names, replayed by write_xlsx too."""
    p = d.get("project") or {}
    title = p.get("title", "ใบประมาณราคา (BOQ)")
    rates = {**RATES, **(d.get("rates") or {})}
    out = []

    def cmd(command, **params):
        out.append((command, params))

    def put(rng, values):
        cmd("range.setValues", range=rng, values=values, parse=False)   # keep "1.10" as text, not 1.1

    def band(rng, fill, color="#FFFFFF", bold=True, size=None):
        cmd("home.fillColor", range=rng, color=fill)
        cmd("home.fontColor", range=rng, color=color)
        if bold:
            cmd("home.bold", range=rng, on=True)
        if size:
            cmd("home.fontSize", range=rng, size=size)

    def header_block(sheet_title, last_col):
        put("A1", [[title]])
        cmd("home.mergeAcross", range=f"A1:{last_col}1")
        band(f"A1:{last_col}1", NAVY, size=16)
        put("A2", [[sheet_title]])
        cmd("home.fontSize", range="A2", size=13)
        cmd("home.bold", range="A2", on=True)
        cmd("home.fontColor", range="A2", color=NAVY)
        put("A3", [[p.get("desc", "")]])
        cmd("home.fontColor", range="A3", color=MUTED)
        cmd("home.italic", range="A3", on=True)
        cmd("home.rowHeight", rows="1:1", height=34)
        cmd("home.rowHeight", rows="2:2", height=24)

    F = lambda r: f'=IF($K{r}="{CUSTOMER}","(ลูกค้าจัดหา)",IF(OR($C{r}="",$E{r}=""),"",ROUND($C{r}*$E{r},2)))'
    H = lambda r: f'=IF(OR($C{r}="",$G{r}=""),"",ROUND($C{r}*$G{r},2))'
    I = lambda r: f'=IF(AND($F{r}="",$H{r}=""),"",N($F{r})+N($H{r}))'
    info_sheet = "ข้อมูลโครงการ"
    has_info = not blank and any(d.get(k) for k in ("facts", "key", "pieces"))   # a blank template carries no house data
    names = [s["name"] for s in d["sheets"]]

    cmd("sheet.rename", name=info_sheet if has_info else "สรุปราคา")
    for i, name in enumerate((["สรุปราคา"] if has_info else []) + names):
        cmd("home.insertSheet", name=name, before=i + 1 if has_info else i + 2)

    totals = {}   # sheet → (row of the sheet total, row of the customer-supplied value)
    for sheet in d["sheets"]:
        name = sheet["name"]
        cmd("sheet.activate", sheet=name)
        header_block(sheet["title"], "K")
        put("A5", [HEAD])
        band("A5:K5", GREEN)
        cmd("home.wrapText", range="A5:K5", on=True)
        cmd("home.alignCenter", range="A5:K5")
        cmd("home.alignMiddle", range="A5:K5")
        cmd("home.rowHeight", rows="5:5", height=40)
        r, sec_totals, warn, first_item = 6, [], [], 6
        for sec in sheet["sections"]:
            code, sname = sec["code"], sec["name"]
            put(f"A{r}", [[code, sname]])
            band(f"A{r}:K{r}", SAND, INK)
            r += 1
            a = r
            for row in sec["rows"]:
                lab, q, u, e, g, note, sup = (list(row) + [None] * 7)[:7]
                if blank:
                    q = e = g = None
                    note = ""
                put(f"A{r}", [["", "  " + lab, q, u, e, F(r), g, H(r), I(r), note or "", sup or ""]])
                if q is not None:
                    cmd("home.numberFormat", range=f"C{r}", code=fmt(q))
                if (note or "").startswith("⚠"):
                    warn.append(f"J{r}")
                r += 1
            b = r - 1
            put(f"A{r}", [["", f"รวม {code} {sname}", None, None, None, f"=SUM(F{a}:F{b})", None, f"=SUM(H{a}:H{b})",
                           f"=N(F{r})+N(H{r})", None, None]])
            band(f"A{r}:K{r}", MINT, INK)
            cmd("home.borders", range=f"A{r}:K{r}", preset="top", style="thin")
            sec_totals.append(r)
            r += 2
        last_item = r - 1
        s_sum = lambda col: "=" + "+".join(f"N({col}{t})" for t in sec_totals)
        put(f"A{r}", [["", f'รวม{sheet["name"]}ทั้งหมด', None, None, None, s_sum("F"), None, s_sum("H"), f"=N(F{r})+N(H{r})", None, None]])
        band(f"A{r}:K{r}", GREEN)
        cmd("home.fontSize", range=f"A{r}:K{r}", size=12)
        put(f"A{r+1}", [["", "มูลค่างานที่ลูกค้าจัดหาเอง (ไม่รวมในยอดข้างบน)", None, None, None,
                         f'=SUMPRODUCT(--($K{first_item}:$K{last_item}="{CUSTOMER}"),$C{first_item}:$C{last_item},$E{first_item}:$E{last_item})',
                         None, None, None, None, None]])
        cmd("home.italic", range=f"A{r+1}:K{r+1}", on=True)
        cmd("home.fontColor", range=f"A{r+1}:K{r+1}", color=MUTED)
        totals[name] = (r, r + 1)
        cmd("home.numberFormat", range=f"E6:I{r+1}", code=MONEY)
        cmd("home.borders", range=f"A5:K{r}", preset="all", style="thin", color=GREY)
        for c in warn:
            cmd("home.fontColor", range=c, color=AMBER)
        cmd("home.fontName", range=f"A1:K{r+1}", name=FONT)
        cmd("home.rowHeight", rows=f"6:{r+1}", height=22)
        for cols, w in [("A:A", 6), ("B:B", 62), ("C:C", 10), ("D:D", 8), ("E:I", 14), ("J:J", 72), ("K:K", 10)]:
            cmd("home.columnWidth", cols=cols, chars=w)
        cmd("view.freezePanes", cell="C6")
        cmd("pageLayout.size", paper="A4")
        cmd("pageLayout.orientation", orientation="landscape")
        cmd("pageLayout.scaleToFit", width=1)
        cmd("pageLayout.printTitles", rows="5:5")
        cmd("sheet.tabColor", sheet=name, color=GREEN)

    # ---- สรุปราคา
    np_rows = [] if blank else unpriced(d)
    cmd("sheet.activate", sheet="สรุปราคา")
    header_block("สรุปราคาค่าก่อสร้าง", "F")
    put("B4", [["เลขที่เอกสาร", p.get("doc_no", "")], ["โครงการ", p.get("name", "")], ["เจ้าของ", p.get("owner", "")],
               ["สถานที่", p.get("place", "")], ["วันที่", p.get("date", "")], ["ผู้ถอดปริมาณ", p.get("by", "")]])
    cmd("home.bold", range="B4:B9", on=True)
    cmd("home.fontColor", range="B4:B9", color=NAVY)
    put("A11", [["ลำดับ", "หมวดงาน", "ค่าวัสดุ (บาท)", "ค่าแรง (บาท)", "รวมราคา (บาท)", "สัดส่วน"]])
    band("A11:F11", GREEN)
    cmd("home.alignCenter", range="A11:F11")
    for i, name in enumerate(names):
        t, r = totals[name][0], 12 + i
        put(f"A{r}", [[str(i + 1), name, f"='{name}'!F{t}", f"='{name}'!H{t}", f"=N(C{r})+N(D{r})", f'=IF($E$16=0,"",E{r}/$E$16)']])
    put("A16", [["", "รวมราคางานก่อสร้าง", "=SUM(C12:C15)", "=SUM(D12:D15)", "=SUM(E12:E15)", '=IF(E16=0,"",1)']])
    band("A16:F16", MINT, INK)
    put("B18", [["ค่าดำเนินการ", rates["overhead"], None, "=ROUND($E$16*C18,2)"], ["กำไรบริหารงาน", rates["profit"], None, "=ROUND($E$16*C19,2)"],
                ["รวมก่อนภาษีมูลค่าเพิ่ม", None, None, "=E16+N(E18)+N(E19)"], ["ภาษีมูลค่าเพิ่ม (VAT)", rates["vat"], None, "=ROUND(E20*C21,2)"]])
    put("A22", [["ราคารวมทั้งสิ้น", None, None, None, "=E20+E21", None]])
    band("A22:F22", NAVY, size=13)
    put("A23", [["", "(ตัวอักษร)", "=BAHTTEXT(E22)"]])
    cmd("home.mergeAcross", range="C23:F23")
    cmd("home.italic", range="C23", on=True)
    put("B24", [["พื้นที่ใช้สอย", None if blank else d["floor_area"], "ตร.ม."], ["ราคาต่อตารางเมตร", '=IF(N(C24)=0,"",ROUND(E22/C24,0))', "บาท/ตร.ม."]])
    if np_rows:
        put("E25", [[f"⚠ ยังไม่รวม {len(np_rows)} รายการที่ไม่มีราคา"]])
        cmd("home.fontColor", range="E25", color=AMBER)
    cmd("home.bold", range="B25:C25", on=True)
    put("B26", [["มูลค่าที่ลูกค้าจัดหาเอง (ไม่รวมในยอด)", "=" + "+".join(f"N('{n}'!F{totals[n][1]})" for n in names), "บาท"]])
    notes = [] if blank else list(d.get("notes") or [])
    notes = ["เอกสารนี้เป็นใบประมาณราคาเบื้องต้นจากโมเดล 3 มิติ ปริมาณและราคาเป็นค่าประมาณ ยังไม่ผ่านการตรวจของวิศวกรหรือผู้ประมาณราคา (QS) "
             "ไม่ใช่ใบเสนอราคาและไม่ใช่เอกสารประกอบสัญญาจนกว่าผู้รับผิดชอบจะตรวจและลงนาม"] + notes
    if np_rows:
        notes.append(f"ยังไม่มีราคา {len(np_rows)} รายการ ยอดรวมและราคาต่อ ตร.ม. จึงยังต่ำกว่าจริง · แถว ⚠ คือค่าประมาณหรือแบบยังไม่ระบุ")
    put("A28", [["หมายเหตุ"]] + [[f"{i}. {t}"] for i, t in enumerate(notes, 1)])
    cmd("home.bold", range="A28", on=True)
    cmd("home.fontColor", range=f"A29:A{28 + len(notes)}", color=MUTED)
    put("H11", [["หมวดงาน", "บาท"]] + [[n, f"=E{12+i}"] for i, n in enumerate(names)])
    cmd("home.numberFormat", range="C12:E22", code=MONEY)
    cmd("home.numberFormat", range="F12:F16", code="0.0%")
    cmd("home.numberFormat", range="C18:C21", code="0%" if all(abs(v * 100 - round(v * 100)) < 1e-9 for v in rates.values()) else "0.0%")
    cmd("home.numberFormat", range="C24", code="#,##0.00")
    cmd("home.numberFormat", range="C25", code="#,##0")
    cmd("home.numberFormat", range="C26", code=MONEY)
    cmd("home.numberFormat", range="I12:I15", code="#,##0")
    cmd("home.borders", range="A11:F22", preset="all", style="thin", color=GREY)
    cmd("insert.chart", range="H11:I15", type="doughnut", title="สัดส่วนค่าก่อสร้างตามหมวด", at="H17", width=460, height=300)
    cmd("home.alignCenter", range="A12:A15")
    band("H11:I11", SAND, INK)
    cmd("home.borders", range="H11:I15", preset="all", style="thin", color=GREY)
    cmd("home.fontName", range=f"A1:I{28 + len(notes)}", name=FONT)
    for cols, w in [("A:A", 14), ("B:B", 36), ("C:E", 16), ("F:F", 10), ("H:H", 22), ("I:I", 14)]:
        cmd("home.columnWidth", cols=cols, chars=w)
    cmd("view.gridlines", on=False)
    cmd("sheet.tabColor", sheet="สรุปราคา", color=NAVY)

    # ---- ข้อมูลโครงการ (only when the file brings facts, key quantities or per-piece rows)
    if has_info:
        facts, key, pieces = d.get("facts") or [], d.get("key") or [], d.get("pieces") or []
        cmd("sheet.activate", sheet=info_sheet)
        header_block("ข้อมูลโครงการและสเปกงานก่อสร้าง", "F")
        r = 5
        if facts:
            put(f"A{r}", [["ข้อมูลอาคาร", ""]] + [list(f) for f in facts])
            band(f"A{r}:F{r}", GREEN)
            cmd("home.mergeAcross", range=f"A{r}:F{r}")
            for i in range(r + 1, r + 1 + len(facts)):
                cmd("home.mergeAcross", range=f"B{i}:F{i}")
            cmd("home.bold", range=f"A{r+1}:A{r+len(facts)}", on=True)
            cmd("home.fontColor", range=f"A{r+1}:A{r+len(facts)}", color=NAVY)
            r += len(facts) + 2
        if key:
            put(f"A{r}", [["ปริมาณหลักทั้งหลัง", "", "", ""]])
            band(f"A{r}:F{r}", GREEN)
            cmd("home.mergeAcross", range=f"A{r}:F{r}")
            put(f"A{r+1}", [["รายการ", "ปริมาณ", "หน่วย", "รายละเอียด"]])
            band(f"A{r+1}:F{r+1}", SAND, INK)
            put(f"A{r+2}", [list(x) for x in key])
            for i, row in enumerate(key):
                cmd("home.mergeAcross", range=f"D{r+2+i}:F{r+2+i}")
                if isinstance(row[1], (int, float)):
                    cmd("home.numberFormat", range=f"B{r+2+i}", code=fmt(row[1]))
            cmd("home.alignLeft", range=f"B{r+2}:B{r+1+len(key)}")
            cmd("home.bold", range=f"B{r+2}:B{r+1+len(key)}", on=True)
            cmd("home.borders", range=f"A{r+1}:F{r+1+len(key)}", preset="all", style="thin", color=GREY)
            r += len(key) + 3
        if pieces:
            put(f"A{r}", [["ถอดต่อชิ้น × จำนวน"]])
            band(f"A{r}:F{r}", GREEN)
            cmd("home.mergeAcross", range=f"A{r}:F{r}")
            put(f"A{r+1}", [["งาน", "รายละเอียดต่อชิ้น", "ต่อชิ้น", "หน่วย", "จำนวน", "รวม"]])
            band(f"A{r+1}:F{r+1}", SAND, INK)
            put(f"A{r+2}", [[w, det, per, unit, n, f"=ROUND(C{r+2+i}*E{r+2+i},2)"] for i, (w, det, per, unit, n, nu) in enumerate(pieces)])
            cmd("home.numberFormat", range=f"C{r+2}:C{r+1+len(pieces)}", code="#,##0.000#")
            cmd("home.numberFormat", range=f"F{r+2}:F{r+1+len(pieces)}", code="#,##0.00")
            for i, row in enumerate(pieces):
                cmd("home.numberFormat", range=f"E{r+2+i}", code=fmt(row[4]))
            cmd("home.bold", range=f"F{r+2}:F{r+1+len(pieces)}", on=True)
            cmd("home.borders", range=f"A{r+1}:F{r+1+len(pieces)}", preset="all", style="thin", color=GREY)
            r += len(pieces) + 3
        cmd("home.fontName", range=f"A1:F{r}", name=FONT)
        for cols, w in [("A:A", 30), ("B:B", 58), ("C:C", 12), ("D:D", 12), ("E:E", 12), ("F:F", 14)]:
            cmd("home.columnWidth", cols=cols, chars=w)
        cmd("pageLayout.size", paper="A4")
        cmd("pageLayout.orientation", orientation="landscape")
        cmd("pageLayout.scaleToFit", width=1)
        cmd("view.gridlines", on=False)
        cmd("sheet.tabColor", sheet=info_sheet, color=NAVY)
        cmd("sheet.activate", sheet="สรุปราคา")
    return out


def write_jsonl(op_list, path):
    with open(path, "w", encoding="utf-8") as fh:
        for command, params in op_list:
            fh.write(json.dumps({"command": command, "params": params}, ensure_ascii=False) + "\n")


def write_xlsx(op_list, path):
    """Replay the commands with openpyxl. An unknown command is an error, so the two outputs cannot drift apart."""
    from copy import copy
    from openpyxl import Workbook
    from openpyxl.chart import DoughnutChart, Reference
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from openpyxl.utils import get_column_letter, range_boundaries
    from openpyxl.utils.cell import column_index_from_string

    wb = Workbook()
    ws = wb.active
    rgb = lambda c: "FF" + c.lstrip("#").upper()

    def cells(rng):
        c1, ra, c2, rb = range_boundaries(rng)
        return [ws.cell(row=r, column=c) for r in range(ra, rb + 1) for c in range(c1, c2 + 1)]

    def font(rng, **kw):
        for c in cells(rng):
            f = copy(c.font)
            for k, v in kw.items():
                setattr(f, k, v)
            c.font = f

    def align(rng, **kw):
        for c in cells(rng):
            a = copy(c.alignment)
            for k, v in kw.items():
                setattr(a, k, v)
            c.alignment = a

    def span(s, to_int):   # "5:7" → [5, 6, 7] · "E:I" → [5..9]
        a, b = (s.split(":") + [s])[:2]
        return range(to_int(a), to_int(b) + 1)

    for command, p in op_list:
        if command == "sheet.rename":
            ws.title = p["name"]
        elif command == "home.insertSheet":
            wb.create_sheet(p["name"], p["before"] - 1)
        elif command == "sheet.activate":
            ws = wb[p["sheet"]]
            wb.active = wb.index(ws)
        elif command == "range.setValues":
            c0, r0, _, _ = range_boundaries(p["range"])
            for i, row in enumerate(p["values"]):
                for j, v in enumerate(row):
                    if v is not None and v != "":
                        ws.cell(row=r0 + i, column=c0 + j, value=v)
        elif command == "home.fillColor":
            for c in cells(p["range"]):
                c.fill = PatternFill("solid", fgColor=rgb(p["color"]))
        elif command == "home.fontColor":
            font(p["range"], color=rgb(p["color"]))
        elif command == "home.bold":
            font(p["range"], b=p["on"])
        elif command == "home.italic":
            font(p["range"], i=p["on"])
        elif command == "home.fontSize":
            font(p["range"], sz=p["size"])
        elif command == "home.fontName":
            font(p["range"], name=p["name"])
        elif command == "home.wrapText":
            align(p["range"], wrap_text=p["on"])
        elif command in ("home.alignCenter", "home.alignLeft"):
            align(p["range"], horizontal=command[10:].lower())
        elif command == "home.alignMiddle":
            align(p["range"], vertical="center")
        elif command == "home.mergeAcross":
            c1, ra, c2, rb = range_boundaries(p["range"])
            for r in range(ra, rb + 1):
                ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        elif command == "home.borders":
            side = Side(style=p.get("style", "thin"), color=rgb(p["color"]) if p.get("color") else None)
            for c in cells(p["range"]):
                b = copy(c.border)
                c.border = Border(top=side, left=side if p["preset"] == "all" else b.left, right=side if p["preset"] == "all" else b.right,
                                  bottom=side if p["preset"] == "all" else b.bottom)
        elif command == "home.rowHeight":
            for r in span(p["rows"], int):
                ws.row_dimensions[r].height = p["height"]
        elif command == "home.columnWidth":
            for c in span(p["cols"], column_index_from_string):
                ws.column_dimensions[get_column_letter(c)].width = p["chars"]
        elif command == "home.numberFormat":
            for c in cells(p["range"]):
                c.number_format = p["code"]
        elif command == "view.freezePanes":
            ws.freeze_panes = p["cell"]
        elif command == "view.gridlines":
            ws.sheet_view.showGridLines = p["on"]
        elif command == "sheet.tabColor":
            wb[p["sheet"]].sheet_properties.tabColor = rgb(p["color"])
        elif command == "pageLayout.size":
            ws.page_setup.paperSize = {"A4": 9, "A3": 8, "Letter": 1}[p["paper"]]
        elif command == "pageLayout.orientation":
            ws.page_setup.orientation = p["orientation"]
        elif command == "pageLayout.scaleToFit":
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = p["width"], 0
        elif command == "pageLayout.printTitles":
            ws.print_title_rows = p["rows"]
        elif command == "insert.chart":
            c1, ra, c2, rb = range_boundaries(p["range"])
            ch = DoughnutChart()
            ch.title = p["title"]
            ch.add_data(Reference(ws, min_col=c2, min_row=ra, max_row=rb), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=c1, min_row=ra + 1, max_row=rb))
            ch.width, ch.height = p["width"] / 37.8, p["height"] / 37.8   # px → cm
            ws.add_chart(ch, p["at"])
        else:
            raise ValueError(f"build_boq: no xlsx writer for {command}")
    wb.save(path)


def selftest():
    import os
    import tempfile
    from openpyxl import load_workbook
    row = lambda *a: list(a)
    d = {"project": {"title": "BOQ ทดสอบ", "name": "บ้านทดสอบ"}, "floor_area": 50,
         "sheets": [{"name": n, "title": f"หมวดที่ {i} : {n}", "sections": [{"code": f"{i}.10", "name": "ทดสอบ", "rows": [
             row("คอนกรีต St.240 ksc", 4.5, "ลบ.ม.", 2000, None, "0.30 × 14 = 4.2 → 4.5"),
             row("ค่าแรง", 86.99, "ตร.ม.", None, 500, ""),
             row("สีเจ้าของจัดหา", 2, "ถัง", 2000, 100, "⚠ ประมาณ", CUSTOMER),
             row("ยังไม่มีราคา", 3, "ชุด", None, None, "")]}]}
             for i, n in enumerate(["งานโครงสร้าง", "งานสถาปัตยกรรม", "งานสุขาภิบาล", "งานระบบไฟฟ้า"], 1)]}
    assert check(d) == 16 and len(unpriced(d)) == 4
    e = expected(d)
    # per sheet: material 4.5×2000 = 9,000 · labour 86.99×500 + 2×100 = 43,695 · customer paint 2×2000 kept apart
    assert e["sheets"][0] == {"name": "งานโครงสร้าง", "material": 9000.0, "labour": 43695.0, "customer": 4000.0}, e["sheets"][0]
    # 4 sheets = 210,780 · +5% 10,539 · +10% 21,078 = 242,397 · VAT 7% 16,967.79
    assert e["subtotal"] == 210780.0 and e["overhead"] == 10539.0 and e["profit"] == 21078.0 and e["vat"] == 16967.79, e
    assert e["total"] == 259364.79 and e["per_m2"] == 5187, e
    assert expected({**d, "rates": {"overhead": 0.03}})["overhead"] == 6323.4
    assert r2(17.18 * 0.25) == 4.30 and r2(2.675) == 2.68
    one = lambda r, **kw: {**d, **kw, "sheets": [{**d["sheets"][0], "sections": [{"code": "1", "name": "x", "rows": [r]}]}] + d["sheets"][1:]}
    sheet0 = lambda **kw: {**d, "sheets": [{**d["sheets"][0], **kw}] + d["sheets"][1:]}
    for bad in ({**d, "sheets": d["sheets"][:3]}, {**d, "floor_area": 0}, {**d, "rates": {"vat": 7}}, {**d, "rates": {"tax": 0.07}},
                one(row("คอนกรีต", 4.2, "ลบ.ม.", 1, None, "")), one(row("=HYPERLINK(1)", 1, "ชุด", 1, None, "")),
                one(row("a", "=1+1", "ชุด", 1, None, "")), one(row("a", 0, "ชุด", 1, None, "")), one(row("a", 1, "ชุด", -5, None, "")),
                one(row("a", 1, "ชุด", 1, None, "@SUM(1)")), one(row("a", 1, "ชุด", 1, None, "--1+1")), one(row("a", 1, "ชุด", 1, None, "-@SUM(1)")),
                one(row("a", 1, "ชุด", 1, None, "\u200b=1")), one(row("a", 1, "=1", 1, None, "")), one(row("a", 1, "ชุด", 1, None, "", "ผู้รับเหมา")),
                one(row("a", 1, "ชุด", float("nan"), None, "")), one(row("a", 1, "ชุด", 1, None, 5)),
                {**d, "project": {"owner": '=HYPERLINK("http://x","ดู")'}}, {**d, "key": [["ก", float("inf"), "ชุด", ""]]}, {**d, "notes": ["+1+1"]}, {**d, "facts": [["ก", "=1"]]},
                {**d, "facts": [["ก"]]}, {**d, "pieces": [["ก", "ข", "0.3", "ลบ.ม.", 14, "หลุม"]]}, {**d, "key": [["ก", 1, "ชุด"]]},
                sheet0(name="สรุปราคา"), sheet0(name="งานสถาปัตยกรรม"), sheet0(name=" "), sheet0(title="=1"), sheet0(sections=[]),
                sheet0(sections=[{"code": "=1", "name": "x", "rows": [row("a", 1, "ชุด", 1, None, "")]}]),
                sheet0(sections=[{"code": "1", "name": "x", "rows": []}])):
        try:
            check(bad)
        except ValueError:
            continue
        raise AssertionError(f"check() should refuse this file: {str(bad)[:200]}")
    assert check({**d, "project": None, "rates": None, "notes": None, "sheets": d["sheets"]}) == 16
    assert check(one(row("ระดับ -0.50 ถึง +0.60", 1, "ชุด", 1, None, "- ไม่มี", ""))) == 13   # only a leading formula sign is refused
    with tempfile.TemporaryDirectory() as tmp:
        x, j = os.path.join(tmp, "t.xlsx"), os.path.join(tmp, "t.jsonl")
        write_xlsx(ops(d), x)
        write_jsonl(ops(d), j)
        wb = load_workbook(x)
        assert wb.sheetnames == ["สรุปราคา", "งานโครงสร้าง", "งานสถาปัตยกรรม", "งานสุขาภิบาล", "งานระบบไฟฟ้า"], wb.sheetnames
        ws = wb["งานโครงสร้าง"]
        assert ws["A6"].value == "1.10" and ws["C7"].value == 4.5 and ws["F7"].value.startswith("=IF($K7="), (ws["A6"].value, ws["F7"].value)
        assert ws["F11"].value == "=SUM(F7:F10)" and ws["K9"].value == CUSTOMER and ws["A5"].fill.fgColor.rgb == "FF2E5E4E"
        s = wb["สรุปราคา"]
        assert s["C12"].value == "='งานโครงสร้าง'!F13" and s["E22"].value == "=E20+E21" and s["C23"].value == "=BAHTTEXT(E22)"
        assert len(s._charts) == 1 and s["E25"].value.startswith("⚠ ยังไม่รวม 4")
        lines = [json.loads(l) for l in open(j, encoding="utf-8")]
        assert lines[0] == {"command": "sheet.rename", "params": {"name": "สรุปราคา"}} and len(lines) == len(ops(d))
        d2 = {**d, "facts": [["ฐานราก", "14 หลุม"]], "key": [["คอนกรีตรวม", 24.81, "ลบ.ม.", "ฐาน 4.22"]],
              "pieces": [["ฐานราก F1", "1.00 × 1.00 × 0.30 ม.", 0.30, "ลบ.ม./หลุม", 14, "หลุม"]]}
        assert check(d2) == 16
        write_xlsx(ops(d2), x)
        wb = load_workbook(x)
        assert wb.sheetnames[0] == "สรุปราคา" and wb.sheetnames[-1] == "ข้อมูลโครงการ" and len(wb.sheetnames) == 6, wb.sheetnames
        info = wb["ข้อมูลโครงการ"]
        assert info["A6"].value == "ฐานราก" and info["B10"].value == 24.81 and info["F14"].value == "=ROUND(C14*E14,2)", [info["A6"].value, info["B10"].value, info["F14"].value]
        write_xlsx(ops(d2, blank=True), x)
        wb = load_workbook(x)
        ws = wb["งานโครงสร้าง"]
        assert ws["C7"].value is None and ws["E7"].value is None and ws["B7"].value == "  คอนกรีต St.240 ksc"
        assert "ข้อมูลโครงการ" not in wb.sheetnames, wb.sheetnames   # a blank template carries no house data
    print("selftest ok")


def main(argv):
    for stream in (sys.stdout, sys.stderr):   # Thai totals on a Windows console or pipe (cp1252/cp874) would crash
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if argv == ["--selftest"]:
        return selftest()
    blank = "--blank" in argv
    args = [a for a in argv if a != "--blank"]
    opt = dict(zip(args[1::2], args[2::2]))
    if len(args) < 3 or len(args) % 2 == 0 or not opt or set(opt) - {"--out", "--jsonl"} or len(opt) != len(args) // 2:
        sys.exit(__doc__)
    src = os.path.abspath(args[0])
    for flag, ext in (("--out", ".xlsx"), ("--jsonl", ".jsonl")):
        if flag in opt and (not opt[flag].lower().endswith(ext) or os.path.abspath(opt[flag]) == src):
            sys.exit(f"build_boq: {flag} ต้องเป็นไฟล์ {ext} และไม่ใช่ไฟล์ปริมาณเอง: {opt[flag]}")
    try:
        with open(src, encoding="utf-8") as fh:
            d = json.load(fh)
        n = check(d)
        op_list = ops(d, blank=blank)
        e = expected(d)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as err:
        sys.exit(f"build_boq: {args[0]}: {err}")
    try:
        if "--jsonl" in opt:
            write_jsonl(op_list, opt["--jsonl"])
            print("wrote", opt["--jsonl"], len(op_list), "commands")
        if "--out" in opt:
            write_xlsx(op_list, opt["--out"])
            print("wrote", opt["--out"])
    except ImportError:
        sys.exit('build_boq: --out needs openpyxl: python3 -m pip install "openpyxl>=3.1,<4" (or use --jsonl with gridcraft-cli)')
    except OSError as err:
        sys.exit(f"build_boq: เขียนไฟล์ไม่ได้ (โฟลเดอร์ไม่มี หรือไฟล์เปิดค้างอยู่ในโปรแกรมอื่น): {err}")
    if not blank:
        print(f"rows {n} · unpriced {len(unpriced(d))}")
        for sh in e["sheets"]:
            print(f'  {sh["name"]}: วัสดุ {sh["material"]:,.2f} · ค่าแรง {sh["labour"]:,.2f} · ลูกค้าจัดหา {sh["customer"]:,.2f}')
        print(f'  รวมงานก่อสร้าง {e["subtotal"]:,.2f} · รวมทั้งสิ้น {e["total"]:,.2f} บาท · {e["per_m2"]:,} บาท/ตร.ม.')


if __name__ == "__main__":
    main(sys.argv[1:])
