"""Synthetic teaching fixtures, not real businesses, news, or validation data."""
import csv
import io

SCENARIOS = [
    {"id": "pricing", "title": "A competitor cuts prices", "tag": "Competition",
     "description": "Sales are slipping while a rival undercuts your flagship product.",
     "question": "A competitor has cut prices. Should we match them or protect our margins? Compare sales, pricing and our promotion policy.",
     "expected_action": "protect_margin",
     "current": {"units": 760, "price": 100, "unit_cost": 78, "competitor_price": 82},
     "document_title": "Competitor pricing brief & promotion policy",
     "document": "SYNTHETIC CASE: Competitor pricing. A rival reduced the price of its comparable product to SGD 82. Our flagship remains SGD 100. Sales teams report price objections. Promotion policy: do not approve a blanket price cut below a 20% gross margin. Consider a limited bundle, loyalty benefit or targeted experiment before matching a rival. Validate demand elasticity with a holdout group. Finance must approve any price change. A competitor price is not proof of identical product value."},
    {"id": "retention", "title": "Customers are leaving", "tag": "Customers",
     "description": "Churn rises despite healthy product quality and stable pricing.",
     "question": "Why are we losing customers, and should we prioritize retention over new customer acquisition? Use customer metrics and support notes.",
     "expected_action": "retain_customers",
     "current": {"units": 880, "customers": 500, "churned": 95},
     "document_title": "Customer interviews & retention playbook",
     "document": "SYNTHETIC CASE: Customer retention. Exit interviews cite slow onboarding and difficulty reaching support. Customers do not mention product defects as the primary reason for leaving. Retention playbook: contact at-risk customers, shorten onboarding and track cohort churn before increasing acquisition spend. Run a controlled service recovery pilot. Avoid promising that discounts will fix service problems. Customer counts represent active accounts at period start; churned counts are losses during that period."},
    {"id": "quality", "title": "Returns are increasing", "tag": "Product",
     "description": "A product issue is pushing return rates above the normal range.",
     "question": "Product returns have increased. Should we pause promotions and fix product quality before expanding sales? Review returns and quality guidance.",
     "expected_action": "fix_quality",
     "current": {"units": 920, "returns": 145, "unit_cost": 76},
     "document_title": "Quality incident log & returns SOP",
     "document": "SYNTHETIC CASE: Product quality. Recent returns cite a faulty connector in the latest batch. The quality team is investigating supplier consistency. Quality SOP: when return rates exceed 8%, investigate the affected batch, contact the supplier and pause promotion of the affected product pending review. Do not infer causality from return rates alone. Track defect reasons and replacement outcomes. Resume promotion only after quality checks and analyst sign-off."},
    {"id": "expansion", "title": "A new market opens up", "tag": "Market",
     "description": "Growing demand, strong margins and spare capacity suggest a pilot.",
     "question": "Should we pilot expansion into a growing regional market? Check market demand, revenue growth, margins and the expansion policy.",
     "expected_action": "expand_market",
     "current": {"units": 1280, "market_growth": 0.22, "stock_units": 1800, "unit_cost": 63},
     "document_title": "Regional demand brief & expansion policy",
     "document": "SYNTHETIC CASE: Regional market expansion. Distributor interviews suggest growing demand, but willingness to pay has not been independently verified. Expansion policy: use a capped regional pilot when gross margin exceeds 25%, market growth exceeds 10%, customer churn is below 8% and returns are below 5%. Validate distributor economics and customer acquisition cost before committing to a full launch. Maintain a control market and define stop conditions."},
    {"id": "supply", "title": "Supply cannot keep up", "tag": "Operations",
     "description": "Low stock and longer supplier lead times put sales at risk.",
     "question": "Inventory is low and suppliers are late. Should we stabilize supply before spending more on marketing? Compare stock, lead times and supply policy.",
     "expected_action": "stabilize_supply",
     "current": {"units": 1080, "stock_units": 90, "lead_time_days": 34},
     "document_title": "Supplier update & inventory policy",
     "document": "SYNTHETIC CASE: Supply disruption. The main supplier reports a production backlog. Replenishment lead time has increased and stock coverage has fallen. Supply policy: when coverage is below 10 days or supplier lead time exceeds 21 days, prioritize replenishment and qualify a backup supplier. Avoid broad demand-generating promotions until inventory is stable. Confirm lead times directly and compare expedited freight cost against contribution margin."},
]

BASE = dict(units=1000, price=100, unit_cost=65, competitor_price=96, customers=500,
            churned=20, returns=20, market_growth=0.04, stock_units=1200, lead_time_days=7)
HEADERS = ["period", "product", "region", "segment", "units", "revenue", "cost", "returns",
           "customers", "churned", "price", "competitor_price", "market_growth", "stock_units", "lead_time_days"]


def scenario_csv(scenario):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADERS)
    for period in ("previous", "current"):
        metrics = {**BASE, **(scenario["current"] if period == "current" else {})}
        for product, segment, weight in (("Core", "Consumer", 0.5), ("Plus", "Professional", 0.3), ("Lite", "Student", 0.2)):
            units = round(metrics["units"] * weight)
            writer.writerow([period, product, "Singapore", segment, units, units*metrics["price"],
                             units*metrics["unit_cost"], round(metrics["returns"]*weight),
                             round(metrics["customers"]*weight), round(metrics["churned"]*weight),
                             metrics["price"], metrics["competitor_price"], metrics["market_growth"],
                             round(metrics["stock_units"]*weight), metrics["lead_time_days"]])
    return out.getvalue()


def seed(db):
    for scenario in SCENARIOS:
        dataset_id = "demo_" + scenario["id"]
        try:
            db.dataset(dataset_id)
        except ValueError:
            db.import_csv(scenario["title"], scenario_csv(scenario), synthetic=True, dataset_id=dataset_id)
        if not any(d["dataset_id"] == dataset_id for d in db.documents()):
            db.add_document(scenario["document_title"], scenario["document"],
                            "Synthetic teaching fixture · Group 4", dataset_id, synthetic=True)


def public_scenarios():
    return [{k: v for k, v in s.items() if k in ("id", "title", "tag", "description", "question")}
            | {"dataset_id": "demo_" + s["id"]} for s in SCENARIOS]
