import pytest

from solo_leveling.domain.annotation import SegmentInput
from solo_leveling.interfaces.http.remote_annotations import (
    RemoteAnnotationInput,
    annotation_input_fingerprint,
)


def value(**changes):
    data = dict(kind="note", body=" 메모 e\u0301 ", step=None, creation_origin="unknown", segments=())
    data.update(changes)
    return RemoteAnnotationInput(**data)


def test_fingerprint_is_stable_but_does_not_normalize_user_values():
    assert annotation_input_fingerprint(value()) == annotation_input_fingerprint(value())
    assert annotation_input_fingerprint(value(body="메모 é")) != annotation_input_fingerprint(value())
    assert annotation_input_fingerprint(value(body="메모")) != annotation_input_fingerprint(value(body=" 메모 "))


def test_fingerprint_keeps_segment_order_and_explicit_defaults():
    a = SegmentInput("a", 0, 1, "A")
    b = SegmentInput("b", 0, 1, "B")
    assert annotation_input_fingerprint(value(segments=(a, b))) != annotation_input_fingerprint(value(segments=(b, a)))
    assert annotation_input_fingerprint(value(creation_origin="unknown")) != annotation_input_fingerprint(
        value(creation_origin="user")
    )


@pytest.mark.parametrize("origin", ["model", "", None])
def test_unknown_origin_must_be_explicitly_supported(origin):
    with pytest.raises(ValueError):
        value(creation_origin=origin)
