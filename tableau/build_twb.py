"""Generate the supply chain network dashboard as a packaged Tableau workbook (.twbx).

How it works:
- The workbook is written in Tableau's current XML (version 18.1), in the same form Tableau Public 2026.2.2
  saves (see the HR analytics project, where that form was established).
- Data travels inside the package as a Hyper extract written by make_extract.py from sc_workbench: one long
  table with a record type per subject (Lane, Node, Supplier, Snapshot), so every parameter reaches every view.
- The map is the navigation: clicking a warehouse or lane sets the Warehouse parameter (a parameter action);
  clicking empty map sets it back to All. The map itself ignores that parameter, so it never collapses.
- Parameters: Warehouse, Period, Category and As-of week, combined in two calculated fields, In scope
  (every sheet but the map) and In scope on map.
"""
import argparse
import re
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import duckdb

import make_extract

ROOT = Path(__file__).resolve().parents[1]
NAME = "Supply Chain Network"
OUT = ROOT / "tableau" / f"{NAME}.twbx"
DS = "federated.scworkbench"
CONN = "textscan.scworkbench"
VERSION = "18.1"
HEADER = f"source-build='2026.2.2 (20262.26.0819.2015)' source-platform='win' version='{VERSION}'"
# Format features the workbook declares. Tableau only accepts <edit-parameter-action> (click to set a parameter)
# when ParameterAction and ParameterActionClearSelection are declared here, as Tableau itself writes them.
MANIFEST = ("AnimationOnByDefault", "MarkAnimation", "ObjectModelEncapsulateLegacy", "ObjectModelExtractV2",
            "ObjectModelTableType", "ParameterAction", "ParameterActionClearSelection", "SchemaViewerObjectModel",
            "SetMembershipControl", "SheetIdentifierTracking", "SortTagCleanup", "VConnDownstreamExtractsWithWarnings",
            "WindowsPersistSimpleIdentifiers")
TABLE = "sc_workbench"
DASH = "Network"


def uid(kind, name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"supply-chain/{kind}/{name}")).upper()


def simple_id(kind, name):
    """Stable uuid per sheet, dashboard or window, so two builds are byte-identical."""
    return "<simple-id uuid='{%s}' />" % uid(kind, name)


INK, OCHRE, TEAL, RUST, PLUM = "#2f55b0", "#c27a1a", "#1f8f7a", "#c0533a", "#8456b0"
PAPER, RAIL, RULE, TEXT, MUTED = "#fbfaf7", "#f2efe7", "#e3ded2", "#1d2330", "#5b6170"
SERIF, SANS = "Georgia", "Segoe UI"

TYPE_MAP = {"VARCHAR": "string", "DATE": "date", "DOUBLE": "real", "BIGINT": "integer", "INTEGER": "integer",
            "BOOLEAN": "boolean", "HUGEINT": "integer"}


def q(v):
    return quoteattr(str(v))


def f(name):
    return f"[{DS}].[{name}]"


# ---------------------------------------------------------------- parameters
# id, caption, source column, datatype
PARAMS = [("Parameter 1", "Warehouse", "dc_city", "string"), ("Parameter 2", "Period", "fiscal_year", "string"),
          ("Parameter 3", "Category", "category", "string"), ("Parameter 4", "As-of week", "period_date", "date")]
PARAM_VALUES = {}   # parameter id -> list of values
PRESET = {}         # parameter id -> starting value (test switch)


def param_literal(dtype, value):
    return f"#{value}#" if dtype == "date" else '"' + value + '"'


def param_values():
    con = duckdb.connect(str(ROOT / "data" / "sc.duckdb"), read_only=True)
    out = {}
    for pid, _, col, dtype in PARAMS:
        if dtype == "date":
            rows = con.sql("SELECT DISTINCT period_date FROM sc_workbench WHERE record_type = 'Snapshot' ORDER BY 1").fetchall()
            out[pid] = [str(r[0]) for r in rows]
        else:
            rows = con.sql(f"SELECT DISTINCT {col} FROM sc_workbench WHERE {col} IS NOT NULL ORDER BY 1").fetchall()
            out[pid] = ["All"] + [r[0] for r in rows]
    con.close()
    return out


def param_start(pid, dtype):
    if pid in PRESET:
        return PRESET[pid]
    return PARAM_VALUES[pid][-1] if dtype == "date" else "All"


def param_columns(indent, members=True):
    pad, cols = " " * indent, []
    for pid, caption, _, dtype in PARAMS:
        start = escape(param_literal(dtype, param_start(pid, dtype)), {'"': "&quot;"})
        body = f"{pad}  <calculation class='tableau' formula='{start}' />\n"
        if members:
            body += f"{pad}  <members>\n" + "".join(
                f"{pad}    <member value={q(param_literal(dtype, v))} />\n" for v in PARAM_VALUES[pid]) + f"{pad}  </members>\n"
        typ = "quantitative" if dtype == "date" else "nominal"
        cols.append(f"{pad}<column caption={q(caption)} datatype='{dtype}' name='[{pid}]' param-domain-type='list' "
                    f"role='measure' type='{typ}' value='{start}'>\n{body}{pad}</column>")
    return "\n".join(cols)


