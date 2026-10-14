# Open questions for the lab / Laboratuvara sorulacaklar

The Excel export (`app/export_groups.py`) works with the three assumptions below. Each one is a single place in the code, so changing it later is quick.
Excel çıktısı aşağıdaki üç varsayımla çalışıyor; her biri kodda tek bir yerde, değiştirmesi kolay.

## 1. What exactly is "Çap" (diameter)? / "Çap" tam olarak nasıl ölçülüyor?

- **Assumption / Varsayım:** Çap = equivalent circular diameter (ECD) = `2·√(area/π)` of the cell-covered region.
- **Why we are not sure:** in the lab's sample workbook the Çap does not equal the ECD of the same row. Example (first Piramit row): Alan 13283.6 → ECD would be 130.1, the sheet says 125.2.
- **Ask / Sor:** How was Çap measured in ImageJ? Feret (max), min Feret, mean of the two axes of the fitted ellipse, or something else? Same for Çevre: traced outline or fitted ellipse?
- **Where to change:** `METRICS` in `app/export_groups.py` (`'ecd'` → `'feret_max'` etc.; Feret max is already measured). A min-Feret / ellipse-axis value would need one small addition in `app/measure.py`.

## 2. Which region goes into the Excel? / Excel'e hangi bölge yazılıyor?

- **Assumption:** the **cell-covered** region (`cells`, the pixels assigned to the object), not the envelope (smooth outline around gaps) and not the dense core.
- **Ask:** In ImageJ, did the lab trace only the cells, or the whole outline of the spheroid including gaps between cell clumps? If the whole outline, the envelope may match the lab's numbers better.
- **Where to change:** `REGION = 'cells'` in `app/export_groups.py` (`'envelope'` or `'core'`).

## 3. Meaning of the two numbers in the file names / Dosya adındaki iki sayı

- **Assumption:** the first number group 600 / 400 / 200 is the **well size**, the second 50 / 100 / 150 / 200 is the **cell concentration**. The order inside the name varies (`o 600 50 d5`, `OVAL 50 600 D1`), so the app decides by value; a lone `200` (e.g. `p 200 200`, `p 200 100`) is resolved from the other number, and anything unclear is left empty and highlighted as "needs labels".
- **Evidence:** in the sample workbook, the Piramit Çap grows with the second number, which fits "concentration".
- **Ask:** Is that right? Are there other well sizes or concentrations than 600/400/200 and 50/100/150/200, and other days than 1/3/5/7?
- **Where to change:** `app/labels.py` (`SIZES`, `CONCS`, `DAYS`).

## Not reproduced / Üretilmeyen

- The sheet **"Ayrılmış Analiz"** in the lab's workbook: its purpose is unknown, so it is not generated. Ask what it contains and whether it is needed.
