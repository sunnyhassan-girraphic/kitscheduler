# Kit scheduler

Tracks inventory (engines, components, standalone gear, peripherals,
cables, I/O devices, licenses), groups assets into reusable **kits**, and
books kits (or individual assets, or staff) against **jobs** on a calendar.
Booking a kit locks everything inside it &mdash; including components
nested inside any engine in the kit &mdash; for that date range.

All custom pages (everything except the Django admin) share a dark,
amber-accented theme (`rgb(234, 179, 8)`) defined once in
`templates/inventory/base.html`. The Django admin keeps its own default
styling, but its header links back to the dashboard (click "Kit
scheduler" in the admin's top-left, or use the cog icon in the main nav
to go the other way).

The whole app renders about 20% larger than its raw CSS sizing (via
`zoom: 1.2` on `body` in `base.html`), and the dashboard specifically
renders at 50% (`zoom: 1.5`, scoped with a `:has(.dashboard-page)`
selector) since it benefits from being easy to read at a glance.

Each Job can have its own `custom_color` (a hex color, settable via a
color-wheel picker on the Job admin form). If a job has no custom color,
it falls back to its category's default color, configurable per category
in the admin under **Category colors**. If neither is set, jobs render in
the standard amber accent. See `Job.resolve_color()` in `models.py`.

## How the data model works

```
Asset (Engine)  <───────────────────────────┐
     └─ parent_engine (Sonnet Boxes,        │
        or components directly)             │
Asset (Sonnet Box)  <─────────┐              │
     └─ parent_engine          ├─ nests into ┘
        (components only)      ┘
Asset (Component)

Kit ──< KitAssetTag (through) >── Asset (direct members: Engines, Sonnet Boxes,
                                  and/or any loose asset, including Licenses;
                                  each membership can carry an optional Tag,
                                  e.g. "MAIN")

Job ──< KitBooking >── Kit            (date range; locks the kit + everything in it)
Job ──< AssetBooking >── Asset        (date range; books one asset directly,
                                        no kit needed \u2014 e.g. a License on its own)
Job ──< StaffBooking >── StaffMember  (date range; independent of kits/assets)
```

- **Asset** is one table for eight types: Engine, Sonnet Box, Component,
  Standalone, Peripheral, Cable, I/O Device, License.
  - **Component** assets nest inside a container (`parent_engine`), which
    can be either an **Engine** or a **Sonnet Box**. A GPU inside `UK-ENG-30`
    is a Component with `parent_engine = UK-ENG-30`.
  - **Sonnet Box** assets are themselves containers (they hold Components,
    same as an Engine) and can *also* be nested inside an Engine
    (`parent_engine` pointing at the Engine) so a fully-built Engine can
    show two levels of nesting: Engine → Sonnet Box → Component. An Engine
    cannot nest inside anything, and a Sonnet Box cannot nest inside
    another Sonnet Box.
  - Every asset type, including Engine and Sonnet Box, uses the same
    `make_model` field for "what kind/model this is" (e.g. an Engine's
    `make_model` holds G1/G2/G2 Bantam/G3/NUC11BT/NUC12DCM). There's no
    separate Engine-only type field &mdash; the Engines/Sonnet Boxes pages
    filter and the CSV export both work off `make_model` directly.
  - **License** assets use their real name as the `asset_id` (e.g. `UK 6`,
    not a generated code). `license_type` is a fixed tick-one choice
    (Permanent / Network / Dongle / Software), and `functionalities` is a
    tick-many `ManyToManyField` to `LicenseFunctionality` (a bank managed
    from Settings, seeded with things like SDI In/Out, NDI, Streaming,
    Unreal Render Blade, Viz). `license_duration_start`/`_end` is the
    overall active/expiry window shown on the Timeline; within that window
    the license can still be booked (via `AssetBooking`) against different
    jobs for shorter stretches. The old free-text `license_type`/
    `license_functionality` CharFields are kept on the model for backward
    compatibility but are no longer written to &mdash; a data migration
    (`0006_seed_tags_and_functionalities`) moved existing values across on
    upgrade. A License is a normal Asset otherwise &mdash; it can go in a
    Kit, or be booked directly on a Job on its own via `AssetBooking`, and
    it's unique like any other asset (can't be double-booked).
  - Every asset has a `status` (Available / In use / Needs repair /
    Maintenance / Missing) and an `archived` flag, which is a soft-delete:
    archived assets are hidden from pickers and availability but keep
    their booking history.
