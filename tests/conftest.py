"""Tes memakai geografi sintetis yang deterministik, terlepas dari data/kampung.csv di laptop ini.
Tes geografi nyata (test_geografi_nyata) mengarahkan ulang DATA_DIR ke folder data/ sendiri."""
import os
import tempfile

os.environ.setdefault("SINTETIS_DATA_DIR", tempfile.mkdtemp(prefix="sintetis_data_kosong_"))
