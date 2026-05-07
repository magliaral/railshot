---
title: railshot
emoji: 🚂
colorFrom: green
colorTo: red
sdk: streamlit
sdk_version: 1.30.0
app_file: app.py
pinned: false
license: mit
short_description: Prepare model railway photos for Rocrail / iTrain
---

# railshot — Hugging Face Spaces version

This is the Hugging Face Spaces deployment of [railshot](https://github.com/magliaral/railshot).

Upload a photo of a model railway vehicle (locomotive or coach) and get back
a clean, scaled, transparent PNG ready for use in Rocrail or iTrain.

**Settings explained:**

- **Pixels per mm:** scale factor. Set this once for your whole collection
  and keep it constant. E.g. `2.0` means a 165 mm long coach becomes 330 px
  wide.
- **Model length (mm):** the actual length of the vehicle in the photo.
  Measure on the model with calipers, buffer beam to buffer beam.
- **Auto-rotate:** levels the bottom of the model if it's slightly tilted.
- **Add digital rail:** overlays a rail underneath, so wheels visually rest
  on a track.

For the full CLI, batch processing, custom rail templates, etc., see the
[GitHub repo](https://github.com/magliaral/railshot).

## Privacy

This Space processes your image on Hugging Face's servers. Photos are
processed in memory and not stored persistently. For full privacy, run
railshot locally — it works the same way without any cloud connection.

## License

MIT