- **Kit** is a named, reusable bundle (e.g. "NFL away kit"). Its direct
  members can be Engines, Sonnet Boxes, and/or any other loose asset (a
  spare cable, a License, a standalone monitor), via the `KitAssetTag`
  through model. You do **not** need to add an engine's components (or a
  nested Sonnet Box's components) to the kit separately &mdash;
  `Kit.all_asset_ids()` automatically includes anything nested up to two
  levels deep. Each `KitAssetTag` row can carry an optional `Tag`
  (e.g. "MAIN", "BACKUP") so people know what a given asset is for within
  that specific kit &mdash; the tag bank is managed from Settings and is
  scoped per kit-membership, not per asset globally.
- **Job** is an event with a date range and one of five categories: Prep,
  Rig, TX, Warehouse, Tech development. Categories are just labels &mdash;
  any job can have zero, one, or many kit bookings, asset bookings, and
  staff bookings, in any combination. A Warehouse job might have a person
  but no kit; a TX job might have a kit but no one from the team on it;
  a job can have just a Prep and a Rig with no TX at all.
- **KitBooking** links a Kit to a Job for a date range. A kit (and
  everything inside it) is unavailable on a date if any `KitBooking` for
  that kit covers that date. Double-booking the same kit across
  overlapping windows for different jobs is blocked.
- **AssetBooking** links a single Asset directly to a Job for a date
  range, without going through a Kit &mdash; the main use case is booking
  a License on its own, optionally for one of its ticked functionalities
  (see the Timeline). Same idea as KitBooking, one level down.
- **StaffBooking** links a StaffMember to a Job for a date range,
  completely independent of KitBooking/AssetBooking on the same job.
  Unlike kit/asset bookings, overlapping staff bookings are **allowed**
  (schedules are often provisional) &mdash; the calendar flags the overlap
  visually instead of blocking it.
- **StaffMember** and **StaffBooking** track who's on which job, completely
  independently of kits. A job can have kits with nobody assigned, people
  assigned with no kit, or any mix &mdash; there's no required link between
  a staff booking and a kit booking on the same job. Unlike kit bookings,
  overlapping staff bookings are **allowed** (schedules are often
  provisional) but the calendar flags the overlap with an amber dot so it's
  visible without blocking anyone.
- **Tag** and **LicenseFunctionality** are small banks (name + optional
  color for Tag) editable from the Settings page, so the team doesn't have
  to free-type kit tags or license functionalities each time.

## Local setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Generate a real secret key and put it in `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 3. Database

**Fastest path (no setup):** leave `DATABASE_URL` unset (or delete that line
from `.env`). The project falls back to a local SQLite file automatically.

**Postgres (recommended before relying on this for real):**

Install Postgres locally, then create a database and user:

```bash
psql postgres -c "CREATE USER kitscheduler WITH PASSWORD 'kitscheduler';"
psql postgres -c "CREATE DATABASE kitscheduler OWNER kitscheduler;"
```

Or with Docker, no local Postgres install needed:

```bash
docker run --name kitscheduler-db -e POSTGRES_USER=kitscheduler \
  -e POSTGRES_PASSWORD=kitscheduler -e POSTGRES_DB=kitscheduler \
  -p 5432:5432 -d postgres:16
```

Either way, keep the `DATABASE_URL` line in `.env` pointed at it (the
default in `.env.example` already matches the commands above).

### 4. Migrate and create an admin user

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 5. Run it

```bash
python manage.py runserver
```

