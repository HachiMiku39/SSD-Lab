#!/bin/zsh
cd "${0:A:h}"
if [[ -x ../"SSD Lab.app"/Contents/MacOS/"SSD Lab" ]]; then
  open ../"SSD Lab.app"
else
  exec python3 -B app.py
fi
