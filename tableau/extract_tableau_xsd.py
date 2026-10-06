"""Copy the workbook schema built into the local Tableau install to .tableau-schema/ (git-ignored).

Tableau loads workbooks against this schema, which is stricter than the published one.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tableau-schema" / "twb_installed.xsd"


def main():
    installs = sorted(Path("C:/Program Files/Tableau").glob("Tableau*/bin/res/tablangres.rcc"), reverse=True)
    if not installs:
        sys.exit("no Tableau install found")
    blob = installs[0].read_bytes()
    best = None
    for m in re.finditer(rb"<xs:schema\b", blob):
        end = blob.find(b"</xs:schema>", m.start())
        doc = blob[m.start():end + len(b"</xs:schema>")]
        if b'name="worksheet-number"' in doc and (best is None or len(doc) > len(best)):
            best = doc
    if best is None:
        sys.exit("workbook schema not found in " + str(installs[0]))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(best)
    print(f"extracted {len(best):,} bytes from {installs[0]}")


if __name__ == "__main__":
    main()
