import base64

from agents.golden_press.client_visual_contract import (
    CLIENT_VISUAL_CONTRACT_VERSION,
    validate_client_visual_contract,
)


def _gif(width: int, height: int) -> str:
    # The validator reads the standard GIF logical-screen dimensions.  The
    # remaining payload is immaterial to this static contract test.
    raw = b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    return "data:image/gif;base64," + base64.b64encode(raw).decode("ascii")


def _document(body: str = "", *, script: str = "", style: str = ".ok { color: red; }") -> str:
    return f"""<!doctype html>
    <html><head><style>{style}</style></head><body>
      <header>
        <div class="client-mark"><img alt="Acme Systems" src="{_gif(2, 2)}"></div>
        <div class="gtm-mark"><img alt="GTM Strategies" src="{_gif(2, 2)}"></div>
      </header>
      <a href="#proof">Show proof</a><section id="proof">Evidence</section>
      {body}
      {f'<script>{script}</script>' if script else ''}
    </body></html>"""


def _rules(result: dict) -> set[str]:
    return {row["rule"] for row in result["violations"]}


def test_valid_client_artifact_passes() -> None:
    result = validate_client_visual_contract(
        _document('<button type="button" onclick="window.print()">Print</button>'),
        client_name="Acme Systems",
    )

    assert result["version"] == CLIENT_VISUAL_CONTRACT_VERSION
    assert result["ok"] is True
    assert result["violations"] == []
    assert result["receipts"]["images"] == 2
    assert result["receipts"]["buttons"] == 1


def test_one_pixel_slots_fail_marks_and_brand_presence() -> None:
    empty = _gif(1, 1)
    html = f"""
      <div class="client-mark"><img alt="Acme Systems" src="{empty}"></div>
      <div class="gtm-mark"><img alt="GTM Strategies" src="{empty}"></div>
      <div class="seal-slot"><img alt="Department of Defense" src="{empty}"></div>
    """

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert result["ok"] is False
    assert {
        "transparent_1x1_mark", "missing_client_mark", "missing_gtm_mark",
    }.issubset(_rules(result))
    assert result["receipts"]["one_pixel_marks"] == 3


def test_malformed_usaspending_route_is_rejected() -> None:
    html = _document(
        '<a href="https://www.usaspending.gov/award/CONT_AWD_ABC_-NONE-_-NONE-">ABC</a>')

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "malformed_usaspending_award_route" in _rules(result)


def test_placeholder_action_hrefs_are_rejected_but_real_fragments_are_not() -> None:
    html = _document(
        '<a href="#">Empty action</a>'
        '<a href="javascript:void(0)">Fake action</a>'
        '<a href="#proof">Real internal action</a>')

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "placeholder_action_href" in _rules(result)
    detail = next(row["detail"] for row in result["violations"]
                  if row["rule"] == "placeholder_action_href")
    assert "2 anchor(s)" in detail


def test_missing_internal_targets_and_duplicate_ids_are_rejected() -> None:
    html = _document(
        '<a href="#missing">Missing</a><div id="repeat"></div><div id="repeat"></div>')

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert {"missing_internal_target", "duplicate_id"}.issubset(_rules(result))


def test_repeated_explanatory_prose_across_table_rows_is_rejected() -> None:
    sentence = (
        "EOS switching, routing and CloudVision align to the named network "
        "modernization scope."
    )
    rows = "".join(
        f"<tr><td>{sentence} <a href='mailto:owner{index}@example.gov'>"
        f"Owner {index}</a></td></tr>"
        for index in range(8)
    )
    html = _document(f"<table><tbody>{rows}</tbody></table>")

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "repeated_table_prose" in _rules(result)


def test_short_repeated_interface_labels_do_not_trigger_prose_rule() -> None:
    rows = "".join(
        "<tr><td><a href='https://example.gov/source'>Official source</a></td></tr>"
        for _ in range(12)
    )
    html = _document(f"<table><tbody>{rows}</tbody></table>")

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "repeated_table_prose" not in _rules(result)


def test_css_braces_ignore_comments_and_strings_but_reject_real_imbalance() -> None:
    valid = _document(style=".ok { content: '}'; } /* { ignored } */")
    invalid = _document(style=".broken { color: red;")

    valid_result = validate_client_visual_contract(valid, client_name="Acme Systems")
    invalid_result = validate_client_visual_contract(invalid, client_name="Acme Systems")

    assert "unbalanced_css_braces" not in _rules(valid_result)
    assert "unbalanced_css_braces" in _rules(invalid_result)


def test_inert_button_is_rejected() -> None:
    html = _document('<button type="button">Save HTML</button>')

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "button_without_action" in _rules(result)


def test_runtime_bound_data_action_button_passes() -> None:
    html = _document(
        '<button type="button" data-action="export">Save HTML</button>',
        script="""
          document.querySelectorAll('[data-action]').forEach((button) => {
            button.addEventListener('click', () => exportReport());
          });
        """,
    )

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "button_without_action" not in _rules(result)


def test_runtime_bound_report_controls_pass() -> None:
    html = _document(
        '<button type="button" data-subscription="print">Print / PDF</button>'
        '<button type="button" data-open="receipt">Show work</button>'
        '<button type="button" data-filter="direct">Direct fit</button>',
        script="""
          document.querySelectorAll('[data-subscription]').forEach((button) => {
            button.addEventListener('click', () => openSubscription(button.dataset.subscription));
          });
          document.querySelectorAll('[data-open]').forEach((button) => {
            button.addEventListener('click', () => openDrawer(button.dataset.open));
          });
          document.querySelectorAll('[data-filter]').forEach((button) => {
            button.addEventListener('click', () => filterCards(button.dataset.filter));
          });
        """,
    )

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "button_without_action" not in _rules(result)


def test_submit_button_requires_and_accepts_real_form_action() -> None:
    invalid = _document('<form><button type="submit">Send</button></form>')
    valid = _document(
        '<form action="mailto:owner@example.com"><button type="submit">Send</button></form>')

    invalid_result = validate_client_visual_contract(invalid, client_name="Acme Systems")
    valid_result = validate_client_visual_contract(valid, client_name="Acme Systems")

    assert "button_without_action" in _rules(invalid_result)
    assert "button_without_action" not in _rules(valid_result)


def test_button_wrapped_by_real_anchor_passes() -> None:
    html = _document(
        '<a href="https://example.gov/source"><button type="button">Source</button></a>')

    result = validate_client_visual_contract(html, client_name="Acme Systems")

    assert "button_without_action" not in _rules(result)
