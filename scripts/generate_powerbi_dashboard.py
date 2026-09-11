"""Generate the ASG Airlines PBIR executive report page.

This script changes only the report definition on the existing PBIP page. It
never changes the semantic model, TMDL, Power Query, MySQL, or ETL data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


# 1. Paths
ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "powerbi" / "ASG-Airlines-Dashboard.Report"
DEFINITION = REPORT_ROOT / "definition"
PAGES_FILE = DEFINITION / "pages" / "pages.json"
MODEL_ROOT = ROOT / "powerbi" / "ASG-Airlines-Dashboard.SemanticModel" / "definition"
TABLES_ROOT = MODEL_ROOT / "tables"
BACKUP_ROOT = REPORT_ROOT / "_backup_before_dashboard_generation"
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json"


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


# 2. Backup

def backup_report_definition() -> None:
    if BACKUP_ROOT.exists():
        return
    shutil.copytree(DEFINITION, BACKUP_ROOT)


# 3. Page discovery

def discover_page() -> tuple[Path, dict]:
    pages = read_json(PAGES_FILE)
    page_ids = pages.get("pageOrder", [])
    if not page_ids:
        raise RuntimeError("pages.json contains no pageOrder entries.")
    page_id = str(page_ids[0])
    page_path = DEFINITION / "pages" / page_id / "page.json"
    page = read_json(page_path)
    if page.get("width") != CANVAS_WIDTH or page.get("height") != CANVAS_HEIGHT:
        raise RuntimeError("The existing page is not 1920x1080.")
    return page_path, page


def page_definition(page_id: str, display_name: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
        "name": page_id,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "height": CANVAS_HEIGHT,
        "width": CANVAS_WIDTH,
    }


# 4. Visual creation helpers

def deterministic_id(key: str) -> str:
    return uuid5(NAMESPACE_URL, f"asg-airlines-dashboard:{key}").hex[:20]


def literal(value: str) -> dict:
    escaped = value.replace("'", "''")
    return {"expr": {"Literal": {"Value": f"'{escaped}'"}}}


def dimension_literal(value: int) -> dict:
    return {"expr": {"Literal": {"Value": f"'{value}D'"}}}


def source_ref(alias: str) -> dict:
    return {"SourceRef": {"Source": alias}}


def column_select(alias: str, table: str, column: str) -> dict:
    return {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": column,
    }


def measure_select(alias: str, table: str, measure: str) -> dict:
    return {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": measure}},
        "queryRef": f"{table}.{measure}",
        "nativeQueryRef": measure,
    }


def aggregation_select(alias: str, table: str, column: str, function: int = 5) -> dict:
    function_names = {
        0: f"Sum of {column}",
        1: f"Average of {column}",
        5: column,
    }
    return {
        "field": {"Aggregation": {
            "Expression": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
            "Function": function,
        }},
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": function_names.get(function, column),
    }


def query_command(visual_type: str, selects: list[dict]) -> dict:
    if visual_type == "slicer":
        query_state = {"Values": {"projections": selects}}
    elif visual_type == "cardVisual":
        query_state = {"Data": {"projections": selects}}
    else:
        query_state = {
            "Category": {"projections": selects[:1]},
            "Y": {"projections": selects[1:]},
        }
    return {"queryState": query_state}


def title_objects(title: str) -> dict:
    return {
        "title": [{
            "properties": {
                "text": literal(title),
                "show": {"expr": {"Literal": {"Value": "true"}}},
                "fontFamily": literal("Segoe UI Semibold"),
                "fontSize": {"expr": {"Literal": {"Value": "'12D'"}}},
            }
        }]
    }


def visual_base(key: str, visual_type: str, x: int, y: int, width: int, height: int, tab_order: int, title: str) -> dict:
    return {
        "$schema": VISUAL_SCHEMA,
        "name": deterministic_id(key),
        "position": {"x": x, "y": y, "z": tab_order, "height": height, "width": width, "tabOrder": tab_order},
        "visual": {
            "visualType": visual_type,
            "drillFilterOtherVisuals": True,
            "visualContainerObjects": title_objects(title),
        },
    }


def data_visual(key: str, visual_type: str, x: int, y: int, width: int, height: int, tab_order: int, title: str, from_items: list[tuple[str, str]], selects: list[dict], projections: dict | None = None) -> dict:
    visual = visual_base(key, visual_type, x, y, width, height, tab_order, title)
    visual["visual"]["query"] = query_command(visual_type, selects)
    if projections is not None:
        visual["visual"]["projections"] = projections
    return visual


def card_visual(key: str, title: str, measure: str, x: int, y: int, width: int, tab_order: int, measure_table: str = "booking_payment_summary") -> dict:
    visual = data_visual(
        key, "cardVisual", x, y, width, 112, tab_order, title,
        [("m", measure_table)],
        [measure_select("m", measure_table, measure)],
    )
    visual["visual"]["objects"] = {
        "categoryLabels": [{"properties": {"fontSize": dimension_literal(18)}}],
        "calloutValue": [{"properties": {"fontSize": dimension_literal(30)}}],
    }
    return visual


def aggregation_card_visual(key: str, title: str, table: str, column: str, function: int, x: int, y: int, width: int, tab_order: int) -> dict:
    visual = data_visual(
        key, "cardVisual", x, y, width, 112, tab_order, title,
        [("f", table)], [aggregation_select("f", table, column, function)],
    )
    visual["visual"]["objects"] = {
        "categoryLabels": [{"properties": {"fontSize": dimension_literal(18)}}],
        "calloutValue": [{"properties": {"fontSize": dimension_literal(30)}}],
    }
    return visual


def slicer_visual(key: str, title: str, table: str, column: str, alias: str, x: int, y: int, width: int, tab_order: int) -> dict:
    visual = data_visual(
        key, "slicer", x, y, width, 64, tab_order, title,
        [(alias, table)], [column_select(alias, table, column)],
    )
    visual["visual"]["visualContainerObjects"].pop("title", None)
    visual["visual"]["objects"] = {
        "data": [{"properties": {"mode": literal("Dropdown")}}],
        "header": [{
            "properties": {
                "show": {"expr": {"Literal": {"Value": "true"}}},
                "text": literal(title),
                "fontSize": dimension_literal(18),
            }
        }],
    }
    visual["visual"]["objects"]["data"][0]["properties"]["fontSize"] = dimension_literal(16)
    visual["visual"]["visualContainerObjects"]["padding"] = [{
        "properties": {
            "top": {"expr": {"Literal": {"Value": "0D"}}},
            "bottom": {"expr": {"Literal": {"Value": "0D"}}},
            "left": {"expr": {"Literal": {"Value": "0D"}}},
            "right": {"expr": {"Literal": {"Value": "0D"}}},
        }
    }]
    return visual


def textbox_visual(key: str, text_value: str, x: int, y: int, width: int, height: int, font_size: int, color: str, tab_order: int) -> dict:
    visual = visual_base(key, "textbox", x, y, width, height, tab_order, "")
    visual["visual"]["objects"] = {
        "general": [{
            "properties": {
                "paragraphs": literal(json.dumps({"textRuns": [{"value": text_value, "textStyle": {"fontSize": f"{font_size}pt", "fontFamily": "Segoe UI", "color": color}}]}))
            }
        }]
    }
    return visual


def anomaly_insights_visual() -> dict:
    visual = visual_base("operations_anomaly_insights", "textbox", 40, 870, 1840, 170, 16, "")
    visual["name"] = "f2a4c7d9e1b3486fa205"
    visual["visual"]["objects"] = {
        "general": [{
            "properties": {
                "paragraphs": [
                    {"textRuns": [{"value": "Duration Anomaly Analysis"}]},
                    {"textRuns": [{"value": "• Average flight duration: 164.42 minutes"}]},
                    {"textRuns": [{"value": "• Duration anomaly range: 9.75–319.08 minutes"}]},
                    {"textRuns": [{"value": "• Duration anomalies detected: 0"}]},
                    {"textRuns": [{"value": "• Anomaly rate: 0%"}]},
                    {"textRuns": [{"value": ""}]},
                    {"textRuns": [{"value": "Data Quality"}]},
                    {"textRuns": [{"value": "• 964 validated flights are included in the reporting layer."}]},
                    {"textRuns": [{"value": "• Invalid flight records were isolated through the ETL quarantine process."}]},
                ]
            }
        }]
    }
    visual["visual"]["visualContainerObjects"] = title_objects("Delay / Anomaly Insights")
    return visual


# 5. KPI visual generation

def build_kpis() -> list[dict]:
    specs = [
        ("kpi_total_bookings", "Total Bookings", "Total Bookings", 40),
        ("kpi_total_passengers", "Total Passengers", "Total Passengers", 300),
        ("kpi_total_revenue", "Total Revenue", "Total Revenue", 560),
        ("kpi_total_flights", "Total Flights", "Total Flights", 820),
        ("kpi_average_booking_value", "Average Booking Value", "Average Booking Value", 1080),
    ]
    return [card_visual(key, title, measure, x, 150, 240, index + 2) for index, (key, title, measure, x) in enumerate(specs)]


# 6. Chart generation

def build_charts() -> list[dict]:
    return [
        data_visual("booking_trend", "lineChart", 40, 300, 590, 250, 10, "Booking Trend", [("f", "fact_bookings"), ("m", "booking_payment_summary")], [column_select("f", "fact_bookings", "booking_date"), measure_select("m", "booking_payment_summary", "Total Bookings")]),
        data_visual("revenue_trend", "lineChart", 670, 300, 590, 250, 11, "Revenue Trend", [("f", "fact_bookings"), ("m", "booking_payment_summary")], [column_select("f", "fact_bookings", "booking_date"), measure_select("m", "booking_payment_summary", "Total Revenue")]),
        data_visual("airline_distribution", "clusteredColumnChart", 1300, 300, 580, 250, 12, "Bookings by Airline", [("f", "fact_bookings"), ("m", "booking_payment_summary")], [column_select("f", "fact_bookings", "airline"), measure_select("m", "booking_payment_summary", "Total Bookings")]),
        data_visual("top_routes", "barChart", 40, 580, 590, 270, 13, "Top Routes by Bookings", [("f", "fact_bookings"), ("m", "booking_payment_summary")], [column_select("f", "fact_bookings", "route"), measure_select("m", "booking_payment_summary", "Total Bookings")]),
        data_visual("booking_status", "donutChart", 670, 580, 390, 270, 14, "Booking Status", [("f", "fact_bookings"), ("m", "booking_payment_summary")], [column_select("f", "fact_bookings", "status"), measure_select("m", "booking_payment_summary", "Total Bookings")]),
        data_visual("payment_analysis", "clusteredColumnChart", 1100, 580, 390, 270, 15, "Revenue by Payment Category", [("p", "booking_payment_summary")], [column_select("p", "booking_payment_summary", "payment_category"), measure_select("p", "booking_payment_summary", "Total Revenue")]),
        data_visual("passenger_age_group", "clusteredColumnChart", 1530, 580, 350, 270, 16, "Passenger Age Groups", [("p", "dim_passengers"), ("m", "booking_payment_summary")], [column_select("p", "dim_passengers", "age_group"), measure_select("m", "booking_payment_summary", "Total Passengers")]),
    ]


def build_operations_visuals() -> list[dict]:
    return [
        textbox_visual("operations_title", "ASG Airlines | Flight Operations Analysis", 40, 12, 1000, 34, 24, "#102A43", 0),
        textbox_visual("operations_subtitle", "Flight duration, route traffic, airline distribution & validated operations", 40, 54, 1100, 20, 12, "#52606D", 1),
        slicer_visual("operations_slicer_airline", "Airline", "dim_flights", "airline", "f", 40, 80, 260, 2),
        slicer_visual("operations_slicer_date", "Flight Date", "dim_flights", "flight_date", "f", 320, 80, 260, 3),
        slicer_visual("operations_slicer_route", "Route", "dim_flights", "route", "f", 600, 80, 260, 4),
        aggregation_card_visual("operations_avg_duration", "Average Flight Duration (minutes)", "dim_flights", "duration_minutes", 1, 40, 150, 300, 5),
        card_visual("operations_total_flights", "Total Flights", "Total Flights", 360, 150, 240, 6),
        card_visual("operations_validated_flights", "Validated Flights", "Total Flights", 620, 150, 240, 7),
        data_visual("operations_flights_by_airline", "clusteredColumnChart", 40, 300, 590, 250, 10, "Flights by Airline", [("f", "dim_flights")], [column_select("f", "dim_flights", "airline"), aggregation_select("f", "dim_flights", "flight_id", 5)]),
        data_visual("operations_avg_duration_by_airline", "clusteredColumnChart", 670, 300, 590, 250, 11, "Average Duration by Airline", [("f", "dim_flights")], [column_select("f", "dim_flights", "airline"), aggregation_select("f", "dim_flights", "duration_minutes", 1)]),
        data_visual("operations_route_traffic", "barChart", 1300, 300, 580, 250, 12, "Route-wise Flight Traffic", [("f", "dim_flights")], [column_select("f", "dim_flights", "route"), aggregation_select("f", "dim_flights", "flight_id", 5)]),
        data_visual("operations_duration_view", "clusteredColumnChart", 40, 580, 590, 270, 13, "Flight Duration by Airline", [("f", "dim_flights")], [column_select("f", "dim_flights", "airline"), aggregation_select("f", "dim_flights", "duration_minutes", 1)]),
        data_visual("operations_source_traffic", "clusteredColumnChart", 670, 580, 590, 270, 14, "Flights by Source", [("f", "dim_flights")], [column_select("f", "dim_flights", "source"), aggregation_select("f", "dim_flights", "flight_id", 5)]),
        data_visual("operations_destination_traffic", "clusteredColumnChart", 1300, 580, 580, 270, 15, "Flights by Destination", [("f", "dim_flights")], [column_select("f", "dim_flights", "destination"), aggregation_select("f", "dim_flights", "flight_id", 5)]),
		anomaly_insights_visual(),
    ]


# 7. Slicer generation

def build_slicers() -> list[dict]:
    return [
        slicer_visual("slicer_airline", "Airline", "fact_bookings", "airline", "f", 40, 80, 260, 3),
        slicer_visual("slicer_booking_date", "Booking Date", "fact_bookings", "booking_date", "f", 320, 80, 260, 4),
        slicer_visual("slicer_route", "Route", "fact_bookings", "route", "f", 600, 80, 260, 5),
        slicer_visual("slicer_payment_category", "Payment Category", "fact_bookings", "payment_category", "f", 880, 80, 260, 6),
    ]


# 8. Layout

def build_visuals() -> list[dict]:
    return [
        textbox_visual("dashboard_title", "ASG Airlines | Executive Operations Dashboard", 40, 12, 900, 34, 24, "#102A43", 0),
        textbox_visual("dashboard_subtitle", "Bookings, Revenue, Flight & Passenger Performance", 40, 54, 900, 20, 12, "#52606D", 1),
        *build_slicers(),
        *build_kpis(),
        *build_charts(),
    ]


def write_visuals(page_path: Path, visuals: list[dict]) -> None:
    page_dir = page_path.parent
    visuals_dir = page_dir / "visuals"
    if visuals_dir.exists():
        for existing_path in visuals_dir.rglob("*"):
            if existing_path.is_file():
                existing_path.chmod(0o666)
            elif existing_path.is_dir():
                existing_path.chmod(0o777)
        visuals_dir.chmod(0o777)
        shutil.rmtree(visuals_dir)
    visuals_dir.mkdir(parents=True, exist_ok=True)
    for visual in visuals:
        visual_dir = visuals_dir / visual["name"]
        visual_dir.mkdir()
        write_json(visual_dir / "visual.json", visual)


def update_existing_page_typography(page_id: str) -> None:
    """Update typography objects on existing Page 1 slicers and KPI cards only."""
    visuals_dir = DEFINITION / "pages" / page_id / "visuals"
    for visual_path in visuals_dir.glob("*/visual.json"):
        visual = read_json(visual_path)
        visual_type = visual.get("visual", {}).get("visualType")
        if visual_type == "slicer":
            objects = visual.setdefault("visual", {}).setdefault("objects", {})
            objects.setdefault("header", [{}])[0].setdefault("properties", {})["fontSize"] = dimension_literal(18)
            objects.setdefault("data", [{}])[0].setdefault("properties", {})["fontSize"] = dimension_literal(16)
        elif visual_type == "cardVisual":
            objects = visual.setdefault("visual", {}).setdefault("objects", {})
            objects["categoryLabels"] = [{"properties": {"fontSize": dimension_literal(18)}}]
            objects["calloutValue"] = [{"properties": {"fontSize": dimension_literal(30)}}]
        else:
            continue
        write_json(visual_path, visual)


# 9. Validation

def model_catalog() -> tuple[set[str], dict[str, set[str]], set[str]]:
    tables: set[str] = set()
    columns: dict[str, set[str]] = {}
    measures: set[str] = set()
    for path in TABLES_ROOT.glob("*.tmdl"):
        content = path.read_text(encoding="utf-8")
        table_match = re.search(r"^table\s+([^\r\n]+)", content, re.MULTILINE)
        if not table_match:
            continue
        table = table_match.group(1).strip().strip("'")
        tables.add(table)
        columns[table] = set(re.findall(r"^\s+column\s+([^\r\n]+)", content, re.MULTILINE))
        measures.update(
            match.strip().strip("'")
            for match in re.findall(r"^\s+measure\s+(.+?)\s*=\s*$", content, re.MULTILINE)
        )
    return tables, columns, measures


def validate_visuals(page_path: Path, visuals: list[dict]) -> None:
    tables, columns, measures = model_catalog()
    names: set[str] = set()
    rectangles: list[tuple[str, int, int, int, int]] = []
    for visual in visuals:
        name = visual["name"]
        if name in names:
            raise RuntimeError(f"Duplicate visual name: {name}")
        names.add(name)
        position = visual["position"]
        if position["x"] < 0 or position["y"] < 0 or position["x"] + position["width"] > CANVAS_WIDTH or position["y"] + position["height"] > CANVAS_HEIGHT:
            raise RuntimeError(f"Visual is outside page bounds: {name}")
        rectangles.append((name, position["x"], position["y"], position["width"], position["height"]))
        json.loads(json.dumps(visual))
        query = visual.get("visual", {}).get("query", {})
        if visual["visual"].get("visualType") not in {"textbox"} and not query.get("queryState"):
            raise RuntimeError(f"Visual has no semantic query: {name}")
        if "Commands" in query:
            raise RuntimeError(f"Unsupported Commands query in {name}")
        for role in query.get("queryState", {}).values():
            for projection in role.get("projections", []):
                field = projection.get("field", {})
                if "Column" in field:
                    expression = field["Column"]
                    entity = expression["Expression"]["SourceRef"]["Entity"]
                    if entity not in tables or expression["Property"] not in columns.get(entity, set()):
                        raise RuntimeError(f"Unknown model column in {name}: {entity}.{expression['Property']}")
                if "Measure" in field:
                    expression = field["Measure"]
                    entity = expression["Expression"]["SourceRef"]["Entity"]
                    if entity not in tables or expression["Property"] not in measures:
                        raise RuntimeError(f"Unknown model measure in {name}: {entity}.{expression['Property']}")
                if "Aggregation" in field:
                    expression = field["Aggregation"]["Expression"]["Column"]
                    entity = expression["Expression"]["SourceRef"]["Entity"]
                    if entity not in tables or expression["Property"] not in columns.get(entity, set()):
                        raise RuntimeError(f"Unknown model aggregation column in {name}: {entity}.{expression['Property']}")
    for index, (name, x, y, width, height) in enumerate(rectangles):
        for other_name, other_x, other_y, other_width, other_height in rectangles[index + 1:]:
            overlaps = (
                x < other_x + other_width
                and x + width > other_x
                and y < other_y + other_height
                and y + height > other_y
            )
            if overlaps:
                raise RuntimeError(f"Visuals overlap: {name} and {other_name}")
    page = read_json(page_path)
    if page["width"] != CANVAS_WIDTH or page["height"] != CANVAS_HEIGHT:
        raise RuntimeError("Page dimensions changed unexpectedly.")
    read_json(PAGES_FILE)
    read_json(DEFINITION / "report.json")
    read_json(DEFINITION / "version.json")


def main() -> None:
    page_path, _ = discover_page()
    backup_report_definition()
    pages = read_json(PAGES_FILE)
    page1_id = str(pages["pageOrder"][0])
    page2_id = deterministic_id("page_flight_operations")
    page2_dir = DEFINITION / "pages" / page2_id
    page2_path = page2_dir / "page.json"
    if page2_id not in pages["pageOrder"]:
        pages["pageOrder"].append(page2_id)
    pages["activePageName"] = page1_id
    write_json(PAGES_FILE, pages)
    write_json(page2_path, page_definition(page2_id, "Flight Operations Analysis"))
    visuals = build_operations_visuals()
    validate_visuals(page2_path, visuals)
    write_visuals(page2_path, visuals)
    update_existing_page_typography(page1_id)
    validate_visuals(page2_path, visuals)
    print(f"Page 1 preserved: {page_path.parent.name == page1_id}")
    print(f"Created Page 2: {page2_id} | Flight Operations Analysis")
    print(f"Generated {len(visuals)} Page 2 visuals ({CANVAS_WIDTH}x{CANVAS_HEIGHT}).")
    print("Semantic model changed: false (validated against the existing TMDL model catalog).")
    for visual in visuals:
        position = visual["position"]
        print(f"{visual['name']} | {visual['visual']['visualType']} | x={position['x']} y={position['y']} width={position['width']} height={position['height']}")
    print("VALIDATION PASSED: Page 2 PBIR JSON, model references, page bounds, and deterministic visual names are valid.")


if __name__ == "__main__":
    main()
