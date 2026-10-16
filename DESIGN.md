---
name: Dify AutoPE
description: Independent evaluation control room with explicit release-risk gates.
colors:
  ink: "#241d3d"
  muted: "#706a84"
  orchid: "#7657f6"
  orchid-deep: "#583ccf"
  orchid-soft: "#eee9ff"
  rose: "#df5abb"
  cloud: "#f7f6ff"
  panel: "rgba(255, 255, 255, .8)"
  line: "rgba(105, 83, 196, .12)"
  status-orchid: "#8261f6"
  status-lilac: "#aa89f7"
  status-pink: "#e1a8df"
  go-start: "#6251d7"
  go-end: "#9b7be8"
  no-go-start: "#b13467"
  no-go-end: "#dc6a99"
  success: "#087a64"
  error: "#b43268"
  radar-baseline: "#8568ff"
  radar-candidate: "#e35cbe"
typography:
  page-header:
    fontFamily: "Noto Sans SC Variable, PingFang SC, sans-serif"
    fontSize: "clamp(31px, 4vw, 48px)"
    fontWeight: 640
    lineHeight: 1.12
    letterSpacing: "-.04em"
  body:
    fontFamily: "PingFang SC, Noto Sans SC, Microsoft YaHei, sans-serif"
  section-title:
    fontSize: "21px"
    letterSpacing: "-.025em"
  label:
    fontSize: "13px"
    fontWeight: 700
  metadata:
    fontSize: "12px"
rounded:
  input: "9px"
  nav: "12px"
  rail: "22px"
  strip: "16px"
  card: "15px"
  panel: "18px"
spacing:
  compact: "11px"
  base: "20px"
  card: "24px"
  panel: "27px"
components:
  action-primary:
    backgroundColor: "{colors.orchid}"
    textColor: "#fff"
    rounded: "12px"
  navigation-active:
    backgroundColor: "linear-gradient(100deg, rgba(232, 225, 255, .98), rgba(248, 242, 255, .92))"
    textColor: "#4e35ba"
    rounded: "{rounded.nav}"
    padding: "10px"
  metric-card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "20px"
  report-panel:
    backgroundColor: "rgba(255, 255, 255, .88)"
    rounded: "{rounded.panel}"
    padding: "27px"
  input-field:
    backgroundColor: "rgba(255, 255, 255, .76)"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "10px 11px"
  gate-go:
    backgroundColor: "linear-gradient(102deg, #6251d7, #9b7be8)"
    textColor: "#fff"
    rounded: "17px"
    padding: "20px 23px"
  gate-no-go:
    backgroundColor: "linear-gradient(102deg, #b13467, #dc6a99)"
    textColor: "#fff"
    rounded: "17px"
    padding: "20px 23px"
---

# Design System: Dify AutoPE

## Overview

**Creative North Star: "The Orchid Control Room"**

Dify AutoPE is an Operate-mode workspace: a quiet, soft-lavender field keeps long evaluation sessions calm while exact data, risks, and release gates remain easy to scan. A translucent white navigation rail and softly lifted white panels keep the interface composed rather than decorative.

Orchid is the active operational signal. It concentrates in the status strip, selected navigation, primary controls, and analytic comparison; rose only introduces the candidate series and a failed gate. The square purple mascot is the compact brand marker. It is not replaced with a generic glyph.

**Key Characteristics:**

- Ambient lavender-to-pink background behind translucent, white control surfaces.
- Orchid status signals and plain, high-contrast risk outcomes before supporting analytics.
- Compact Vant icon navigation with a horizontally scrollable mobile rail.
- Radar comparison always paired with a text table for accessible reading.

## Colors

The palette is a cool orchid operational field: dark violet text supplies the reading contrast, while translucent white separates working surfaces from the ambient background.

### Primary

- **Operational Orchid:** primary controls, selected navigation, chart baseline series, and focus treatment.
- **Deep Orchid:** darker active text and table emphasis.
- **Soft Orchid:** low-emphasis hover and selected-surface tint.

### Secondary

- **Candidate Rose:** the candidate radar series and its visual comparison accent; reserve it for contrast with the baseline.

### Tertiary

- **Status Orchid-to-Pink:** the top status strip and informational notice surfaces use the three-stop orchid, lilac, and pink progression.
- **Release Gate Gradients:** GO remains within the violet family; NO-GO shifts to the defined rose gradient so the decision changes in both wording and surface treatment.

### Neutral

- **Control Ink:** primary headings, key values, and navigation brand text.
- **Quiet Metadata:** helper text, captions, and secondary navigation labels.
- **Cloud Field:** the base canvas underneath the lavender and pink ambient glows.
- **Translucent Panel:** cards and working surfaces; retain translucency so the ambient field remains perceptible.

**The Signal Budget Rule.** Orchid is the default active signal. Candidate rose and NO-GO rose are comparison and risk signals, not general decoration.

## Typography

**Display Font:** Noto Sans SC Variable, with PingFang SC fallback; it is self-hosted through `@fontsource-variable/noto-sans-sc` and reserved for page headers.

**Body Font:** PingFang SC, Noto Sans SC, Microsoft YaHei, sans-serif.

