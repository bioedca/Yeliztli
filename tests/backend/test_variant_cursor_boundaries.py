"""Software-only pagination fixtures: synthetic IDs and no genotype calls."""

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, raw_variants, reference_metadata, samples

# Explicit expected order; insertion below deliberately reverses it. Noncanonical
# contigs share the chromosome-order bucket, but their positions overlap.
ORDERED_KEYS = [
    ("1", 10, "rsYELIZTLI_BEFORE"),
    ("1", 20, "iYELIZTLI_A"),
    ("1", 20, "rsYELIZTLI_A"),
    ("1", 20, "rsYELIZTLI_B"),
    ("2", 1, "rsYELIZTLI_C"),
    ("10", 1, "rsYELIZTLI_D"),
    ("X", 5, "rsYELIZTLI_X"),
    ("MT", 3, "rsYELIZTLI_MT"),
    ("CONTIG_A", 20, "iYELIZTLI_E"),
    ("CONTIG_A", 20, "rsYELIZTLI_F"),
    ("CONTIG_A", 30, "rsYELIZTLI_G"),
    ("CONTIG_B", 1, "rsYELIZTLI_H"),
    ("CONTIG_B", 20, "rsYELIZTLI_I"),
]


@pytest.fixture(params=["raw-explorer", "annotated-explorer", "annotations"])
def cursor_client(request, tmp_data_dir):
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    reference = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(reference)
    with reference.begin() as conn:
        sid = conn.execute(
            samples.insert().values(
                name="synthetic_cursor_sample",
                db_path="samples/cursor.db",
                file_format="23andme_v5",
                file_hash="synthetic-cursor-fixture",
            )
        ).lastrowid
    reference.dispose()

    sample = sa.create_engine(f"sqlite:///{tmp_data_dir / 'samples' / 'cursor.db'}")
    create_sample_tables(sample)
    table = raw_variants if request.param == "raw-explorer" else annotated_variants
    with sample.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {"chrom": chrom, "pos": pos, "rsid": rsid, "genotype": "--"}
                for chrom, pos, rsid in reversed(ORDERED_KEYS)
            ],
        )
    sample.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        registry = DBRegistry(settings)
        try:
            with (
                patch("backend.api.routes.variants.get_registry", return_value=registry),
                patch("backend.api.routes.annotations_api.get_registry", return_value=registry),
            ):
                from backend.main import create_app

                with TestClient(create_app()) as client:
                    endpoint = (
                        "/api/annotations" if request.param == "annotations" else "/api/variants"
                    )
                    yield client, sid, endpoint
        finally:
            registry.dispose_all()
            reset_registry()


def _keys(items):
    return [(item["chrom"], item["pos"], item["rsid"]) for item in items]


def test_total_order_separates_same_positions_and_contigs(cursor_client):
    client, sid, endpoint = cursor_client
    response = client.get(endpoint, params={"sample_id": sid, "limit": 100})
    assert response.status_code == 200
    assert _keys(response.json()["items"]) == ORDERED_KEYS


@pytest.mark.parametrize("boundary", [1, 8, 10])
def test_complete_cursor_resumes_after_exact_boundary_row(cursor_client, boundary):
    client, sid, endpoint = cursor_client
    chrom, pos, rsid = ORDERED_KEYS[boundary]
    response = client.get(
        endpoint,
        params={
            "sample_id": sid,
            "limit": 1,
            "cursor_chrom": chrom,
            "cursor_pos": pos,
            "cursor_rsid": rsid,
        },
    )
    assert response.status_code == 200
    assert _keys(response.json()["items"]) == [ORDERED_KEYS[boundary + 1]]


@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("filtered", [False, True], ids=["all-rows", "filtered-contig"])
def test_every_page_returns_exact_membership_once(cursor_client, limit, filtered):
    client, sid, endpoint = cursor_client
    expected = [key for key in ORDERED_KEYS if not filtered or key[0] == "CONTIG_A"]
    assert expected and (not filtered or len(expected) < len(ORDERED_KEYS))
    params = {"sample_id": sid, "limit": limit}
    if filtered:
        params.update(
            {"chrom": "CONTIG_A"}
            if endpoint == "/api/annotations"
            else {"filter": "chrom:CONTIG_A"}
        )
    collected = []
    for _ in range(len(expected) + 1):
        response = client.get(endpoint, params=params)
        assert response.status_code == 200
        page = response.json()
        assert page["items"]
        collected.extend(_keys(page["items"]))
        if not page["has_more"]:
            assert [page[f"next_cursor_{part}"] for part in ("chrom", "pos", "rsid")] == [None] * 3
            break
        last = page["items"][-1]
        assert [page[f"next_cursor_{part}"] for part in ("chrom", "pos", "rsid")] == [
            last[part] for part in ("chrom", "pos", "rsid")
        ]
        params.update(
            {f"cursor_{part}": page[f"next_cursor_{part}"] for part in ("chrom", "pos", "rsid")}
        )
    else:
        pytest.fail("Complete cursor traversal did not terminate")
    assert collected == expected
    assert len(collected) == len(set(collected))


def test_empty_terminal_page_clears_entire_cursor(cursor_client):
    client, sid, endpoint = cursor_client
    response = client.get(
        endpoint,
        params={
            "sample_id": sid,
            "cursor_chrom": "ZZ_SYNTHETIC",
            "cursor_pos": 999,
            "cursor_rsid": "end",
        },
    )
    assert response.status_code == 200
    page = response.json()
    assert page["items"] == [] and page["has_more"] is False
    assert [page[f"next_cursor_{part}"] for part in ("chrom", "pos", "rsid")] == [None] * 3


@pytest.mark.parametrize("chrom,pos", [("1", 20), ("CONTIG_A", 20), ("CONTIG_B", 0), ("X", 0)])
def test_position_seek_is_inclusive_only_for_explorer(cursor_client, chrom, pos):
    client, sid, endpoint = cursor_client
    response = client.get(
        endpoint, params={"sample_id": sid, "cursor_chrom": chrom, "cursor_pos": pos}
    )
    if endpoint == "/api/annotations":
        assert response.status_code == 422
        assert (
            response.json()["detail"]
            == "cursor_chrom, cursor_pos and cursor_rsid must be sent together."
        )
        return
    assert response.status_code == 200
    first = next(
        index for index, key in enumerate(ORDERED_KEYS) if key[0] == chrom and key[1] >= pos
    )
    assert _keys(response.json()["items"]) == ORDERED_KEYS[first:]


@pytest.mark.parametrize(
    "cursor",
    [
        {"cursor_chrom": "1"},
        {"cursor_pos": 20},
        {"cursor_rsid": "rsYELIZTLI_A"},
        {"cursor_chrom": "1", "cursor_rsid": "rsYELIZTLI_A"},
        {"cursor_pos": 20, "cursor_rsid": "rsYELIZTLI_A"},
    ],
)
def test_malformed_partial_cursor_is_rejected(cursor_client, cursor):
    client, sid, endpoint = cursor_client
    response = client.get(endpoint, params={"sample_id": sid, **cursor})
    assert response.status_code == 422
    assert "cursor_chrom" in response.json()["detail"]
