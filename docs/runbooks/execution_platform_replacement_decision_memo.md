# Execution Platform Replacement Decision Memo

## Objective

Decide whether replacing any part of the current brokerage, OMS, routing, or execution stack is justified, and define the narrowest layer worth owning first.

## What this memo is for

Use this memo when the question is:

- should we keep building on top of an external broker or execution vendor
- should we replace the internal OMS or routing layer
- should we own more of the execution stack

This memo is not for deciding whether an own-account intraday strategy is ready to trade live. That belongs in:

- [own_account_intraday_implementation_checklist.md](C:\Users\marcc\PycharmProjects\StockMarketPredictions\docs\runbooks\own_account_intraday_implementation_checklist.md)

## Core rule

Do not replace infrastructure because the existing stack feels generic. Replace a layer only if it is currently blocking a material requirement that produces measurable trading, product, or operational value.

## Replacement ladder

### 1. Keep broker and execution vendor, own only the product layer

You own:

- UI
- watchlists
- analytics
- journals
- internal workflow

You do not own:

- brokerage
- customer routing
- custody
- clearing

This is the default starting position.

### 2. Keep brokerage, replace internal OMS / execution workflow

You own:

- internal order state
- route selection logic
- execution analytics
- operator controls
- reconciliation layer

You still do not own:

- carrying
- custody
- customer asset protection

This is the first plausible replacement step when execution workflow is a real differentiator.

### 3. Replace more of the execution layer while keeping custody and clearing outsourced

You own:

- OMS
- routing
- risk controls
- certification burden
- execution-quality measurement
- operator tooling

You still outsource:

- custody
- clearing
- much of the legal perimeter

This step only makes sense if order flow is large enough and execution quality is a core business variable.

### 4. Full platform replacement

You own:

- brokerage responsibilities
- carrying or carrying relationships
- customer protection burden
- books and records
- supervision
- incident response
- business continuity around customer flow

This is not a normal extension of an own-account desk. It is a different business.

## Decision gates

Infrastructure replacement is justified only if all of these are true:

1. The current vendor blocks a material requirement.
2. The blocked requirement has a measurable economic or operational value.
3. The replacement layer is narrow enough to be isolated.
4. The team can own the operational burden continuously.
5. The replacement does not accidentally create a broker-dealer or customer-platform problem when that is not the actual goal.

If any of those fail, keep buying the layer instead of building it.

## What counts as a material requirement

Good reasons:

- routing quality is limiting execution performance
- internal order state is too weak to support the workflow
- reconciliation gaps are creating operational risk
- vendor economics break at projected volume
- required order controls or analytics are not available in the current stack

Weak reasons:

- the existing system feels generic
- the team wants more technical ownership
- a vendor UI is annoying
- building an OMS sounds strategically important

## What to avoid

Do not mix these problems:

- own-account strategy validation
- customer-platform product design
- broker-dealer scope expansion

Each one has different success criteria. Combining them hides risk and slows decisions.

## Practical recommendation

For this codebase, the default posture should remain:

- own-account desk first
- external broker or execution provider retained
- internal workflow, analytics, and controls improved locally

Only open an execution-platform replacement project when a specific layer is failing against a measured requirement.

## Next review questions

Before approving any replacement work, answer these directly:

1. Which exact layer is under consideration: UI, OMS, routing, risk, carrying, or full brokerage?
2. What measurable requirement is blocked today?
3. What is the expected gain: execution quality, cost, compliance posture, or product capability?
4. What new operational burden appears immediately if we own that layer?
5. Why is the narrowest replacement layer not enough?
