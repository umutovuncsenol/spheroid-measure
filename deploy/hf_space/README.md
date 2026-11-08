---
title: Spheroid Review
emoji: 🔬
colorFrom: blue
colorTo: green
sdk: gradio
app_file: space_app.py
python_version: "3.12"
pinned: false
---

# Spheroid Review (online)

Upload microwell photos, set the scale, detect the spheroids with the fine-tuned Cellpose model,
review the outlines and download the lab's Excel.

The Space uses the free Gradio SDK only as a Python runtime: `space_app.py` starts the app's own web
server (Flask + gunicorn) on port 7860.

Source code: https://github.com/umutovuncsenol/spheroid-measure