def param_dependencies(indent, members=False):
    pad = " " * indent
    return f"{pad}<datasource-dependencies datasource='Parameters'>\n{param_columns(indent + 2, members)}\n{pad}</datasource-dependencies>"


def parameters_datasource():
    return f"""    <datasource hasconnection='false' inline='true' name='Parameters' version='{VERSION}'>
      <aliases enabled='yes' />
{param_columns(6)}
    </datasource>"""


# ---------------------------------------------------------------- calculated fields
SCOPE_COMMON = ("([Parameters].[Parameter 3] = 'All' OR [category] = [Parameters].[Parameter 3]) "
                "AND IIF([record_type] = 'Snapshot', [period_date] = [Parameters].[Parameter 4], "
                "[Parameters].[Parameter 2] = 'All' OR [fiscal_year] = [Parameters].[Parameter 2])")
CALCS = {
    "Calc_InScope": ("In scope", "boolean", "dimension", "nominal",
                     "([Parameters].[Parameter 1] = 'All' OR [dc_city] = [Parameters].[Parameter 1]) AND " + SCOPE_COMMON),
    "Calc_InScopeMap": ("In scope on map", "boolean", "dimension", "nominal",
                        "[record_type] <> 'Supplier' AND " + SCOPE_COMMON),
    "Calc_OTIF": ("OTIF", "real", "measure", "quantitative", "SUM([otif_lines]) / SUM([lines])"),
    # one value per lane across both of its points, so a line is one colour end to end
    "Calc_OTIFBand": ("OTIF band", "string", "measure", "nominal",
                      "IF MIN({EXCLUDE [point_order] : SUM([otif_lines]) / SUM([lines])}) < 0.7 THEN 'Under 70%' "
                      "ELSEIF MIN({EXCLUDE [point_order] : SUM([otif_lines]) / SUM([lines])}) < 0.85 THEN '70 to 85%' "
                      "ELSE '85% or more' END"),
    "Calc_Turnover": ("Inventory turnover", "real", "measure", "quantitative",
                      "365 * SUM([cogs]) / SUM([stock_value_days])"),
    "Calc_LeadTime": ("Fulfilment lead time (days)", "real", "measure", "quantitative",
                      "SUM([lead_days_sum]) / SUM([lines])"),
    "Calc_CarryingLakh": ("Carrying cost (lakh)", "real", "measure", "quantitative", "SUM([carrying_cost]) / 100000"),
    "Calc_StockLakh": ("Average stock (lakh)", "real", "measure", "quantitative",
                       "SUM([stock_value_days]) / SUM([days]) / 100000"),
    "Calc_Products": ("Products", "integer", "measure", "quantitative", "SUM(IIF([record_type] = 'Snapshot', 1, 0))"),
    "Calc_HighRisk": ("Products at high risk", "integer", "measure", "quantitative",
                      "SUM(IIF([risk_band] = 'High', 1, 0))"),
    "Calc_HighShare": ("High-risk share", "real", "measure", "quantitative",
                       "SUM(IIF([risk_band] = 'High', 1, 0)) / SUM(IIF([record_type] = 'Snapshot', 1, 0))"),
    "Calc_RiskBand": ("Stockout risk band", "string", "measure", "nominal",
                      "IF SUM(IIF([record_type] = 'Snapshot', 1, 0)) = 0 THEN 'Delivery city' "
                      "ELSEIF SUM(IIF([risk_band] = 'High', 1, 0)) / SUM(IIF([record_type] = 'Snapshot', 1, 0)) >= 0.05 "
                      "THEN '5% or more at high risk' ELSEIF SUM(IIF([risk_band] = 'High', 1, 0)) "
                      "/ SUM(IIF([record_type] = 'Snapshot', 1, 0)) >= 0.02 THEN '2 to 5%' ELSE 'Under 2%' END"),
    "Calc_Item": ("Product at warehouse", "string", "dimension", "nominal", "[sku] + ', ' + [dc_city]"),
    "Calc_PointSize": ("Map point size", "real", "measure", "quantitative",
                       "IIF(SUM(IIF([record_type] = 'Snapshot', 1, 0)) > 0, 4, 1)"),
    # map tooltips: lane values across both ends of the line
    "Calc_LaneCity": ("Lane city", "string", "measure", "nominal", "MAX({EXCLUDE [point_order] : MAX([city])})"),
    "Calc_LaneOTIF": ("Lane OTIF", "real", "measure", "quantitative",
                      "MIN({EXCLUDE [point_order] : SUM([otif_lines]) / SUM([lines])})"),
    "Calc_LaneLead": ("Lane lead time", "real", "measure", "quantitative",
                      "MIN({EXCLUDE [point_order] : SUM([lead_days_sum]) / SUM([lines])})"),
    "Calc_LaneLines": ("Lane order lines", "integer", "measure", "quantitative",
                       "MIN({EXCLUDE [point_order] : SUM([lines])})"),
    "Calc_PointNote": ("Map point note", "string", "measure", "nominal",
                       "IF SUM(IIF([record_type] = 'Snapshot', 1, 0)) > 0 THEN "
                       "STR(SUM(IIF([risk_band] = 'High', 1, 0))) + ' of ' + STR(SUM(IIF([record_type] = 'Snapshot', 1, 0))) "
                       "+ ' products at high stockout risk this week' ELSE 'Delivery city, served by ' + MIN([dc_city]) END"),
    "Calc_SupOnTime": ("On time", "real", "measure", "quantitative", "SUM([pos_on_time]) / SUM([pos])"),
    "Calc_SupLate": ("Days late vs quoted", "real", "measure", "quantitative",
                     "(SUM([po_lead_days_sum]) - SUM([po_quoted_days_sum])) / SUM([pos])"),
    "Calc_SupDefect": ("Defects", "real", "measure", "quantitative", "SUM([units_rejected]) / SUM([units_received])"),
    "Calc_SupFill": ("Received in full", "real", "measure", "quantitative", "SUM([pos_in_full]) / SUM([pos])"),
}
FORMATS = {"Calc_LaneOTIF": "p0.0%", "Calc_LaneLead": "n0.0", "Calc_LaneLines": "n#,##0", "Calc_OTIF": "p0.0%", "Calc_Turnover": "n0.0", "Calc_LeadTime": "n0.0", "Calc_CarryingLakh": "c\"₹\"#,##0.0\" L\"",
           "Calc_StockLakh": "n#,##0.0", "Calc_HighShare": "p0%", "Calc_SupOnTime": "p0%", "Calc_SupLate": "n0.0",
           "Calc_SupDefect": "p0.0%", "Calc_SupFill": "p0%", "risk_score": "p0%", "cover_days": "n0"}
