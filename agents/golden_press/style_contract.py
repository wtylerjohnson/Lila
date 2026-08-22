"""Every class a renderer emits must exist in the stylesheet that ships with it.

WHY THIS EXISTS. On 2026-08-07 a Federal Market Map shipped in which NOT ONE
of the twenty-four classes the renderer emitted had a single CSS rule. The
renderer had invented a private vocabulary (mm, mmnav, tick, kv, opps, lede,
n) and the document it was spliced into styled only `sb-*`. The result was
29,000 characters of unstyled HTML: navigation rendered as
"CompanyMarketCompetitionOpportunities", ticker items collided into
"DEPARTMENT OFSources Sought", and raw 32-character hex notice ids were shown
to a client.

Every test in the suite passed. The document compiled, validated, carried its
evidence, cleared the banned-term lint and the em dash lint, and was
structurally perfect. Nothing checked whether it could be LOOKED AT.

This is that check, and it is one function: the set of classes in the emitted
HTML minus the set of classes defined in the stylesheet must be empty. It
takes microseconds and it is the difference between a document and a
deliverable.

IT IS DELIBERATELY UNFORGIVING. A renderer that wants a new class must add
the rule that styles it. "It will inherit something reasonable" is exactly
the assumption that produced the unstyled artifact.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

STYLE_CONTRACT_VERSION = "style_contract.v1.2026-08-07"

# Classes carried for behaviour rather than appearance: the runtime binds to
# them, so they legitimately have no style rule. Each needs a reason.
BEHAVIOURAL = {
    "work-trigger": "runtime binds the show-your-work drawer to it",
    "is-editing": "runtime toggles it while the operator edits",
    "is-active": "runtime toggles it on the open drawer",
    "is-drop": "runtime toggles it while a logo is dragged over a slot",
}

# A rule may style a class ONLY inside an ancestor: `.market-proof .money`
# styles nothing when `.money` sits in a `.metric`. Substring-matching the
# selector text calls that class "defined" and reports a false green, which
# is exactly what happened on 2026-08-07: the audit said 0 unstyled while
# every amount in the document rendered unstyled.
_RULE = re.compile(r"([^{}]+)\{[^}]*\}")
_SIMPLE = re.compile(r"^\.(-?[_a-zA-Z][\w-]*)"
                     r"(?:[.:][\w()-]+)*$")

_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
_SCRIPT_BLOCK = re.compile(r"<script[^>]*>.*?</script>", re.S | re.I)
_SELECTOR = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")


def emitted_classes(html: str) -> set:
    """Every class actually used in the markup, scripts excluded.

    Script bodies are stripped first: a class NAME inside a JS string is the
    runtime's business, and counting it here would let an unstyled class hide
    behind a mention in code.
    """
    markup = _SCRIPT_BLOCK.sub(" ", html or "")
    found: set = set()
    for group in _CLASS_ATTR.findall(markup):
        found.update(token for token in group.split() if token)
    return found


def defined_classes(css: str) -> set:
    """Every class named anywhere in a selector. Context-blind, so it
    OVER-reports; `standalone_classes` is the honest set."""
    return set(_SELECTOR.findall(css or ""))


def standalone_classes(css: str) -> set:
    """Classes that style an element wherever it appears.

    A selector qualifies when at least one of its comma-separated parts is a
    single compound with no descendant, child or sibling combinator:
    `.money` and `.plain-state.needs` qualify, `.market-proof .money` does
    not.
    """
    out: set = set()
    for rule in _RULE.finditer(css or ""):
        for part in rule.group(1).split(","):
            part = part.strip()
            if not part or any(c in part for c in ">+~") or " " in part:
                continue
            match = _SIMPLE.match(part)
            if match:
                out.add(match.group(1))
    return out


def compound_rules(css: str) -> dict:
    """class -> companion classes required ON THE SAME ELEMENT.

    `.plain-state.needs` styles `needs` only when the element also carries
    `plain-state`. Reading that as a bare `.plain-state` rule would report
    `needs` as unstyled, which it is not.
    """
    needs: dict = {}
    for rule in _RULE.finditer(css or ""):
        for part in rule.group(1).split(","):
            token = part.strip().replace(">", " ").replace("+", " ").split()
            if not token:
                continue
            names = _SELECTOR.findall(token[-1])
            if len(names) < 2:
                continue
            for name in names:
                needs.setdefault(name, set()).update(set(names) - {name})
    return needs


def structural_hooks(css: str) -> set:
    """Classes whose rules style their DESCENDANTS rather than themselves.

    `.brand-copy strong` gives `.brand-copy` no rule of its own, and that is
    correct: the container exists to scope its children. Reporting it as
    unstyled is a false alarm, and a guard that cries wolf gets switched off.
    """
    out: set = set()
    for rule in _RULE.finditer(css or ""):
        for part in rule.group(1).split(","):
            tokens = part.strip().replace(">", " ").replace("+", " ").split()
            if len(tokens) > 1:
                out.update(_SELECTOR.findall(" ".join(tokens[:-1])))
    return out


def contextual_rules(css: str) -> dict:
    """class -> the ancestor classes that any descendant rule requires.

    `.market-proof .money` yields {"money": {"market-proof", "pursuit-lead"}},
    so an emitted `.money` is styled only under one of those.
    """
    needs: dict = {}
    for rule in _RULE.finditer(css or ""):
        for part in rule.group(1).split(","):
            tokens = part.strip().replace(">", " ").replace("+", " ").split()
            if len(tokens) < 2:
                continue
            leaf = _SELECTOR.findall(tokens[-1])
            ancestors = set(_SELECTOR.findall(" ".join(tokens[:-1])))
            if not ancestors:
                continue
            for name in leaf:
                needs.setdefault(name, set()).update(ancestors)
    return needs


class _Ancestry(HTMLParser):
    """Every element's class list plus the classes of all its ancestors."""

    VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.seen: list = []

    def handle_starttag(self, tag, attrs) -> None:
        values = {k: v or "" for k, v in attrs}
        classes = [c for c in values.get("class", "").split() if c]
        inherited: set = set()
        for frame in self.stack:
            inherited.update(frame)
        if classes:
            self.seen.append((classes, inherited))
        if tag not in self.VOID:
            self.stack.append(classes)

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag) -> None:
        if tag not in self.VOID and self.stack:
            self.stack.pop()


