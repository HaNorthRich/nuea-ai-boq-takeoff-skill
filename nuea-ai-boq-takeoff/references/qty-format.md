# Quantity file format (`<house>_qty.json`)

One JSON file per house. `build_boq.py` reads it, checks it, and builds the workbook. Worked example with 153 rows:
`examples/ep7_house_qty.json`.

```json
{
  "project": {
    "title": "ใบประมาณราคา (BOQ) · บ้านคุณเอ",
    "desc": "บ้านพักอาศัย ค.ส.ล. ชั้นเดียว 3 ห้องนอน 2 ห้องน้ำ",
    "doc_no": "BOQ-001", "name": "บ้านคุณเอ", "owner": "–", "place": "–",
    "date": "11 พ.ย. 2569", "by": "Claude Code จากโมเดล Blender"
  },
  "floor_area": 86.99,
  "rates": {"overhead": 0.05, "profit": 0.10, "vat": 0.07},
  "sheets": [
    {"name": "งานโครงสร้าง", "title": "หมวดที่ 1 : งานโครงสร้าง", "sections": [
      {"code": "1.1", "name": "งานฐานราก", "rows": [
        ["คอนกรีต St.240 ksc", 4.5, "ลบ.ม.", 2000, null, "ฐาน 1.00×1.00×0.30 × 14 หลุม = 4.2 → 4.5"],
        ["ค่าแรงช่างโครงสร้างชั้นที่ 1", 86.99, "ตร.ม.", null, 500, "พื้นที่ห้อง + เฉลียง"],
        ["สีทาฝ้าเพดาน 5 แกลลอน", 2, "ถัง", 1500, null, "ฝ้า 137.98 ตร.ม. ÷ 87.5", "ลูกค้า"]
      ]}
    ]}
  ],
  "notes": ["ไม่รวม: รั้ว ภูมิทัศน์ เครื่องปรับอากาศ"],
  "facts": [["ฐานราก", "ฐานแผ่ 1.00×1.00×0.30 ม. 14 หลุม"]],
  "key": [["คอนกรีตโครงสร้างรวม", 24.81, "ลบ.ม.", "ฐาน 4.22 · คาน 6.78 · พื้น 9.02"]],
  "pieces": [["ฐานราก F1", "1.00 × 1.00 × 0.30 ม. · DB12 14 ท่อน", 0.30, "ลบ.ม./หลุม", 14, "หลุม"]]
}
```

| Key | Required | Meaning |
|---|---|---|
| `project` | no | Header text of every sheet and the info block of สรุปราคา. Missing keys print empty. |
| `floor_area` | yes | Usable area in m² (rooms + porch, no carport). Used for บาท/ตร.ม. |
| `rates` | no | ค่าดำเนินการ, กำไร, VAT as fractions (7 % = 0.07). The defaults 0.05, 0.10, 0.07 are sample values: ask the user for theirs, and set `vat` to 0 when the quoting party is not VAT-registered. The prices in the example above are made up too. |
| `sheets` | yes | **Exactly four** work categories, in the order they appear in สรุปราคา. `name` is the sheet tab (unique, at most 31 characters, none of `[]:*?/\'`). |
| `sections[].rows[]` | yes | `[รายการ, ปริมาณ, หน่วย, ราคาวัสดุ, ค่าแรง, หมายเหตุ, ผู้จัดหา]`. The last two may be left off. |
| `notes` | no | Numbered lines under สรุปราคา (exclusions, price date). A line about checking by an engineer is always added first. |
| `facts`, `key`, `pieces` | no | Build the ข้อมูลโครงการ sheet: building facts, headline quantities, and "per piece × count" rows. Leave all three out and the sheet is not made. |

## Row rules (`build_boq.py` refuses a file that breaks them)

- `ปริมาณ` is a number above 0, or `null` for a row kept as a reminder. Work that the house does not have is deleted, not zeroed.
- `ราคาวัสดุ` and `ค่าแรง` are numbers per unit, or `null`. A row with a quantity and neither price counts as "ยังไม่มีราคา";
  the summary states how many there are, because the total is too low until they are filled.
- No text anywhere in the file (rows, project, notes, facts, key, pieces, sheet and section names) may start with
  `=`, `+`, `@`, or `-` followed directly by anything but a space. The workbook writes its own formulas; text from a drawing or a
  web page must not become one. Write `ระดับ -0.50` rather than `-0.50 ระดับ`.
- A row labelled `คอนกรีต…` in `ลบ.ม.` is ready-mix: quantity in steps of 0.5.
- `ผู้จัดหา` is `"ลูกค้า"` or absent. Such a row shows `(ลูกค้าจัดหา)`: its material value stays out of the total and is
  summed apart; its labour, if the row has one, still counts.
- `หมายเหตุ` holds the arithmetic and the source of the quantity (`"14 หลุม × 0.30 = 4.2 → 4.5"`, `"แบบ S-02"`).
  Start it with `⚠` when the number is an estimate or the drawing does not say: the cell turns amber.

## What the workbook computes

Per row: รวมค่าวัสดุ = ROUND(ปริมาณ × ราคาวัสดุ, 2), รวมค่าแรง = ROUND(ปริมาณ × ค่าแรง, 2). Per section and per sheet: sums.
สรุปราคา: the four sheet totals, + ค่าดำเนินการ, + กำไร, + VAT, the total in Thai words (`BAHTTEXT`), and บาท/ตร.ม.
`build_boq.py` prints the same totals computed in Python; the two must agree to the satang.
