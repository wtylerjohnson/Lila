# Opportunity posture: win-new vs displacement

**Status: PROPOSAL. Nothing built. No bands, no schema change, no rendering.**
Written 2026-07-28 from two real finds in the store-backed L1 dry run.

## The problem

Both genuine Red Hat finds are actionable, and they demand opposite plays. The
band structure has no slot for the distinction, so today they would render
side by side as "notices", and the reader would have to work out the
difference themselves.

| notice | what it is | what the client does |
|---|---|---|
| VA `7D20--Abacus Visual Playbook Builder and Automation Orchestrator`, Sources Sought | requirement still being written, names Ansible Automation Platform and OpenShift | shape the requirement, get in before the RFP |
| HHS `Notice of Intent for NIH/NCATS Rocky Linux Subscription Renewal`, Special Notice | an existing RHEL-shaped estate renewing on a free RHEL-compatible rebuild | defend, or concede and learn why |

The first is **win-new**. The second is **displacement**, and it is arguably
the more valuable of the two because it is a customer leaving. A report that
files them together buries that.

## Proposal: one field, derived, never authored

Add `posture` to `GoldenRecord`, alongside the existing `notice_leverage_rank`:

    posture: Optional[Literal["win_new", "displacement", "defend", "unclassified"]]
    posture_signals: list[str]     # WHY, so the call is auditable
    posture_confidence: Literal["clear", "probable"]

Three rules govern it, all borrowed from what already works here:

1. **Derived deterministically from fields the store already holds.** No LLM
   reads the notice. An LLM classifying posture would be a model asserting a
   commercial fact about a client's account, which is exactly the class of
   claim the deterministic-rendering work removed from this system. Same
   reasoning as `notice_leverage_rank`: a lookup table, not a judgement.

2. **`posture_signals` carries the evidence.** Every posture states which
   signals fired. A reader who disagrees can see the basis and overrule it,
   the same way `relevance_matched` works today.

3. **`unclassified` is a first-class answer.** Most notices are neither. A
   forced binary would manufacture a posture for every row, and a wrong
   posture is worse than none because it prescribes the wrong play.

## The signals, and where each already exists

All of these are present in the notice store today. Nothing needs fetching.

### Displacement

| signal | source | notes |
|---|---|---|
| intent-to-sole-source language | `description_prefix` | `sam_lanes._SOLE` already detects this exact phrasing |
| a COMPETITOR entity named | entity matchers | must survive `reject_entity_hit`, so the reseller and trout guards apply |
| renewal or subscription language | `description_prefix` | "renewal", "subscription", "continued support", "follow-on" |
| the CLIENT is absent | entity matchers | the strongest signal, and the cheapest: an incumbent defending its own renewal is usually named in its own notice |
| notice type is Special Notice or Justification | `notice_type` | already stored, already ranked |

> **DROPPED 2026-07-28: "incumbent named via the Awardee column."** It looked
> like free incumbent data and it is not. Measured on the live store: 25.8% of
> all non-empty `awardee` values are the literal string `"null"`, and
> in-corridor pre-solicitation is worse. Of 359 Sources Sought and
> Presolicitation notices carrying an "awardee", 354 are literally `"null"`
> and the remaining 5 are `"null 19111-5098"`, a null concatenated with a ZIP
> code. **Zero real incumbents.** All 12,190 genuine awardee names sit on
> Award Notices, which are rank 4 and excluded from the solicitation lane by
> design. The column is worth storing; it is not worth believing where posture
> would need it. Incumbency must come from entity matching against the notice
> text, or from the L2 award lane, not from this field.

The NIH notice fires four of the five that survive: sole-source intent, Rocky Linux named as a
competitor, "Subscription Renewal" in the title, Red Hat absent.

**Proposed rule:** competitor named AND client absent AND (renewal language OR
sole-source intent) -> `displacement`, confidence `clear`. Drop to `probable`
when the renewal and sole-source signals are both missing.

### Win-new

| signal | source | notes |
|---|---|---|
| leverage rank 1 | `notice_leverage_rank` | Sources Sought and RFI: the requirement is still being written |
| no incumbent named | entity matchers | no competitor and no client |
| capability language, no named product | capability vs entity hits | the buyer is describing a need, not a brand |
| response deadline in the future | `deadline` | a closed RFI is history, not an opening |

The Abacus notice fires rank 1 and capability language, but it DOES name
products, so under this rule it would be `win_new` at `probable` rather than
`clear`. That feels right: a Sources Sought naming Ansible and OpenShift is
partly a shaped requirement already.

### Defend

Client named, renewal or follow-on language, no competitor. Worth separating
from displacement because the play is retention, not re-entry. Listed for
completeness; I would not build it in a first pass, since we have no example
in hand and a category with no observed instance is a category invented in
advance of evidence.

## What I would NOT do

- **No band per posture.** Band grammar is load-bearing and validated, and the
  events band already showed what a conditional band costs. Posture should be
  a mark ON a record, and the existing candidate-review band can order by it.
- **No posture on L2 award records.** An award is a placed contract; posture
  is about an open opportunity. Applying it there would invite reading a
  historical award as a live play.
- **No confidence score as a number.** `clear` and `probable` are honest about
  the resolution available. A 0.73 would not be.

## Open questions for the operator

1. Is `defend` worth carrying before we have seen one?
2. On a displacement notice, should the report name the competitor product in
   the client-facing file, or keep it internal? The client-file doctrine says
   negatives convert to forward actions, and "NIH is renewing on Rocky Linux"
   is a negative with an obvious forward action.
3. Should posture affect ORDER within the candidate-review band, or only
   render as a label? Ordering by it is a stronger claim than labelling.
