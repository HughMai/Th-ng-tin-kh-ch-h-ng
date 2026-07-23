# Design System — Sổ Thu Chi ("Quiet Ledger", teal)

This app **inherits** htp-crm's design system wholesale. [`../htp-crm/DESIGN.md`](../htp-crm/DESIGN.md)
is the parent document and stays authoritative for anything not restated here: the type scale,
the ink ramp, spacing, touch targets, motion, and the contrast rules all carry over unchanged.

This file records only what is **different**, and why. As in the CRM: this file is the source of
truth for *why*, [`static/app.css`](static/app.css) is the source of truth for *what*. If they
disagree, `app.css` is the bug.

**The one thing to remember:** *"Nhập xong trong mười giây."* Every decision below is tested
against that sentence. Speed of entry beats completeness of data, reporting depth, and polish.

## Product Context

- **What this is:** the family's daily money book. Manual Thu (money in) and Chi (money out),
  by day and by month, with every đồng customers paid into the CRM flowing in automatically.
- **Who it's for:** two people — a parent (fifties, mid-range Android, one-handed, often in the
  xưởng) and an outside kế toán (desk, wants the month view). No customer ever sees this app.
- **Space:** the category is Vietnamese sổ thu chi apps (MISA SME, Sổ Thu Chi, MoneyLover). They
  are category-first: you pick a danh mục before you can type a number. This one is amount-first.
- **Project type:** internal tool, mobile-first. No document surface, no print view, no export.
- **Primary viewport:** 390×844.

## What differs from the parent system

### 1. Brand ramp is teal, not navy

The only token change. Two apps from the same family sit side by side on the same phone home
screen; identical navy tiles would get opened wrong, and entering a job's cost into the CRM (or
a customer into the sổ) is a costly mistake to unwind.

| Token | CRM (parent) | Here | Contrast |
|---|---|---|---|
| `--brand` | `#0f4c81` | `#0e6866` | 6.6:1 with white |
| `--brand-deep` | `#0c3d68` | `#0b5250` | 7.9:1 on `--brand-soft` |
| `--brand-soft` | `#eef3f8` | `#e8f2f1` | — |
| `--brand-tint` | `#dce9f4` | `#d3e7e6` | focus ring only |

Teal was chosen over the other obvious options because it stays clear of the two colors that
already carry meaning in this app: green is Thu, red is Chi (below). A green brand would make
every screen read as "money in"; a red one as an error state. `--zalo` is dropped — there are
no Zalo actions here.

**Identity:** app name "Sổ Thu Chi", PWA short name "Thu Chi", teal `TC` icon tile.

### 2. Money color is semantic, and money is the only thing that gets colored

- **Thu** → `--ok-text` `#146c34`. **Chi** → `--danger-text` `#b91c1c`.
- This holds everywhere: entry amounts, day totals, month ledger columns, and the Thu/Chi
  segmented toggle (which is the one segmented control that ignores `--brand` and takes the
  money colors instead — the control *is* the money decision).
- Red here means "tiền ra", **not** "error". Errors use callouts, never colored numbers.
- Nothing else gets a status color. No colored chips for methods, no colored row backgrounds.

### 3. The entry form is the hero, not the data

The CRM opens on a reading surface (Hôm nay). This app opens on a **writing** surface: the
quick-entry card is the first thing on the day view, above the totals and the list.

- The amount input is `--fs-2xl` (27px, 700, `tabular-nums`, centered, 64px tall) — the top of
  the inherited scale. Per the parent system, "the largest thing on a screen is always money";
  here the money being typed is that thing. No token above 27px was invented for it.
- `autofocus` + `inputmode="numeric"` — the keypad is up before the first tap.
- Defaults are pre-chosen so a typical entry is *amount → save*: **Chi**, **Tiền mặt**, today.
  Chi is the default because Thu mostly arrives from the CRM on its own.
- Grouping dots are applied **on blur, never on keystroke**. This is inherited from a real CRM
  bug: rewriting `.value` mid-keystroke makes GBoard re-commit the digit just typed, which turned
  `3000000` into `3333000000`. See `static/app.js`.

### 4. CRM rows look like what they are

A row that came from the CRM carries a small `CRM` tag in `--brand-soft`/`--brand-deep`, shows the
customer's name and whether it was đặt cọc or thanh toán, and has **no Sửa link**. The absence of
the edit affordance is the design: those numbers are fixed in the CRM, and this app re-reads live.

### 5. Two screens, one nav

Bottom nav is **Hôm nay** / **Tháng** only. The day view is the app; the month view is the
accountant's read. No dashboard, no charts, no filters, no search. The month ledger prints every
day of the month including empty ones, so it reads like a calendar rather than a filtered list —
an empty day is information ("nothing happened"), not an absence.

## Carried over unchanged (do not re-decide)

Be Vietnam Pro self-hosted 400/500/600/700, `tabular-nums` on all money · 16.5px body · warm
paper `#f4f3f1` + brown-grey ink ramp · `--ink-faint` `#6f6a64` as the AA floor · 4px spacing
scale · 48px primary touch targets · hierarchical radii · one hairline instead of shadows ·
140ms `ease-out`, background/border/opacity only, never transform · **light mode only,
permanently** · minimum 4.5:1 text contrast on both `--paper` and `--surface`.

## Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-23 | Inherit "Quiet Ledger" rather than design fresh | The parent already uses the CRM daily; a second visual language is a second thing to learn for zero benefit. Also the cheapest correct answer. |
| 2026-07-23 | Teal brand ramp (`#0e6866`) | Distinguishes the two home-screen icons, and stays clear of the green/red that carry money meaning here. 6.6:1 on white. |
| 2026-07-23 | Thu green / Chi red everywhere, and nowhere else | The single fastest read on the screen. Costs the ability to use status colors decoratively — accepted. |
| 2026-07-23 | Entry form above the totals | North star is "nhập 10 giây". Reading is secondary; the CRM already owns reporting. |
| 2026-07-23 | Amount field capped at `--fs-2xl` (27px) | Wanted 34px, but inventing a size above the inherited scale forks the system for one field. 27px in a 64px field is already the largest element on the page. |
| 2026-07-23 | No categories, no số dư | Locked product decisions (see `CLAUDE.md`); both would slow entry, which is the one thing this app optimizes. |