PALETTES = {}


def fmt_attr(name):
    return f" default-format={q(FORMATS[name])}" if name in FORMATS else ""


def referenced(formula):
    return set(re.findall(r"(?<![.\]])\[([a-z_]+)\]", formula))


CALC_GEO = {}


def geo_attr(name):
    return f" semantic-role='{CALC_GEO[name]}'" if name in CALC_GEO else ""


def calc_columns():
    return "\n".join(f"""      <column caption={q(cap)} datatype={q(dt)}{fmt_attr(name)} name='[{name}]' role={q(role)}{geo_attr(name)} type={q(typ)}>
        <calculation class='tableau' formula={q(formula)} />
      </column>""" for name, (cap, dt, role, typ, formula) in CALCS.items())


# ---------------------------------------------------------------- data source
REMOTE_TYPE = {'string': 129, 'date': 133, 'real': 5, 'integer': 20, 'boolean': 11}
COLTYPES = {}   # field -> (datatype, role, type)
OVERRIDE = {"point_order": ("integer", "dimension", "ordinal")}
GEO = {"lat": "[Geographical].[Latitude]", "lon": "[Geographical].[Longitude]"}
CAPTIONS = {"sku": "Product", "dc_city": "Warehouse", "supplier": "Supplier", "category": "Category",
            "reasons": "Why", "risk_score": "Risk", "cover_days": "Days of cover"}


def cap_attr(field):
    return f" caption={q(CAPTIONS[field])}" if field in CAPTIONS else ""


def base_column(c, t, indent=6):
    dt, role, typ = COLTYPES[c]
    extra = f" aggregation='Avg'" if c in GEO else ""
    sem = f" semantic-role='{GEO[c]}'" if c in GEO else ""
    return f"{' ' * indent}<column{extra}{cap_attr(c)} datatype={q(dt)}{fmt_attr(c)} name={q('[' + c + ']')} role={q(role)}{sem} type={q(typ)} />"


def metadata_records(cols, parent, extract):
    out = []
    for i, (c, t) in enumerate(cols):
        tail = (f"              <object-id>[{TABLE}]</object-id>\n" if not extract
                else "              <collation flag='0' name='binary' />\n" if t == "string" else "")
        agg = "Sum" if t in ("real", "integer") else "Year" if t == "date" else "Count"
        out.append(f"""            <metadata-record class='column'>
              <remote-name>{escape(c)}</remote-name>
              <remote-type>{REMOTE_TYPE[t]}</remote-type>
              <local-name>[{escape(c)}]</local-name>
              <parent-name>{parent}</parent-name>
              <remote-alias>{escape(c)}</remote-alias>
              <ordinal>{i}</ordinal>
              <local-type>{t}</local-type>
              <aggregation>{agg}</aggregation>
              <contains-null>true</contains-null>
{tail}            </metadata-record>""")
    return "\n".join(out)


