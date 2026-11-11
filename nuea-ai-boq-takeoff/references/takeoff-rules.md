# Take-off rules: BOQ row → quantity

Read in step 3. Measures named here come from `blend_takeoff.py` (`net`, `ext_frac`, `run_length`, `total_length`,
`members`, `pieces`, `area.slope/up/down`, roof `eave/ridge/hip/valley/rake`). Every row's note carries the arithmetic
and its source: `"ฐาน 14 ชิ้น × 1.00×1.00×0.30 = 4.2 → 4.5"` · `"แบบ S-02"` · `"⚠ แบบไม่ระบุ ถามวิศวกร"`.

## Source order
1. Construction drawings when the user has them: structure sizes, rebar, levels, schedules (doors, windows, lights,
   sanitary). A model can be an older revision than the drawing. When the two disagree, use the drawing and list the
   difference in the report.
2. The model: lengths, areas, counts, finishes and decoration the drawings do not list (cornice, trims, columns,
   arches, gutters). Mark those rows `จากโมเดล 3D ตัดออกได้`.
3. A typical allowance when neither has it, for non-structural items only (tie wire, nails, pipe and wire runs,
   fittings): flag the row `⚠`. A structural size, bar count, spacing or concrete grade that neither the drawing nor
   the model gives is never assumed: leave the quantity `null`, flag the row `⚠`, and put the question for the
   engineer in the note.

## Units and rounding
- Ready-mix concrete: net m³ → round **up** to 0.5 (`ceil(x*2)/2`). `build_boq.py` refuses anything else.
- Rebar sold in 10 m bars: `ceil(m × factor / 10)`; factor 1.10 main bars (laps), 1.05 mats and offcuts. Stirrups:
  count `(L / s + 1)` × stirrup length (0.20×0.40 beam ≈ 1.06 m, 0.20×0.20 column ≈ 0.75 m). Modelled rebar
  (`members`): `bar≈6mm` = RB6, `≈12mm` = DB12; the total length already includes what was modelled.
- Steel sections 6 m: `ceil(m × 1.05 / 6)`; hips and valleys with mitres × 1.10. PVC pipe 4 m. Round counts up.
- Tie wire ≈ 18 kg per tonne of rebar in the section. Nails ≈ 0.25 kg per m² of formwork.

## 1 งานโครงสร้าง
Every member size, bar count, spacing and grade used below is read from the drawing or the model as drawn. The
formulas only turn those into quantities; they do not choose a size.
- **ฐานราก**: count = footing `pieces` (or the drawing); size B×B×T from volume ÷ pieces. Concrete n·B²·T; lean
  concrete 0.05 and sand under it n·B²·thickness; mat bars 2 directions × (B/s + 1) × (B − 0.10 + 2 × 0.15 hooks);
  formwork n·4B·T. Structure labour = house floor area with the porch, without the carport.
- **คานคอดิน**: gross length = beam centrelines (or grid lines); net = gross − columns × width. Concrete net·b·h;
  main bars n × gross × 1.10; stirrups; formwork 2·h·net (add the soffit when the beam hangs clear of the ground).
  Termite treatment = floor area. A plan label like "GB3+0.60" is the beam-top level, not a deeper beam.
- **พื้น**: hollow-core planks = area of the plank bays between beams. Topping thickness and steel per the drawing
  (RB6@0.20 both ways ≈ 10 m/m² + 10 %). Cast slabs (baths, porch) area × thickness + steps; RB9@0.20 both ways
  10 m/m². `Slab.up` is the gross footprint with walls; `Floor_*.up` are finish areas inside the walls.
- **ตอม่อและเสา**: count from column `pieces` or the drawing; H = footing top → wall top. Concrete n·b²·H; main bars
  (count as drawn) × (H + 0.90) × 1.10; stirrups; formwork 4b·H. Use the grade the drawing states. Drawing notes give
  **cylinder** strength and ready-mix price lists quote **cube**: 240 cylinder ≈ 280 cube, 280 cylinder ≈ 350 cube.
  Say which one the row means and let the engineer confirm.
- **หลังคากระเบื้องคอนกรีต**: A = tiled slope area (sum the tile meshes' `area.slope`; a wing's hip plane still
  holds the part buried under the main roof, the tile mesh does not). Tiles `ceil(A × 10.5 × 1.05, 10)`; ridge caps
  = cap length × 3.3 × 1.03; end caps = hip ends + ridge ends; roof labour = A, not tiles ÷ 10.5 (that would count
  the 5 % cutting waste); bird stop = eave length; valley flashing = valley run. Steel from the frame `members` by
  cross-section (50 cm² = 100×50 rafter, and so on); battens at the tile lap. Paint: steel surface × 2 coats ÷ 35
  m²/gallon. Plates = column heads + king posts + carport posts. Tile clips ≈ 1/3 of the tiles. A concrete-tile
  roof is heavy: put `⚠ วิศวกรยืนยันขนาดจันทัน-แป` on the frame rows.