- Dashboard (today's agenda, this week's availability, upcoming jobs, items needing attention): <http://localhost:8000/>
- Calendar / booking view: <http://localhost:8000/calendar/> and Staff
  schedule grid at <http://localhost:8000/staff/> &mdash; the two pages
  share the same interaction model:
  - Week or month range; drag across a row to book a date range in one
    motion; book against an existing job or create a brand new one right
    there (name, category, notes, and an optional color override) without
    leaving the dialog.
  - A job spanning multiple days renders as ONE continuous block running
    across those days, not a separate block per day &mdash; the day-column
    grid lines stay visible underneath/through the block the whole way.
    Two overlapping bookings on the same kit/person stack into separate
    rows within the cell rather than hiding each other.
  - Each job renders in its own color: a custom per-job color if one was
    set (in the booking dialog or the admin), otherwise the job's category
    default (configurable in Settings), otherwise amber.
  - Click a job block to see its full details (dates, category, notes,
    everything booked on it). From there: **Delete job** removes it and
    every kit/staff booking on it; **Edit in admin** jumps to the full
    edit form; **Clone** duplicates it into a new "Copy - " job with the
    same kit/person already attached, ready to adjust dates.
  - Today's column is marked with a thicker amber bar across the top of
    its header cell only (not repeated down every row).
  - The name column is drag-to-resize (hover the right edge of the header
    cell); each grid remembers its own width in the browser's local
    storage.