def datasource(cols):
    rel_cols = "\n".join(f"            <column datatype={q(t)} name={q(c)} ordinal={q(i)} />" for i, (c, t) in enumerate(cols))
    csv_relation = f"""<relation connection='{CONN}' name='{TABLE}.csv' table='[{TABLE}#csv]' type='table'>
          <columns character-set='UTF-8' header='yes' locale='en_US' separator=','>
{rel_cols}
          </columns>
        </relation>"""
    base_cols = "\n".join(base_column(c, t) for c, t in cols)
    palette_insts = "\n".join(instance(fld, deriv)[1].strip().join(["      ", ""]) for fld, deriv in PALETTE_FIELDS)
    graph_relation = csv_relation.replace("\n          ", "\n                  ").replace("\n        </relation>", "\n                </relation>")
    return f"""    <datasource caption='Supply chain' inline='true' name='{DS}' version='{VERSION}'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='{TABLE}' name='{CONN}'>
            <connection class='textscan' directory='Data/{TABLE}' filename='{TABLE}.csv' workgroup-auth-mode='as-is' />
          </named-connection>
        </named-connections>
        {csv_relation}
        <metadata-records>
{metadata_records(cols, f'[{TABLE}.csv]', False)}
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
{base_cols}
{calc_columns()}
      <column caption='{TABLE}' datatype='table' name='[__tableau_internal_object_id__].[{TABLE}]' role='measure' type='quantitative' />
{palette_insts}
      <extract count='-1' enabled='true' object-id='{TABLE}' units='records' user-specific='false'>
        <connection author-locale='en_US' class='hyper' dbname='Data/Extracts/{TABLE}.hyper' default-settings='yes' schema='Extract' sslmode='' tablename='Extract' update-time='10/07/2026 05:00:00 AM'>
          <relation name='Extract' table='[Extract].[Extract]' type='table' />
          <metadata-records>
{metadata_records(cols, '[Extract]', True)}
          </metadata-records>
        </connection>
      </extract>
      <layout dim-ordering='alphabetic' measure-ordering='alphabetic' show-structure='true' />
      <style>
{palette_rules()}
      </style>
{param_dependencies(6)}
      <object-graph>
        <objects>
          <object caption='{TABLE}' id='{TABLE}'>
            <properties context=''>
              {graph_relation}
            </properties>
            <properties context='extract'>
              <relation name='Extract' table='[Extract].[Extract]' type='table' />
            </properties>
          </object>
        </objects>
      </object-graph>
    </datasource>"""


def palette_rules():
    out = []
    for (fld, deriv), mapping in zip(PALETTE_FIELDS, PALETTE_MAPS):
        ref = instance(fld, deriv)[0]
        maps = "\n".join(f"            <map to='{hex_}'>\n              <bucket>&quot;{k}&quot;</bucket>\n            </map>"
                         for k, hex_ in mapping.items())
        out.append(f"""          <encoding attr='color' field='{ref}' type='palette'>
{maps}
          </encoding>""")
    return "        <style-rule element='mark'>\n" + "\n".join(out) + "\n        </style-rule>" if out else ""


# ---------------------------------------------------------------- worksheets
def dep_column(field):
    if field in CALCS:
        cap, dt, role, typ, formula = CALCS[field]
        return (f"            <column caption={q(cap)} datatype={q(dt)}{fmt_attr(field)} name='[{field}]' role={q(role)}{geo_attr(field)} type={q(typ)}>\n"
                f"              <calculation class='tableau' formula={q(formula)} />\n            </column>")
    return base_column(field, None, 12)


def instance(field, deriv):
    """Column-instance name for a field: derivation None/Sum/Avg/User; discrete aggregate calcs get :nk."""
    prefix = {"None": "none", "Sum": "sum", "Avg": "avg", "User": "usr"}[deriv]
    dt, role, typ = COLTYPES[field]
    if deriv == "None" and role == "dimension":
        kind, t = {"ordinal": "ok", "quantitative": "qk"}.get(typ, "nk"), typ
    elif deriv == "User" and typ == "nominal":
        kind, t = "nk", "nominal"
    else:
        kind, t = "qk", "quantitative"
    name = f"[{prefix}:{field}:{kind}]"
    return name, f"            <column-instance column='[{field}]' derivation={q(deriv)} name='{name}' pivot='key' type={q(t)} />"


def shelf(refs, join=" / "):
    if len(refs) <= 1:
        return "".join(refs)
    return f"({refs[0]}{join}{shelf(refs[1:], join)})"


class Pane:
    def __init__(self, mark, color=None, size=None, lod=(), path=None, text=(), label=False, tooltip=None):
        self.mark, self.color, self.size, self.lod, self.path, self.text, self.label = mark, color, size, lod, path, text, label
        self.tooltip = tooltip   # list of lines; each line a list of plain strings and (field, derivation) pairs