def unstyled_in_context(html: str, *, css: Any = None,
                        allow: Any = ()) -> list:
    """Classes that reach the page with no rule that can actually match them.

    This is the honest check. A class passes when it has a standalone rule,
    or when the element sits inside an ancestor a descendant rule names.
    """
    sheet = css if css is not None else stylesheet_of(html)
    markup = _SCRIPT_BLOCK.sub(" ", html or "")
    standalone = standalone_classes(sheet)
    contextual = contextual_rules(sheet)
    compounds = compound_rules(sheet)
    hooks = structural_hooks(sheet)
    permitted = set(BEHAVIOURAL) | {str(a) for a in (allow or ())}

    parser = _Ancestry()
    parser.feed(markup)
    problems: dict = {}
    for classes, ancestors in parser.seen:
        context = ancestors | set(classes)
        for name in classes:
            if name in standalone or name in permitted or name in hooks:
                continue
            companion = compounds.get(name)
            if companion and (companion & set(classes)):
                continue
            required = contextual.get(name)
            if required and (required & context):
                continue
            if required:
                problems[name] = (
                    f"styled only inside {sorted(required)[:4]}, but it is "
                    f"used inside {sorted(context - {name})[:4] or 'nothing'}")
            else:
                problems[name] = "no rule of any kind"
    return sorted(problems.items())


def stylesheet_of(html: str) -> str:
    return "\n".join(_STYLE_BLOCK.findall(html or ""))


def unstyled(html: str, *, css: Any = None, allow: Any = ()) -> set:
    """Classes used in the markup that nothing styles. Empty is the contract."""
    sheet = css if css is not None else stylesheet_of(html)
    permitted = set(BEHAVIOURAL) | {str(a) for a in (allow or ())}
    return emitted_classes(html) - defined_classes(sheet) - permitted


def audit(html: str, *, css: Any = None, allow: Any = ()) -> dict:
    """A full receipt, for a test failure that explains itself."""
    sheet = css if css is not None else stylesheet_of(html)
    used, have = emitted_classes(html), defined_classes(sheet)
    missing = unstyled(html, css=sheet, allow=allow)
    return {
        "classes_used": len(used),
        "classes_defined": len(have),
        "stylesheet_chars": len(sheet),
        "unstyled": sorted(missing),
        "unstyled_count": len(missing),
        "styled_fraction": (
            round((len(used) - len(missing)) / len(used), 4) if used else 0.0),
    }


def require_styled(html: str, *, css: Any = None, allow: Any = (),
                   label: str = "document") -> None:
    """Raise unless every emitted class is styled."""
    report = audit(html, css=css, allow=allow)
    if report["unstyled"]:
        raise AssertionError(
            f"{label}: {report['unstyled_count']} of {report['classes_used']} "
            f"classes have no CSS rule, so that markup renders unstyled. "
            f"Unstyled: {', '.join(report['unstyled'][:24])}"
            + ("..." if report["unstyled_count"] > 24 else "")
            + f"\nThe stylesheet is {report['stylesheet_chars']:,} chars and "
            f"defines {report['classes_defined']} classes. A renderer that "
            f"needs a new class must ship the rule that styles it.")
