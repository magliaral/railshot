# rocrail_prep — Lok- und Wagenfotos für Rocrail aufbereiten

Python-Script für **Spur-N-Sammlungen** (1:160), das Studio-Fotos von
Modellbahn-Fahrzeugen so aufbereitet, dass sie in Rocrail beim
Zusammenstellen eines Zugs **lückenlos aneinandergereiht** werden können:

- Pixel-genauer Schnitt links/rechts (Puffer auf Puffer)
- Massstabsgetreue Skalierung über alle Fahrzeuge
- Default-Höhe 80 px (Rocrail-Wiki-Norm)
- Räder auf gemeinsamer Grundlinie (Schienenkante)
- **Optional: digitale Schiene unter den Wagen** (konsistente Optik
  über die ganze Sammlung)
- Optional: Auto-Rotation (Wagen waagrecht)
- Optional: Auto-Perspektive (Stirnseiten senkrecht)
- Optional: Pre-Crop (Studio-ROI vor rembg)
- Transparenter PNG-Hintergrund

## Setup

```powershell
pip install "rembg[cpu]" pillow numpy
pip install opencv-python      # nur für --auto-perspective
pip install scipy              # optional, hilft bei --pre-crop auto
```

Beim ersten Lauf zieht `rembg` automatisch das ONNX-Modell
(~170 MB für `isnet-general-use`, einmalig nach `~\.u2net\`).

## Schnellstart

```powershell
python rocrail_prep.py wagen.jpg -o wagen.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 --auto-rotate --rail
```

Das produziert ein PNG (typisch ~330×84 px wenn Schiene aktiv) mit
transparentem Hintergrund, Wagen unten bündig auf einer Schiene.

## Massstab definieren — der zentrale Wert

Der wichtigste Parameter ist **`--px-per-mm`**: wie viele Pixel pro
Millimeter Modelllänge. Den legst du **einmal** für deine ganze
Sammlung fest und benutzt für alle Bilder denselben Wert.

### Methode A: Über Höhe definieren (empfohlen für Loks mit Pantograph)

1. **Höchste Lok ausmessen** (Schienenoberkante bis Dach mit
   eingefahrenem Pantograph). Z.B. Re 460 ≈ 30 mm.
2. **Soll-Höhe wählen:** sagen wir 60 px (lässt 20 px Reserve oben für
   ausgefahrenen Panto, plus 4 px für Schiene).
3. **Ausrechnen:** `60 px / 30 mm = 2.0 px/mm`

Damit ergibt sich für deine Sammlung:

| Fahrzeug          | Länge (mm) | Output-Breite (bei 2.0 px/mm) |
|-------------------|------------|-------------------------------|
| Astoro Triebkopf  | 172        | 344 px                        |
| EW IV             | 165        | 330 px                        |
| RAe Steuerwagen   | 158        | 316 px                        |
| Re 460            | 116        | 232 px                        |
| Tigerli           | 58         | 116 px                        |

### Methode B: Über Länge definieren

1. **Hauptwagen ausmessen** (z.B. EW IV ≈ 165 mm).
2. **Soll-Breite entscheiden** (z.B. 250 px).
3. **Ausrechnen:** `250 px / 165 mm = 1.515 px/mm`

| px-per-mm | EW IV (165 mm) | Re 460 (116 mm) | Tigerli (58 mm) |
|-----------|----------------|-----------------|------------------|
| 1.515     | 250 px         | 176 px          | 88 px            |
| 1.82      | 300 px         | 211 px          | 105 px           |
| 2.0       | 330 px         | 232 px          | 116 px           |

## Studio-Setup für saubere Resultate

1. **Klarer Hintergrund** rund um den Wagen — keine zweite Lok, keine
   dunklen Wände, keine Bildschirme im Sichtfeld.
2. **Wagen formatfüllend** — möglichst 90 % der Bildbreite, damit rembg
   genug Pixel zum Erkennen hat.
3. **Frontale Sicht** — Kamera parallel zum Wagen ausrichten.
4. **Konstante Beleuchtung** — vermeidet harte Schatten unter dem Wagen.
5. **Helle Unterlage** — kein Schienenstück fotografieren! Das Script
   fügt eine digitale Schiene später hinzu (siehe nächster Abschnitt).

## Digitale Schiene unter dem Wagen

Statt eine Schiene mitzufotografieren (was rembg verwirren kann), legt
das Script eine **konsistente digitale Schiene** unter jeden Wagen.
Vorteile:

- Schiene ist **pixel-genau gleich** auf allen Bildern
- rembg muss sich nicht mit Schienen-Details rumärgern
- Schiene wird **auf Wagenbreite zugeschnitten** (nicht skaliert) —
  Schwellen-Abstände bleiben konstant
- Bei aneinandergereihten Wagen in Rocrail wirkt die Schiene als
  durchgehende Linie

### Verwendung

```powershell
python rocrail_prep.py wagen.jpg -o wagen.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 --auto-rotate `
    --rail
```

Das Script erwartet eine Datei `rail.png` im selben Ordner wie das
Script. Diese ist im Repository mitgeliefert (4 px hoch, 800 px breit).

### Eigene Schiene gestalten

Wenn dir die mitgelieferte Schiene nicht gefällt, erstelle eine eigene
PNG-Datei mit folgenden Eigenschaften:

- **Höhe:** 3-8 px (subtil, sonst dominiert sie das Bild)
- **Breite:** beliebig, mindestens so breit wie der längste Wagen
- **Format:** PNG mit Alpha-Kanal
- **Inhalt:** dein gewünschtes Schienen-Aussehen (Schwellen,
  Schienenkopf, Schotter etc.)

Dann mit `--rail-image meine_schiene.png` einbinden.

### Canvas-Verhalten

- **Default (`--rail-extend`):** Canvas wird unten um Schienenhöhe
  vergrössert. Wagen bleibt unverändert, Schiene kommt drunter dazu.
- **Alternative (`--no-rail-extend`):** Schiene wird über die untere
  Wagenkante gelegt. Canvas-Höhe bleibt gleich, dafür wird ein paar
  Pixel des Wagenbodens überdeckt.

## Pre-Crop: Störquellen ausserhalb des Studios eliminieren

Wenn nicht alles im Bild zum Studio gehört (Kabel, andere Loks am Rand,
Wandelement), kann ein Pre-Crop **vor** rembg viel helfen:

```powershell
python rocrail_prep.py wagen.jpg -o wagen.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 `
    --pre-crop "170,80,1965,820"
```

Format: `"X1,Y1,X2,Y2"` in Pixeln des Originalfotos. Koordinaten kannst
du in Paint, IrfanView oder GIMP auslesen — Maus auf die obere linke und
untere rechte Ecke deines Studios zeigen, Pixelposition ablesen.

Bei festem Studio-Setup misst du die ROI **einmal** und nutzt sie für
alle weiteren Fotos.

Auto-Variante (sucht hellste Region):

```powershell
--pre-crop "auto"
--pre-crop "auto 180"   # mit eigener Helligkeitsschwelle
```

## Auto-Rotation und Auto-Perspektive

```powershell
# Untere Wagenkante auf horizontal ausrichten (sicher)
--auto-rotate

# Plus Stirnseiten gerade ziehen (experimentell, braucht opencv)
--auto-rotate --auto-perspective
```

Beide Korrekturen haben Sicherheitsgrenzen eingebaut: zu starke
Korrekturen (>5° Rotation, >30 px Perspektivverschiebung) werden
ignoriert und das Original bleibt unverändert.

Im Terminal-Output siehst du was passiert ist:

```
OK  ew4.jpg  ->  ew4.png  (330 x 84 px)  [rot -0.64°, bbox 1478x233 (aspect 6.34)]
```

## Batch-Verarbeitung mit lengths.json

`lengths.json` für deine ganze Sammlung anlegen:

```json
{
  "ew4_a_184-7": 165,
  "ew4_b_xxx": 165,
  "re460_001": 116,
  "tigerli": 58,
  "shimms_454": 103
}
```

Schlüssel = Dateiname **ohne** Extension (oder mit, beides geht).
Werte = Modelllänge in mm.

```powershell
python rocrail_prep.py ./fotos -o ./out `
    --mode scale --px-per-mm 2.0 --lengths lengths.json `
    --pre-crop "170,80,1965,820" --auto-rotate --rail
```

## PowerShell-Funktionen für den Alltag

In dein PowerShell-Profil schreiben (`notepad $PROFILE`):

```powershell
function rrp-one {
    param(
        [Parameter(Mandatory)][string]$In,
        [Parameter(Mandatory)][string]$Out,
        [Parameter(Mandatory)][int]$LengthMm
    )
    python rocrail_prep.py $In -o $Out `
        --mode scale --px-per-mm 2.0 --length-mm $LengthMm `
        --pre-crop "170,80,1965,820" --auto-rotate --rail
}

function rrp-batch {
    param(
        [string]$In = "./fotos",
        [string]$Out = "./out"
    )
    python rocrail_prep.py $In -o $Out `
        --mode scale --px-per-mm 2.0 --lengths lengths.json `
        --pre-crop "170,80,1965,820" --auto-rotate --rail
}
```

Aufruf danach:

```powershell
rrp-one -In wagen.jpg -Out wagen.png -LengthMm 165
rrp-batch
```

## Debug-Modus

Falls etwas schiefgeht, alle Zwischenschritte als PNG dumpen:

```powershell
python rocrail_prep.py wagen.jpg -o wagen.png `
    [...andere Optionen...] `
    --debug-dir ./debug --verbose
```

Erzeugt in `./debug/wagen/` einen Ordner mit nummerierten PNG-Dateien:

| Datei                        | Inhalt                          |
|------------------------------|---------------------------------|
| `00_input.png`               | Original-Foto                   |
| `01_pre_crop.png`            | Nach Pre-Crop                   |
| `02_rembg.png`               | Nach Hintergrundentfernung      |
| `03_edge_clean.png`          | Halbtransparente Reste entfernt |
| `04_rotated_+0.64deg.png`    | Nach Auto-Rotation              |
| `05_perspective.png`         | Nach Auto-Perspektive           |
| `06_cropped_NNNNxNNN.png`    | Nach Bbox-Crop (mit Grösse!)    |
| `07_scaled_330x60.png`       | Nach Skalierung                 |
| `08_with_rail_330x64.png`    | Nach Schienen-Hinzufügung       |
| `09_final_330x80.png`        | Final im Canvas                 |

## Output-Datei überprüfen

Manche Bildbetrachter zeigen Transparenz nicht korrekt an. Verifizieren:

```powershell
python -c "from PIL import Image; img = Image.open('test.png'); print('Mode:', img.mode)"
```

Erwartung: `Mode: RGBA`. Bei `Mode: RGB` fehlt der Alpha-Kanal.

Visuell: PyCharm zeigt Transparenz als **graues Schachbrett-Muster**.
Auch GIMP, Paint.NET und Browser machen das richtig.

## Rocrail-Bildgrösse

Das Rocrail-Wiki sagt:
- **Höhe: 80 px** (Norm)
- **Maximale Dateigrösse: 50 KB**
- Format: PNG mit transparentem Hintergrund

Bei den typischen Pixelgrössen im Spur-N-Bereich (200-400 px Breite,
80 px Höhe) liegen freigestellte Wagen-PNGs typischerweise bei 15-35 KB
— die 50 KB Limitation ist also unkritisch.

**Wichtig zur Aneinanderreihung:** in Rocrail definierst du jeden
Wagen mit seiner Modelllänge in mm. Rocrail setzt dann selber die
Wagen zum Zug zusammen anhand dieser Längen. Dein einzelnes Wagen-Bild
braucht also keinen Zug zu enthalten.

## Alle Optionen

| Flag                    | Default             | Zweck                                         |
|-------------------------|---------------------|-----------------------------------------------|
| `--mode`                | `scale`             | `height` oder `scale`                         |
| `--canvas-height`       | `80`                | Output-Pixelhöhe (Rocrail-Norm)               |
| `--max-width`           | —                   | Hard-Cap Breite (height-Modus)                |
| `--px-per-mm`           | —                   | **Massstab: Pixel pro mm Modelllänge**        |
| `--length-mm`           | —                   | Länge des aktuellen Fahrzeugs in mm           |
| `--lengths`             | —                   | JSON mit pro-Datei-Längen                     |
| `--pre-crop`            | —                   | ROI vor rembg (`X1,Y1,X2,Y2` oder `auto`)     |
| `--pre-crop-padding`    | `20`                | Sicherheitsrand um ROI                        |
| `--auto-rotate`         | aus                 | Untere Wagenkante waagrecht stellen           |
| `--min-rotation-deg`    | `0.2`               | Untere Schwelle (sonst keine Rotation)        |
| `--max-rotation-deg`    | `5.0`               | Obere Schwelle (Schutz vor Fehlern)           |
| `--auto-perspective`    | aus                 | Stirnseiten senkrecht stellen                 |
| `--min-perspective-px`  | `1.5`               | Untere Schwelle                               |
| `--max-perspective-px`  | `30`                | Obere Schwelle                                |
| `--h-alpha-threshold`   | `128`               | Strenge horizontal (puffer-genau)             |
| `--v-alpha-threshold`   | `32`                | Strenge vertikal (nachgiebig)                 |
| `--h-min-column-pixels` | `3`                 | Filter gegen Cutout-Artefakte                 |
| `--edge-clean-threshold`| `64`                | Halbtransparenz-Schleier killen               |
| `--pad-left`            | `0`                 | Padding links (= 0 für Rocrail!)              |
| `--pad-right`           | `0`                 | Padding rechts (= 0 für Rocrail!)             |
| `--pad-top`             | `1`                 | Padding oben                                  |
| `--pad-bottom`          | `0`                 | Padding unten                                 |
| **`--rail`**            | aus                 | **Schiene unter Wagen legen**                 |
| **`--rail-image`**      | `rail.png`          | **Pfad zum Schienen-Template**                |
| **`--no-rail-extend`**  | —                   | **Schiene überlagern statt Canvas erweitern** |
| `--align`               | `bottom`            | Ausrichtung im Canvas                         |
| `--model`               | `isnet-general-use` | rembg-Modell                                  |
| `--debug-dir`           | —                   | Zwischenschritte als PNG ablegen              |
| `-v` / `--verbose`      | aus                 | Mehr Debug-Output bei Fehlern                 |

## Modell-Empfehlung für rembg

- `isnet-general-use` — bestes Allround-Resultat, etwas langsamer
- `u2net` — robuster Klassiker
- `u2netp` — schnell und klein, etwas weniger genau

Bei spiegelnden Loks oder feinen Stromabnehmern liefert
`isnet-general-use` meist den saubersten Cutout.

## Fehlerbilder und Lösungen

### Kein transparenter Hintergrund (alles schwarz)

Bekannter rembg-Bug. Das Script hat einen Workaround eingebaut, der bei
Bedarf automatisch greift. Falls trotzdem schwarz statt transparent:

```powershell
pip install "rembg[cpu]" --upgrade
```

### Wagen wird abgeschnitten links/rechts

```powershell
--h-alpha-threshold 96    # weniger streng (default 128)
--pad-left 1 --pad-right 1   # 1 px Sicherheit
```

### Stromabnehmer/Antenne fehlt oben

```powershell
--v-alpha-threshold 16    # nachgiebiger (default 32)
--pad-top 3
```

### Halo-Schleier um Wagen

```powershell
--edge-clean-threshold 96    # strenger (default 64)
```

### Lücken zwischen Wagen in Rocrail

```powershell
--edge-clean-threshold 96 --h-alpha-threshold 160    # beide strenger
```

### Wagen ist im Output zu klein/gross

`--px-per-mm` anpassen. **Aber**: wenn du mittendrin änderst, müssen
**alle** Wagen mit dem neuen Wert neu generiert werden — sonst zerfällt
der gemeinsame Massstab.

### Schiene wirkt zu prominent

Die mitgelieferte `rail.png` durch eine eigene, dezentere Variante
ersetzen. Dünner machen (3 px statt 4 px), Farben gedeckter wählen,
weniger Schwellenkontrast.

### Schiene wird nicht gefunden

```
FileNotFoundError: Schienen-Datei nicht gefunden: ...
```

`rail.png` muss im selben Ordner wie `rocrail_prep.py` liegen — oder
expliziten Pfad mit `--rail-image C:\pfad\zu\schiene.png` angeben.

### Auto-Perspektive macht das Bild schlechter

`--auto-perspective` weglassen. Bei Wagen mit gerundeten Übergängen
schwer zuverlässig zu erkennen — mechanisch korrekt fotografieren ist
robuster.

### Pre-Crop schneidet den Wagen ab

ROI-Koordinaten in Paint/GIMP nochmal nachmessen. Pufferbohlen müssen
**innerhalb** der ROI sein. Tipp: `--pre-crop-padding 30` als
Sicherheitsabstand.

## Workflow-Empfehlung für eine ganze Sammlung

1. **Studio-Setup einmal sauber bauen** und nicht mehr verändern.
2. **Höchste Lok ausmessen** → bestimmt `--px-per-mm`.
3. **Pre-Crop-ROI ausmessen** — einmal, gilt für alle Bilder.
4. **Test-Bild verarbeiten**, Ergebnis in Rocrail ansehen, ggf. Massstab
   nachjustieren.
5. **Schienen-Optik anpassen** — falls nötig `rail.png` editieren.
6. **PowerShell-Funktionen anlegen** mit deinen festen Parametern.
7. **`lengths.json`** für alle Wagen pflegen.
8. **Sammlung im Batch durchlaufen**, Resultate in Rocrail-Image-Ordner.

## Was das Script NICHT macht

- Mehrere Fahrzeuge auf einem Foto erkennen — bitte ein Wagen pro Foto.
- Spiegelung oder Drehen für die "andere Seite" — wenn du das brauchst,
  fotografiere beide Seiten oder nutze Rocrails Mirror-Option.
- Farb-/Helligkeitskorrektur — geht idealerweise schon beim
  Fotografieren (Weissabgleich, gleichmässiges Licht).
- Komplette Züge zusammensetzen — das macht Rocrail selber zur Laufzeit
  anhand der Wagen-Längendefinitionen.