class Sheet:
    def __init__(self, name, mark=None, rows=(), cols=(), color=None, text=(), size=None, lod=(), record=None,
                 scope="Calc_InScope", extra_filters=(), sort=None, label=False, hide_labels=(), axes=(), widths=(),
                 panes=None, col_join=" / ", map_style=False, mark_color=INK, cell_font=None):
        self.name, self.rows, self.cols = name, rows, cols
        self.panes = panes or [Pane(mark, color, size, lod, None, text, label)]
        self.record, self.scope, self.extra_filters, self.sort = record, scope, extra_filters, sort
        self.hide_labels, self.axes, self.widths, self.col_join = hide_labels, axes, widths, col_join
        self.map_style, self.mark_color, self.cell_font = map_style, mark_color, cell_font

    def xml(self):
        fields, insts = set(), {}

        def use(field, deriv):
            fields.add(field)
            name, x = instance(field, deriv)
            insts[name] = x
            return f(name[1:-1])

        rows = shelf([use(*r) for r in self.rows], " + " if self.map_style else " / ")
        cols = shelf([use(*c) for c in self.cols], self.col_join)
        pane_xml = []
        for i, p in enumerate(self.panes):
            enc = []
            if p.color:
                enc.append(f"<color column='{use(*p.color)}' />")
            if p.size:
                enc.append(f"<size column='{use(*p.size)}' />")
            for t in p.text:
                enc.append(f"<text column='{use(*t)}' />")
            for d in p.lod:
                enc.append(f"<lod column='{use(*d)}' />")
            if p.path:
                enc.append(f"<path column='{use(*p.path)}' />")
            tip = ""
            if p.tooltip:
                on_card = [x for x in (p.color, p.size, p.path, *p.lod, *p.text) if x]
                runs = []
                for n, line in enumerate(p.tooltip):
                    for part in line:
                        if isinstance(part, tuple):
                            ref = use(*part)
                            if part not in on_card:
                                enc.append(f"<tooltip column='{ref}' />")
                                on_card.append(part)
                            bold = " fontname='Segoe UI Semibold'" if n == 0 else ""
                            runs.append(f"<run fontcolor='{TEXT}'{bold} fontsize='{10 if n == 0 else 9}'><![CDATA[<{ref}>]]></run>")
                        else:
                            runs.append(f"<run fontcolor='{MUTED}' fontsize='9'>{escape(part)}</run>")
                    if n < len(p.tooltip) - 1:
                        runs.append("<run>Æ&#10;</run>")
                tip = ("\n            <customized-tooltip>\n              <formatted-text>\n                "
                       + "\n                ".join(runs) + "\n              </formatted-text>\n            </customized-tooltip>")
            encodings = ("\n            <encodings>\n              " + "\n              ".join(enc) + "\n            </encodings>") if enc else ""
            attrs = ""
            if self.map_style:
                attrs = f" id='{i + 1}'" + f" y-axis-name='{f(instance(*self.rows[i])[0][1:-1])}'" + (" y-index='1'" if i else "")
            color_fmt = "" if p.color else f"\n                <format attr='mark-color' value='{self.mark_color}' />"
            pane_xml.append(f"""          <pane{attrs} selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class={q(p.mark)} />{encodings}{tip}
            <style>
              <style-rule element='mark'>
                <format attr='mark-labels-show' value='{'true' if p.label else 'false'}' />{color_fmt}
              </style-rule>
            </style>
          </pane>""")
        if self.map_style:
            pane_xml.insert(0, """          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='Automatic' />
          </pane>""")

        filters, slices = [], []
        if self.record:
            ref = use("record_type", "None")
            filters.append(f"""          <filter class='categorical' column='{ref}'>
            <groupfilter function='member' level='[none:record_type:nk]' member='&quot;{self.record}&quot;' user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate' />
          </filter>""")
            slices.append(ref)
        ref = use(self.scope, "None")
        filters.append(f"""          <filter class='categorical' column='{ref}'>
            <groupfilter function='member' level='[none:{self.scope}:nk]' member='true' user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate' />
          </filter>""")
        slices.append(ref)
        by_field = {}
        for fld, member in self.extra_filters:
            by_field.setdefault(fld, []).append(member)
        for fld, members in by_field.items():
            ref = use(fld, "None")
            ui = "user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate'"
            if len(members) == 1:
                body = f"            <groupfilter function='member' level='[none:{fld}:nk]' member='&quot;{members[0]}&quot;' {ui} />"
            else:   # several values: a union, as Tableau writes a multi-value filter
                body = (f"            <groupfilter function='union' {ui}>\n"
                        + "".join(f"              <groupfilter function='member' level='[none:{fld}:nk]' member='&quot;{m}&quot;' />\n"
                                  for m in members) + "            </groupfilter>")
            filters.append(f"          <filter class='categorical' column='{ref}'>\n{body}\n          </filter>")
            slices.append(ref)
        axis, header = [], []
        for fld, deriv, scope, title in self.axes:
            axis.append(f"<format attr='title' class='0' field='{use(fld, deriv)}' scope='{scope}' value={q(title)} />")
        if self.map_style:
            axis.append(f"<encoding attr='space' class='1' field='{f(instance(*self.rows[1])[0][1:-1])}' field-type='quantitative' fold='true' scope='rows' type='space' />")
        for fld, deriv, px in self.widths:
            header.append(f"<format attr='width' field='{use(fld, deriv)}' value='{px}' />")
        sort = ""
        if self.sort:
            dim, meas, direction = self.sort
            sort = f"          <computed-sort column='{use(*dim)}' direction='{direction}' using='{use(*meas)}' />\n"
        for fld in list(fields):
            if fld in CALCS:
                fields |= referenced(CALCS[fld][4]) & set(COLTYPES)
        deps = "\n".join(dep_column(x) for x in sorted(fields)) + "\n" + "\n".join(insts[k] for k in sorted(insts))
        mapsources = "          <mapsources>\n            <mapsource name='Tableau' />\n          </mapsources>\n" if self.map_style else ""
        return f"""    <worksheet name={q(self.name)}>
      <table>
        <view>
          <datasources>
            <datasource caption='Supply chain' name='{DS}' />
            <datasource name='Parameters' />
          </datasources>
{mapsources}{param_dependencies(10)}
          <datasource-dependencies datasource='{DS}'>
{deps}
          </datasource-dependencies>
{chr(10).join(filters)}
{sort}          <slices>
{chr(10).join('            <column>' + s + '</column>' for s in slices)}
          </slices>
          <aggregation value='true' />
        </view>
        <style>
{self.style(axis, header)}
        </style>
        <panes>
{chr(10).join(pane_xml)}
        </panes>
        {f'<rows>{rows}</rows>' if rows else '<rows />'}
        {f'<cols>{cols}</cols>' if cols else '<cols />'}
      </table>
      {simple_id('worksheet', self.name)}
    </worksheet>"""

    def style(self, axis, header):
        rules = [f"""          <style-rule element='worksheet'>
            <format attr='font-family' value='{SANS}' />
            <format attr='font-size' value='9' />
            <format attr='color' value='{MUTED}' />{''.join(chr(10) + f"            <format attr='display-field-labels' scope='{sc}' value='false' />" for sc in self.hide_labels)}
          </style-rule>""",
                 """          <style-rule element='gridline'>
            <format attr='line-visibility' scope='cols' value='off' />
            <format attr='line-visibility' scope='rows' value='off' />
          </style-rule>""",
                 f"""          <style-rule element='table'>
            <format attr='background-color' value='{PAPER}' />
          </style-rule>"""]
        if self.cell_font:
            fam, size, col = self.cell_font
            rules.append(f"""          <style-rule element='cell'>
            <format attr='font-family' value='{fam}' />
            <format attr='font-size' value='{size}' />
            <format attr='color' value='{col}' />
            <format attr='text-align' value='left' />
          </style-rule>""")
        rules.append("""          <style-rule element='table-div'>
            <format attr='line-visibility' scope='cols' value='off' />
            <format attr='line-visibility' scope='rows' value='off' />
          </style-rule>
          <style-rule element='header-div'>
            <format attr='line-visibility' scope='cols' value='off' />
            <format attr='line-visibility' scope='rows' value='off' />
          </style-rule>""")
        if self.map_style:
            rules.append("""          <style-rule element='map'>
            <format attr='washout' value='0.4' />
            <format attr='map-style' value='light' />
          </style-rule>""")
        for el, items in (("axis", axis), ("header", header)):
            if items:
                rules.append(f"          <style-rule element='{el}'>\n" + "".join(f"            {x}\n" for x in items) + "          </style-rule>")
        return "\n".join(rules)


