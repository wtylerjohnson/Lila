"""A broken upstream extract is refused at the DOWNLOADER, not just the store.

INCIDENT 2026-07-30: GSA's nightly export published a header-only 685-byte
object two nights running (verified at the source: Content-Length: 685).
`download_extract` streamed it, promoted it, and printed "0 MB in 1s" as
success; the idempotent skip then served the poison file all day, so even a
later good upstream rewrite would never have been fetched; and _latest_path
handed it to the events lane, which reported "0 event-language notices
screened" — a broken input wearing a quiet market's clothes. The store's own
floor guard held (both days REFUSED, store untouched), which is why the blast
radius was staleness, not corruption.

The rules pinned here: validate before promote, a failed day leaves NO file,
an existing below-floor file is poison to be discarded rather than trusted,
and only credible extracts are ever served as "the latest". One floor owner:
tools/notice_store.min_credible_extract_bytes, both gates.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api import sam_extract as se  # noqa: E402

HEADER = b'"NoticeId","Title","Sol#"\n'
GOOD = HEADER + b'"abc","A notice","X-1"\n' * 40      # comfortably over floor


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("LILA_SAM_EXTRACT_DIR", str(cache))
    monkeypatch.setenv("LILA_SAM_EXTRACT_ARCHIVE_DIR", str(tmp_path / "arch"))
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "200")
    return cache


class _FakeHTTP:
    """Stands in for httpx.Client; serves the queued payloads in order."""

    payloads: list[bytes] = []
    served = 0

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def head(self, url):
        class _R:
            headers = {"content-length": "685",
                       "last-modified": "Thu, 30 Jul 2026 03:30:04 GMT"}
        return _R()

    def stream(self, method, url):
        cls = type(self)
        body = cls.payloads[min(cls.served, len(cls.payloads) - 1)]
        cls.served += 1

        class _Resp:
            def __enter__(self_r):
                return self_r

            def __exit__(self_r, *exc):
                return False

            def raise_for_status(self_r):
                return None

            def iter_bytes(self_r, n):
                yield body
        return _Resp()


@pytest.fixture()
def http(monkeypatch):
    _FakeHTTP.payloads, _FakeHTTP.served = [], 0
    monkeypatch.setattr(se.httpx, "Client", _FakeHTTP)
    return _FakeHTTP


def test_a_good_payload_promotes(cache, http):
    http.payloads = [GOOD]
    path = se.download_extract()
    assert path.exists() and path.stat().st_size == len(GOOD)


def test_a_header_only_payload_is_refused_and_leaves_no_file(cache, http):
    """The whole incident in one assertion: refusal must leave NOTHING for
    the idempotent skip to trust or for _latest_path to serve."""
    http.payloads = [HEADER]
    with pytest.raises(se.ExtractRefused) as err:
        se.download_extract(attempts=2, backoff_s=0)
    assert list(cache.glob("opportunities_*")) == []
    assert list(cache.glob("*.part")) == []
    assert "below the 200-byte floor" in str(err.value)
    assert "Content-Length=685" in str(err.value)   # upstream's own evidence
    assert http.served == 2                          # it did retry


def test_a_transient_truncation_heals_on_retry(cache, http):
    http.payloads = [HEADER, GOOD]
    path = se.download_extract(attempts=2, backoff_s=0)
    assert path.stat().st_size == len(GOOD)


def test_an_existing_poison_file_is_discarded_not_trusted(cache, http):
    """The idempotent skip is only for CREDIBLE files. On 2026-07-29 a
    04:59 pull landed the poison file and the 06:30 job then trusted it,
    so the day could never heal even after upstream recovered."""
    se._today_path().write_bytes(HEADER)
    http.payloads = [GOOD]
    path = se.download_extract()
    assert path.stat().st_size == len(GOOD)
    assert http.served == 1


def test_an_existing_good_file_short_circuits_without_download(cache, http):
    se._today_path().write_bytes(GOOD)
    http.payloads = [b"never served"]
    path = se.download_extract()
    assert path.stat().st_size == len(GOOD)
    assert http.served == 0


def test_latest_path_never_serves_a_below_floor_file(cache):
    (cache / "opportunities_2026-07-28.csv").write_bytes(GOOD)
    (cache / "opportunities_2026-07-29.csv").write_bytes(HEADER)
    (cache / "opportunities_2026-07-30.csv").write_bytes(HEADER)
    latest = se._latest_path()
    assert latest is not None
    assert latest.name == "opportunities_2026-07-28.csv"


def test_latest_path_reports_no_extract_over_a_poison_only_cache(cache):
    """No extract at all is the honest answer; the events lane already
    discloses that state instead of screening zero rows silently."""
    (cache / "opportunities_2026-07-30.csv").write_bytes(HEADER)
    assert se._latest_path() is None


def test_the_floor_has_one_owner():
    from tools.notice_store import min_credible_extract_bytes

    os.environ.pop("LILA_MIN_EXTRACT_BYTES", None)
    assert min_credible_extract_bytes() == 50_000_000


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