- **หลังคาเมทัลชีท**: sheet m² = slope area × 1.05 (laps); flashing = where a roof meets a wall. No tile, cap or clip rows.
- **รางน้ำฝน**: gutter `run_length`, downpipes `total_length`, only when modelled or drawn. Otherwise leave the row
  out and list it as excluded. Count elbows, end caps and brackets too.
- **โรงรถ**: sheet = carport roof plan; frame from `members` (100 cm² = 100×100, 50 = 100×50, 25 = 50×50). Concrete
  carport columns go with the footings and columns. Floor: area × 0.15 → 0.5 step, RB9@0.15 both ways 13.4 m/m² +
  10 %, sand 5 cm.

## 2 งานสถาปัตยกรรม
- **ผนัง**: block area = Σ wall `net` (openings already cut). Bricks per m² depend on the unit: about 40 for a
  large hollow clay block, about 117–130 for small solid clay brick in a half-brick wall, 8.3 for 20×60 AAC. Say
  which the house uses and add 5 % breakage.
- **ฉาบ** = 2 × Σ `net` + exposed column and beam faces + opening reveals. Tiles go on plaster.
- **เสาเอ็น-ทับหลัง** = Σ wall lengths + 2 posts per opening × wall height + heads and sills.
- **เซี้ยม** = Σ opening perimeters × 2 faces + external corners × wall height.
- **สี**: exterior = Σ `net × ext_frac` − exterior tiles and cladding; interior = interior faces (both faces of
  interior walls, inner face of exterior walls) − wall tiles; primer = exterior + interior paint area, never the
  tiled area. 5-gallon pail ≈ 87.5 m² at 2 coats, 2.5-gallon ≈ 43.75 m². Exterior work above arm's reach needs a
  scaffolding row.
- **กระเบื้องพื้น** = `Floor_*.up` by room type (bath anti-slip apart) × 1.05; porch and steps = treads + risers.
  **กระเบื้องผนัง** bath = 2(w + d) × tile height − door; kitchen splash = counter run × 0.6. Adhesive 1 bag per
  5 m², grout 1 bag per 3 m² of floor + wall tile.
- **ฝ้า** = `Ceiling.down` (bath and kitchen apart when they are moisture-resistant board); eave soffit =
  `Roof_Soffit.down`, or `*_Eave.down` − building footprint (the eave box spans the whole roof plan). Insulation as
  the drawing says: on the ceiling (ceiling area) or under the battens (slope area).
- **บัวพื้น** = room perimeters − door widths − tiled walls.
- **ประตู/หน้าต่าง**: the `## Openings` list grouped by size and EXT/int; schedule code from the name (D1, W1) or the
  drawing. One frame per opening; hardware per door leaf (handle or knob, 3 hinges, door stop).
- **ตกแต่ง (จากโมเดล)**: cornice run length, architraves per opening, porch columns by piece, arches, cladding area.
- **ห้องน้ำ/ครัว**: placed fixtures by piece, counter length, sink, floor taps, soap holders. Things the builder
  must buy belong in a row even when the user's own BOQ form has no line for them.

## 3 สุขาภิบาล · 4 ไฟฟ้า
A sales model has no pipes or wiring, and its lights are render lamps, not electrical points. Take fixtures from the
model and the drawing's schedules. Without a drawing, use a typical layout and flag every row `⚠ ประมาณ`: light 1
per bedroom, bath, kitchen, 2 living, 1 per outside door; switch per room door; sockets 2 per bedroom, 3 living, 2
kitchen + 1 fridge; AC circuit per bedroom; septic tank 1000 L up to 7 persons; grease trap for the kitchen; floor
drain per bath; manhole per sewer turn. Pipe and wire lengths are estimates from the plan route. A price "per
point" usually covers conduit and wire of the branch circuit, not the fitting itself, the breakers or the mains:
ask what the user's price covers. Mains cable and utility meters are normally outside the contract: list them as
excluded.

## Before you call it done
- Every object under `## other` in `takeoff.md` is assigned to a row or dismissed in one line (cars, plants: not BOQ).
- Every work category the house has appears, and things left out are named in `notes` (fence, landscape, AC, …).
- Rows with `⚠` are listed in the report with the question the user or the engineer must answer.
- The quantities are an estimate for pricing, not a design check. The report and the workbook both say that a
  licensed engineer or a quantity surveyor checks them before anyone orders material, sends a quotation or signs a
  contract.