OTIF_COLORS = {"Under 70%": RUST, "70 to 85%": OCHRE, "85% or more": TEAL}
RISK_COLORS = {"5% or more at high risk": RUST, "2 to 5%": OCHRE, "Under 2%": TEAL, "Delivery city": "#9aa1b0"}
BAND_COLORS = {"High": RUST, "Watch": OCHRE, "Low": "#c9cfdd"}
PALETTE_FIELDS = [("Calc_OTIFBand", "User"), ("Calc_RiskBand", "User"), ("risk_band", "None")]
PALETTE_MAPS = [OTIF_COLORS, RISK_COLORS, BAND_COLORS]


def sheets():
    kpi = (SERIF, 22, TEXT)
    return [
        Sheet("Map", rows=[("lat", "Avg"), ("lat", "Avg")], cols=[("lon", "Avg")], map_style=True,
              scope="Calc_InScopeMap",
              panes=[Pane("Line", color=("Calc_OTIFBand", "User"),
                          lod=[("lane_id", "None"), ("dc_city", "None")], path=("point_order", "None"),
                          tooltip=[[("dc_city", "None"), " to ", ("Calc_LaneCity", "User")],
                                   ["OTIF ", ("Calc_LaneOTIF", "User"), ", order to delivery ", ("Calc_LaneLead", "User"), " days"],
                                   [("Calc_LaneLines", "User"), " order lines. Click to filter to this warehouse"]]),
                     Pane("Circle", color=("Calc_RiskBand", "User"), size=("Calc_PointSize", "User"),
                          lod=[("map_point", "None"), ("dc_city", "None")],
                          tooltip=[[("map_point", "None")], [("Calc_PointNote", "User")]])]),
        Sheet("KPI OTIF", "Text", record="Lane", text=[("Calc_OTIF", "User")], cell_font=kpi),
        Sheet("KPI turnover", "Text", record="Node", text=[("Calc_Turnover", "User")], cell_font=kpi),
        Sheet("KPI lead time", "Text", record="Lane", text=[("Calc_LeadTime", "User")], cell_font=kpi),
        Sheet("KPI carrying cost", "Text", record="Node", text=[("Calc_CarryingLakh", "User")], cell_font=kpi),
        Sheet("Stockout gauge", "Bar", record="Snapshot", rows=[("dc_city", "None")], cols=[("Calc_Products", "User")],
              color=("risk_band", "None"), hide_labels=["rows"], axes=[("Calc_Products", "User", "cols", "")],
              extra_filters=[("risk_band", "High"), ("risk_band", "Watch")], label=True),
        Sheet("Watch list", "Text", record="Snapshot", extra_filters=[("risk_band", "High")],
              rows=[("Calc_Item", "None"), ("reasons", "None")],
              text=[("risk_score", "Sum")], sort=(("Calc_Item", "None"), ("risk_score", "Sum"), "DESC"),
              widths=[("Calc_Item", "None", 180), ("reasons", "None", 200)]),
        Sheet("Supplier scorecard", "Bar", record="Supplier", rows=[("supplier", "None")],
              cols=[("Calc_SupOnTime", "User"), ("Calc_SupLate", "User"), ("Calc_SupDefect", "User"), ("Calc_SupFill", "User")],
              col_join=" + ", label=True, hide_labels=["rows"],
              sort=(("supplier", "None"), ("Calc_SupOnTime", "User"), "ASC")),
    ]


