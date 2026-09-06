# Web-Ready Exporter — pikaohje suomeksi

Täysi dokumentaatio englanniksi: [README.md](README.md).

**Mitä se tekee.** Vie Blender-mallin selaimeen yhdellä napilla: auditoi ongelmat, siivoaa ja desimoi
kopiot (alkuperäinen scene ei muutu), pakkaa GLB:n Dracolla ja WebP:llä, lukee GLB:n takaisin ja **kaatuu,
jos yksikin solmunimi katosi**. `Anchor_`-, `Hotspot_`- ja muut suojatut etuliitteet eivät koskaan yhdisty.
Raportti `.report.md` + `.report.json` tulee GLB:n viereen.

**Asennus.** Lataa `web_ready_exporter-x.y.z.zip` [Releases-sivulta](https://github.com/MikkoNurminenn/web-ready-exporter/releases)
→ Blender → Edit → Preferences → Get Extensions → ▾ → *Install from Disk…*. Paneeli: 3D-näkymä → N → **Web Export**.

**Käyttö GUI:ssa.** Aseta GLB-polku, kolmiobudjetti ja tekstuurimaksimi → **Audit** (ei muuta mitään) →
**Export web GLB** → **Open report**. Yksityiskohdat konsolissa.

**Headless (Codex, Claude, CI):**

```bash
blender -b scene.blend --python wre_cli.py -- --out out/model.glb --tris 300000 --tex 1024 --strict
```

Onnistuessa viimeinen rivi on `WRE_OK {...}` ja exit-koodi 0; epäonnistuessa `WRE_FAIL …` ja exit-koodi 1.
Aja ensin `--audit`, lue ERROR/WARN-rivit, sitten vienti. Konfiguraattorimalleissa **älä** käytä `--merge`,
koska sovellus hakee osia nimellä. Lue tulos `model.report.json`:sta, älä tuo GLB:tä takaisin Blenderiin
mittaamaan (glTF halkoo verteksit saumoista ja topologia näyttää rikkinäiseltä).

**Testi:** `tests/run_tests.sh "/Applications/Blender.app/Contents/MacOS/Blender"` — yksi Blender kerrallaan.