**Character:** Dense Chinese operational information is rendered in a clear native UI stack, while the variable Noto Sans SC heading adds a deliberate, confident report title without switching the whole control room into display typography.

### Hierarchy

- **Page header** (640, responsive 31–48px, 1.12): the one dominant page-level statement.
- **Section title** (21px): panel and chart headings.
- **Metric value** (31px, tabular numerals): compact scan targets in metric cards.
- **Body** (14px, 1.72): explanatory text, constrained to a 62ch header measure.
- **Label** (700, 13px): form labels and structured operational metadata.
- **Metadata** (12px): chart notes, table copy, thresholds, and navigation secondary text.

**The One Display Rule.** Use the self-hosted variable face for page headers; keep controls, metrics, and dense reporting in the body stack.

## Layout

The desktop shell is a two-column control room: a 236px navigation rail sits inside an 18px outer margin, while the workspace uses a fluid horizontal inset from 20px to 66px. The top status strip establishes the working context before the page header and content.

Content is card-based and intentionally spacious: standard metric grids use 15px gaps, report metrics expand to four equal columns, and larger panels use 21px separation. At 980px and below, the rail becomes a single top region with an overflowable four-column navigation grid, and multi-column content collapses to one column. At 620px and below, navigation becomes a horizontal row, top-strip context text is removed, and stacked report content prioritizes readable order over side-by-side density.

**The Gate-First Rule.** On reports, the full-width GO or NO-GO banner precedes metrics and chart interpretation; no chart may outrank the release decision.

## Elevation & Depth

Depth comes from translucent white layers, `backdrop-filter` blur, and diffuse violet shadows rather than hard borders or dark floating slabs. The navigation rail and main panels are the strongest lifts; metric and row cards use reduced, softer elevation. The ambient circles remain behind all interaction surfaces.

### Shadow Vocabulary

- **Control rail and panel lift** (`0 18px 46px rgba(78, 57, 146, .12)`): translucent sidebar and principal panels.
- **Metric lift** (`0 10px 24px rgba(81, 64, 137, .07)`): compact summary cards.
- **Status lift** (`0 14px 30px rgba(112, 75, 205, .21)`): the top status strip.
- **Gate lift** (`0 16px 34px rgba(90, 61, 186, .2)`): release-decision banner.

**The Soft-Lift Rule.** Shadows are violet, broad, and low-opacity. Avoid crisp black shadows or high-contrast borders that would break the atmospheric field.

## Shapes

The system uses gently rounded rectangles, scaled by importance: 9px fields, 12px navigation targets, 15px compact cards, 16px status strip, 18px principal panels, and a 22px navigation rail. The mascot is the only signature illustration: its rounded purple square and dark expressive face anchor the product identity. Vant icons remain line-based companions rather than replacing that mark.

## Components

### Navigation

The translucent rail carries the product mark, a stacked icon-and-two-line navigation item, and a small platform-ownership note. Active items use the defined pale orchid gradient and deep orchid text; hover applies a soft orchid fill and a 2px right translation. At mobile widths the rail becomes horizontal and scrollable rather than compressing labels into unreadable fragments.

### Status Strip

The top strip is a rounded orchid-to-pink context band with white content, a white development tag, and a notification icon. It may use the existing `signal-rise` entrance only when reduced motion is not requested.

### Buttons

- **Primary:** Vant primary actions inherit operational orchid and the 12px medium radius, with the existing soft orchid shadow.
- **Focus:** keyboard focus uses a visible 3px orchid halo with a 3px offset.

### Cards / Containers

- **Metric cards:** translucent white with 20px internal padding, 15px corners, muted labels, and tabular numeric values.
- **Report panels:** slightly more opaque white with 27px padding and 18px corners.
- **Selectable rows:** retain the compact-card language; selection adds the defined pale orchid-to-pink tint and a stronger violet lift.

### Inputs / Fields

Fields are translucent white with a subtle violet border and 9px corners. Focus shifts the border to operational orchid and adds the existing 3px soft-orchid ring; controls never rely on placeholder text as their only label.

### Release Gate Banner

The release decision is a full-width, 17px-radius banner. GO uses the violet gradient; NO-GO uses the rose gradient. Both retain white copy and pair the outcome label with failed-case count, so color is never the sole carrier of risk.

### Report Comparison

The report chart uses an orchid circular-marker baseline and rose diamond-marker candidate series. The radar graphic has an accessible ARIA description and is followed by an equivalent three-column text table; both views use the same dimensions and scores.

## Do's and Don'ts

### Do:

- **Do** preserve the soft lavender/pink ambient field behind translucent white surfaces.
- **Do** use the purple square AutoPE mascot and Vant icon components in their established roles.
- **Do** place release gates before metrics and analytical graphics, with explicit GO/NO-GO wording and failed-case count.
- **Do** supply the text table alongside the four-dimension radar chart.
- **Do** keep the page heading in self-hosted Noto Sans SC Variable and retain the mobile horizontal navigation treatment.

### Don't:

- **Don't** use chart color as the only explanation of baseline, candidate, or release outcome.
- **Don't** let analytics, scores, or candidate styling visually override a failed gate.
- **Don't** replace the translucent rail and diffuse elevation with opaque dark dashboard chrome.
- **Don't** use the rose candidate/risk signal as the default action color.
