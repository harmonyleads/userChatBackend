from app.services.identity import canonical_page_id


def test_explicit_page_id_wins() -> None:
    assert (
        canonical_page_id("page-9", "https://example.com/a", "asset-1", "Guide.pdf")
        == "page-9"
    )


def test_url_identity_ignores_one_trailing_slash() -> None:
    left = canonical_page_id(None, "https://example.com/helix", None, None)
    right = canonical_page_id(None, "https://example.com/helix/", None, None)
    assert left == right
    assert left.startswith("url_")


def test_asset_name_identity_is_stable() -> None:
    assert canonical_page_id(None, None, None, "Helix_Embed_FAQ.pdf") == canonical_page_id(
        None, None, None, " Helix_Embed_FAQ.pdf "
    )


def test_asset_id_and_asset_name_are_different_pages() -> None:
    by_id = canonical_page_id(None, None, "asset-1", "Helix_Embed_FAQ.pdf")
    by_name = canonical_page_id(None, None, None, "Helix_Embed_FAQ.pdf")
    assert by_id != by_name
    assert by_id.startswith("asset_")