- All inventory, filterable by type/status/search: <http://localhost:8000/assets/>
- Engines with their full nested configuration, filterable by make/model: <http://localhost:8000/engines/>.
  To change which components are nested in an engine, open the engine in the admin and use the
  Components picker in its "Engine details" section &mdash; an available/chosen list (same UX as
  the Kit page's asset picker) scoped to existing, unassigned, non-Engine assets. To add a
  component that doesn't exist as an asset yet, create it first in the main Asset list, then
  come back and select it here.
- Kits as cards (members, nested component counts, booked-today status): <http://localhost:8000/kits/>
- Daily staff agenda (what's on today, jump to any date): <http://localhost:8000/staff/agenda/> &mdash;
  category badges use each job's real resolved color instead of a flat grey.
- Settings (job category default colors; CSV export): <http://localhost:8000/settings/> &mdash;
  reachable via the cog icon in the nav bar. Exporting downloads a zip with four CSVs
  (assets, engine configurations, kits, jobs) for backup/sharing in a spreadsheet &mdash;
  this is a convenience export, not a substitute for real database backups, since
  there's no importer to load it back in.
- Admin (add/edit assets, kits, jobs): <http://localhost:8000/admin/> &mdash; reachable from any page via
  the Settings page's "Open admin" link; clicking "Kit scheduler" in the admin header returns to the dashboard

### 6. (Optional) import the team's real inventory

A one-off import command brings in the team's full inventory export &mdash;
292 real assets (engines, components nested inside their engines,
standalone gear, peripherals, cables, I/O devices) plus 16 licenses from
the team's licensing spreadsheet &mdash; as real `Asset` records with
correct `make_model`, `license_type`, and `license_functionality` fields,
real component IDs (e.g. `UK-GPU-45`), and real license names (e.g. `T9`,
not a generated code).

```bash
# Preview what would be created, without writing anything
python manage.py import_legacy_data --dry-run

# Actually import
python manage.py import_legacy_data
```

It's safe to re-run &mdash; assets are matched by `asset_id`, so running it
twice won't create duplicates. It only seeds assets; you'll still build
kits and jobs yourself once the assets are in.

A few notes on how some fields were derived from the source data, in case
something looks off and you want to check it against the original sheet:
engine hardware type (G1/G2/G2&nbsp;Bantam/G3/NUC11BT/NUC12DCM) was
extracted from a free-text make/model column (e.g. "IP: 192.168.10.10,
Model NUC11BT") into `make_model`, with the IP address kept in the
asset's notes rather than discarded. A handful of engines had no usable
type information in the export at all (`AU-LEN-01`, `UK-LEN-01`,
`UK-ENG-18`) and are imported with `make_model` left blank rather than
guessed. Source status "Broken" maps to this app's "Needs repair".

There's also a small command to create the team's `StaffMember` records
(Dom, Fabio, Sunny, Charlie, Josh):

```bash
python manage.py seed_staff
```

Also safe to re-run.

## Day-to-day usage

1. **Add assets** in the admin (`/admin/inventory/asset/`). Set `parent
   engine` on any Component that's physically mounted inside an Engine
   (or, better, open the Engine itself and use its Components picker).
   Set `make_model` on Engine assets (G1, G2, G3, etc.) so they're
   filterable on the Engines page. For Licenses, use the real name as the
   Asset ID (e.g. `T9`) and fill in `license_type`/`license_functionality`.
2. **Build kits** (`/admin/inventory/kit/`). Add Engines and/or any other
   loose assets (including Licenses) as direct members. Nested components
   come along automatically.
3. **Add staff** (`/admin/inventory/staffmember/`), if not already seeded.
4. **Create jobs** (`/admin/inventory/job/`) with a name, category (Prep /
   Rig / TX / Warehouse / Tech development), and date range.
5. **Book kits, individual assets, and/or staff onto jobs** from the Job
   admin page (three inline sections), or book kits/staff from the
   calendar view at `/calendar/` &mdash; click and drag across a row to
   pick a date range, or click a single empty day, then pick a job and
   confirm dates. Click an existing job block to see its full details
   (dates, category, notes, everything booked on it); from there you can
   jump to "Edit in admin" to change or remove it, or clone it into a new
   job with the same kit/person pre-attached. Kits, direct asset bookings,
   and staff are all booked independently of each other &mdash; a job can
   have a kit with no one assigned, a person with no kit, a License booked
   on its own, or any combination.

## What's deliberately not built yet

- **Asset/Engine/Kit views are read-focused** &mdash; `/assets/`,
  `/engines/`, and `/kits/` are for browsing and filtering; editing kit
  membership, asset details, etc. still happens through the linked admin
  pages rather than inline on those screens.
- The dashboard's "needs attention" card only flags assets with status
  Needs repair or Missing &mdash; it doesn't yet flag overdue maintenance,
  jobs with no kit/staff assigned, or other softer warning signs.
- Direct asset bookings (`AssetBooking`, e.g. a License booked on its own)
  aren't yet shown on the calendar view at `/calendar/` &mdash; only kit and
  staff bookings are. They're fully usable from the Job admin page in the
  meantime.
- Drag-to-book only works within a single row (one kit or one person at a
  time) &mdash; dragging down across multiple rows to book several
  kits/people onto the same job at once was deliberately scoped out.
- Engine type and asset type are free-text/fixed-choice fields rather than
  admin-editable lookup tables, and the admin's Engine/License detail
  fieldsets are always visible (just collapsed) rather than dynamically
  shown only when the relevant asset type is selected.
- No custom UI for managing assets/kits/jobs outside the Django admin (the
  admin covers full CRUD already; a fully custom UI for that is a separate,
  larger follow-up).
- No authentication tiers/roles &mdash; anyone with an admin login can edit
  anything. Add Django's built-in group/permission system if you need to
  restrict who can do what. As long as this stays running only on
  `localhost`/your own laptop, the only people who can log in at all are
  whoever you've created accounts for with `createsuperuser` &mdash; there's
  no self-registration anywhere in the app. Once this moves to somewhere
  reachable on the internet, revisit `ALLOWED_HOSTS`, `DEBUG=False`, and
  serving over HTTPS (see "Deploying" below) so that property still holds.
- The CSV export under Settings is a convenience download (assets, engine
  configurations, kits, jobs) for backup/sharing in a spreadsheet &mdash;
  it is **not** a real backup and there's no importer to load it back in.
  For an actual backup, use `pg_dump` against the Postgres database on a
  schedule.
- No automated tests yet. The model and view logic were exercised manually
  during development (nesting validation, engine type / license field
  rules, kit composition, conflict detection on overlapping bookings,
  inventory/engine view filtering) but a `tests.py` suite should be added
  before this becomes load-bearing.

## Deploying

This is unconfigured for any specific host on purpose. In broad strokes,
for a small team tool, Railway, Render, or Fly.io are all reasonable low-effort
options: provision a Postgres instance, set the same environment variables
from `.env.example` (`DJANGO_SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`,
`DATABASE_URL`), run `python manage.py migrate` and `collectstatic` as part
of the build/deploy step, and run the app behind gunicorn rather than
`runserver`.
