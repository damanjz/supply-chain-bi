"""Validate a .twb against Tableau's published XSD (github.com/tableau/tableau-document-schemas).

The XSD imports a 'user' namespace schema that isn't published; a permissive stub stands in for it.
Usage: python tableau/validate_twb.py path/to/workbook.twb [path/to/schema.xsd]
"""
import sys
import tempfile
from pathlib import Path

import xmlschema

USER_NS = "http://www.tableausoftware.com/xml/user"
STUB = f"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="{USER_NS}" elementFormDefault="qualified">
  <xs:attributeGroup name="UserAttributes-AG"><xs:anyAttribute processContents="lax"/></xs:attributeGroup>
  <xs:element name="localizable"><xs:complexType><xs:anyAttribute processContents="lax"/></xs:complexType></xs:element>
</xs:schema>"""
PUBLISHED_XSD = Path(__file__).resolve().parents[1] / "tableau" / "schema" / "twb_2026.2.0.xsd"
# Tableau validates against the schema built into the installed product, which is stricter than the
# published one. extract_tableau_xsd.py pulls it from the local install (it is never committed).
INSTALLED_XSD = Path(__file__).resolve().parents[1] / ".tableau-schema" / "twb_installed.xsd"
DEFAULT_XSD = INSTALLED_XSD if INSTALLED_XSD.exists() else PUBLISHED_XSD


def validate(twb, xsd=DEFAULT_XSD):
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "user.xsd"
        stub.write_text(STUB, encoding="utf-8")
        # Tableau's built-in schema has two malformed groups (Sort-G, ShelfSorts-G) that Tableau itself tolerates
        schema = xmlschema.XMLSchema(str(xsd), locations={USER_NS: str(stub)}, validation="lax")
        errors = list(schema.iter_errors(str(twb)))
    return errors


if __name__ == "__main__":
    errs = validate(sys.argv[1], *(sys.argv[2:3] or [DEFAULT_XSD]))
    for e in errs[:15]:
        print(f"- {e.path}: {e.reason}")
    print("XSD OK" if not errs else f"XSD FAIL: {len(errs)} errors")
    sys.exit(1 if errs else 0)
