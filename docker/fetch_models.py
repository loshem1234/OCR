"""Build-time download of Tesseract's 'best' Ancient Greek model for the scholarly Greek profile.

Pinned to an exact upstream commit and verified by SHA-256: the image build fails rather than silently
installing a different model than the one that was benchmarked.
"""
import hashlib
import os
import sys
import urllib.request

URL = os.environ.get("GRC_BEST_URL", "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/"
                                     "e12c65a915945e4c28e237a9b52bc4a8f39a0cec/grc.traineddata")
SHA256 = os.environ.get("GRC_BEST_SHA256", "dfc9bda286cd9d8755b1832e5731a5425d1cf0803393a5fa6dee078466178cf1")
DEST = os.environ.get("GRC_BEST_DEST", "/usr/share/tesseract-ocr/5/tessdata_best/grc.traineddata")

data = urllib.request.urlopen(URL, timeout=180).read()
digest = hashlib.sha256(data).hexdigest()
if digest != SHA256:
    sys.exit(f"grc.traineddata checksum mismatch: got {digest}, expected {SHA256}")
os.makedirs(os.path.dirname(DEST), exist_ok=True)
with open(DEST, "wb") as f:
    f.write(data)
print(f"installed tessdata_best grc ({len(data):,} bytes) -> {DEST}")