# ---------------------------------------------------------------- dashboard
W, H = 1440, 900


def z(x, y, w, h):
    return f"x='{round(x * 100000 / W)}' y='{round(y * 100000 / H)}' w='{round(w * 100000 / W)}' h='{round(h * 100000 / H)}'"


ZONE_STYLE = """            <zone-style>
              <format attr='border-color' value='#000000' />
              <format attr='border-style' value='none' />
              <format attr='border-width' value='0' />
              <format attr='margin' value='4' />
            </zone-style>"""
GUTTER = 20
ROOT_STYLE = f"""          <zone-style>
            <format attr='border-color' value='#000000' />
            <format attr='border-style' value='none' />
            <format attr='border-width' value='0' />
            <format attr='margin' value='4' />
            <format attr='margin-top' value='{GUTTER}' />
            <format attr='margin-right' value='{GUTTER}' />
            <format attr='margin-bottom' value='{GUTTER}' />
            <format attr='margin-left' value='{GUTTER}' />
          </zone-style>"""


def text_zone(zid, box, runs):
    body = "".join(f"<run bold='{b}' fontcolor='{c}' fontname='{fn}' fontsize='{s}'>{escape(t)}</run>" + ("<run>Æ&#10;</run>" if nl else "")
                   for t, fn, s, c, b, nl in runs)
    return f"""          <zone {z(*box)} id='{zid}' type-v2='text'>
            <formatted-text>{body}</formatted-text>
{ZONE_STYLE}
          </zone>"""


def sheet_zone(zid, box, sheet):
    return f"""          <zone {z(*box)} id='{zid}' name={q(sheet)} show-title='false'>
{ZONE_STYLE}
          </zone>"""


def label(text):
    return [(text, SANS, 10, TEXT, "false", False)]


