# Findings before implementation

Both references place the opportunity first, separate published buyer requirements from proposed product/partner approaches, embed contacts with the opportunity, put a first question and progress conditions beside the next action, and keep source/value explanations nearby. Their dense section hierarchy is easier to scan than the current Press receipt table. Counts and facts in those manually curated reports are reference observations only.

At c487c0d:
- `press_html.render_html` wraps entire research subjects in one cell of a four-column table. Subject description and amount meaning are inside the original-source drawer. A rep sees data receipts before a useful decision summary.
- The priority view iterates only `receipt.leads`; a reviewed forecast or award with the required zero children cannot appear there. The existing three-case cap and explicit-review rule must remain.
- `target_html.render_research` is a flat definition list and keeps the next question at the same visual weight as metadata. It has the necessary source-bound input for a clearer shared component.
- `external_product_render._lead_rows` prints only `next_action.verb` and reads `due_at`/`due_date`, although the current contract uses `object` and `due`. This loses what to do and when.
- FORECAST-POCS-056 already carries original POCs into native output. Extend it; do not rebuild or re-enrich contacts.

We will extend existing rendering/projection patterns. No new source, persisted business schema, gate, priority quota, or second editing runtime is needed.
