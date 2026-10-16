# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vue 3 + TypeScript + Vite + Vant + Pinia + Vue Router + Alova + Apache ECharts;
Python 3.12 + FastAPI. This was explicitly requested to reuse the skincare storefront's
technology choices, with ECharts added for evaluation reporting.

## Users

Business annotators, evaluation operators, Prompt owners, and release approvers who need
to evaluate Dify agent workflows safely.

## Product Purpose

Dify_AutoPE is an independent evaluation platform. It calls a Dify workflow runtime API as
the system under test, captures normalized results, collects business labels, compares
baseline and candidate versions, and prepares a human-approved prompt release decision.

## Positioning

The platform is not a Dify workflow. It owns evaluation data, approval history, hard gates,
and reports; Dify only executes the agent workflow being tested.

## Capabilities and Constraints

- Dataset upload, labeling, evaluation runs, reports, prompt candidates, and release review
  are separate workspaces.
- Each case scores business understanding, clarification, tool use, and safety compliance,
  each out of 25. High-risk and red-line cases require 100% pass rate.
- Production Dify prompts cannot be changed by the runtime API or by an automated score.
- Dify credentials remain server-side and are referenced by name, never returned to the web
  client or stored in reports.
- The phase-0/1 foundation persists the control plane with SQLAlchemy and records audited writes;
  development/test may seed illustrative data. PostgreSQL migrations, external identity, external
  secret resolution, and a durable execution worker remain mandatory before operational use.

## Product Principles

- Separate the workspaces; do not create a combined upload-label-evaluate-optimize screen.
- Preserve hard safety and authorization gates independently of quality scoring.
- Make evidence, versions, and approval state inspectable.
- Show risk explicitly; charts never hide a failed high-risk or red-line case.

## Accessibility & Inclusion

All primary navigation and actions must be keyboard accessible. Charts need accessible text
summaries and must not communicate state through color alone.

