#!/usr/bin/env python3
"""One-shot: scaffold all 20 Accelerate 2.0 projects from Hubble snapshot."""

import json
import re
from pathlib import Path
from datetime import date

TODAY = date.today().isoformat()
ROOT = Path(__file__).resolve().parent.parent

PROJECTS = [
    {"name": "Vixxo", "slug": "vixxo", "products": "Payments, Checkout, Connect, Tax", "ae": "Duncan Walsh", "project_id": 45266078},
    {"name": "I Am Transformation", "slug": "i-am-transformation", "products": "Payments", "ae": "Scott Fournier", "project_id": 45243544},
    {"name": "HERMEQ", "slug": "hermeq", "products": "Payments", "ae": "Dylan Meagher", "project_id": 45334578},
    {"name": "North American Software Associates", "slug": "north-american-software-associates", "products": "Connect", "ae": "Kat Bunting", "project_id": 45282030},
    {"name": "Trust Event Solutions", "slug": "trust-event-solutions", "products": "Connect", "ae": "Mark Daml", "project_id": 45369427},
    {"name": "Baltimore Community Foundation", "slug": "baltimore-community-foundation", "products": "Payments", "ae": "Kat Bunting", "project_id": 45252195},
    {"name": "KonstructIQ", "slug": "konstructiq", "products": "Connect, Treasury, Issuing", "ae": "Matthew Bowman", "project_id": 45355156},
    {"name": "Tipz", "slug": "tipz", "products": "Connect", "ae": "Hughes Reece", "project_id": 45287384},
    {"name": "Practice Better", "slug": "practice-better", "products": "Unified Auth (UA2 Migration)", "ae": "Elise Wassmann", "project_id": 45287198},
    {"name": "Drugpak", "slug": "drugpak", "products": "Payments", "ae": "Drew Sanders", "project_id": 45273432},
    {"name": "FixifyPMS", "slug": "fixifypms", "products": "TBD", "ae": "Eitan Dombey", "project_id": 45300230},
    {"name": "Akatia", "slug": "akatia", "products": "Connect, Billing", "ae": "Mark Daml", "project_id": 45287741},
    {"name": "HomeMinder", "slug": "homeminder", "products": "Billing, Connect", "ae": "Navya Kumar", "project_id": 45270482},
    {"name": "Self Storage Facility Management", "slug": "self-storage-facility-management", "products": "Connect", "ae": "Sabine Rizvi", "project_id": 44619951},
    {"name": "DrivewayPass", "slug": "drivewaypass", "products": "Connect", "ae": "Drew Sanders", "project_id": 45262511},
    {"name": "Hospital of Emotions", "slug": "hospital-of-emotions", "products": "Payments", "ae": "Stephen Dennis", "project_id": 45296186},
    {"name": "Beacon", "slug": "beacon", "products": "Connect", "ae": "Darby Sween", "project_id": 45304780},
    {"name": "Diagonal", "slug": "diagonal", "products": "Connect", "ae": "Mark Daml", "project_id": 45348904},
    {"name": "Engage Raise", "slug": "engage-raise", "products": "Connect, Payments, Link, OCS", "ae": "Alex Kallis", "project_id": 45303924},
    {"name": "JobFlow", "slug": "jobflow", "products": "Connect", "ae": "Mark Daml", "project_id": 45404112},
]


def product_activation_lines(products_str):
    return "\n".join(f"- [ ] {p.strip()}" for p in products_str.split(","))


def write_project(p):
    slug = p["slug"]
    name = p["name"]
    products = p["products"]
    ae = p["ae"]
    project_id = p["project_id"]

    folder = ROOT / "projects" / "active" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "issues").mkdir(exist_ok=True)
    (folder / "drafts").mkdir(exist_ok=True)
    (folder / "raw").mkdir(exist_ok=True)

    # PROJECT.md
    project_md = f"""# {name}

## Overview
- **Account ID(s)**: TBD
- **Products**: {products}
- **Status**: Integration
- **Priority**: Medium
- **Started**: TBD
- **Due**: TBD
- **AONR**: TBD
- **SFDC Opportunity Owner**: {ae}

## Key Contacts
- TBD

## Communication
- **Email search**: TBD
- **Slack channels**: TBD
- **Stripe contacts**: Diego Ramirez Levy

## External Links
- Handover: TBD
- Manifest: TBD
- Salesforce: TBD
- Kantata Project ID: {project_id}
- Kantata Workspace: https://app.mavenlink.com/workspaces/{project_id}
- CSAT: TBD

## Product Activation
{product_activation_lines(products)}

## Notes
Scaffolded {TODAY} from Hubble snapshot (Accelerate 2.0). Populate Account ID, Key Contacts, and External Links from handover thread or Salesforce.
"""
    (folder / "PROJECT.md").write_text(project_md)

    # timeline.md
    timeline_md = f"""## {TODAY} — Setup

- **Source**: Hubble snapshot (Accelerate 2.0 scaffold)
- **Summary**: Project scaffolded. Products: {products}. AE: {ae}. Kantata ID: {project_id}. Pending: Account ID, Key Contacts, Handover/Salesforce links.
"""
    (folder / "timeline.md").write_text(timeline_md)

    # action-items.md
    (folder / "action-items.md").write_text("# Action Items\n\n## Open\n\n## Closed\n")

    # raw/comms.md
    (folder / "raw" / "comms.md").write_text(f"# {name} — Communications\n\n")

    # asana.json
    asana = {"task_gid": "REPLACE", "project_gid": "REPLACE", "section": "REPLACE", "subtask_gids": {}}
    (folder / "asana.json").write_text(json.dumps(asana, indent=2) + "\n")

    # scan-state.json
    scan_state = {"last_email_scan": None, "last_slack_scan": None, "logged_email_ids": [], "logged_slack_thread_ids": []}
    (folder / "scan-state.json").write_text(json.dumps(scan_state, indent=2) + "\n")

    print(f"  [OK] {slug}")


if __name__ == "__main__":
    print(f"Scaffolding {len(PROJECTS)} Accelerate 2.0 projects...")
    for p in PROJECTS:
        write_project(p)
    print(f"\nDone. Run sync-to-asana.py then hubble-reconcile --backfill for each slug.")