def dashboard():
    zones, zid = [], [10]

    def nid():
        zid[0] += 1
        return zid[0]

    zones.append(text_zone(nid(), (24, 14, 560, 36), [("Supply chain network", SERIF, 22, TEXT, "false", False)]))
    zones.append(text_zone(nid(), (24, 52, 560, 22), [
        ("Synthetic FMCG distributor: 4 warehouses, 40 suppliers, 200 products, 36 cities, Apr 2023 to Mar 2026",
         SANS, 9, MUTED, "false", False)]))
    for i, (pid, *_rest) in enumerate(PARAMS):
        zones.append(f"          <zone {z(620 + i * 200, 20, 192, 56)} id='{nid()}' mode='compact' param='[Parameters].[{pid}]' type-v2='paramctrl'>\n"
                     f"{ZONE_STYLE}\n          </zone>")
    # left: the map, then the supplier scorecard
    zones.append(text_zone(nid(), (24, 92, 900, 22), label(
        "Lanes coloured by OTIF, warehouses by the share of products at high stockout risk. Click a warehouse or lane to filter")))
    zones.append(sheet_zone(nid(), (24, 116, 900, 480), "Map"))
    zones.append(text_zone(nid(), (24, 608, 900, 22), label("Supplier scorecard: on time, days late against quoted lead time, defects, received in full")))
    zones.append(sheet_zone(nid(), (24, 632, 900, 252), "Supplier scorecard"))
    # right: KPIs, the stockout gauge, the watch list
    kpis = [("KPI OTIF", "OTIF, order lines"), ("KPI turnover", "Inventory turns a year"),
            ("KPI lead time", "Order to delivery, days"), ("KPI carrying cost", "Carrying cost")]
    for i, (sheet, text) in enumerate(kpis):
        x, y = 944 + (i % 2) * 240, 92 + (i // 2) * 84
        zones.append(text_zone(nid(), (x, y, 232, 22), [(text, SANS, 9, MUTED, "false", False)]))
        zones.append(sheet_zone(nid(), (x, y + 22, 232, 54), sheet))
    zones.append(text_zone(nid(), (944, 268, 472, 22), label("Products at risk of running out in the next 14 days, as of the chosen week")))
    zones.append(sheet_zone(nid(), (944, 292, 472, 150), "Stockout gauge"))
    zones.append(text_zone(nid(), (944, 454, 472, 22), label("Watch list: products at high risk, as of the chosen week")))
    zones.append(sheet_zone(nid(), (944, 478, 472, 406), "Watch list"))
    return f"""    <dashboard name='{DASH}'>
      <style>
        <style-rule element='table'>
          <format attr='background-color' value='{PAPER}' />
        </style-rule>
      </style>
      <size maxheight='{H}' maxwidth='{W}' minheight='{H}' minwidth='{W}' />
      <datasources>
        <datasource name='Parameters' />
      </datasources>
{param_dependencies(6, members=True)}
      <zones>
        <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>
{chr(10).join(zones)}
{ROOT_STYLE}
        </zone>
      </zones>
      {simple_id('dashboard', DASH)}
    </dashboard>"""


def actions():
    """Clicking a warehouse or lane sets the Warehouse parameter; clearing the selection sets it back to All."""
    src = instance("dc_city", "None")[0]
    return f"""  <actions>
    <edit-parameter-action caption='Pick a warehouse on the map' name='[Action1_{uid("action", "warehouse").replace("-", "")}]'>
      <activation type='on-select' />
      <source dashboard='{DASH}' type='sheet' worksheet='Map' />
      <agg-type type='attr' />
      <clear-option type='assign-fixed-value' value='s:LROOT:All' />
      <params>
        <param name='source-field' value='{f(src[1:-1])}' />
        <param name='target-parameter' value='[Parameters].[Parameter 1]' />
      </params>
    </edit-parameter-action>
  </actions>
"""


CARDS = """      <cards>
        <edge name='left'>
          <strip size='160'>
            <card type='pages' />
            <card type='filters' />
            <card type='marks' />
          </strip>
        </edge>
        <edge name='top'>
          <strip size='2147483647'>
            <card type='columns' />
          </strip>
          <strip size='2147483647'>
            <card type='rows' />
          </strip>
        </edge>
      </cards>"""


def build():
    con = duckdb.connect(str(ROOT / "data" / "sc.duckdb"), read_only=True)
    cols = [(c, TYPE_MAP[t.split("(")[0]]) for c, t, *_ in con.sql(f"DESCRIBE {TABLE}").fetchall()]
    con.close()
    PARAM_VALUES.update(param_values())
    for c, t in cols:
        COLTYPES[c] = OVERRIDE.get(c, (t, "measure" if t in ("real", "integer") else "dimension",
                                       "quantitative" if t in ("real", "integer") else "ordinal" if t == "date" else "nominal"))
    for name, (cap, dt, role, typ, _) in CALCS.items():
        COLTYPES[name] = (dt, role, typ)
    ws = sheets()
    windows = [f"""    <window class='worksheet' name={q(s.name)}>
{CARDS}
      {simple_id('window', s.name)}
    </window>""" for s in ws]
    viewpoints = "".join(f"\n        <viewpoint name={q(s.name)} />" for s in ws)
    windows.append(f"""    <window class='dashboard' maximized='true' name='{DASH}'>
      <viewpoints>{viewpoints}
      </viewpoints>
      <active id='-1' />
      {simple_id('window', DASH)}
    </window>""")
    manifest = "".join(f"    <{m} />\n" for m in MANIFEST)
    return f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook {HEADER} xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
{manifest}  </document-format-change-manifest>
  <preferences />
  <style-theme name='clean' />
  <style>
    <style-rule element='animation'>
      <format attr='animation-on' value='ao-off' />
    </style-rule>
  </style>
  <datasources>
{parameters_datasource()}
{datasource(cols)}
  </datasources>
  <mapsources>
    <mapsource name='Tableau' />
  </mapsources>
{actions()}  <worksheets>
{chr(10).join(s.xml() for s in ws)}
  </worksheets>
  <dashboards>
{dashboard()}
  </dashboards>
  <windows source-height='30'>
{chr(10).join(windows)}
  </windows>
</workbook>
"""


def package(out):
    twb = build()
    hyper = ROOT / ".captures" / f"{TABLE}.hyper"
    make_extract.write_extract(hyper)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(Path(out).with_suffix(".twb").name, twb)
        zf.write(hyper, f"Data/Extracts/{TABLE}.hyper")
        zf.write(ROOT / "data" / "model" / f"{TABLE}.csv", f"Data/{TABLE}/{TABLE}.csv")
    return twb


def main():
    ap = argparse.ArgumentParser(description="Generate the packaged Tableau workbook.")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--preset", nargs="*", default=[], metavar="CAPTION=VALUE",
                    help="test switch: start a parameter on a value, e.g. Warehouse=Kolkata")
    ap.add_argument("--twb-only", action="store_true", help="write the .twb next to --out without packaging (for validation)")
    args = ap.parse_args()
    by_caption = {cap: pid for pid, cap, *_ in PARAMS}
    for item in args.preset:
        cap, val = item.split("=", 1)
        PRESET[by_caption[cap]] = val
    if args.twb_only:
        out = Path(args.out).with_suffix(".twb")
        out.write_text(build(), encoding="utf-8")
    else:
        package(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
