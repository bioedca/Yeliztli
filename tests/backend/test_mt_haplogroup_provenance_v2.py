"""Fail-closed tests for the schema-v3 mtDNA provenance frontier."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import scripts.build_haplogroup_bundle as haplogroup_builder
from scripts.build_haplogroup_bundle import (
    _MT_SOURCE,
    _index_mt_tree,
    _mt_migration_complete_ready,
    _mt_parse_substitution_notation,
    _mt_validate_exact_record,
    _summarize_mt_provenance,
    _validate_mt_registry_against_tree,
    _validate_mt_source,
    _validate_mt_source_schema,
    build_bundle,
    build_mt_tree,
)

LEGACY_EXACT_NAMES_SHA256 = "7d968626b02229ba77f7e58a32b337621c71a1a071e4564d5e815d5c3dee4d5d"
LEGACY_V1_SEMANTIC_SHA256 = "521dedbac66952e7df628dda8da495b6e03f640b3b6765006835d805cd32d63a"
LEGACY_V1_COVERAGE_SHA256 = "375c6a5af32e22bd71026391b5a0552bfa260bac09cf6666f84bab6ea52b7947"
BASELINE_COMMIT = "e463604fc5b4af4d5887c9e9a76c2f54598ef312"
BASELINE_SNAPSHOT_SHA256 = "f8aecb8ba02e5c2becbccfc40846bd3c8668d4b8c6de5be1761ab78c0d83a87e"
BASELINE_EXACT_NAMES_SHA256 = "3e3386bf2d57ce5814df595576223e08addccba96c92818b7d1cf338b02bf5d9"
BASELINE_V1_SEMANTIC_SHA256 = "c044b73c08b339d0be782306b84d593982b13242d995d541522da6f5bc9fc7c6"
BASELINE_V1_COVERAGE_SHA256 = "d88d4491671f99175bea3c6188affb3b0bbbd31681e0f7c35103ac4f194da6e6"
BASELINE_V2_REGISTRY_SEMANTIC_SHA256 = (
    "3eaa8bb5a9cc33c1a892bd70a7007b8293c2698d4e541a95566f7547c914c553"
)
BASELINE_V2_COVERAGE_SHA256 = "9e9d25bd07652d0637fde59d9292b6a4cba1c593268c2301bba1c910b9bd338b"
LOCKED_EXACT_NAMES_SHA256 = "cbd8af4204fdbabb36d9c97a1ecb7100279d71d337f9e56aecc9dc44ccfd1454"
LOCKED_EXACT_SEMANTIC_SHA256 = "5b2a64dc6b5328bdb4523938ebcfd079a2a6ea3129b5fda7d9cc379ea3649fe0"
LOCKED_EXACT_COVERAGE_SHA256 = "6763bae78f35afc14e8782cdd0b179c06980db2836f43cb9c7d169794ec19d5f"
BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "0dc2cc812e511bc89b76fca6ed13614d8ddb75a6ebe6321bde670096c44fba61"
)
LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "cbd8af4204fdbabb36d9c97a1ecb7100279d71d337f9e56aecc9dc44ccfd1454"
)
BASELINE_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256 = (
    "ecc1dbf4c93872031e102ee166eac50e31d6468395e5d0053357af44f8a9785a"
)
LOCKED_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256 = (
    "a7e8897c43b9144b4363e9eb3e50d0fc662444a98e55ad09d69710db27af0160"
)
INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256 = (
    "7b4848980e34ca1eff9739f964906d68eb4acdbbcd5e93227e17ece79296aefb"
)
INITIAL_PENDING_NAMES_SHA256 = "996c2c96c22d37a2aa7edf1f4639d626ccc5199ecc5eb35984aa84204e05a591"
ARRAY_MANIFEST_SHA256 = "42de22517a4644884596e36b0499a4fc45f264986c63f6fb239452b88719f977"
SOURCE_METADATA_SHA256 = "13755a154c19c603bac63a2195287165271571ece1e36e178a666aa35184d04b"
STATE_PARTITION_SHA256 = "f9a0d2ecd09f05ae1d5fbd41123d0d9e63b79d8f08c2e3b1c4c1f873ebb6a1fd"
BASELINE_EMITTED_TREE_SHA256 = "02a40be2096dd8c60e6e2934ba68a813f07478117a749e60e94e0608bed21914"
LOCKED_EMITTED_TREE_SHA256 = "0d3f25360e57573a61910787224ddfc701fb77515208857132791ec936570d31"
U5_CONFLICT_EVIDENCE_PACKET = (
    Path(__file__).resolve().parents[2]
    / "data/science-evidence/2026-08-03-u5-16270-conflict-guard"
)

PRIMARY_EXPORTS = ["pgp_4139", "pgp_4162", "pgp_4187", "pgp_huA08F4D"]
HISTORICAL_EXPORTS = [*PRIMARY_EXPORTS, "pgp_1050"]

EXPECTED_EXPORTS = {
    "pgp_4139": {
        "filename": "pgp_4139.txt",
        "vendor": "23andMe",
        "generated": "2020-09-10",
        "role": "primary_modern_23andme",
        "sha256": "f4a37d23e75d7406afef22b55fe723eb5c8c7901823365410fd2abf988fd4619",
        "line_count": 638564,
    },
    "pgp_4162": {
        "filename": "pgp_4162.txt",
        "vendor": "23andMe",
        "generated": "2024-07-30",
        "role": "primary_modern_23andme",
        "sha256": "2e9cbdd1a69ad7b226751d2741c0b56a7d1f1625a4e6e10384239783dadefa94",
        "line_count": 643555,
    },
    "pgp_4187": {
        "filename": "pgp_4187.txt",
        "vendor": "23andMe",
        "generated": "2017-12-21",
        "role": "primary_modern_23andme",
        "sha256": "19481e7e2e94f441ce25d2d98ecbe90b3de59533c40b52242ed9572d3cb91127",
        "line_count": 638483,
    },
    "pgp_huA08F4D": {
        "filename": "pgp_huA08F4D.txt",
        "vendor": "23andMe",
        "generated": "2026-04-29",
        "role": "primary_modern_23andme",
        "sha256": "8663f40f503b4a2873ef152095d88762c6d739af72876c637e38727693bf251c",
        "line_count": 631479,
    },
    "pgp_ancestry_4190": {
        "filename": "pgp_ancestry_4190.txt",
        "vendor": "AncestryDNA",
        "generated": "2018-04-22",
        "role": "other_vendor_comparator_only",
        "sha256": "9ccba5275793a6e07fe191e1ce92eb9ea3c7095159f0a4572a3de2990d984e58",
        "line_count": 650429,
    },
    "pgp_1050": {
        "filename": "pgp_1050.txt",
        "vendor": "23andMe",
        "generated": "2014-02-01",
        "role": "historical_fifth_23andme_only",
        "sha256": "30b6e03db180e17b097e06aa94d9352ca195b2948e38bde28ced3285fb8921c7",
        "line_count": 1001437,
    },
}

EXPECTED_COHORTS = {
    "primary_four_23andme": {"export_ids": PRIMARY_EXPORTS},
    "historical_five_23andme_including_2014": {
        "extends": "primary_four_23andme",
        "export_ids": HISTORICAL_EXPORTS,
    },
}

DIRECT_MOTIF_EXACT_NODES = [
    "A",
    "A2",
    "A5",
    "B4",
    "B4a",
    "B4b",
    "B4c",
    "B5",
    "C",
    "C1",
    "C4",
    "C5",
    "D",
    "D1",
    "D2",
    "D3",
    "D4",
    "D4a",
    "D4b",
    "D5",
    "E",
    "F",
    "F1",
    "F1a",
    "F1b",
    "F2",
    "G",
    "G1",
    "G2",
    "G2a",
    "H",
    "H1",
    "H10",
    "H11",
    "H13",
    "H13a",
    "H1a",
    "H1a1",
    "H1b",
    "H1c",
    "H1e",
    "H2",
    "H2a",
    "H2a1",
    "H2a2a",
    "H2a2a1",
    "H3",
    "H4",
    "H5a",
    "H6",
    "H6a",
    "H7",
    "HV0",
    "HV1",
    "I",
    "J",
    "J1",
    "J1b",
    "J1c",
    "J1d",
    "J2",
    "J2a",
    "J2b",
    "JT",
    "K",
    "K1",
    "K1a",
    "K1b",
    "K2",
    "K2a",
    "K2b",
    "L0",
    "L0a",
    "L0a1",
    "L0a2",
    "L0b",
    "L0d",
    "L0d1",
    "L0d2",
    "L0f",
    "L0k",
    "L1",
    "L1b",
    "L1b1",
    "L1b2",
    "L1c",
    "L1c1",
    "L1c2",
    "L1c3",
    "L2",
    "L2a",
    "L2a1",
    "L2a2",
    "L2b",
    "L2b1",
    "L2c",
    "L2d",
    "L2e",
    "L3",
    "L3a",
    "L3b",
    "L3b1",
    "L3d",
    "L3e",
    "L3e1",
    "L3e2",
    "L3f",
    "L4",
    "L4a",
    "L4b",
    "L5",
    "L5a",
    "L5b",
    "L6",
    "M",
    "M1",
    "M7",
    "M7a",
    "M7b",
    "M7c",
    "M8",
    "M8a",
    "M9",
    "N",
    "N1",
    "N1a",
    "N1b",
    "N9",
    "N9a",
    "N9b",
    "P",
    "R",
    "S",
    "S1",
    "S2",
    "T",
    "T1",
    "T1a",
    "T2",
    "T2a",
    "T2b",
    "T2c",
    "T2e",
    "T2f",
    "U",
    "U1",
    "U1a",
    "U1b",
    "U2",
    "U2e",
    "U3",
    "U3a",
    "U3b",
    "U4",
    "U4a",
    "U4b",
    "U4c",
    "U5a",
    "U5a1",
    "U5a2",
    "U5b",
    "U5b1",
    "U5b2",
    "U6",
    "U6a",
    "U7",
    "U8",
    "U8a",
    "U8b",
    "U9",
    "V",
    "V1",
    "V7",
    "W",
    "W1",
    "W3",
    "X",
    "X1",
    "X2",
    "X2a",
    "X2b",
    "Y1",
    "Y2",
    "Y_mt",
    "Z",
    "Z1",
]
DIRECT_MOTIF_LEGACY_PARTIAL_NODES: list[str] = []

ROOT_L0_NODES = [
    "L0",
    "L0a",
    "L0a1",
    "L0a2",
    "L0b",
    "L0d",
    "L0d1",
    "L0d2",
    "L0f",
    "L0k",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
]

BATCH02_RECORD_SHA256 = {
    "L1b": (
        "018b36a18990bcdb81b677d226407873dce1f55b8dc55665d57c999d44b216a2",
        "79fee1fccdab23a53773ef1292e65b7f8f3fb670aed373e29835250cf87c0e1b",
    ),
    "L1b1": (
        "26db8ace969a35579783a19d2d0af15db11d483845905d0519fc91e93165d630",
        "8ebb16cde84669276d662425e5d49f2e711560202cb996c008d9311f83e8fccd",
    ),
    "L1b2": (
        "f24f164f6fcc7de8f1565c138f864379c0beea20c879f5dadcc4ef719ade2503",
        "786b9fa21d97beb8f964503c83baf6b8344a78694a9cf05387126c448337a128",
    ),
    "L1c": (
        "34d876f487e956ef5ed41f5e3e12b1d077871d37db4ea5f7ef19ebd6e84148d2",
        "06e746537d8fd140d5fafb8436e2cff9d80502d6b4a40535e2e9166d1accd934",
    ),
    "L1c1": (
        "7ba6d252929aea75c21ac127d92a026b7cb7e2eb3069db0e4b7a15aedee2ae7a",
        "d91ad8c212f34b13bbd4aa25cfc35ab94df7a2a66e2b9dee776105dd53dd4fbd",
    ),
    "L1c2": (
        "71fe5b913854134c2bf988d5765de7dd0d49c6cdc984acde5283b30d89394ae6",
        "720ae3ee856e54cd71abf6c1202958071fcdf538a9cf295c695e46fe2f46fbbe",
    ),
    "L1c3": (
        "a86ff8682b992d9d3981c574b40b0bf8962e19388bf9dbcc64dbee3fccf3c520",
        "61c8d4f62b5e7ec1279675cb86552608453dfd61174ae68229423aeb7f1317ba",
    ),
    "L2a": (
        "92de4368c7da724a873a862b1acb75b3baa06f96fdea9af1c92b76b5ea2b65e7",
        "beb5df1623626c2e83aef2ab16c30d0dc13f81a0691ab7cd736cee59784a98eb",
    ),
    "L2a1": (
        "77e60070fe0bb5293d4cd3aae7ef930c2dbacbd51b394094ce76a3183bdd632b",
        "79fd851acffe9662cfdf767b97a293f9f96c86caf7b4a033d5d8d38b2715bcf8",
    ),
    "L2a2": (
        "9bc58901603e9dd80fd1ebceaad2733c351416e9c8bda54f872f9b6d1a50a5ed",
        "5576ca7f343b7c02c831c1f2b8f5816e785734ac97e031137803aabde9215934",
    ),
    "L2b": (
        "611bd57866cbd75ed8ae089dafeb2134b5ad4fb6149549ed8d72bd913ff3c3f9",
        "f3d5f51d9810d45ce42bc8799845d4f9ef602e11b4f22f4a5a66a9479a277edd",
    ),
    "L2b1": (
        "e4561e1ceda5b37ed8a4a19009c5d50ce5609b0ceb4bf6ca112189547ede05f4",
        "04e403cbd63e387510538292fa9b459928a853d8468e90476c01981fea3e19c8",
    ),
    "L2c": (
        "ad1d24ea430a991676884903145f5cce8e379ac6864eb566d8cfb42ae725b1d3",
        "51ae15e1bd36f737d85d8d5fa2c374a16e90515cff2e53d3509259e47bea7e25",
    ),
    "L2d": (
        "18d3d720aa391cb0150b032610a0083ba903f9a971afa5e02787725b11c09d51",
        "043cbd489a35f17d5ad6b0102c7ec48d01ea77f2c058d7c35c11fdf877a0b37c",
    ),
    "L2e": (
        "8076a34349a94eede9889f6706597aa1fd2d594d05118dd1c43472dfa5f4deb0",
        "ff941690e51e46cfc1e259ee8f5835d047972aa4b6148b7f398c8e7ba101f0e0",
    ),
    "L4a": (
        "413f4334dca3de70524b273fc265cf3761f4d3ba52de37c86c409912d7883570",
        "d665c865a602fc392625be5be6b86d914a8db8df5bd200df5c611393bf07a248",
    ),
    "L4b": (
        "f6b8e853cceccab2d4ab63a0cfbe1ba0367bcb4d23da288e9166fb39be0cfe26",
        "43ffa4e1cf902ea42c372196bdf41cae88e09d37b7211e6911fc4284af739e09",
    ),
    "L5a": (
        "2bfc05d678e9e1a8e1b522b511607a7c964614f712e94d84568dbebc15c5f3a2",
        "e0840db8001622f23db5254d7851538121b85349f5385ed699b6771712d4e916",
    ),
    "L5b": (
        "234014a7983fdbe96bbdeeb2c9c9a4489e919c417f1d8906bd705edc14bbe942",
        "fcdee436c003a5614eb0d56694d23ab1cdc4a045e0edcde4443d376c2f3fd31a",
    ),
}

BATCH02_TOPOLOGY = {
    "L1b": ("L1", "L1", "L1", ()),
    "L1b1": ("L1b", "L1b", "L1b", ()),
    "L1b2": ("L1b", "L1b", "L1b2'3", ("L1b2'3",)),
    "L1c": ("L1", "L1", "L1", ()),
    "L1c1": ("L1c", "L1c", "L1c1'2'4'6", ("L1c1'2'4'5'6", "L1c1'2'4'6")),
    "L1c2": ("L1c", "L1c", "L1c2'4", ("L1c1'2'4'5'6", "L1c1'2'4'6", "L1c2'4")),
    "L1c3": ("L1c", "L1c", "L1c", ()),
    "L2a": ("L2", "L2", "L2a'b'c'd", ("L2a'b'c'd",)),
    "L2a1": ("L2a", "L2a", "L2a1'2'3'4", ("L2a1'2'3'4",)),
    "L2a2": ("L2a", "L2a", "L2a2'3", ("L2a1'2'3'4", "L2a2'3'4", "L2a2'3")),
    "L2b": ("L2", "L2", "L2b'c", ("L2a'b'c'd", "L2b'c'd", "L2b'c")),
    "L2b1": ("L2b", "L2b", "L2b", ()),
    "L2c": ("L2", "L2", "L2b'c", ("L2a'b'c'd", "L2b'c'd", "L2b'c")),
    "L2d": ("L2", "L2", "L2b'c'd", ("L2a'b'c'd", "L2b'c'd")),
    "L2e": ("L2", "L2", "L2", ()),
    "L4a": ("L4", "L4", "L4", ()),
    "L4b": ("L4", "L4", "L4", ()),
    "L5a": ("L5", "L5", "L5", ()),
    "L5b": ("L5", "L5", "L5", ()),
}

BATCH02_FLATTENED_STEPS = {
    "L1b2'3": ("L1b", ("C195T",), 1, "flattened_unreportable_source_intermediate"),
    "L1c1'2'4'5'6": ("L1c", ("A297G",), 2, "flattened_source_intermediate"),
    "L1c1'2'4'6": ("L1c1'2'4'5'6", ("C198T", "T10321C"), 2, "flattened_source_intermediate"),
    "L1c2'4": (
        "L1c1'2'4'6",
        ("5899.1C", "C12049T", "A13149G"),
        1,
        "flattened_source_intermediate",
    ),
    "L2a'b'c'd": ("L2", ("T195C!", "T11944C"), 4, "flattened_source_intermediate"),
    "L2a1'2'3'4": (
        "L2a",
        ("C2789T", "C7274T", "A7771G", "G11914A!", "A13803G", "A14566G", "C16294T"),
        2,
        "flattened_source_intermediate",
    ),
    "L2a2'3'4": (
        "L2a1'2'3'4",
        ("C146T!!", "A6752G", "T16189C!", "T16229C", "T16311C!"),
        1,
        "flattened_source_intermediate",
    ),
    "L2a2'3": ("L2a2'3'4", ("G709A", "C15939T", "C16291T"), 1, "flattened_source_intermediate"),
    "L2b'c'd": ("L2a'b'c'd", ("C2332T",), 3, "flattened_source_intermediate"),
    "L2b'c": (
        "L2b'c'd",
        ("C198T", "G1442A", "T7624a", "G12236A", "G15110A", "G15217A"),
        2,
        "flattened_source_intermediate",
    ),
}

BATCH02_OLD_MARKERS = {
    "L1b": ((6185, "C"), (10115, "C"), (16126, "C")),
    "L1b1": ((5393, "T"), (12950, "G")),
    "L1b2": ((6446, "G"), (14869, "A")),
    "L1c": ((1048, "T"), (9072, "G"), (16129, "C")),
    "L1c1": ((3483, "T"), (7859, "C")),
    "L1c2": ((8655, "T"), (13404, "C")),
    "L1c3": ((9947, "A"), (15452, "A")),
    "L2a": ((3594, "C"), (5836, "G"), (13803, "G")),
    "L2a1": ((3918, "A"), (11914, "A"), (15784, "C")),
    "L2a2": ((4158, "C"), (10688, "A")),
    "L2b": ((1227, "A"), (6680, "C")),
    "L2b1": ((6722, "G"), (14769, "G")),
    "L2d": ((1442, "A"), (6293, "C")),
    "L2e": ((3200, "A"), (8404, "T")),
    "L4a": ((7424, "A"), (14401, "C")),
    "L4b": ((2626, "C"), (10289, "G")),
    "L5a": ((7055, "G"),),
    "L5b": ((11002, "G"),),
}

BATCH03_RECORD_SHA256 = {
    "L3a": (
        "c0cc003d21eaab1802620b7f28200324fed51b6a79df5fe53801cb7dfb6dde63",
        "418abba15447cf55517ee0e795c953ce6f9af66fbed811cbbe760aaf7656e042",
    ),
    "L3b": (
        "06610ebaf693e87afc9e2ee9becbc684ad09e33e41ee704e8dee2e479d7a28dc",
        "807e95d5e16f91012352a95008c9e2e248c118419e30788bec7dc7549a2a371e",
    ),
    "L3b1": (
        "df59a3f23db0f5270c4dc3d5bc11dc443d4a74a349e788c7b19774a580920d3d",
        "c0bc6552a0776700cf0318eba2d591cf2320993f5882c038d7b8cf7272ceaf1c",
    ),
    "L3d": (
        "fbeb0c26465bbf24185c3f238dd18046f4a8a49e2b4a0d366fab9e3f9b6fcbc2",
        "ab0b0405be04bc310e2176c8ada7c49dc8adc49587e912a10e1066d508970670",
    ),
    "L3e": (
        "f56b97514015c06022365ad923429c787d2098a24411ddc697c05fbd9c482f1a",
        "d4dc5a099fa52b3806bad4390145d5bedbad0711e055edc18d4b0b8a90bb0f87",
    ),
    "L3e1": (
        "81412c8cda9d284ae6f926b0bfa521f5fd767326d3dd7ceb64281f1d6a837166",
        "c365fe453ad524352754c28d01fbb0ae99c0d4bea45c75cbf751b7b2515f741a",
    ),
    "L3e2": (
        "138637974450d27986689103991123097eeb98ef2c325a7530a1afd580591680",
        "2c4e9661e93c4eb10381748e05d14b61d18f579f6338a383e0ca2cae12138039",
    ),
    "L3f": (
        "284f644e32f77f64c65ddde931249e65996371ef25f86cbe0e1e476a614fa64f",
        "b49a96b248de56943d220aa2d19716f73b93a50f96e6370fd3ae03bf1392cec9",
    ),
    "M": (
        "8964160a7500da51dac8a99d854be729b39641a8653f019b146084e01b98bf70",
        "111d37fddc3a1d4213fa6be0addd932533a2d03d71ed3e3b22957a7c1a60e057",
    ),
    "D": (
        "e8ca8c4859ddb4a945e11469fdc25176240bf949e74ecfbb4d0b4bb61df3314c",
        "569432a88c24d34846fab0fb33c08adfede62d3b0b3304a9a477b6cac81452a7",
    ),
    "G": (
        "f1963ec2141894b43cf9d1dda3425e796620c9d86b7019b210174031a28a8e49",
        "7ec512d786776a092460cc37697605233cfc1c9ee07de3305bbea7b95d185b17",
    ),
    "M1": (
        "ec90624b8b1f4b74510a0cabf5a2d08d648ff9b505c955cbb99293d7f89f6f11",
        "b54b02d362824cddd657b9aac98e6a5607281bf4eb59ee873613474095f87c2f",
    ),
    "M7": (
        "63dfc6ae72f4b0d6c04d41365139ce518b1d5812b3c1bf75f0a023b0d2a1ba13",
        "2e7f010c075818221a5efa33fb84fdc033bbbacf8edf56b124dabd8abd89f9a2",
    ),
    "M8": (
        "1dc1f4d48a6d1f31c911937fa1e3f7cfa37e114f1779ab69383b1d12a76089d9",
        "8e9e8cdc667248bda616007d09483f71b901dc8523ccb00f6d54fa9e31fa0608",
    ),
    "M9": (
        "014f774a307ffd537de3f054ff86eda6a0239a6bc4c494e65fbd237e68233d3e",
        "f9e5b8299a250ed3391e1e69acd436e9bbc22b45145276b2f3b1657e8253998a",
    ),
}

BATCH03_TOPOLOGY = {
    "L3a": ("L3", "L3", "L3", ()),
    "L3b": ("L3", "L3", "L3b'f", ("L3b'f",)),
    "L3b1": ("L3b", "L3b", "L3b", ()),
    "L3d": ("L3", "L3", "L3c'd", ("L3c'd",)),
    "L3e": ("L3", "L3", "L3e'i'k'x", ("L3e'i'k'x",)),
    "L3e1": ("L3e", "L3e", "L3e", ()),
    "L3e2": ("L3e", "L3e", "L3e", ()),
    "L3f": ("L3", "L3", "L3b'f", ("L3b'f",)),
    "M": ("L3", "L3", "L3", ()),
    "D": ("M", "M", "M80'D", ("M80'D",)),
    "G": ("M", "M", "M12'G", ("M12'G",)),
    "M1": ("M", "M", "M1'20'51", ("M1'20'51",)),
    "M7": ("M", "M", "M", ()),
    "M8": ("M", "M", "M", ()),
    "M9": ("M", "M", "M", ()),
}

BATCH03_FLATTENED_STEPS = {
    "L3b'f": (
        "L3",
        (("T15944d", False),),
        2,
        "flattened_unreportable_source_intermediate",
    ),
    "L3c'd": (
        "L3",
        (("T152C!", False), ("A13105G!", True)),
        1,
        "flattened_source_intermediate",
    ),
    "L3e'i'k'x": (
        "L3",
        (("C150T", False), ("A10819G", False)),
        1,
        "flattened_source_intermediate",
    ),
    "M1'20'51": (
        "M",
        (("T14110C", True),),
        1,
        "flattened_source_intermediate",
    ),
    "M12'G": (
        "M",
        (("G14569A", True),),
        1,
        "flattened_source_intermediate",
    ),
    "M80'D": (
        "M",
        (("C4883T", True),),
        1,
        "flattened_source_intermediate",
    ),
}

BATCH03_OLD_MARKERS = {
    "L3a": ((4386, "C"), (10086, "G")),
    "L3b": ((2352, "C"), (10143, "A")),
    "L3b1": ((6221, "C"), (12049, "A")),
    "L3d": ((8618, "C"), (15514, "C")),
    "L3e": ((2352, "C"), (14905, "A")),
    "L3e1": ((3675, "A"), (9554, "A")),
    "L3e2": ((2352, "C"), (5261, "A")),
    "L3f": ((4218, "C"), (15670, "C")),
    "M": ((489, "C"), (10951, "A"), (14783, "C"), (15043, "A")),
    "G": ((4833, "G"), (5108, "C")),
    "M1": ((6446, "A"), (6680, "C"), (12950, "C"), (16129, "A"), (16249, "C")),
    "M7": ((4071, "T"), (6455, "T")),
    "M9": ((3394, "C"), (14308, "A"), (16362, "C")),
}

BATCH04_RECORD_SHA256 = {
    "D1": (
        "7450dada564b596fba7e0e2ac2f9c69c6bec4dfdd7acb3f72bb39215591daa93",
        "6ccd07b252aa4139319b974944dfe254473605c23376efbf997954b2b40326d6",
    ),
    "D2": (
        "d7fa02d7536a40a074fe1ce7d56e613b650ae190ce5e443d4d123df36c3cf543",
        "f32c4d81724a33c98166cededa8a0892cffd0eb6ac44fd2e762de8b0b74e48bb",
    ),
    "D3": (
        "d57f1eb856c205c5894b682bc82a97f704b4dd8ae429fe83301651fff75876ef",
        "02a3b59c4fc22db3c29b0c47271906c065112e420b59c35cf92df7c611ebc8a6",
    ),
    "D4": (
        "789c4a0d07fc58af823d3bcbae2ad5038757473098b7f26235081cf980520647",
        "5312a3a4f744b6387ed6514df8eacf0321c5fa3d26e5ed51c84138eb38c5b06c",
    ),
    "D4a": (
        "62c4d3de5e6560427bfced8fb95f7fc712d68dcdf1b756f86c877f6138b2ee01",
        "d10df1b517f501f0e4765be027aa15738e8f94295f03c5670f228e31d634f488",
    ),
    "D4b": (
        "e66386f620edc6195ccf77b1cb0fe21bea1f903d2b77e6b79070f12a5caf28e5",
        "8b9f1df9a09b9d95f739960c0d712d95a522985f5353d48d04dfd7cf5de0b75b",
    ),
    "D5": (
        "80f713c929fff5c7308e21875c6faa0473630087b0c59e204de11289666c69fa",
        "d1f35bab715122076ed51c41354d622e1b379d294409e7a804bd2f7b7b41af33",
    ),
}

BATCH04_TOPOLOGY = {
    "D1": ("D4", "D4", "D4", ()),
    "D2": ("D4", "D4", "D4e1", ("D4e", "D4e1'3", "D4e1")),
    "D3": ("D4b", "D4b", "D4b1c", ("D4b1", "D4b1c")),
    "D4": ("D", "D", "D", ()),
    "D4a": ("D4", "D4", "D4", ()),
    "D4b": ("D4", "D4", "D4", ()),
    "D5": ("D", "D", "D+16189", ("D+16189",)),
}

BATCH04_FLATTENED_STEPS = {
    "D+16189": (
        "D",
        (("T16189C!", False),),
        1,
        "flattened_unreportable_source_intermediate",
    ),
    "D4b1": (
        "D4b",
        (("C10181T", False), ("T15440C", False), ("A15951G", False), ("G16319A", False)),
        1,
        "flattened_source_intermediate",
    ),
    "D4b1c": (
        "D4b1",
        (("T239C", False), ("A297G", False), ("G951A", False)),
        1,
        "flattened_source_intermediate",
    ),
    "D4e": (
        "D4",
        (("C11215T", False),),
        1,
        "flattened_source_intermediate",
    ),
    "D4e1'3": (
        "D4e",
        (("C9536T", False),),
        1,
        "flattened_source_intermediate",
    ),
    "D4e1": (
        "D4e1'3",
        (("G3316A", False), ("(T16092C)", False)),
        1,
        "flattened_source_intermediate",
    ),
}

BATCH04_FLATTENED_PREFIX_POSITIONS = {
    "D2": {3316, 9536, 11215, 16092},
    "D3": {239, 297, 951, 10181, 15440, 15951, 16319},
    "D5": {16189},
}

BATCH04_OLD_MARKERS = {
    "D1": ((5178, "A"), (16325, "C")),
    "D3": ((3394, "C"), (10181, "T")),
    "D4a": ((12026, "G"),),
    "D5": ((1048, "T"), (4883, "T")),
}

BATCH05_NAMES = [
    "E",
    "G1",
    "G2",
    "G2a",
    "M7",
    "M7a",
    "M7b",
    "M7c",
    "M8a",
    "C",
    "C1",
    "C4",
    "C5",
    "Z",
    "Z1",
]

BATCH05_RECORD_SHA256 = {
    "E": (
        "11f2a05da692cb1f62740f78b8bffc626f215171bd847d70a162cc25e5ecef66",
        "e23c545f1197e025def1f6e58eeca4215c509bf2b114a38f9dca9d8ed5ddc13e",
    ),
    "G1": (
        "4325cf1d3fc6358e08df0d5c707692845ee9b6c2aedba04c31e8808eeb3353a6",
        "97949521a27841e4ad429f0a4202d637d80f6c06b5aba506590b087514fe8077",
    ),
    "G2": (
        "390190958a2550824afb35b0cc122898d5b871fc9a7645a1e9f17f038ed2fa90",
        "c6220d202c983d06340c5b58fdb3da493768f30a85aec075c1d5f0cf8995caea",
    ),
    "G2a": (
        "9586baf50d97949ffd300fa0f3d5e31ec43394258cdf1fc621705e701a7f4a59",
        "ff660f05e3b1595b8bfe1f5234b7f039a4dcebd4c7b1dc7fbf751eb43f447b4d",
    ),
    "M7": (
        "63dfc6ae72f4b0d6c04d41365139ce518b1d5812b3c1bf75f0a023b0d2a1ba13",
        "2e7f010c075818221a5efa33fb84fdc033bbbacf8edf56b124dabd8abd89f9a2",
    ),
    "M7a": (
        "06f4f128b91fe5035da466156c2059ca0b11b7892f925e299afa4b80403125c4",
        "69799972589eb050df0d5a3caf2d9bc3243443f587fd32efe213bec8621272fe",
    ),
    "M7b": (
        "038093154ca6f6a0bc9764e5f7b1f3a73db5404d322437cdb7a399046850d0b1",
        "0ff31c95ac5bc9ba6f29ce25d1db89f74fc919a9499cbbe145346768a8970323",
    ),
    "M7c": (
        "2a4ee0990185cd6e41ed70f41b1af864539dbc680e31841508807f4f950428ca",
        "74096bdbb22fdbdffa0f09840b41eac7c936b756d6aff06e65842bd9004441d5",
    ),
    "M8a": (
        "1727b3cc1bdb2185c3af1e6561356ea3ff15419af70f88010da2d191796742d7",
        "63c0aaf8541840c25777019ef1f1c5fde44541d1404ecd0a52622927dbc3b23c",
    ),
    "C": (
        "4ee661d543ed351ba86d5df0999efd5aae7530a990f986406e6e8d8404808d10",
        "a0f0f9f573f52f1a00c2f6d86df0a5ad5f49123b4e148387efbc88d443b18102",
    ),
    "C1": (
        "082c3ea9a02749dc279409fbcf37a3c54507981369bdf92517d00244fb4d0096",
        "490bb53689a3ac00cd6cee57777bd5037dcd775c64b1fb375697d0e856b7b7dd",
    ),
    "C4": (
        "f57d30e05bcdb63176cb9c72a765fd85b435e6dd878cea23ca5c9e5fc0cf9f1e",
        "96c7afcc85d02fbe0b13369f484865e7ec9dc0a0a46982ea7d25dfccbd9232c1",
    ),
    "C5": (
        "3b195ddaf40280bdfefc0bd4618652aa39690a7b3e650c152e884b5da03d4b8d",
        "39ed4cae3242dcdeb41fa33165385b17d1e62fbdcb74278e271c5b54b45c1060",
    ),
    "Z": (
        "55dc9263db9fedba426a4de1eaba2cb008354ddce9928762b8cf18c866cbcfcf",
        "9b971e884989c3074b2a81da1dbbeb2ac6561e7ca7ed4772da5b812e91fcaa69",
    ),
    "Z1": (
        "3ff74ac8e542ae2f4c6e274a4dcffe833962c6c8096fd0a6b4b898a3df8adf54",
        "eaf32f60237be090e4e4605c3dca954689d41bcf66e8c193dc7abdcffeb13255",
    ),
}

BATCH05_PROMOTIONS = {"C1", "C4", "C5", "E", "G2a", "M7a", "M7b", "M7c"}

BATCH05_TOPOLOGY = {
    "E": ("M9", "M9", "M9", ()),
    "G1": ("G", "G", "G", ()),
    "G2": ("G", "G", "G", ()),
    "G2a": ("G2", "G2", "G2a'c", ("G2a'c",)),
    "M7": ("M", "M", "M", ()),
    "M7a": ("M7", "M7", "M7", ()),
    "M7b": ("M7", "M7", "M7b'c", ("M7b'c",)),
    "M7c": ("M7", "M7", "M7b'c", ("M7b'c",)),
    "M8a": ("M8", "M8", "M8", ()),
    "C": ("M8", "M8", "CZ", ("CZ",)),
    "C1": ("C", "C", "C", ()),
    "C4": ("C", "C", "C", ()),
    "C5": ("C", "C", "C", ()),
    "Z": ("M8", "M8", "CZ", ("CZ",)),
    "Z1": ("Z", "Z", "Z+152", ("Z+152",)),
}

BATCH05_FLATTENED_STEPS = {
    "G2a'c": (
        "G2",
        (("G9575A", True),),
        1,
        "flattened_source_intermediate",
    ),
    "M7b'c": (
        "M7",
        (("C4071T", False),),
        2,
        "flattened_source_intermediate",
    ),
    "CZ": (
        "M8",
        (("A249d", False),),
        2,
        "flattened_unreportable_source_intermediate",
    ),
    "Z+152": (
        "Z",
        (("T152C!", False),),
        1,
        "flattened_source_intermediate",
    ),
}

BATCH05_SOURCE_ONLY_PREFIX_POSITIONS = {
    "M7b": {4071},
    "M7c": {4071},
    "C": {249},
    "Z": {249},
    "Z1": {152},
}

BATCH05_OLD_MARKERS = {
    "E": ((7598, "A"), (12405, "T"), (14110, "C")),
    "G2a": ((7600, "A"),),
    "M7": ((6455, "T"),),
    "M7a": ((4386, "C"), (8684, "T")),
    "M7b": ((5351, "G"), (9824, "A")),
    "M7c": ((3606, "G"), (11665, "T")),
    "C1": ((6026, "T"), (11969, "A"), (13263, "G")),
    "C4": ((5979, "T"), (11365, "C")),
    "C5": ((1607, "G"), (9545, "G")),
}

BATCH06_NAMES = [
    "N",
    "A",
    "A2",
    "A5",
    "N1b",
    "N9",
    "N9a",
    "N9b",
    "Y_mt",
    "Y1",
    "Y2",
]

BATCH06_RECORD_SHA256 = {
    "N": "9b50219a3f35ddc383df6f8da90c458b92213782b213fd238fb27e88faa85685",
    "A": "113bcd83f7c9c670ae4711203f4c8b029131aafd8cfcfbf7a16aba77cd558648",
    "A2": "a1953cfc93cae1aea70a0604b6d05410b20febc368fcf3b19fc61353b383229c",
    "A5": "20e73dfaa044cc22d6113e002f9ed3e551d7580217c636c70f3dbad7b7c91075",
    "N1b": "8d2c18acaaff55b193050294fa8bf9df7191e5db55e561b6b2b59fe7b540c62f",
    "N9": "7ff0699af1782b240753da55778e28f346b71bc62ce2aac5e5d98ded3fe30618",
    "N9a": "4082f1d49bf56b7ba449064be15dc60c6c029efa125686bdf2bad494cd0ecb1a",
    "N9b": "49545298ba43e11d9d1df0ce6166b57502531a036714059deec3a2100342a309",
    "Y_mt": "1dc5db0afa8008ac917e44c2d1dd6b5d0df5f4159f7b4536738e32bc44880452",
    "Y1": "a18d54cb11966397ffda05dd23faee8fd8a0145b7ddba21d6f21556c220131e8",
    "Y2": "73ee379da72daeb629b0e2ad8e3058f5dfa403eb67ece7a4aa767b5b7d9255d2",
}

BATCH06_DIRECT_MOTIFS = {
    "N": (
        ("G8701A", False),
        ("C9540T", True),
        ("G10398A", False),
        ("C10873T", False),
        ("A15301G!", False),
    ),
    "A": (
        ("A235G", True),
        ("A663G", True),
        ("A1736G", False),
        ("T4248C", True),
        ("A4824G", True),
        ("C8794T", True),
        ("C16290T", False),
        ("G16319A", False),
    ),
    "A2": (
        ("T146C!", True),
        ("C152T!!", False),
        ("A153G", False),
        ("G8027A", True),
        ("G12007A", True),
        ("C16111T", True),
    ),
    "A5": (("A8563G", True), ("C11536T", True)),
    "N1b": (
        ("T152C!", False),
        ("G1598A", True),
        ("C2639T", False),
        ("G5471A", True),
        ("G8251A", True),
        ("A8836G", False),
        ("C16176g", True),
        ("G16390A", True),
    ),
    "N9": (("G5417A", True),),
    "N9a": (
        ("C150T", False),
        ("G5231A", True),
        ("A12358G", False),
        ("G12372A", True),
        ("C16257a", False),
        ("C16261T", True),
    ),
    "N9b": (
        ("G5147A", True),
        ("C10607T", True),
        ("G11016A", True),
        ("A13183G", True),
        ("A14893G", True),
        ("T16189C!", False),
    ),
    "Y_mt": (
        ("G8392A", True),
        ("A10398G!", True),
        ("T14178C", True),
        ("A14693G", True),
        ("T16126C", True),
        ("T16223C", True),
        ("T16231C", True),
    ),
    "Y1": (("T146C!", False), ("G3834A", True), ("(C16266T)", False)),
    "Y2": (
        ("T482C", True),
        ("G5147A", True),
        ("T6941C", True),
        ("G7859A", True),
        ("A14914G", True),
        ("A15244G", True),
        ("T16311C!", False),
    ),
}

BATCH06_TOPOLOGY = {
    "N": ("N", "L3", "L3", "L3", ()),
    "A": ("A", "N", "N", "N", ()),
    "A2": ("A2", "A", "A", "A+152+16362", ("A+152", "A+152+16362")),
    "A5": ("A5", "A", "A", "A", ()),
    "N1b": ("N1b", "N1", "N1", "N1", ()),
    "N9": ("N9", "N", "N", "N", ()),
    "N9a": ("N9a", "N9", "N9", "N9", ()),
    "N9b": ("N9b", "N9", "N9", "N9", ()),
    "Y_mt": ("Y", "N9", "N9", "N9", ()),
    "Y1": ("Y1", "Y_mt", "Y", "Y", ()),
    "Y2": ("Y2", "Y_mt", "Y", "Y", ()),
}

BATCH06_FLATTENED_STEPS = {
    "A+152": ("A", (("T152C!", False),), "flattened_source_intermediate"),
    "A+152+16362": (
        "A+152",
        (("T16362C", True),),
        "flattened_source_intermediate",
    ),
}

BATCH06_PROMOTIONS = {"A", "A2", "A5", "N1b", "N9a", "N9b"}
BATCH06_DIRECT_MOTIF_PROMOTIONS = {*BATCH06_PROMOTIONS, "Y_mt", "Y1", "Y2"}

BATCH06_OLD_MARKERS = {
    "N": ((8701, "A"), (9540, "T"), (10873, "T")),
    "A": ((235, "G"), (663, "G"), (1736, "G"), (4824, "G")),
    "A2": ((8027, "A"), (16111, "T")),
    "A5": ((11884, "G"),),
    "N1b": ((6261, "A"),),
    "N9a": ((5231, "A"), (12358, "G")),
    "N9b": ((1598, "A"), (12549, "G")),
    "Y_mt": ((8392, "A"), (10398, "G"), (14178, "C")),
}

BATCH06_PRESERVED_PROVENANCE_SHA256 = {
    "N1": "3d73c2f3f91093e7d7dfb8a09d92f142ddbc0cfcb60e8b24e60a06e1434216bb",
    "N1a": "595cfdfe75e0887c08b999e2caedca629cd2e037c2a052f8932eadccf2783d21",
    "I": "6cd5c0b4983c255032e1913e7bc724dfcf6f1c530f1b59c9be3c8cedb04fc57d",
}

BATCH07_NAMES = ["S", "S1", "S2", "W", "W1", "W3", "X", "X1", "X2", "X2a", "X2b"]

BATCH07_RECORD_SHA256 = {
    "S": "eb957e6652928bb9af8ad26500f8d96538811c79ba8231d76ebcce66cfbf5b54",
    "S1": "8bd6edbb21145b9aece27c793f6d2113345cec41d38eefd9464a6393c3f9b662",
    "S2": "484fabb6161d1fc338e7acc5a7fe85d998d89c50d5a9f9b470351559c2727d74",
    "W": "f220741f035234a16aa909506e8fe9327a9e6695a0a7de8b39cb8865a6903d3c",
    "W1": "965bccda1bb13f9baa5db18335f65db4b09e1beca325d5ace4ba599519b09159",
    "W3": "50549c4511c9461516810bd71bb5cdb5e70673aa957dc991a029f4ffaf044425",
    "X": "0ca8a7a52f231e28f9cc1827043b52c3083292167a667103e0fcc9ef112d7c3d",
    "X1": "7d1681dacfb9f64fd769d8bb078124986c52ee859440ad85c0e6ce6029ef38b7",
    "X2": "4e51591066844003ae0aa9b405702fa66bee47be3c82a2d6b457739d57871a59",
    "X2a": "36a74acaf12395abe18c6091dd0041ed25efa4be2bfb1c3cbc8558847166037f",
    "X2b": "8a7798bf73921a5f89aa90b286408d74df148514ca055a14e8ca3a3ef12131bc",
}

BATCH07_DIRECT_MOTIFS = {
    "S": (("T8404C", True),),
    "S1": (("G14384c", True), ("T16075C", True)),
    "S2": (("C2380T", True), ("G3438A", True), ("T6167C", True)),
    "W": (
        ("T195C!", False),
        ("T204C", False),
        ("G207A", True),
        ("T1243C", True),
        ("A3505G", True),
        ("G5460A", True),
        ("G8251A", True),
        ("G8994A", True),
        ("A11947G", True),
        ("G15884c", True),
        ("C16292T", True),
    ),
    "W1": (("C7864T", True),),
    "W3": (("T1406C", True),),
    "X": (
        ("T6221C", True),
        ("C6371T", True),
        ("A13966G", True),
        ("T14470C", True),
        ("T16189C!", False),
        ("C16278T!", False),
    ),
    "X1": (
        ("T5302C", True),
        ("A14587G", False),
        ("T15654C", True),
        ("(C16104T)", True),
        ("T16278C!!", False),
    ),
    "X2": (("T195C!", False), ("G1719A", True)),
    "X2a": (
        ("A200G", False),
        ("A8913G", True),
        ("T14502C", True),
        ("G16213A", False),
    ),
    "X2b": (("C8393T", True), ("G15927A", False)),
}

BATCH07_TOPOLOGY = {
    "S": ("S", "N", "N", "N", ()),
    "S1": ("S1", "S", "S", "S", ()),
    "S2": ("S2", "S", "S", "S", ()),
    "W": ("W", "N", "N", "N2", ("N2",)),
    "W1": ("W1", "W", "W", "W", ()),
    "W3": ("W3", "W", "W", "W+194", ("W+194",)),
    "X": ("X", "N", "N", "N", ()),
    "X1": ("X1", "X", "X", "X1'3", ("X1'2'3", "X1'3")),
    "X2": ("X2", "X", "X", "X1'2'3", ("X1'2'3",)),
    "X2a": ("X2a", "X2", "X2", "X2a'j", ("X2+225", "X2a'j")),
    "X2b": ("X2b", "X2", "X2", "X2b'd", ("X2+225", "X2b'd")),
}

BATCH07_FLATTENED_STEPS = {
    "N2": (
        "N",
        (
            ("A189G", False),
            ("G709A", False),
            ("G5046A", False),
            ("C11674T", False),
            ("T12414C", False),
        ),
        1,
    ),
    "W+194": ("W", (("C194T", False),), 1),
    "X1'2'3": ("X", (("A153G", False),), 2),
    "X1'3": ("X1'2'3", (("T146C!", False),), 1),
    "X2+225": ("X2", (("G225A", False),), 2),
    "X2a'j": ("X2+225", (("A12397G", False),), 1),
    "X2b'd": ("X2+225", (("G13708A", False),), 1),
}

BATCH07_MARKER_PROMOTIONS = {"X1"}
BATCH07_DIRECT_MOTIF_PROMOTIONS = {"X1", "X2", "X2a"}
BATCH07_OLD_MARKERS = {"X1": ((6253, "C"),)}

BATCH08_NAMES = ["R", "H", "H1", "H2", "H3", "H4", "H6", "H7", "H10", "H11", "H13"]

BATCH08_RECORD_SHA256 = {
    "R": "a3fbc9fdcea49ab64beecf943d48799fa74932c91556e00a226c0cbbbf17489a",
    "H": "af158d72e2a961509974dda04e6a99a2de78e9d231403e2f761335d1b3b83d62",
    "H1": "e580571219981e37e2cd4db9b74724ffa94e9817076e5a020f464669fa54d24a",
    "H2": "df14e327f934036d308d562af3770f9c101c2620c7ba43fc44aeb7c84717fbbd",
    "H3": "38d703e0a9a29ef58834ea53911277d42ca324b4d9242002ae95cfa66e837f8f",
    "H4": "474cfad34cf7f2cb9e971ee68d4191dc5d345c2c10b88dd349cca71949d4bceb",
    "H6": "a70fd7630ac117bf19c731f4c15ea29e5f8c62b54bfa758ebaf04f1770382d39",
    "H7": "cd2eea7fbeed4a1009c56751a2e90910309b39f82c8714274a5b9374ab08f74e",
    "H10": "738c755c330109b5fb97f1d5cad2e36d92cb79101915afff4273b6d2a45c531a",
    "H11": "949cccdb831e4eaa727f41f11d758b40e6a0de433b56bdd7e0bc7de30e81a89d",
    "H13": "6c0ae385ff294295ef1d6b9301a91d9b1cf4ef5940577c1ad09e16f4bbcf6c8a",
}
BATCH08_STRUCTURAL_SHA256 = {
    "R0": "67fde974f96f417dff32a6e3a82b2b0e89e6e30fe78ef867ed92adaf2a2fff9d",
    "HV": "1a52fcbe305a7d7f383308854f28b1a5a0a6bdbcf38fbd1b208d0bf1640dfca6",
    "H5": "7ff19538f9818809ef9a5424ff1500a24e7dde6945463ef74553a298e4afc852",
}
BATCH08_OMITTED_SHA256 = {
    "H5'36": "f899da1f58d58a12af2c249dba59f0d3bd9344cea4edfa83256ef0a3e446575d",
    "H+195": "d23a5a5c464a7e91d513d4c3c8f8a0b7d46e0ede018d5e9f55a54e77198e9690",
}

BATCH08_DIRECT_MOTIFS = {
    "R": (("T12705C", True), ("T16223C", True)),
    "H": (("G2706A", True), ("T7028C", True)),
    "H1": (("G3010A", True),),
    "H2": (("G1438A", True),),
    "H3": (("T6776C", True),),
    "H4": (("C3992T", True), ("T5004C", True), ("G9123A", False)),
    "H6": (("T239C", True), ("T16362C", True), ("(A16482G)", True)),
    "H7": (("A4793G", True),),
    "H10": (("T14470a", True),),
    "H11": (("T8448C", True), ("G13759A", True), ("T16311C!", False)),
    "H13": (("C14872T", True),),
}

BATCH08_TOPOLOGY = {
    "R": ("R", "N", "N", "N", ()),
    "H": ("H", "HV", "HV", "HV", ()),
    "H1": ("H1", "H", "H", "H", ()),
    "H2": ("H2", "H", "H", "H", ()),
    "H3": ("H3", "H", "H", "H", ()),
    "H4": ("H4", "H", "H", "H", ()),
    "H6": ("H6", "H", "H", "H", ()),
    "H7": ("H7", "H", "H", "H", ()),
    "H10": ("H10", "H", "H", "H", ()),
    "H11": ("H11", "H", "H", "H+195", ("H+195",)),
    "H13": ("H13", "H", "H", "H", ()),
}

BATCH08_STRUCTURAL_MOTIFS = {
    "R0": (("G73A", False), ("A11719G", False)),
    "HV": (("T14766C", False),),
    "H5": (("T16304C", False),),
}

BATCH08_STRUCTURAL_TOPOLOGY = {
    "R0": ("R0", "R", "R", "R", ()),
    "HV": ("HV", "R0", "R0", "R0", ()),
    "H5": ("H5", "H", "H", "H5'36", ("H5'36",)),
}

BATCH08_FLATTENED_STEPS = {
    "H5'36": ("H", "flattened_source_intermediate", (("C456T", False),), "H5"),
    "H+195": (
        "H",
        "flattened_unreportable_source_intermediate",
        (("T195C!", False),),
        "H11",
    ),
}

BATCH08_MARKER_PROMOTIONS = {"R", "H", "H2", "H3", "H4", "H7", "H11"}
BATCH08_DIRECT_MOTIF_PROMOTIONS = {*BATCH08_MARKER_PROMOTIONS, "H6"}

# H was the only promoted record whose old runtime markers were a strict subset
# of the audited set. R, H2, H3, H4, H6, and H7 retained their runtime markers;
# H11's replaced m.13101 set is covered with the other removed rows below.
BATCH08_OLD_MARKERS = {"H": ((2706, "A"),)}

BATCH09_REGULAR_NAMES = [
    "H1a",
    "H1a1",
    "H1b",
    "H1c",
    "H1e",
    "H2a",
    "H2a1",
    "H2a2a",
    "H2a2a1",
    "H5a",
    "H6a",
    "H13a",
]

BATCH09_RECORD_SHA256 = {
    "H1a": "b1840c819b332146cab1ed7e477ad0f08d20b4f76f913ede959c8b24bb2c564e",
    "H1a1": "3995e004a3dfca5f9c289280d29476af48ce83135ba8d42f0594d8d4b2b8c437",
    "H1b": "465259d674d9a71b886b74982f858e4eca23159de5391c51400b77de5c8502b8",
    "H1c": "41b3d9abbed3ef02629ad88cbd6fcc354305c12a00b9801e1ac5dd25c56ac89a",
    "H1e": "d4ca8a88230354c8e415d2ab0ffb321b1ba1fb1958e1b91fd0ed964dc54c9755",
    "H2a": "818b0f1cc9567e3829824f3442350ab1cdc2b5da01d1b36e6233b0157e81da53",
    "H2a1": "62be82d9a498aa6e07a3dd3c983e0e49c34ba5b930b35bf79cc2ecb264e233d7",
    "H2a2a": "d257611bc507d60dd3c0bcc392a74d93096e55d0b3c35585cc2291190c59de7c",
    "H2a2a1": "ee444a772dcb1d868532b99e95e857d1134bf815e0f731b6c696d544d59f35bc",
    "H5a": "1f08856b1889b9bb5810cff02878d10f224c7a4fe52e97e7a6810ae064a31389",
    "H6a": "5ec0e218b2263059169e5cb77a559ce5566f20c0f87b6ecdaa5e65bb5f81ee6a",
    "H13a": "91af4046a6bcb57435db77b4fd565b21365088688d05285040e9c2b30800fafd",
}
BATCH09_STRUCTURAL_SHA256 = {
    "H2a2": "8bd355ce7bf8e33bb4b5e0b85b49ab99b430d74cb22ef26f5dede1a291afcb83"
}
BATCH09_OMITTED_SHA256 = {
    "H1+16189": "11388c13274eac3ecd14a9cd7a61bfccd1e96e4739b756fe0baffdc932e89989"
}

BATCH09_DIRECT_MOTIFS = {
    "H1a": (("A73G!", True), ("A16162G", True)),
    "H1a1": (("T6365C", True), ("T16209C", False)),
    "H1b": (("T16356C", True),),
    "H1c": (("T477C", True),),
    "H1e": (("G5460A", True),),
    "H2a": (("G4769A", True),),
    "H2a1": (("G951A", True), ("C16354T", True)),
    "H2a2a": (("G8860A", True), ("G15326A", False)),
    "H2a2a1": (("G263A", True),),
    "H5a": (("T4336C", True),),
    "H6a": (("G3915A", True), ("G9380A", False)),
    "H13a": (("C2259T", True),),
}

BATCH09_TOPOLOGY = {
    "H1a": ("H1a", "H1", "H1", "H1", ()),
    "H1a1": ("H1a1", "H1a", "H1a", "H1a", ()),
    "H1b": ("H1b", "H1", "H1", "H1+16189", ("H1+16189",)),
    "H1c": ("H1c", "H1", "H1", "H1", ()),
    "H1e": ("H1e", "H1", "H1", "H1", ()),
    "H2a": ("H2a", "H2", "H2", "H2", ()),
    "H2a1": ("H2a1", "H2a", "H2a", "H2a", ()),
    "H2a2a": ("H2a2a", "H2a2", "H2a2", "H2a2", ()),
    "H2a2a1": ("H2a2a1", "H2a2a", "H2a2a", "H2a2a", ()),
    "H5a": ("H5a", "H5", "H5", "H5", ()),
    "H6a": ("H6a", "H6", "H6", "H6", ()),
    "H13a": ("H13a", "H13", "H13", "H13", ()),
}

BATCH09_STRUCTURAL_MOTIFS = {"H2a2": (("G750A", False),)}
BATCH09_STRUCTURAL_TOPOLOGY = {"H2a2": ("H2a2", "H2a", "H2a", "H2a", ())}
BATCH09_FLATTENED_STEPS = {
    "H1+16189": (
        "H1",
        "flattened_unreportable_source_intermediate",
        (("T16189C!", False),),
        "H1b",
    )
}

BATCH09_MARKER_PROMOTIONS = {
    "H1a1",
    "H1b",
    "H1c",
    "H1e",
    "H2a",
    "H2a1",
    "H2a2a",
    "H2a2a1",
    "H5a",
}
BATCH09_DIRECT_MOTIF_PROMOTIONS = set(BATCH09_MARKER_PROMOTIONS)
BATCH09_EDGE_ONLY_RECORDS = {"H6a", "H13a"}
BATCH09_PREEXISTING_EXACT = {"H1a"}
BATCH09_STRUCTURAL_PROMOTIONS = {"H2a2"}
BATCH09_PROMOTED_RECORDS = (
    BATCH09_MARKER_PROMOTIONS | BATCH09_STRUCTURAL_PROMOTIONS | BATCH09_EDGE_ONLY_RECORDS
)
BATCH09_AUTHORITATIVE_CITATIONS = (
    {
        "doi": "10.1002/humu.20921",
        "pmid": "18853457",
        "accessed": "2026-07-20",
    },
    {
        "doi": "10.3390/ijms22115747",
        "pmid": "34072215",
        "pmcid": "PMC8198973",
        "accessed": "2026-07-20",
    },
)
BATCH09_PROMOTED_RECORD_EVIDENCE = {
    name: BATCH09_AUTHORITATIVE_CITATIONS for name in sorted(BATCH09_PROMOTED_RECORDS)
}
BATCH09_OLD_MARKERS = {
    "H1a1": ((14587, "G"),),
    "H1b": ((3010, "A"), (16189, "C")),
    "H1c": ((4310, "G"),),
    "H1e": ((3796, "G"), (9066, "G")),
    "H2a2": ((750, "A"),),
    "H2a2a": ((8860, "A"), (15326, "A")),
}

BATCH10_REGULAR_NAMES = [
    "HV0",
    "HV1",
    "V",
    "V1",
    "V7",
    "B4",
    "B4a",
    "B4b",
    "B4c",
    "B5",
    "F",
    "F1",
    "F1a",
    "F1b",
    "F2",
    "P",
]
BATCH10_RECORD_SHA256 = {
    "HV0": "b2de75f07f8793d67e022026b37f9a02d7650aa644d3456b18e3d64b10eead53",
    "HV1": "826fecba3d41e997de7d54e531d03fc951b203333f72e847af274395e60e5d28",
    "V": "7f0d3cc71946b838d55d34c5871aa89190939b2b86fdbcf04b301f955180e726",
    "V1": "e240ba861b434a368cbc0c44884a6a76beb4b5dfb50b00134b228846077aec06",
    "V7": "21523c52c1699e921412c48f70b00e2fa90046580acacc5b17d8413614f98cd4",
    "B4": "c94e92106775f65521010e49f4b11879b5d59452bc0448bff45d0c05249ca805",
    "B4a": "5cec278d2ecca87c9a3d8f48241fa31d876a397bb509bbbdeb1946213cc72d94",
    "B4b": "09b7946051a88080b2d26846011ffe3bedf7399da6b1ed5f468c21866d899737",
    "B4c": "086da82e8145bc66ee8b37ec66b6da737f97ce982026d5d74d5696e4c11b480b",
    "B5": "8fb8666bdd0c566099bb4118eb96bc26fe4879543084be8cc4063126732ece68",
    "F": "f438ec36e767f58d77142b9155a9f1d49f4eb5279d6654fdc8cd1de33e4a18b6",
    "F1": "37d6eaf4666e437201b23716c536926a7e0dd1c563c78b5c33316a2f55793469",
    "F1a": "cf6700a667f69e90fbdb172ef1a9f218f363683b0d6521f4315511f3853bdf44",
    "F1b": "37d6b463a805b15835fceaf3bfd4fe0e5e186f6469c30f3da27bf4bbf2be8121",
    "F2": "335dc885d91711f1d02f67f841016e606650f41944d5420b82d6b3d70afa3b80",
    "P": "78bdcf7969ddbc0bc3f84bd7e965a2d655e6c18f5948000ed0db976d3d72a7f6",
}
BATCH10_DIRECT_MOTIFS = {
    "HV0": (("T72C", True), ("T16298C", False)),
    "HV1": (("A8014t", True), ("C16067T", True)),
    "V": (("G4580A", True),),
    "V1": (("A8869G", True),),
    "V7": (("A93G", True), ("G7444A", True)),
    "B4": (("T16217C", True),),
    "B4a": (("T5465C", True), ("G9123A", False)),
    "B4b": (("G499A", True), ("G4820A", True), ("G13590A", True)),
    "B4c": (("T1119C", True), ("G15346A", True)),
    "B5": (
        ("G709A", False),
        ("G8584A", True),
        ("T9950C", True),
        ("A10398G!", True),
        ("T16140C", True),
    ),
    "F": (("A249d", False), ("T6392C", True), ("G10310A", True)),
    "F1": (("G6962A", True), ("T10609C", True), ("G12406A", False), ("C12882T", True)),
    "F1a": (("C4086T", True), ("T16172C", True)),
    "F1b": (
        ("T152C!", False),
        ("C10976T", True),
        ("C12633T", True),
        ("G14476A", True),
        ("C16232a", True),
        ("T16249C", True),
        ("T16311C!", False),
    ),
    "F2": (
        ("T1005C", True),
        ("T1824C", False),
        ("A7828G", True),
        ("T10535C", True),
        ("G10586A", True),
        ("T12338C", True),
        ("G13708A", True),
    ),
    "P": (("A15607G", True),),
}
BATCH10_TOPOLOGY = {
    "HV0": ("HV0", "HV", "HV", "HV", ()),
    "HV1": ("HV1", "HV", "HV", "HV", ()),
    "V": ("V", "HV0", "HV0", "HV0a", ("HV0a",)),
    "V1": ("V1", "V", "V", "V", ()),
    "V7": ("V7", "V", "V", "V", ()),
    "B4": ("B4", "B", "B4'5", "B4'5", ()),
    "B4a": ("B4a", "B4", "B4", "B4+16261", ("B4+16261",)),
    "B4b": ("B4b", "B4", "B4", "B4b'd'e'j", ("B4b'd'e'j",)),
    "B4c": ("B4c", "B4", "B4", "B4", ()),
    "B5": ("B5", "B", "B4'5", "B4'5", ()),
    "F": ("F", "R", "R", "R9", ("R9",)),
    "F1": ("F1", "F", "F", "F", ()),
    "F1a": ("F1a", "F1", "F1", "F1a'c'f", ("F1a'c'f",)),
    "F1b": ("F1b", "F1", "F1", "F1+16189", ("F1+16189",)),
    "F2": ("F2", "F", "F", "F", ()),
    "P": ("P", "R", "R", "R", ()),
}
BATCH10_STRUCTURAL_SHA256 = {
    "B": "6683b1ffac4184d132b9308a8e8c5bc7bb70bcbe11a99e26c212f59adde38227"
}
BATCH10_STRUCTURAL_MOTIFS = {"B": (("8281-8289d", False),)}
BATCH10_STRUCTURAL_TOPOLOGY = {"B": ("B4'5", "R", "R", "R+16189", ("R+16189",))}
BATCH10_FLATTENED_STEPS = {
    "HV0a": ("HV0", "flattened_source_intermediate", (("C15904T", False),), ("V",)),
    "R+16189": (
        "R",
        "flattened_unreportable_source_intermediate",
        (("T16189C!", False),),
        ("B",),
    ),
    "R9": (
        "R",
        "flattened_source_intermediate",
        (("C3970T", False), ("G13928c", False), ("T16304C", False)),
        ("F",),
    ),
    "B4+16261": (
        "B4",
        "flattened_source_intermediate",
        (("C16261T", False),),
        ("B4a",),
    ),
    "B4b'd'e'j": (
        "B4",
        "flattened_source_intermediate",
        (("A827G", False), ("C15535T", False)),
        ("B4b",),
    ),
    "F1a'c'f": (
        "F1",
        "flattened_source_intermediate",
        (("G9053A", False), ("G13759A", False), ("G16129A!", False)),
        ("F1a",),
    ),
    "F1+16189": (
        "F1",
        "flattened_unreportable_source_intermediate",
        (("T16189C!", False),),
        ("F1b",),
    ),
}
BATCH10_OMITTED_SHA256 = {
    "HV0a": "1c5278b16ac5b81d6610805704e06436a94605488c4694eb8dded1fa37e553cf",
    "R+16189": "16ef5610e1356cdb38222782219368d2d910c12145be111d8ffc293ea386af58",
    "R9": "d58982f6e04336ca210d38ae0b7fd4f0f0daf45ce475789f1cf5c3f0cebce02f",
    "B4+16261": "c69c53f7da2b8cb6965321479916651c6ea2f42c16aeb0f4c1bd65cc918a0643",
    "B4b'd'e'j": "78722c258ded650b8914aa3a60dc0a4b140c3dd6694cbde82a026fc35cef2ab1",
    "F1a'c'f": "5295da16bff8402faf72e58e1e5a385ead66564a6f59efae585b495461d0003d",
    "F1+16189": "240550e3c8238e01943fa23a5333c8a314b4b3bc9b4befdeb370702a0205649e",
}
BATCH10_MARKER_PROMOTIONS = set(BATCH10_REGULAR_NAMES)
BATCH10_DIRECT_MOTIF_PROMOTIONS = set(BATCH10_REGULAR_NAMES)
BATCH10_STRUCTURAL_PROMOTIONS = {"B"}
BATCH10_PROMOTED_RECORDS = BATCH10_MARKER_PROMOTIONS | BATCH10_STRUCTURAL_PROMOTIONS
BATCH10_PROMOTED_RECORD_EVIDENCE = {
    name: BATCH09_AUTHORITATIVE_CITATIONS for name in sorted(BATCH10_PROMOTED_RECORDS)
}
BATCH10_OLD_MARKERS = {
    "HV1": ((16067, "T"),),
    "V": ((4580, "A"), (15904, "C")),
    "V1": ((4732, "G"),),
    "V7": ((5263, "T"),),
    "B": ((827, "G"), (8281, "C"), (15301, "A")),
    "B4": ((3453, "G"), (9123, "A")),
    "B4a": ((6719, "C"), (9123, "A")),
    "B4b": ((3453, "G"), (4820, "A")),
    "B4c": ((3497, "T"),),
    "B5": ((210, "G"), (1809, "C"), (6960, "C")),
    "F": ((249, "A"), (6392, "C"), (10310, "A")),
    "F1": ((3970, "T"), (12406, "A")),
    "F1a": ((3970, "T"), (13759, "A")),
    "F1b": ((7828, "G"),),
    "F2": ((4218, "C"), (13928, "C")),
    "P": ((1438, "G"), (3705, "T"), (16176, "G")),
}

BATCH11_REGULAR_NAMES = [
    "JT",
    "J",
    "J1",
    "J1b",
    "J1c",
    "J1d",
    "J2",
    "J2a",
    "J2b",
    "T",
    "T1",
    "T1a",
    "T2",
    "T2a",
    "T2b",
    "T2c",
    "T2e",
    "T2f",
]
BATCH11_RECORD_SHA256 = {
    "JT": "ec18fb553584a8dae2927705aa875ea48c8935e105fc3cc65f6927192568e4c9",
    "J": "9619bd4c9628345af0af2014581139b7f8bbfd65c221e74ec906ace95cedb0ae",
    "J1": "2a5bebb82b5d8de2e264f790fc86d0609269a04d7e4c6f99abce4532224493b9",
    "J1b": "5503a296e52469e32e44ed966b9a5194b830c4cc9bf7e961ddd1ed7d97f41c7b",
    "J1c": "6dc1498327827aa286a638a5517f008bec39affc4f0ab3dc7aafd4d72b965933",
    "J1d": "dca1fc35db3371466c60d5bd2a30260f56f31a887d902ccda7924796a44f9a68",
    "J2": "317053fdfc37bbbec9a9154290bb683c1aba4b3e4e4c00d8b3ada59eae7a06fd",
    "J2a": "4e67071d73a6e8426ac893879d07e8c5d9f5ed7092c9b0538692cb779cb1c531",
    "J2b": "68e7daff86c88fad1dc98979eb95d366568d6384da93e7e024f84d949aa4b17b",
    "T": "802b8048b9a90ccf73f301cb2aa4fd6dc86caa87b49bfa35bb7ff7655b303755",
    "T1": "1bb9526afb8ac6f73d8b5704851cd6d1fcde68612da15bd95f5b77bda139fd58",
    "T1a": "47ee90523b63eef5c04a60dba7b4511b279d17115d995d413008b9fe19b8920f",
    "T2": "4e139c3d57b84570edf4b106e77c2deba0c60ba44ed8e0f9c32ad97890cd4906",
    "T2a": "fbfcae629b3e0f4a07924549eeb3010192f7db79785c76f26e970073ebfc1afe",
    "T2b": "7bfcd6eb2e06c26e10cf940273d7436cecbaca65d4817b3eafdffabad31eae4d",
    "T2c": "72fad92f82b9928242c85dfa8b13e25ee9f21754aa27f8ea7d187eab7391cda9",
    "T2e": "57cd5a02102b9de91254caf2b7df1aa0e1ff5af7052d5a2f8efb1471f5a5292a",
    "T2f": "6368e89d03c5a2a07eb58c795c5b612e12f26cfe3f958be18f4953075592ff40",
}
BATCH11_DIRECT_MOTIFS = {
    "JT": (("A11251G", True), ("C15452a", True), ("T16126C", True)),
    "J": (
        ("C295T", True),
        ("T489C", True),
        ("A10398G!", True),
        ("A12612G", True),
        ("G13708A", True),
        ("C16069T", True),
    ),
    "J1": (("C462T", True), ("G3010A", True)),
    "J1b": (
        ("G8269A", False),
        ("G16145A", True),
        ("(C16222T)", True),
        ("C16261T", True),
    ),
    "J1c": (("(G185A)", False), ("(G228A)", True), ("T14798C", True)),
    "J1d": (("T152C!", False), ("G7789A", False), ("A7963G", True)),
    "J2": (("(C150T)", False), ("T152C!", False), ("C7476T", True), ("G15257A", True)),
    "J2a": (("T195C!", False), ("A10499G", True), ("G11377A", True)),
    "J2b": (("C5633T", True), ("G15812A", True), ("C16193T", False)),
    "T": (
        ("G709A", False),
        ("G1888A", True),
        ("A4917G", True),
        ("G8697A", True),
        ("T10463C", True),
        ("G13368A", True),
        ("G14905A", False),
        ("A15607G", True),
        ("G15928A", True),
        ("C16294T", False),
    ),
    "T1": (("C12633a", True), ("A16163G", True), ("T16189C!", False)),
    "T1a": (("C16186T", True),),
    "T2": (("A11812G", True), ("A14233G", True), ("(C16296T)", False)),
    "T2a": (("T13965C", True),),
    "T2b": (("G930A", False), ("G5147A", True), ("T16304C", False)),
    "T2c": (("C10822T", True),),
    "T2e": (("G16153A", True),),
    "T2f": (("C8270T", True), ("8281-8289d", False)),
}
BATCH11_TOPOLOGY = {
    "JT": ("JT", "R", "R", "R2'JT", ("R2'JT",)),
    "J": ("J", "JT", "JT", "JT", ()),
    "J1": ("J1", "J", "J", "J", ()),
    "J1b": ("J1b", "J1", "J1", "J1", ()),
    "J1c": ("J1c", "J1", "J1", "J1", ()),
    "J1d": ("J1d", "J1", "J1", "J1+16193", ("J1+16193",)),
    "J2": ("J2", "J", "J", "J", ()),
    "J2a": ("J2a", "J2", "J2", "J2", ()),
    "J2b": ("J2b", "J2", "J2", "J2", ()),
    "T": ("T", "JT", "JT", "JT", ()),
    "T1": ("T1", "T", "T", "T", ()),
    "T1a": ("T1a", "T1", "T1", "T1", ()),
    "T2": ("T2", "T", "T", "T", ()),
    "T2a": ("T2a", "T2", "T2", "T2", ()),
    "T2b": ("T2b", "T2", "T2", "T2", ()),
    "T2c": ("T2c", "T2", "T2", "T2", ()),
    "T2e": ("T2e", "T2", "T2", "T2+150", ("T2+150",)),
    "T2f": ("T2f", "T2", "T2", "T2+16189", ("T2+16189",)),
}
BATCH11_FLATTENED_STEPS = {
    "R2'JT": ("R", "flattened_source_intermediate", (("T4216C", False),), ("JT",)),
    "J1+16193": (
        "J1",
        "flattened_unreportable_source_intermediate",
        (("C16193T", False),),
        ("J1d",),
    ),
    "T2+150": ("T2", "flattened_source_intermediate", (("C150T", False),), ("T2e",)),
    "T2+16189": (
        "T2",
        "flattened_unreportable_source_intermediate",
        (("T16189C!", False),),
        ("T2f",),
    ),
}
BATCH11_OMITTED_SHA256 = {
    "R2'JT": "fafe23f325bd12bade3cba4794ed17b862f648ea744cbe56a01f4207e0f2575b",
    "J1+16193": "67a1bd7bc36ed0da10bb411c15970add2f81103d29fe112fbf044792b286495d",
    "T2+150": "704d8a606c101b641c1058b854b4604a45ce8055f45e4a68616f76142cb0e42a",
    "T2+16189": "499607dab227c9d190d529c418da6678b90ba3cb1dd42222bd3fb918aa615bf7",
}
BATCH11_MARKER_PROMOTIONS = set(BATCH11_REGULAR_NAMES) - {"J1d", "T2a"}
BATCH11_DIRECT_MOTIF_PROMOTIONS = set(BATCH11_MARKER_PROMOTIONS)
BATCH11_EDGE_ONLY_RECORDS = {"J1d", "T2a"}
BATCH11_PROMOTED_RECORD_EVIDENCE = {
    name: BATCH09_AUTHORITATIVE_CITATIONS for name in sorted(BATCH11_MARKER_PROMOTIONS)
}
BATCH11_OLD_MARKERS = {
    "JT": ((489, "C"), (11251, "G")),
    "J": ((295, "T"), (489, "C"), (10398, "G"), (12612, "G"), (16069, "T")),
    "J1": ((3010, "A"), (13708, "A")),
    "J1b": ((8269, "A"), (15452, "A")),
    "J1c": ((9055, "A"), (13708, "A")),
    "J2": ((7476, "T"),),
    "J2a": ((7476, "T"), (15257, "A")),
    "J2b": ((6261, "A"), (13708, "A")),
    "T": (
        (709, "A"),
        (1888, "A"),
        (4917, "G"),
        (8697, "A"),
        (10463, "C"),
        (13368, "A"),
        (16294, "T"),
    ),
    "T1": ((6185, "C"), (16189, "C")),
    "T1a": ((6253, "C"), (16163, "G")),
    "T2": ((11812, "G"),),
    "T2b": ((5147, "A"), (15907, "G")),
    "T2c": ((6489, "G"),),
    "T2e": ((7859, "C"),),
    "T2f": ((12633, "G"),),
}

BATCH12_REGULAR_NAMES = [
    "U",
    "U1",
    "U1a",
    "U1b",
    "U2",
    "U2e",
    "U3",
    "U3a",
    "U3b",
    "U4",
    "U4a",
    "U4b",
    "U4c",
    "U6",
    "U6a",
    "U7",
    "U8",
    "U9",
]
BATCH12_RECORD_SHA256 = {
    "U": "2d4176c4c98bc66fb9bc8d57dc7f3a478b13d2118d8ba9320979946e8c636d06",
    "U1": "366a1b9fbed2580fbbcad00ffcd30cae724392c2231ca9197898c97c3815d8df",
    "U1a": "2bd347401c9f8c40573a6553c955a7b32b8e26c4086c5fdd520bd7277b78d7c5",
    "U1b": "815cc45e4e4ae3d036d6c9a67db05386408ec759e18b4e7d8f8e08dd97d4ca80",
    "U2": "d93f61c6565c721ce1cc91a8264537899be82adbae03af5ad222aec751e5ed72",
    "U2e": "194fac0f0aab3f61b129b54d7a7df66e032b4a6eab1f5dd1e2fbafe274f60828",
    "U3": "d186bc42202f62234a02e859a3397d0822e507eb939ece16b213218eecd6e52b",
    "U3a": "f018254a22941dda74b4075d3fa438e8b94ccaf952b79cc9dba2852b05f6c07b",
    "U3b": "e772bf27f7317e691d00d30391bd5b49a694798f9e24afc4f8059e9069bf5009",
    "U4": "47fc0938d776f77565ea9b853707748fbd99da9dab4c553ff5589bfb54450df8",
    "U4a": "22bd1b60f6aee328759fbb87229e2fc98c20bd85a0abbeaa420e242bfdd4eb80",
    "U4b": "f04940dbe4c484366aee7409a74a31b10713a02f1a15a0b0ec6a0ab51bf1c5a5",
    "U4c": "7af5c7ae43b6e0cd7cd6380d57826be983e97dd67db453897556610e5103b543",
    "U6": "c9f73f861d247d2ba2167026010ddce649c22cbcbbc918cc022d3f4f4ef5d2df",
    "U6a": "afea7172c3f6d8f55188398f6e819607556ef295e489f17b09ed186d3b2f632a",
    "U7": "5a540571fe5cb295ec1103decec7a642d8f840422d02cc3403422b747d746865",
    "U8": "f2930b6b19c703bdf45113c7551860c876571f6ae1dc580386718948859b7a77",
    "U9": "bb2acff73441832540ee42c8291781c1516441654f3a9cc43f138353efdebac5",
}
BATCH12_DIRECT_MOTIFS = {
    "U": (("A11467G", True), ("A12308G", True), ("G12372A", True)),
    "U1": (
        ("C285T", True),
        ("T12879C", True),
        ("A13104G", False),
        ("A14070G", True),
        ("G15148A", True),
        ("A15954c", True),
        ("T16249C", True),
    ),
    "U1a": (("C2218T", True), ("G14364A", False), ("T16189C!", False)),
    "U1b": (
        ("T146C!", True),
        ("T2387C", True),
        ("C8395T", True),
        ("T10885C", False),
        ("A11566G", True),
        ("G15172A", True),
        ("(C16111T)", True),
        ("C16327T", True),
    ),
    "U2": (("A16051G", True),),
    "U2e": (
        ("A508G", True),
        ("A3720G", True),
        ("A5390G", True),
        ("T5426C", True),
        ("C6045T", True),
        ("T6152C", False),
        ("A10876G", False),
        ("T13020C", True),
        ("T13734C", True),
        ("A15907G", True),
        ("G16129c", True),
        ("T16189C!", False),
        ("T16362C", True),
    ),
    "U3": (("C150T", False), ("A14139G", True), ("T15454C", True), ("A16343G", True)),
    "U3a": (("C6518T", True), ("A10506G", True), ("C13934T", True), ("G16390A", True)),
    "U3b": (("A4188G", True), ("C4640a", False), ("T9656C", True), ("T13743C", True)),
    "U4": (
        ("T4646C", True),
        ("A6047G", True),
        ("C11332T", False),
        ("C14620T", False),
        ("T15693C", True),
        ("T16356C", True),
    ),
    "U4a": (("C8818T", True),),
    "U4b": (("T7705C", True),),
    "U4c": (("T10907C", True),),
    "U6": (("A3348G", True), ("T16172C", True)),
    "U6a": (("G7805A", True), ("A14179G", True), ("C16278T!", False)),
    "U7": (
        ("T152C!", False),
        ("T980C", True),
        ("C3741T", True),
        ("C5360T", False),
        ("C8137T", True),
        ("C8684T", True),
        ("C10142T", True),
        ("T13500C", True),
        ("G14569A", True),
        ("(A16309G)", True),
        ("A16318t", True),
    ),
    "U8": (("T9698C", True),),
    "U9": (("G3531A", True), ("G3834A", True), ("C6386T", False), ("T14094C", True)),
}
BATCH12_TOPOLOGY = {
    "U": ("U", "R", "R", "R", ()),
    "U1": ("U1", "U", "U", "U", ()),
    "U1a": ("U1a", "U1", "U1", "U1", ()),
    "U1b": ("U1b", "U1", "U1", "U1", ()),
    "U2": ("U2", "U", "U", "U2'3'4'7'8'9", ("U2'3'4'7'8'9",)),
    "U2e": ("U2e", "U2", "U2", "U2+152", ("U2+152",)),
    "U3": ("U3", "U", "U", "U2'3'4'7'8'9", ("U2'3'4'7'8'9",)),
    "U3a": ("U3a", "U3", "U3", "U3a'c", ("U3a'c",)),
    "U3b": ("U3b", "U3", "U3", "U3", ()),
    "U4": ("U4", "U", "U", "U4'9", ("U2'3'4'7'8'9", "U4'9")),
    "U4a": ("U4a", "U4", "U4", "U4", ()),
    "U4b": ("U4b", "U4", "U4", "U4", ()),
    "U4c": ("U4c", "U4", "U4", "U4", ()),
    "U6": ("U6", "U", "U", "U", ()),
    "U6a": ("U6a", "U6", "U6", "U6a'b'd", ("U6a'b'd",)),
    "U7": ("U7", "U", "U", "U2'3'4'7'8'9", ("U2'3'4'7'8'9",)),
    "U8": ("U8", "U", "U", "U2'3'4'7'8'9", ("U2'3'4'7'8'9",)),
    "U9": ("U9", "U", "U", "U4'9", ("U2'3'4'7'8'9", "U4'9")),
}
BATCH12_FLATTENED_STEPS = {
    "U2'3'4'7'8'9": (
        "U",
        "flattened_source_intermediate",
        (("A1811G", False),),
        ("U2", "U3", "U4", "U7", "U8", "U9"),
    ),
    "U2+152": ("U2", "flattened_source_intermediate", (("T152C!", False),), ("U2e",)),
    "U3a'c": (
        "U3",
        "flattened_source_intermediate",
        (("A2294G", False), ("T4703C", False), ("G9266A", False)),
        ("U3a",),
    ),
    "U4'9": (
        "U2'3'4'7'8'9",
        "flattened_source_intermediate",
        (("T195C!", False), ("G499A", False), ("T5999C", False)),
        ("U4", "U9"),
    ),
    "U6a'b'd": (
        "U6",
        "flattened_source_intermediate",
        (("A16219G", False),),
        ("U6a",),
    ),
}
BATCH12_OMITTED_SHA256 = {
    "U2'3'4'7'8'9": "99f0aa66e2659a2d9a453f7cb8a32f5424cf3a0af8a3075846c4286d21b13740",
    "U2+152": "3162156911804c3d9563f24d03dd93aeac7892a8ecbfc9f7d49e6268c1727f96",
    "U3a'c": "49f7cc3df3cd65b0390469267ae1cb0279f955c38ece4778219161cb16c30d4a",
    "U4'9": "5bc2de46190ac708462465316c4eb0552df2d3fef459f903f1c0ad5c03d2c9f0",
    "U6a'b'd": "73b4c1ffb64adc6e858b0cd68bc4b6eb4d0fa083ef2bf7e52fdcec94129ad5e8",
}
BATCH12_MARKER_PROMOTIONS = {
    "U",
    "U1",
    "U1a",
    "U1b",
    "U3",
    "U4",
    "U4a",
    "U4b",
    "U4c",
    "U6",
    "U6a",
    "U7",
    "U8",
    "U9",
}
BATCH12_LEGACY_UPGRADES = {"U2e"}
BATCH12_PREEXISTING_REPAIRS = {"U2", "U3a", "U3b"}
BATCH12_PROMOTED_RECORD_EVIDENCE = {
    name: BATCH09_AUTHORITATIVE_CITATIONS
    for name in sorted(BATCH12_MARKER_PROMOTIONS | BATCH12_LEGACY_UPGRADES)
}
BATCH12_PREVIOUS_EXACT_NAMES_SHA256 = (
    "ec8fba30938462e5664ca84a0b91a2b1edd02e4e10734a882d5b43194a4c9485"
)
BATCH12_PREVIOUS_DIRECT_EXACT_NAMES_SHA256 = (
    "bedeb636aab72d7ec54ffeca1cdd7da00c3ae3e4164c394ef5155fa3f24f2da4"
)
BATCH12_PREVIOUS_LEGACY_PARTIAL_NAMES_SHA256 = (
    "b10316564e474a8c44d255138c557ff14c4a766addc956cb961eab5036501431"
)
BATCH12_PREVIOUS_PENDING_NAMES_SHA256 = (
    "ed7d2801f6a18b42a8afb983be40aef6053285e1561a368ce6ba6645c82656b5"
)
BATCH12_PREVIOUS_OMITTED_NAMES_SHA256 = (
    "4634ccafcf8fc91fd37da6e626c928a589d8fb8d9d8dafde5608c5ab8f5f4953"
)
BATCH12_U2E_PRESERVED_MARKER_SHA256 = {
    508: "b09b7142f94e122bd20a6918da5b9439322ef3269f240b40246624663c2bdcf1",
    13020: "095c28df84f195f6a90000f39ef1d7d72ae5806d2827d0dc94e01753e1869762",
}
BATCH12_OLD_MARKERS = {
    "U": ((13133, "T"), (12308, "G"), (12372, "A")),
    "U1": ((3531, "A"), (7581, "C")),
    "U1a": ((6026, "T"),),
    "U1b": ((4991, "A"),),
    "U2e": ((508, "G"), (13020, "C")),
    "U3": ((1811, "G"), (15454, "C")),
    "U4": ((3714, "G"), (11339, "C")),
    "U4a": ((5999, "C"),),
    "U4b": ((1811, "G"),),
    "U4c": ((11332, "T"),),
    "U6": ((3348, "G"),),
    "U6a": ((16219, "G"),),
    "U7": ((12308, "G"), (16309, "G")),
    "U9": ((3834, "A"), (11914, "A")),
}

BATCH13_NAMES = [
    "U5",
    "U5a",
    "U5a1",
    "U5a2",
    "U5b",
    "U5b1",
    "U5b2",
    "U8a",
    "U8b",
    "K",
    "K1",
    "K1a",
    "K1b",
    "K2",
    "K2a",
    "K2b",
]
BATCH13_REGULAR_NAMES = [name for name in BATCH13_NAMES if name != "U5"]
BATCH13_RECORD_SHA256 = {
    "U5": "b7ed525ea70d1ca8ce49ddb932cfd515993ce985de59fcc4414ee06c59a32389",
    "U5a": "3373c81e7b8d82455d54a850c851e0835880d37c67f7bcd0e8f64b05a4c78fbc",
    "U5a1": "9558abb8432f12aee723d941f50155eba55158e630c0cd5fd846b60ba39d7c18",
    "U5a2": "3e47cf1f7bcb6f288925efd563f1eedf12154f5307bbb2186bd6527061331a11",
    "U5b": "96f6f516f85786077249f2e09ad5f6154601156d618acc023bdc21318ade9e3d",
    "U5b1": "f6a07962a42bc49d6d54b874879cb956c1a9ff9f8d9be42263bae05ad6eff8f3",
    "U5b2": "7f2e45af08872a75a3380532080ff3919ac83ffa0261ca397079e22dca6ac611",
    "U8a": "84f9b708e8d124939b27a68b2dbbcac587e4821485e42b158b9ac5585051f118",
    "U8b": "3bb16df6411da02c69482badf95c22735c7d46a9e7f72d382cdbba9d8baa25dc",
    "K": "b46c1444327ef39d879929d373dd9a21593a55256b2755451107522803d6ddff",
    "K1": "664ddcd8ce1124e57e3c5a5a64e7efbb12456d10571ce1a1a5b6a919d96a0933",
    "K1a": "e35f522a4f50fe11ed17ee24c9353c4bff25de1fe20b8f6c20ad7e60ac3e0cd7",
    "K1b": "85262dcbdf32f56a5c2dd0d0ad98e7a6e14065de56ba2d4d4f29ffe9cf9ff16f",
    "K2": "19cc278db1a9b02ddb5bfc912c72fc501ee4b907c4ff28c7c4a143a845e0da22",
    "K2a": "5221a70df09d1fdf995e2c245fc24dfea5bb1a54a91a9c59e957fa2d7a131560",
    "K2b": "82df2cca83a68b6988e5851645392add7b530a8ba2c2d2e2f515005f172a02d3",
}
BATCH13_DIRECT_MOTIFS = {
    "U5": (("C16192T", False), ("C16270T", False)),
    "U5a": (("A14793G", False), ("C16256T", True)),
    "U5a1": (("A15218G", True), ("A16399G", True)),
    "U5a2": (("G16526A", True),),
    "U5b": (("C150T", False), ("A7768G", False), ("T14182C", True)),
    "U5b1": (("A5656G", True),),
    "U5b2": (("C1721T", True), ("A13637G", True)),
    "U8a": (
        ("T282C", True),
        ("T6392C", True),
        ("C6455T", True),
        ("A7055G", False),
        ("C9365T", True),
        ("G13145A", True),
    ),
    "U8b": (("G9055A", False), ("C14167T", True)),
    "K": (
        ("A10550G", True),
        ("T11299C", True),
        ("T14798C", True),
        ("T16224C", False),
        ("T16311C!", False),
    ),
    "K1": (("T1189C", True), ("A10398G!", True)),
    "K1a": (("C497T", True), ("(T16093C)", False)),
    "K1b": (("G5913A", True),),
    "K2": (("T146C!", False), ("T9716C", True)),
    "K2a": (("T152C!", False), ("G709A", False), ("T4561C", True)),
    "K2b": (("C2217T", False), ("G5231A", True), ("A14037G", True)),
}
BATCH13_TOPOLOGY = {
    "U5": ("U5", "U", "U", "U", ()),
    "U5a": ("U5a", "U5", "U5", "U5a'b", ("U5a'b",)),
    "U5a1": ("U5a1", "U5a", "U5a", "U5a", ()),
    "U5a2": ("U5a2", "U5a", "U5a", "U5a", ()),
    "U5b": ("U5b", "U5", "U5", "U5a'b", ("U5a'b",)),
    "U5b1": ("U5b1", "U5b", "U5b", "U5b", ()),
    "U5b2": ("U5b2", "U5b", "U5b", "U5b", ()),
    "U8a": ("U8a", "U8", "U8", "U8", ()),
    "U8b": ("U8b", "U8", "U8", "U8b'c", ("U8b'c",)),
    "K": ("K", "U8b", "U8b", "U8b", ()),
    "K1": ("K1", "K", "K", "K", ()),
    "K1a": ("K1a", "K1", "K1", "K1", ()),
    "K1b": ("K1b", "K1", "K1", "K1", ()),
    "K2": ("K2", "K", "K", "K", ()),
    "K2a": ("K2a", "K2", "K2", "K2", ()),
    "K2b": ("K2b", "K2", "K2", "K2", ()),
}
BATCH13_FLATTENED_STEPS = {
    "U5a'b": (
        "U5",
        (("T3197C", False), ("G9477A", False), ("T13617C", False)),
        ("U5a", "U5b"),
    ),
    "U8b'c": ("U8", (("A3480G", False),), ("U8b",)),
}
BATCH13_OMITTED_SHA256 = {
    "U5a'b": "8e4f16a636762c132850f49556fffe58302f1e2a8952abdff7e8652bc128c4a7",
    "U8b'c": "51d304be109524fcc79f2e13b3240bae62efc296477de4c8c6653ec12f229d2f",
}
BATCH13_PENDING_PROMOTIONS = {
    "U5",
    "U5a",
    "U5a1",
    "U5a2",
    "U5b",
    "U5b1",
    "U8a",
    "U8b",
}
BATCH13_MARKER_PROMOTIONS = BATCH13_PENDING_PROMOTIONS - {"U5"}
BATCH13_LEGACY_UPGRADES = {"K", "K1", "K1a", "K2a"}
BATCH13_TOPOLOGY_COMPANIONS = {"U5b2", "K1b", "K2", "K2b"}
BATCH13_PREVIOUS_EXACT_NAMES_SHA256 = (
    "8691eae4dabb6978a8def825a1b5a429cc20b2e1a31a8a1a6f5c67d0654c7f57"
)
BATCH13_PREVIOUS_DIRECT_EXACT_NAMES_SHA256 = (
    "064892149df247d891f603c1925182f14ef845383933b0c5132a62a92e90d20e"
)
BATCH13_PREVIOUS_LEGACY_PARTIAL_NAMES_SHA256 = (
    "87c1b4010a6a1512f4e06808e61e785c8edac71f80a0b3808b32d25553d0fb0e"
)
BATCH13_PREVIOUS_PENDING_NAMES_SHA256 = (
    "0753cbd5747f78167442d5a959cbd56a0d7947c2a97eed47137b2c269e549e89"
)
BATCH13_PREVIOUS_OMITTED_NAMES_SHA256 = (
    "a53462f41d8f21e94842ece0c7ff6e579caa9d25d1ce4e540ab28967836169f3"
)
BATCH13_K1B_MARKER_SHA256 = "c595f96189de40ca30fb44946bb8a050a21e87766506852850a15212b75c66df"
BATCH13_OLD_MARKERS = {
    "U5": ((3197, "C"), (9477, "A")),
    "U5a": ((14793, "G"),),
    "U5a1": ((14793, "G"), (16256, "T")),
    "U5a2": ((1700, "C"),),
    "U5b": ((7768, "G"),),
    "U5b1": ((5656, "G"), (12618, "A")),
    "U8a": ((7028, "T"),),
    "U8b": ((3480, "G"),),
}

HISTORICAL_ONLY_REASON = (
    "absent from the primary four; callable only in the historical five-export cohort"
)
UNAVAILABLE_REASON = "absent or non-callable in all pinned 23andMe exports"
NON_SUBSTITUTION_REASON = "non-substitution event is unsupported by the substitution-only caller"
SHARED_INTERMEDIATE_REASON = (
    "shared source intermediate spans multiple emitted siblings; duplicating it would create "
    "non-specific sibling evidence"
)

ROOT_L0_DIRECT_MOTIFS = {
    "L0": [
        (
            "G263A",
            False,
            "omitted because downstream L0a/b/f source path reverses m.263 and would conflict "
            "before descendant traversal",
        ),
        ("C1048T", True, None),
        ("C3516a", True, None),
        ("T5442C", True, None),
        ("T6185C", True, None),
        ("C9042T", False, HISTORICAL_ONLY_REASON),
        ("A9347G", True, None),
        ("G10589A", True, None),
        ("G12007A", True, None),
        ("A12720G", True, None),
    ],
    "L0a": [
        (
            "C146T",
            False,
            "omitted so pgp_4162 can satisfy 2/4 callable L0a markers; retaining this untyped "
            "denominator would block L0a and its descendants on that primary export",
        ),
        ("G5231A", True, None),
        ("G5460A", True, None),
        ("T14308C", True, None),
        ("T16278C", False, HISTORICAL_ONLY_REASON),
        ("C16320T", False, HISTORICAL_ONLY_REASON),
    ],
    "L0a1": [("T5096C", True, None)],
    "L0a2": [
        ("C64T", True, None),
        ("A185G!", False, HISTORICAL_ONLY_REASON),
        ("G5147A", True, None),
        ("A5711G", True, None),
        ("G6257A", True, None),
        ("8281-8289d", False, NON_SUBSTITUTION_REASON),
        ("A8460G", True, None),
        ("A11172G", True, None),
        ("A16129G", True, None),
    ],
    "L0b": [
        ("T6719C", True, None),
        ("G15106A", True, None),
        ("T15622C", True, None),
        ("A16051G", True, None),
        ("A16164G", True, None),
        ("T16187C", False, HISTORICAL_ONLY_REASON),
    ],
    "L0d": [
        ("G1438A", True, None),
        ("T4232C", True, None),
        ("T6815C", False, HISTORICAL_ONLY_REASON),
        ("C8113a", False, HISTORICAL_ONLY_REASON),
        ("G8152A", True, None),
        ("G8251A", True, None),
        ("T12121C", True, None),
        ("G15466A", True, None),
        ("G15930A", True, None),
        ("T15941C", True, None),
        ("T16243C", True, None),
    ],
    "L0d1": [
        ("G719A", True, None),
        ("G2706A", True, None),
        ("G3438A", True, None),
        ("A6266G", True, None),
        ("G13759A", True, None),
    ],
    "L0d2": [
        ("A3981G", True, None),
        ("C4025T", True, None),
        ("A4044G", True, None),
        ("A7154G", True, None),
        ("T11854C", True, None),
        ("A15766G", True, None),
    ],
    "L0f": [
        ("(G207A)", True, None),
        ("C4964T", True, None),
        ("T7148C", False, UNAVAILABLE_REASON),
        ("T9581C", True, None),
        ("C9620T", True, None),
        ("A13470G", True, None),
        ("C14109T", True, None),
        ("C14620T", False, HISTORICAL_ONLY_REASON),
        ("T15852C", True, None),
        ("C16169T", True, None),
        ("C16327T", True, None),
        ("T16368C", False, HISTORICAL_ONLY_REASON),
    ],
    "L0k": [
        ("T199C", True, None),
        ("T850C", True, None),
        ("T1243C", True, None),
        ("G4541A", True, None),
        ("T4907C", True, None),
        ("A5811G", True, None),
        ("A7257G", False, UNAVAILABLE_REASON),
        ("T8911C", True, None),
        ("C8922T", False, UNAVAILABLE_REASON),
        ("G8994A", True, None),
        ("A9136G", True, None),
        ("A10499G", True, None),
        ("A10876G", False, HISTORICAL_ONLY_REASON),
        ("C10920T", True, None),
        ("C11296T", False, HISTORICAL_ONLY_REASON),
        ("T11299C", True, None),
        ("A11653G", True, None),
        ("G13590A", True, None),
        ("T13819C", False, UNAVAILABLE_REASON),
        ("G13928c", True, None),
        ("T14020C", True, None),
        ("T14182C", True, None),
        ("T14371C", True, None),
        ("T14374C", False, UNAVAILABLE_REASON),
        ("A16129G", True, None),
        ("A16166c", False, HISTORICAL_ONLY_REASON),
        ("C16214T", False, HISTORICAL_ONLY_REASON),
        ("C16291g", True, None),
    ],
    "L1": [
        ("G3666A", True, None),
        ("A7055G", False, HISTORICAL_ONLY_REASON),
        ("T7389C", True, None),
        ("T13789C", True, None),
        ("T14178C", True, None),
        ("G14560A", True, None),
    ],
    "L2": [
        (
            "T146C!",
            False,
            "omitted because the downstream L2a2'3'4 source path reverses m.146; "
            "emitting the upstream L2 state would conflict before L2a2 traversal",
        ),
        ("C150T", False, HISTORICAL_ONLY_REASON),
        ("T152C!", False, HISTORICAL_ONLY_REASON),
        ("T2416C", True, None),
        ("G8206A", True, None),
        ("A9221G", True, None),
        ("T10115C", True, None),
        ("G13590A", True, None),
        ("C16311T", False, HISTORICAL_ONLY_REASON),
        ("G16390A", True, None),
    ],
    "L3": [
        ("A769G", False, HISTORICAL_ONLY_REASON),
        ("A1018G", True, None),
        ("C16311T", False, HISTORICAL_ONLY_REASON),
    ],
    "L4": [
        ("T195C!", False, UNAVAILABLE_REASON),
        ("G5460A", True, None),
        ("T16362C", True, None),
    ],
    "L5": [
        ("459.1C", False, NON_SUBSTITUTION_REASON),
        ("T3423C", True, None),
        ("A7972G", True, None),
        (
            "C12432T",
            False,
            "omitted so pgp_huA08F4D can satisfy 2/4 callable L5 markers; retaining this untyped "
            "denominator would block L5 on that primary export",
        ),
        ("A12950G", True, None),
        ("C16148T", True, None),
        ("A16166G", False, HISTORICAL_ONLY_REASON),
    ],
    "L6": [
        ("T146C!", True, None),
        ("T152C!", False, HISTORICAL_ONLY_REASON),
        ("G185c", False, HISTORICAL_ONLY_REASON),
        ("G709A", False, HISTORICAL_ONLY_REASON),
        ("C770T", False, HISTORICAL_ONLY_REASON),
        ("T961C", True, None),
        ("A1461G", True, None),
        ("C4964T", True, None),
        ("T5267C", True, None),
        ("A6002G", True, None),
        ("A6284G", True, None),
        ("C9332T", True, None),
        ("A10978G", True, None),
        ("T11116C", True, None),
        ("C11743T", False, UNAVAILABLE_REASON),
        ("G12771A", True, None),
        ("A13710G", True, None),
        ("C14791T", False, UNAVAILABLE_REASON),
        ("A14959G", False, UNAVAILABLE_REASON),
        ("A15244G", True, None),
        ("T15289C", True, None),
        ("C15499T", False, HISTORICAL_ONLY_REASON),
        ("G16048A", True, None),
        ("T16224C", False, HISTORICAL_ONLY_REASON),
    ],
}

ROOT_L0_TOPOLOGY = {
    "L0": ("mt-MRCA", "mt-MRCA", "mt-MRCA", []),
    "L0a": ("L0", "L0", "L0a'g", ["L0a'b'f'g'k", "L0a'b'f'g", "L0a'b'g", "L0a'g"]),
    "L0a1": ("L0a", "L0a", "L0a1'4", ["L0a1'4"]),
    "L0a2": ("L0a", "L0a", "L0a", []),
    "L0b": ("L0", "L0", "L0a'b'g", ["L0a'b'f'g'k", "L0a'b'f'g", "L0a'b'g"]),
    "L0d": ("L0", "L0", "L0", []),
    "L0d1": ("L0d", "L0d", "L0d1'2", ["L0d1'2"]),
    "L0d2": ("L0d", "L0d", "L0d1'2", ["L0d1'2"]),
    "L0f": ("L0", "L0", "L0a'b'f'g", ["L0a'b'f'g'k", "L0a'b'f'g"]),
    "L0k": ("L0", "L0", "L0a'b'f'g'k", ["L0a'b'f'g'k"]),
    "L1": ("mt-MRCA", "mt-MRCA", "L1'2'3'4'5'6", ["L1'2'3'4'5'6"]),
    "L2": (
        "mt-MRCA",
        "mt-MRCA",
        "L2'3'4'6",
        ["L1'2'3'4'5'6", "L2'3'4'5'6", "L2'3'4'6"],
    ),
    "L3": (
        "mt-MRCA",
        "mt-MRCA",
        "L3'4",
        ["L1'2'3'4'5'6", "L2'3'4'5'6", "L2'3'4'6", "L3'4'6", "L3'4"],
    ),
    "L4": (
        "mt-MRCA",
        "mt-MRCA",
        "L3'4",
        ["L1'2'3'4'5'6", "L2'3'4'5'6", "L2'3'4'6", "L3'4'6", "L3'4"],
    ),
    "L5": (
        "mt-MRCA",
        "mt-MRCA",
        "L2'3'4'5'6",
        ["L1'2'3'4'5'6", "L2'3'4'5'6"],
    ),
    "L6": (
        "mt-MRCA",
        "mt-MRCA",
        "L3'4'6",
        ["L1'2'3'4'5'6", "L2'3'4'5'6", "L2'3'4'6", "L3'4'6"],
    ),
}

ROOT_L0_EMITTED_MARKER_POSITIONS = {
    "L0": [1048, 3516, 5442, 6185, 9347, 10589, 12007, 12720],
    "L0a": [11176, 5231, 5460, 14308],
    "L0a1": [5096],
    "L0a2": [64, 5147, 5711, 6257, 8460, 11172, 16129],
    "L0b": [6719, 15106, 15622, 16051, 16164],
    "L0d": [1438, 4232, 8152, 8251, 12121, 15466, 15930, 15941, 16243],
    "L0d1": [719, 2706, 3438, 6266, 13759],
    "L0d2": [3981, 4025, 4044, 7154, 11854, 15766],
    "L0f": [207, 4964, 9581, 9620, 13470, 14109, 15852, 16169, 16327],
    "L0k": [
        199,
        850,
        1243,
        4541,
        4907,
        5811,
        8911,
        8994,
        9136,
        10499,
        10920,
        11299,
        11653,
        13590,
        13928,
        14020,
        14182,
        14371,
        16129,
        16291,
    ],
    "L1": [3666, 7389, 13789, 14178, 14560],
    "L2": [2416, 8206, 9221, 10115, 13590, 16390],
    "L3": [1018],
    "L4": [5460, 16362],
    "L5": [3423, 7972, 12950, 16148],
    "L6": [
        146,
        961,
        1461,
        4964,
        5267,
        6002,
        6284,
        9332,
        10978,
        11116,
        12771,
        13710,
        15244,
        15289,
        16048,
    ],
}

ROOT_L0_EMITTED_MARKER_SHA256 = {
    "L0": "06e8682c1e9b7781be931a6a46daba2bd85b2d4a68e68457744df38779d40f0d",
    "L0a": "e44d9812edc7ba84d5252e87642f4313bb9e53476ca9ffb99fd9ba1f156dd79b",
    "L0a1": "e4c12dba5c51ff9ba8d2ad49f416cb84e89fbdf1b40327ecc10027fc32d4c269",
    "L0a2": "d8a90d843491bbf9b3a767e1c26dd164543b9dc96f09b9dc7cb27571d90752cf",
    "L0b": "8a8c368f1b32f4413760b7d6966048964638e7b300de20d0ccb7b99b5f58ca9f",
    "L0d": "e484c9f6fc6d528ceb112b2693fe16221a491e64e0fa99fdca4ad68bd48ab393",
    "L0d1": "24f5a60c8d62e5656c167113479e350da0c58d08754a7e8ce0f4469dc5bd95a4",
    "L0d2": "1d786c15eff0e8caa20d76fe705fecd975e5ab7b386662175e0d6cb85d64c068",
    "L0f": "1b1d008b5f4e391ffb6645521e3d612c2227d0e4f1d7c70b462bab55563cc99a",
    "L0k": "46d2aa34f5eebfc7630ad370bc58a4ba0fefc415861410921f89a35054571bab",
    "L1": "9fbd2313e58f9bd3b625a8d983f7dfdc06bffec22dafdd69421cae45f4806f2f",
    "L2": "fc93ff26995b675f76c286454dca9f51b4ef51b6878116a0590738e694c5cca7",
    "L3": "9f0b35f3cbc5d53e09266e297f3a81a283b8ce26490464a622681a8ab8d430ca",
    "L4": "fd9c43faa5ff74507e5f6427279eaf9b48f37ed44885340a908ed868bb7783a1",
    "L5": "6e142259997703726ec8adaa7bcaa81600024a93eb3677d3576e41e0de96ac73",
    "L6": "f5b5eed6e63dfea0474b4357a7cf0585a8e7c0d48b79211db4a4093ae115bdc4",
}

ROOT_L0_OLD_MARKERS = {
    "L0": [(1048, "T"), (5442, "C"), (6185, "C"), (9042, "T"), (10589, "A")],
    "L0a": [(1438, "G"), (5231, "A"), (9042, "T")],
    "L0a1": [(7158, "G"), (9818, "C"), (14308, "A")],
    "L0a2": [(7256, "T"), (11899, "C")],
    "L0b": [(3693, "A"), (5580, "C"), (12171, "G")],
    "L0d": [(1715, "C"), (8251, "A"), (9755, "A")],
    "L0d1": [(8113, "T"), (15466, "G")],
    "L0d2": [(2969, "A"), (10394, "T")],
    "L0f": [(3396, "G"), (10586, "A")],
    "L0k": [(2352, "C"), (11176, "A")],
    "L1": [(3666, "A"), (7055, "G"), (7389, "C"), (10589, "A"), (10810, "C")],
    "L2": [(2789, "C"), (7175, "C"), (7771, "G"), (9221, "G"), (16390, "A")],
    "L3": [(769, "G"), (1018, "G"), (16311, "T")],
    "L4": [(5108, "C"), (10685, "A")],
    "L5": [(5108, "C"), (15301, "A")],
    "L6": [(3396, "G"), (7146, "G"), (10589, "A")],
}

ROOT_L0_FLATTENED_STEPS = {
    "L0a'b'f'g'k": {
        "source_parent": "L0",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate below L0 for L0a, L0b, L0f, and L0k. Its exact motif is retained "
            "as source provenance, but shared mutations are not duplicated onto emitted "
            "siblings because that creates non-specific runtime evidence."
        ),
        "motif": [
            ("A189G", False, HISTORICAL_ONLY_REASON),
            ("T4586C", False, HISTORICAL_ONLY_REASON),
            ("C9818T", False, SHARED_INTERMEDIATE_REASON),
            ("T16172C", False, SHARED_INTERMEDIATE_REASON),
        ],
    },
    "L0a'b'f'g": {
        "source_parent": "L0a'b'f'g'k",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate below L0a'b'f'g'k for L0a, L0b, and L0f. Its exact motif is retained "
            "as source provenance, but shared mutations are not duplicated onto emitted "
            "siblings because that creates non-specific runtime evidence."
        ),
        "motif": [
            ("G73A", False, SHARED_INTERMEDIATE_REASON),
            ("G185A", False, HISTORICAL_ONLY_REASON),
            ("C195T", False, UNAVAILABLE_REASON),
            ("A263G!", False, SHARED_INTERMEDIATE_REASON),
            ("A2245G", False, SHARED_INTERMEDIATE_REASON),
            ("C5603T", False, SHARED_INTERMEDIATE_REASON),
            ("A11641G", False, SHARED_INTERMEDIATE_REASON),
            ("C15136T", False, SHARED_INTERMEDIATE_REASON),
            ("G15431A", False, SHARED_INTERMEDIATE_REASON),
        ],
    },
    "L0a'b'g": {
        "source_parent": "L0a'b'f'g",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate below L0a'b'f'g for L0a and L0b. Its exact motif is retained as "
            "source provenance, but shared mutations are not duplicated onto emitted siblings "
            "because that creates non-specific runtime evidence."
        ),
        "motif": [
            ("A93G", False, SHARED_INTERMEDIATE_REASON),
            ("(A95c)", False, SHARED_INTERMEDIATE_REASON),
            ("T236C", False, SHARED_INTERMEDIATE_REASON),
            ("C8428T", False, SHARED_INTERMEDIATE_REASON),
            ("A8566G", False, SHARED_INTERMEDIATE_REASON),
            ("G9755A", False, SHARED_INTERMEDIATE_REASON),
            ("C16148T", False, SHARED_INTERMEDIATE_REASON),
        ],
    },
    "L0a'g": {
        "source_parent": "L0a'b'g",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this source intermediate "
            "between L0a'b'g and L0a. Its primary-callable m.11176 event is attached to L0a "
            "with L0a'g ownership; historical-only m.16188 remains non-emitted."
        ),
        "motif": [
            ("G11176A", True, None),
            ("C16188g", False, HISTORICAL_ONLY_REASON),
        ],
    },
    "L0a1'4": {
        "source_parent": "L0a",
        "type": "flattened_unreportable_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this source intermediate "
            "between L0a and L0a1 only by m.16168C>T, which is absent from the four primary "
            "exports and callable only in the historical 2014 export."
        ),
        "motif": [
            (
                "C16168T",
                False,
                "absent from the primary four and callable only in the historical 2014 export",
            )
        ],
    },
    "L0d1'2": {
        "source_parent": "L0d",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate below L0d for L0d1 and L0d2. Its exact motif is retained as source "
            "provenance, but its deletion is unsupported and shared substitutions are not "
            "duplicated onto emitted siblings."
        ),
        "motif": [
            ("C498d", False, NON_SUBSTITUTION_REASON),
            ("A3756G", False, SHARED_INTERMEDIATE_REASON),
            ("G9755A", False, SHARED_INTERMEDIATE_REASON),
            ("T16278C", False, HISTORICAL_ONLY_REASON),
        ],
    },
    "L1'2'3'4'5'6": {
        "source_parent": "mt-MRCA",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this root-common source "
            "intermediate for L1 through L6. Its exact motif is retained as source provenance, "
            "but shared mutations are not duplicated across root siblings because that creates "
            "tree-order-dependent runtime calls."
        ),
        "motif": [
            ("C146T", False, SHARED_INTERMEDIATE_REASON),
            ("C182T", False, SHARED_INTERMEDIATE_REASON),
            ("T4312C", False, SHARED_INTERMEDIATE_REASON),
            ("T10664C", False, SHARED_INTERMEDIATE_REASON),
            ("C10915T", False, SHARED_INTERMEDIATE_REASON),
            ("A11914G", False, SHARED_INTERMEDIATE_REASON),
            ("G13276A", False, SHARED_INTERMEDIATE_REASON),
            ("G16230A", False, HISTORICAL_ONLY_REASON),
        ],
    },
    "L2'3'4'5'6": {
        "source_parent": "L1'2'3'4'5'6",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate for L2 through L6 except L1. Its exact motif is retained as source "
            "provenance, but shared mutations are not duplicated across emitted root siblings."
        ),
        "motif": [
            ("C152T", False, HISTORICAL_ONLY_REASON),
            ("A2758G", False, SHARED_INTERMEDIATE_REASON),
            ("C2885T", False, SHARED_INTERMEDIATE_REASON),
            ("G7146A", False, HISTORICAL_ONLY_REASON),
            ("T8468C", False, SHARED_INTERMEDIATE_REASON),
        ],
    },
    "L2'3'4'6": {
        "source_parent": "L2'3'4'5'6",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate for L2, L3, L4, and L6. Its exact motif is retained as source "
            "provenance, but shared mutations are not duplicated across emitted root siblings."
        ),
        "motif": [
            ("C195T", False, UNAVAILABLE_REASON),
            ("A247G", False, HISTORICAL_ONLY_REASON),
            ("A825t", False, SHARED_INTERMEDIATE_REASON),
            ("T8655C", False, SHARED_INTERMEDIATE_REASON),
            ("A10688G", False, SHARED_INTERMEDIATE_REASON),
            ("C10810T", False, SHARED_INTERMEDIATE_REASON),
            ("G13105A", False, SHARED_INTERMEDIATE_REASON),
            ("T13506C", False, SHARED_INTERMEDIATE_REASON),
            ("G15301A", False, SHARED_INTERMEDIATE_REASON),
            ("A16129G", False, SHARED_INTERMEDIATE_REASON),
            ("T16187C", False, HISTORICAL_ONLY_REASON),
            ("C16189T", False, UNAVAILABLE_REASON),
        ],
    },
    "L3'4'6": {
        "source_parent": "L2'3'4'6",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate for L3, L4, and L6. Its exact motif is retained as source provenance, "
            "but shared mutations are not duplicated across emitted root siblings."
        ),
        "motif": [
            ("G4104A", False, SHARED_INTERMEDIATE_REASON),
            ("A7521G", False, HISTORICAL_ONLY_REASON),
        ],
    },
    "L3'4": {
        "source_parent": "L3'4'6",
        "type": "flattened_source_intermediate",
        "reason": (
            "Flattened in issue 1798 batch 01: Build 17 defines this shared source "
            "intermediate for L3 and L4. Its exact motif is retained as source provenance, but "
            "shared mutations are not duplicated across emitted root siblings."
        ),
        "motif": [
            ("T182C!", False, SHARED_INTERMEDIATE_REASON),
            ("T3594C", False, SHARED_INTERMEDIATE_REASON),
            ("T7256C", False, SHARED_INTERMEDIATE_REASON),
            ("T13650C", False, SHARED_INTERMEDIATE_REASON),
            ("T16278C", False, HISTORICAL_ONLY_REASON),
        ],
    },
}


def _canonical_sha256(value: Any) -> str:
    """Independent canonicalizer: do not share the production digest helper."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def _tsv_sha256(rows: list[tuple[Any, ...]]) -> str:
    payload = "".join("\t".join(str(value) for value in row) + "\n" for row in sorted(rows))
    return hashlib.sha256(payload.encode()).hexdigest()


def _v1_semantic_projection(source: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    result = []
    for name in names:
        record = source["nodes"][name]
        markers = []
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort = source["array_cohorts"][coverage["cohort_id"]]["export_ids"]
            markers.append(
                {
                    "rsid": marker["rsid"],
                    "pos": marker["pos"],
                    "ancestral_allele": marker["ancestral_allele"],
                    "allele": marker["allele"],
                    "array_coverage": {
                        "modern_exports_tested": len(cohort),
                        "modern_exports_with_position": len(coverage["position_present_in"]),
                    },
                }
            )
        result.append(
            {
                "node": name,
                "emitted_snps": markers,
                "source_motif": record["direct_source_motif"],
            }
        )
    return sorted(result, key=lambda item: item["node"])


def _v1_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
    rows = []
    for name in names:
        for marker in source["nodes"][name]["emitted_snps"]:
            coverage = marker["array_coverage"]
            rows.append(
                (
                    name,
                    marker["rsid"],
                    marker["pos"],
                    coverage["cohort_id"],
                    len(source["array_cohorts"][coverage["cohort_id"]]["export_ids"]),
                    len(coverage["position_present_in"]),
                )
            )
    return rows


def _locked_semantic_projection(source: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "emitted_parent": source["nodes"][name]["emitted_parent"],
            "source_topology": source["nodes"][name]["source_topology"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
            "emitted_snps": [
                {
                    key: marker[key]
                    for key in (
                        "rsid",
                        "pos",
                        "ancestral_allele",
                        "allele",
                        "motif_owner",
                    )
                }
                for marker in source["nodes"][name]["emitted_snps"]
            ],
        }
        for name in names
    ]


def _baseline_v2_registry_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "emitted_parent": source["nodes"][name]["emitted_parent"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
            "emitted_snps": _locked_semantic_projection(source, [name])[0]["emitted_snps"],
        }
        for name in names
    ]


def _direct_motif_semantic_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
        }
        for name in names
    ]


def _locked_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
    return [
        (
            name,
            marker["rsid"],
            marker["pos"],
            marker["array_coverage"]["cohort_id"],
            ",".join(marker["array_coverage"]["position_present_in"]),
            ",".join(marker["array_coverage"]["callable_snv_in"]),
        )
        for name in names
        for marker in source["nodes"][name]["emitted_snps"]
    ]


def _tree_projection(tree: dict[str, Any]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], parent: str | None) -> None:
        record = {
            "node": node["haplogroup"],
            "parent": parent,
            "defining_snps": node.get("defining_snps", []),
        }
        if optional_conflict_snps := node.get("optional_conflict_snps"):
            record["optional_conflict_snps"] = optional_conflict_snps
        projection.append(record)
        for child in node.get("children", []):
            visit(child, node["haplogroup"])

    visit(tree, None)
    return projection


def _find_node(tree: dict[str, Any], name: str) -> dict[str, Any]:
    if tree["haplogroup"] == name:
        return tree
    for child in tree.get("children", []):
        try:
            return _find_node(child, name)
        except LookupError:
            pass
    raise LookupError(name)


def _issues_text(issues: list[str]) -> str:
    return "\n".join(issues)


def _refresh_semantic_projection_digests(source: dict[str, Any]) -> None:
    migration = source["migration"]
    migration["legacy_v1_semantic_sha256"] = _canonical_sha256(
        _v1_semantic_projection(source, migration["legacy_locked_exact_nodes"])
    )
    migration["baseline_v1_semantic_sha256"] = _canonical_sha256(
        _v1_semantic_projection(source, migration["baseline_exact_nodes"])
    )
    migration["baseline_v2_registry_semantic_sha256"] = _canonical_sha256(
        _baseline_v2_registry_projection(source, migration["baseline_exact_nodes"])
    )
    migration["locked_exact_semantic_sha256"] = _canonical_sha256(
        _locked_semantic_projection(source, migration["locked_exact_nodes"])
    )
    migration["baseline_direct_motif_semantic_sha256"] = _canonical_sha256(
        _direct_motif_semantic_projection(source, migration["baseline_direct_motif_exact_nodes"])
    )
    migration["locked_direct_motif_semantic_sha256"] = _canonical_sha256(
        _direct_motif_semantic_projection(source, migration["locked_direct_motif_exact_nodes"])
    )


def _refresh_coverage_projection_digests(source: dict[str, Any]) -> None:
    migration = source["migration"]
    migration["legacy_v1_coverage_sha256"] = _tsv_sha256(
        _v1_coverage_rows(source, migration["legacy_locked_exact_nodes"])
    )
    migration["baseline_v1_coverage_sha256"] = _tsv_sha256(
        _v1_coverage_rows(source, migration["baseline_exact_nodes"])
    )
    migration["baseline_v2_coverage_membership_sha256"] = _tsv_sha256(
        _locked_coverage_rows(source, migration["baseline_exact_nodes"])
    )
    migration["locked_exact_coverage_membership_sha256"] = _tsv_sha256(
        _locked_coverage_rows(source, migration["locked_exact_nodes"])
    )


def test_production_registry_is_a_complete_dynamic_partition() -> None:
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)

    assert len(inventory.occurrences) == 193
    assert len(inventory.by_name) == 193
    assert not inventory.duplicates
    assert len(inventory.marker_bearing_names) == 186
    assert len(inventory.markerless_names) == 7
    assert inventory.marker_count == 634
    assert inventory.edge_count == 192
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["structural_exceptions"]) | set(
        _MT_SOURCE["pending_nodes"]
    ) == set(inventory.by_name)
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["pending_nodes"]) == set(
        inventory.marker_bearing_names
    )
    assert set(_MT_SOURCE["structural_exceptions"]) == set(inventory.markerless_names)
    assert _MT_SOURCE["schema_version"] == 3
    assert _MT_SOURCE["retired_emitted_nodes"] == {"A4": _a4_retirement_tombstone()}
    assert _MT_SOURCE["direct_source_motif_states"] == {
        "exact_nodes": DIRECT_MOTIF_EXACT_NODES,
        "legacy_partial_nodes": DIRECT_MOTIF_LEGACY_PARTIAL_NODES,
    }
    assert set(DIRECT_MOTIF_EXACT_NODES).isdisjoint(DIRECT_MOTIF_LEGACY_PARTIAL_NODES)
    assert set(DIRECT_MOTIF_EXACT_NODES) | set(DIRECT_MOTIF_LEGACY_PARTIAL_NODES) == set(
        _MT_SOURCE["nodes"]
    )
    assert all(
        _MT_SOURCE["nodes"][name]["source_motif_status"] == "exact"
        for name in DIRECT_MOTIF_EXACT_NODES
    )
    assert all(
        _MT_SOURCE["nodes"][name]["source_motif_status"] == "legacy_partial"
        for name in DIRECT_MOTIF_LEGACY_PARTIAL_NODES
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []
    assert _validate_mt_registry_against_tree(_MT_SOURCE, inventory) == []
    assert _validate_mt_source(_MT_SOURCE) == []
    assert _validate_mt_source(_MT_SOURCE, tree) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "missing=['B']"),
        ("overlap", "marker-exact and pending states overlap: G"),
        ("orphan", "extra=['not-an-emitted-node']"),
    ],
)
def test_partition_rejects_missing_overlap_and_orphan(mutation: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    inventory = _index_mt_tree(build_mt_tree())

    if mutation == "missing":
        source["structural_exceptions"].pop("B")
        issues = _validate_mt_registry_against_tree(source, inventory)
    elif mutation == "overlap":
        source["pending_nodes"]["G"] = {"emitted_parent": "M"}
        issues = _validate_mt_source_schema(source)
    else:
        source["pending_nodes"]["not-an-emitted-node"] = {"emitted_parent": "M"}
        issues = _validate_mt_registry_against_tree(source, inventory)

    assert expected in _issues_text(issues)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("overlap", "exact and legacy-partial direct-source motif states overlap: K"),
        ("missing", "direct-source motif states do not partition the marker-exact nodes"),
        ("orphan", "direct-source motif states do not partition the marker-exact nodes"),
    ],
)
def test_direct_source_motif_states_are_disjoint_and_exhaustive(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    states = source["direct_source_motif_states"]
    if mutation == "overlap":
        states["legacy_partial_nodes"].append("K")
    elif mutation == "missing":
        states["exact_nodes"].remove("K")
    else:
        states["exact_nodes"].append("not-an-emitted-node")
        states["exact_nodes"].sort()

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "invalid provenance fields"),
        ("unknown", "invalid source-motif status"),
        ("disagrees", "source-motif status disagrees with the direct-source motif frontier"),
    ],
)
def test_per_record_source_motif_status_is_explicit_and_agrees_with_frontier(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "missing":
        source["nodes"]["C"].pop("source_motif_status")
    elif mutation == "unknown":
        source["nodes"]["C"]["source_motif_status"] = "assumed"
    else:
        source["nodes"]["C"]["source_motif_status"] = "legacy_partial"

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_duplicate_tree_occurrence_is_reported_before_name_deduplication() -> None:
    tree = build_mt_tree()
    duplicated_name = tree["children"][0]["haplogroup"]
    tree["children"].append(deepcopy(tree["children"][0]))
    inventory = _index_mt_tree(tree)

    assert duplicated_name in inventory.duplicates
    assert len(inventory.duplicates[duplicated_name]) == 2
    issues = _validate_mt_registry_against_tree(_MT_SOURCE, inventory)
    assert issues
    assert all(issue.startswith("Duplicate emitted mtDNA node ") for issue in issues)
    assert any(f"Duplicate emitted mtDNA node {duplicated_name}" in issue for issue in issues)


def test_frontier_and_registry_digests_match_independent_canonicalizers() -> None:
    migration = _MT_SOURCE["migration"]
    snapshot = haplogroup_builder._MT_BASELINE_SNAPSHOT
    archive_source = {
        "array_cohorts": snapshot["array_cohorts"],
        "nodes": snapshot["nodes"],
    }

    assert migration["baseline_commit"] == BASELINE_COMMIT
    assert _canonical_sha256(snapshot) == BASELINE_SNAPSHOT_SHA256
    assert snapshot["baseline_commit"] == BASELINE_COMMIT
    assert snapshot["legacy_locked_exact_nodes"] == migration["legacy_locked_exact_nodes"]
    assert snapshot["baseline_exact_nodes"] == migration["baseline_exact_nodes"]
    assert (
        snapshot["baseline_direct_motif_exact_nodes"]
        == (migration["baseline_direct_motif_exact_nodes"])
    )
    assert _canonical_sha256(migration["legacy_locked_exact_nodes"]) == (LEGACY_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == (BASELINE_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["locked_exact_nodes"]) == (LOCKED_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["locked_direct_motif_exact_nodes"]) == (
        LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == (INITIAL_PENDING_NAMES_SHA256)
    assert (
        _canonical_sha256(
            {
                "array_exports": _MT_SOURCE["array_exports"],
                "array_cohorts": _MT_SOURCE["array_cohorts"],
            }
        )
        == ARRAY_MANIFEST_SHA256
    )
    assert (
        _canonical_sha256({"source": _MT_SOURCE["source"], "references": _MT_SOURCE["references"]})
        == SOURCE_METADATA_SHA256
    )
    assert (
        _canonical_sha256(
            {
                "direct_source_motif_states": _MT_SOURCE["direct_source_motif_states"],
                "omitted_nodes": _MT_SOURCE["omitted_nodes"],
                "retired_emitted_nodes": _MT_SOURCE["retired_emitted_nodes"],
                "structural_exceptions": _MT_SOURCE["structural_exceptions"],
                "pending_nodes": _MT_SOURCE["pending_nodes"],
            }
        )
        == STATE_PARTITION_SHA256
    )
    emitted_tree_digest = _canonical_sha256(_tree_projection(build_mt_tree()))
    assert emitted_tree_digest == LOCKED_EMITTED_TREE_SHA256

    assert (
        _canonical_sha256(
            _v1_semantic_projection(archive_source, migration["legacy_locked_exact_nodes"])
        )
        == LEGACY_V1_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_v1_coverage_rows(archive_source, migration["legacy_locked_exact_nodes"]))
        == LEGACY_V1_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(
            _v1_semantic_projection(archive_source, migration["baseline_exact_nodes"])
        )
        == BASELINE_V1_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_v1_coverage_rows(archive_source, migration["baseline_exact_nodes"]))
        == BASELINE_V1_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(
            _baseline_v2_registry_projection(archive_source, migration["baseline_exact_nodes"])
        )
        == BASELINE_V2_REGISTRY_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_locked_coverage_rows(archive_source, migration["baseline_exact_nodes"]))
        == BASELINE_V2_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(_locked_semantic_projection(_MT_SOURCE, migration["locked_exact_nodes"]))
        == LOCKED_EXACT_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_locked_coverage_rows(_MT_SOURCE, migration["locked_exact_nodes"]))
        == LOCKED_EXACT_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(
            _direct_motif_semantic_projection(
                archive_source, migration["baseline_direct_motif_exact_nodes"]
            )
        )
        == BASELINE_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256
    )
    assert _canonical_sha256(snapshot["emitted_tree_projection"]) == (BASELINE_EMITTED_TREE_SHA256)
    assert (
        _canonical_sha256(
            _direct_motif_semantic_projection(
                _MT_SOURCE, migration["locked_direct_motif_exact_nodes"]
            )
        )
        == LOCKED_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256
    )

    expected_literals = {
        "baseline_snapshot_sha256": BASELINE_SNAPSHOT_SHA256,
        "legacy_locked_exact_nodes_sha256": LEGACY_EXACT_NAMES_SHA256,
        "legacy_v1_semantic_sha256": LEGACY_V1_SEMANTIC_SHA256,
        "legacy_v1_coverage_sha256": LEGACY_V1_COVERAGE_SHA256,
        "baseline_exact_nodes_sha256": BASELINE_EXACT_NAMES_SHA256,
        "baseline_v1_semantic_sha256": BASELINE_V1_SEMANTIC_SHA256,
        "baseline_v1_coverage_sha256": BASELINE_V1_COVERAGE_SHA256,
        "baseline_v2_registry_semantic_sha256": BASELINE_V2_REGISTRY_SEMANTIC_SHA256,
        "baseline_v2_coverage_membership_sha256": BASELINE_V2_COVERAGE_SHA256,
        "locked_exact_nodes_sha256": LOCKED_EXACT_NAMES_SHA256,
        "locked_exact_semantic_sha256": LOCKED_EXACT_SEMANTIC_SHA256,
        "locked_exact_coverage_membership_sha256": LOCKED_EXACT_COVERAGE_SHA256,
        "baseline_direct_motif_exact_nodes_sha256": BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256,
        "baseline_direct_motif_semantic_sha256": BASELINE_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256,
        "locked_direct_motif_exact_nodes_sha256": LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256,
        "locked_direct_motif_semantic_sha256": LOCKED_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256,
        "initial_direct_motif_pending_nodes_sha256": (INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256),
        "initial_pending_nodes_sha256": INITIAL_PENDING_NAMES_SHA256,
        "array_manifest_sha256": ARRAY_MANIFEST_SHA256,
        "source_metadata_sha256": SOURCE_METADATA_SHA256,
        "state_partition_sha256": STATE_PARTITION_SHA256,
        "baseline_emitted_tree_sha256": BASELINE_EMITTED_TREE_SHA256,
        "locked_emitted_tree_sha256": LOCKED_EMITTED_TREE_SHA256,
    }
    for field, expected in expected_literals.items():
        assert migration[field] == expected


def test_archived_baseline_allows_coherent_live_g_and_m1_migration() -> None:
    """Historical anchors come from the archive while live locks can advance."""
    snapshot = haplogroup_builder._MT_BASELINE_SNAPSHOT

    assert (
        snapshot["nodes"]["G"]["direct_source_motif"]
        != (_MT_SOURCE["nodes"]["G"]["direct_source_motif"])
    )
    assert snapshot["nodes"]["M1"]["emitted_snps"] != (_MT_SOURCE["nodes"]["M1"]["emitted_snps"])
    assert _MT_SOURCE["migration"]["baseline_snapshot_sha256"] == (BASELINE_SNAPSHOT_SHA256)
    assert _validate_mt_source_schema(_MT_SOURCE) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("node", "baseline_v1_semantic_sha256 differs from the locked historical value"),
        ("coverage", "baseline_v1_coverage_sha256 differs from the locked historical value"),
        ("tree", "baseline_emitted_tree_sha256 differs from the locked historical value"),
        ("keyset_missing", "nodes do not equal its baseline frontier"),
        ("keyset_extra", "nodes do not equal its baseline frontier"),
        ("commit", "has the wrong baseline commit"),
    ],
)
def test_baseline_archive_drift_fails_closed(mutation: str, expected: str) -> None:
    """Semantic, membership, and provenance drift all invalidate the archive."""
    snapshot = deepcopy(haplogroup_builder._MT_BASELINE_SNAPSHOT)
    if mutation == "node":
        snapshot["nodes"]["G"]["emitted_snps"][0]["allele"] = "A"
    elif mutation == "coverage":
        snapshot["nodes"]["M1"]["emitted_snps"][0]["array_coverage"]["position_present_in"].pop()
    elif mutation == "tree":
        snapshot["emitted_tree_projection"][1]["defining_snps"][0]["allele"] = "A"
    elif mutation == "keyset_missing":
        snapshot["nodes"].pop("G")
    elif mutation == "keyset_extra":
        snapshot["nodes"]["not-a-baseline-node"] = deepcopy(snapshot["nodes"]["G"])
    else:
        snapshot["baseline_commit"] = "0" * 40

    text = _issues_text(_validate_mt_source_schema(_MT_SOURCE, baseline_snapshot=snapshot))
    assert "baseline snapshot differs from the review-locked archive" in text
    assert expected in text


def test_rewriting_registry_archive_digest_cannot_bless_archive_drift() -> None:
    """A coherent source+archive digest rewrite still needs the builder review lock."""
    source = deepcopy(_MT_SOURCE)
    snapshot = deepcopy(haplogroup_builder._MT_BASELINE_SNAPSHOT)
    snapshot["nodes"]["G"]["emitted_snps"][0]["allele"] = "A"
    source["migration"]["baseline_snapshot_sha256"] = _canonical_sha256(snapshot)

    text = _issues_text(_validate_mt_source_schema(source, baseline_snapshot=snapshot))
    assert "migration baseline_snapshot_sha256 does not match the baseline archive" not in text
    assert "baseline snapshot differs from the review-locked archive" in text
    assert "baseline_v1_semantic_sha256 differs from the locked historical value" in text


def test_reviewed_live_tree_lock_can_advance_without_rewriting_baseline() -> None:
    source = deepcopy(_MT_SOURCE)
    tree = build_mt_tree()
    tree["children"][0], tree["children"][1] = tree["children"][1], tree["children"][0]
    advanced_live_digest = _canonical_sha256(_tree_projection(tree))
    source["migration"]["locked_emitted_tree_sha256"] = advanced_live_digest

    assert advanced_live_digest != BASELINE_EMITTED_TREE_SHA256
    assert source["migration"]["baseline_emitted_tree_sha256"] == (BASELINE_EMITTED_TREE_SHA256)
    with patch.object(
        haplogroup_builder,
        "_MT_LOCKED_EMITTED_TREE_SHA256",
        advanced_live_digest,
    ):
        assert _validate_mt_source_schema(source) == []
        assert _validate_mt_registry_against_tree(source, _index_mt_tree(tree)) == []


def test_live_tree_digest_requires_registry_and_builder_lock_agreement() -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["locked_emitted_tree_sha256"] = "0" * 64

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_emitted_tree_sha256 differs from the review-locked live tree" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "emitted tree differs from its live locked fingerprint" in registry_text


def test_coherent_tree_and_stored_live_lock_change_still_requires_builder_review() -> None:
    source = deepcopy(_MT_SOURCE)
    tree = build_mt_tree()
    tree["children"][0], tree["children"][1] = tree["children"][1], tree["children"][0]
    advanced_live_digest = _canonical_sha256(_tree_projection(tree))
    source["migration"]["locked_emitted_tree_sha256"] = advanced_live_digest

    registry_text = _issues_text(_validate_mt_registry_against_tree(source, _index_mt_tree(tree)))
    assert "emitted tree differs from its live locked fingerprint" not in registry_text
    assert "emitted tree differs from the review-locked live tree" in registry_text
    assert source["migration"]["baseline_emitted_tree_sha256"] == (BASELINE_EMITTED_TREE_SHA256)


@pytest.mark.parametrize("destination", ["pending", "structural"])
def test_baseline_exact_node_cannot_regress_to_a_weaker_state(destination: str) -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"].pop("C")
    source["migration"]["locked_exact_nodes"].remove("C")
    source["migration"]["locked_exact_nodes_sha256"] = _canonical_sha256(
        source["migration"]["locked_exact_nodes"]
    )
    if destination == "pending":
        source["pending_nodes"]["C"] = {"emitted_parent": "M8"}
    else:
        source["structural_exceptions"]["C"] = {
            "type": "markerless_passthrough",
            "emitted_parent": "M8",
            "source_status": "pending",
            "reason": "test-only attempted regression",
        }

    issues = _validate_mt_source_schema(source)
    assert "mtDNA baseline exact frontier regressed" in issues
    if destination == "structural":
        registry_issues = _validate_mt_registry_against_tree(
            source, _index_mt_tree(build_mt_tree())
        )
        assert registry_issues


def test_coherent_semantic_drift_cannot_rewrite_locked_digests() -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["G1"]["direct_source_motif"][0]["notation"] = "T8200c"
    _refresh_semantic_projection_digests(source)

    issues = _validate_mt_source_schema(source)
    text = _issues_text(issues)
    assert "does not match its registry projection" not in text
    assert "legacy_v1_semantic_sha256 does not match the baseline archive projection" in text
    assert "baseline_v1_semantic_sha256 does not match the baseline archive projection" in text
    assert (
        "baseline_v2_registry_semantic_sha256 does not match the baseline archive projection"
        in text
    )
    assert "locked_exact_semantic_sha256 differs from the review-locked live value" in text
    assert (
        "baseline_direct_motif_semantic_sha256 does not match the baseline archive projection"
        in text
    )
    assert "locked_direct_motif_semantic_sha256 differs from the review-locked live value" in text


def test_direct_source_motif_exact_frontier_cannot_regress() -> None:
    source = deepcopy(_MT_SOURCE)
    source["direct_source_motif_states"]["exact_nodes"].remove("C")
    source["direct_source_motif_states"]["legacy_partial_nodes"].append("C")
    source["direct_source_motif_states"]["legacy_partial_nodes"].sort()
    source["migration"]["locked_direct_motif_exact_nodes"].remove("C")
    source["migration"]["locked_direct_motif_exact_nodes_sha256"] = _canonical_sha256(
        source["migration"]["locked_direct_motif_exact_nodes"]
    )

    text = _issues_text(_validate_mt_source_schema(source))
    assert "baseline direct-source motif frontier regressed" in text
    assert "direct-source motif pending frontier grew beyond its baseline" in text


def test_coherent_coverage_drift_cannot_rewrite_locked_digests() -> None:
    source = deepcopy(_MT_SOURCE)
    coverage = source["nodes"]["G"]["emitted_snps"][1]["array_coverage"]
    coverage["callable_snv_in"].remove("pgp_4162")
    _refresh_coverage_projection_digests(source)

    issues = _validate_mt_source_schema(source)
    text = _issues_text(issues)
    assert "does not match its registry projection" not in text
    assert (
        "baseline_v2_coverage_membership_sha256 does not match the baseline archive projection"
        in text
    )
    assert (
        "locked_exact_coverage_membership_sha256 differs from the review-locked live value" in text
    )


def test_marker_and_source_direction_drift_fails_both_source_and_tree_guards() -> None:
    source = deepcopy(_MT_SOURCE)
    marker = source["nodes"]["G"]["emitted_snps"][0]
    marker["allele"] = "T"
    source["nodes"]["G"]["direct_source_motif"][0]["derived_allele"] = "T"
    _refresh_semantic_projection_digests(source)

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "semantic_sha256 differs from the review-locked live value" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Marker-exact mtDNA node G has markers" in registry_text


def test_stored_digest_drift_fails_even_when_records_are_unchanged() -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["locked_exact_semantic_sha256"] = "0" * 64

    text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_exact_semantic_sha256 differs from the review-locked live value" in text
    assert "locked_exact_semantic_sha256 does not match its registry projection" in text


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("cohort-member-object", "non-string export member"),
        ("cohort-id-object", "invalid array cohort"),
        ("motif-owner-object", "invalid motif owner"),
    ],
)
def test_json_compatible_non_string_values_fail_without_crashing(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "cohort-member-object":
        source["array_cohorts"]["primary_four_23andme"]["export_ids"].append(
            {"not": "an export ID"}
        )
    elif mutation == "cohort-id-object":
        source["nodes"]["G"]["emitted_snps"][0]["array_coverage"]["cohort_id"] = {
            "not": "a cohort ID"
        }
    else:
        source["nodes"]["G"]["emitted_snps"][0]["motif_owner"] = {"not": "a source node"}

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("marker", "marker with invalid fields"),
        ("coverage", "invalid coverage fields"),
    ],
)
def test_unknown_marker_and_coverage_fields_fail_closed(target: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    marker = source["nodes"]["G"]["emitted_snps"][0]
    if target == "marker":
        marker["unreviewed_field"] = True
    else:
        marker["array_coverage"]["unreviewed_field"] = True

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_substitution_notation_must_match_declared_direction() -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["G"]["direct_source_motif"][0]["notation"] = "C4833T"

    assert "notation disagrees with its declared allele direction" in _issues_text(
        _validate_mt_source_schema(source)
    )


@pytest.mark.parametrize(
    ("notation", "expected"),
    [
        ("T146C!", ("T", 146, "C", False, 1)),
        ("C146T!!", ("C", 146, "T", False, 2)),
        ("(T146C!!!)", ("T", 146, "C", True, 3)),
    ],
)
def test_substitution_notation_preserves_lineage_local_event_marks(
    notation: str, expected: tuple[str, int, str, bool, int]
) -> None:
    assert _mt_parse_substitution_notation(notation) == expected


@pytest.mark.parametrize("notation", ["(T146C", "T146C)", "T146C!?", "A0G"])
def test_invalid_substitution_notation_is_rejected(notation: str) -> None:
    assert _mt_parse_substitution_notation(notation) is None


def test_exact_structural_source_identity_cannot_alias_marker_exact_source() -> None:
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["R0"] = {
        "type": "markerless_passthrough",
        "emitted_parent": "R",
        "source_status": "exact",
        "reason": "test-only attempted source alias",
        "source_node": "G",
        "source_topology": {
            "status": "exact",
            "emitted_parent_source_node": "R",
            "source_parent": "R",
            "flattened_source_path": [],
        },
        "direct_source_motif": [
            {
                "notation": "A73G",
                "mutation_type": "substitution",
                "pos": 73,
                "ancestral_allele": "A",
                "derived_allele": "G",
                "emitted": False,
                "omission_reason": "test-only markerless decision",
            }
        ],
        "emitted_snps": [],
    }

    assert "exact records repeat a direct source-node identity" in _issues_text(
        _validate_mt_source_schema(source)
    )


def test_new_marker_bearing_tree_node_cannot_enter_pending_frontier() -> None:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"]["synthetic-marker-node"] = {"emitted_parent": "M"}
    tree = build_mt_tree()
    _find_node(tree, "M")["children"].append(
        {
            "haplogroup": "synthetic-marker-node",
            "defining_snps": [{"rsid": "test1798", "pos": 42, "allele": "A"}],
            "children": [],
        }
    )

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "pending frontier grew beyond the initial audited tree" in schema_text
    registry_text = _issues_text(_validate_mt_registry_against_tree(source, _index_mt_tree(tree)))
    assert "review-locked live tree" in registry_text


@pytest.mark.parametrize(
    ("category", "name", "wrong_parent", "expected"),
    [
        ("nodes", "Z", "M", "Marker-exact mtDNA node Z declares parent 'M'"),
        (
            "structural_exceptions",
            "U5",
            "U8",
            "Structural mtDNA node U5 declares parent 'U8'",
        ),
        (
            "structural_exceptions",
            "R0",
            "N",
            "Structural mtDNA node R0 declares parent 'N'",
        ),
    ],
)
def test_exact_pending_and_structural_parent_declarations_are_live_checked(
    category: str, name: str, wrong_parent: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    source[category][name]["emitted_parent"] = wrong_parent

    issues = _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    assert expected in _issues_text(issues)


def test_moving_copied_z_from_m8_to_m_is_detected_as_topology_drift() -> None:
    tree = build_mt_tree()
    m8 = _find_node(tree, "M8")
    z = next(child for child in m8["children"] if child["haplogroup"] == "Z")
    m8["children"].remove(z)
    _find_node(tree, "M")["children"].append(z)

    issues = _validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree))
    text = _issues_text(issues)
    assert "Marker-exact mtDNA node Z declares parent 'M8'; emitted parent is 'M'" in text
    assert "review-locked live tree" in text


def test_structural_exceptions_are_narrow_and_markerless() -> None:
    structural = _MT_SOURCE["structural_exceptions"]
    assert set(structural) == {"mt-MRCA", "R0", "HV", "H5", "H2a2", "B", "U5"}
    assert structural["mt-MRCA"] == {
        "type": "root",
        "emitted_parent": None,
        "source_status": "synthetic",
        "source_topology_anchor": "mt-MRCA",
        "reason": "Synthetic tree-walk root; it emits no defining marker.",
    }
    for name in ("R0", "HV", "H5", "H2a2", "B", "U5"):
        assert structural[name]["type"] == "markerless_passthrough"
        assert structural[name]["source_status"] == "exact"
        assert structural[name]["source_node"] == ("B4'5" if name == "B" else name)
        assert structural[name]["emitted_snps"] == []
        assert structural[name]["reason"]

    tree = build_mt_tree()
    _find_node(tree, "R0")["defining_snps"].append(
        {"rsid": "structural-escape", "pos": 1, "allele": "A"}
    )
    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Structural mtDNA pass-through R0 must be markerless" in text
    assert "mtDNA markerless nodes do not equal the structural exceptions" in text


def test_synthetic_root_cannot_be_retyped_or_reparented() -> None:
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["mt-MRCA"].update(
        {"type": "markerless_passthrough", "emitted_parent": "L3"}
    )

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Structural mtDNA pass-through mt-MRCA cannot be the root" in text
    assert "declares parent 'L3'; emitted parent is None" in text


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "Synthetic mtDNA root mt-MRCA has no source-topology anchor"),
        ("blank", "Synthetic mtDNA root mt-MRCA has no source-topology anchor"),
        ("mismatch", "must equal canonical emitted root name 'mt-MRCA'"),
        ("non-root", "Structural mtDNA node R0 has invalid provenance fields"),
    ],
)
def test_synthetic_root_topology_anchor_is_root_only_and_nonblank(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "missing":
        source["structural_exceptions"]["mt-MRCA"].pop("source_topology_anchor")
    elif mutation == "blank":
        source["structural_exceptions"]["mt-MRCA"]["source_topology_anchor"] = " "
    elif mutation == "mismatch":
        source["structural_exceptions"]["mt-MRCA"]["source_topology_anchor"] = "wrong-root"
    else:
        source["structural_exceptions"]["R0"]["source_topology_anchor"] = "R0"

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize("collision", ["direct", "omission", "flattened-path"])
def test_synthetic_root_topology_anchor_cannot_collide_with_source_identity(
    collision: str,
) -> None:
    source = deepcopy(_MT_SOURCE)
    if collision == "direct":
        source["nodes"]["G"]["source_node"] = "mt-MRCA"
    elif collision == "omission":
        source["omitted_nodes"]["mt-MRCA"] = {
            "type": "unreportable_source_node",
            "reason": "test-only synthetic-anchor collision",
        }
    else:
        source["nodes"]["G1"]["source_topology"] = {
            "status": "exact",
            "emitted_parent_source_node": "G",
            "source_parent": "mt-MRCA",
            "flattened_source_path": [
                {
                    "source_node": "mt-MRCA",
                    "source_parent": "G",
                    "reason": "test-only synthetic-anchor collision",
                    "direct_source_motif": [],
                }
            ],
        }

    text = _issues_text(_validate_mt_source_schema(source))
    assert "root source-topology anchors collide with source-node identities: mt-MRCA" in text


def _source_with_exact_root_child(
    child_anchor: str = "mt-MRCA", root_anchor: str = "mt-MRCA"
) -> dict[str, Any]:
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["mt-MRCA"]["source_topology_anchor"] = root_anchor
    source["nodes"]["L0"]["source_topology"]["emitted_parent_source_node"] = child_anchor
    return source


def test_synthetic_root_can_anchor_exact_child_source_topology() -> None:
    source = _source_with_exact_root_child()

    assert (
        source["structural_exceptions"]["mt-MRCA"]
        == (_MT_SOURCE["structural_exceptions"]["mt-MRCA"])
    )
    assert _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree())) == []


def test_exact_root_child_rejects_wrong_declared_parent_anchor() -> None:
    source = _source_with_exact_root_child(child_anchor="not-mt-MRCA")

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "names emitted-parent source 'not-mt-MRCA'; expected 'mt-MRCA'" in text


def test_exact_root_child_rejects_invalid_registry_root_anchor() -> None:
    source = _source_with_exact_root_child(child_anchor="not-mt-MRCA", root_anchor="not-mt-MRCA")

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "must equal canonical emitted root name 'mt-MRCA'" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert (
        "source-topology anchor 'not-mt-MRCA' must equal canonical emitted root name 'mt-MRCA'"
        in registry_text
    )


def test_coherent_synthetic_root_rename_cannot_advance_with_the_live_tree_lock() -> None:
    source = deepcopy(_MT_SOURCE)
    tree = build_mt_tree()
    old_name = "mt-MRCA"
    new_name = "renamed-root"
    tree["haplogroup"] = new_name
    root_record = source["structural_exceptions"].pop(old_name)
    root_record["source_topology_anchor"] = new_name
    source["structural_exceptions"][new_name] = root_record
    for category in ("nodes", "pending_nodes", "structural_exceptions"):
        for record in source[category].values():
            if record.get("emitted_parent") == old_name:
                record["emitted_parent"] = new_name

    state_partition_digest = _canonical_sha256(
        {
            "direct_source_motif_states": source["direct_source_motif_states"],
            "omitted_nodes": source["omitted_nodes"],
            "retired_emitted_nodes": source["retired_emitted_nodes"],
            "structural_exceptions": source["structural_exceptions"],
            "pending_nodes": source["pending_nodes"],
        }
    )
    live_tree_digest = _canonical_sha256(_tree_projection(tree))
    source["migration"]["state_partition_sha256"] = state_partition_digest
    source["migration"]["locked_emitted_tree_sha256"] = live_tree_digest

    with (
        patch.object(haplogroup_builder, "_MT_STATE_PARTITION_SHA256", state_partition_digest),
        patch.object(haplogroup_builder, "_MT_LOCKED_EMITTED_TREE_SHA256", live_tree_digest),
    ):
        schema_text = _issues_text(_validate_mt_source_schema(source))
        registry_text = _issues_text(
            _validate_mt_registry_against_tree(source, _index_mt_tree(tree))
        )

    assert "must use canonical root name 'mt-MRCA'" in schema_text
    assert "must use canonical root name 'mt-MRCA'" in registry_text


def test_six_export_manifest_and_two_23andme_cohorts_are_pinned() -> None:
    assert _MT_SOURCE["array_exports"] == EXPECTED_EXPORTS
    assert _MT_SOURCE["array_cohorts"] == EXPECTED_COHORTS
    assert "pgp_ancestry_4190" not in PRIMARY_EXPORTS
    assert "pgp_ancestry_4190" not in HISTORICAL_EXPORTS
    assert _MT_SOURCE["array_exports"]["pgp_ancestry_4190"]["role"] == (
        "other_vendor_comparator_only"
    )

    k1b = _MT_SOURCE["nodes"]["K1b"]["emitted_snps"][0]
    assert k1b["pos"] == 5913
    assert k1b["array_coverage"] == {
        "cohort_id": "primary_four_23andme",
        "position_present_in": ["pgp_4139", "pgp_4187"],
        "callable_snv_in": [],
    }


def test_issue_1907_d2_record_and_frontiers_are_exact() -> None:
    """D2 keeps its literal motif while batch 04 repairs its D4e ancestry."""
    record = _MT_SOURCE["nodes"]["D2"]
    topology = record["source_topology"]

    assert record["source_node"] == "D2"
    assert record["emitted_parent"] == "D4"
    assert record["source_motif_status"] == "exact"
    assert topology["status"] == "exact"
    assert topology["emitted_parent_source_node"] == "D4"
    assert topology["source_parent"] == "D4e1"
    assert [step["source_node"] for step in topology["flattened_source_path"]] == [
        "D4e",
        "D4e1'3",
        "D4e1",
    ]
    assert record["direct_source_motif"] == [
        {
            "notation": "C8703T",
            "mutation_type": "substitution",
            "pos": 8703,
            "ancestral_allele": "C",
            "derived_allele": "T",
            "emitted": True,
        },
        {
            "notation": "G16129A!",
            "mutation_type": "substitution",
            "pos": 16129,
            "ancestral_allele": "G",
            "derived_allele": "A",
            "emitted": True,
        },
    ]
    assert record["emitted_snps"] == [
        {
            "rsid": "i5008703",
            "pos": 8703,
            "ancestral_allele": "C",
            "allele": "T",
            "motif_owner": "D2",
            "array_coverage": {
                "cohort_id": "primary_four_23andme",
                "position_present_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
                "callable_snv_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
            },
        },
        {
            "rsid": "i5016129",
            "pos": 16129,
            "ancestral_allele": "G",
            "allele": "A",
            "motif_owner": "D2",
            "array_coverage": {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": ["pgp_4162", "pgp_4187", "pgp_huA08F4D"],
            },
        },
    ]

    migration = _MT_SOURCE["migration"]
    assert "D2" not in _MT_SOURCE["pending_nodes"]
    assert "D2" in _MT_SOURCE["direct_source_motif_states"]["exact_nodes"]
    assert "D2" in migration["locked_exact_nodes"]
    assert "D2" in migration["locked_direct_motif_exact_nodes"]
    assert "D2" not in migration["baseline_exact_nodes"]
    assert "D2" not in migration["baseline_direct_motif_exact_nodes"]
    assert "D2" in migration["initial_pending_nodes"]


def test_issue_1907_old_d2_pair_fails_the_marker_exact_tree_guard() -> None:
    """Restoring inherited m.4883 and R-lineage m.12705 cannot pass validation."""
    tree = build_mt_tree()
    d2 = _index_mt_tree(tree).by_name["D2"].node
    d2["defining_snps"] = [
        {"rsid": "i5004883", "pos": 4883, "allele": "T"},
        {"rsid": "i5012705", "pos": 12705, "allele": "C"},
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Marker-exact mtDNA node D2 has markers" in text


def test_issue_1899_n_spine_records_and_frontiers_are_exact() -> None:
    """N/N1/N1a retain literal motifs, ownership, topology, and measured coverage."""
    n = _MT_SOURCE["nodes"]["N"]
    assert n["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "L3",
        "source_parent": "L3",
        "flattened_source_path": [],
    }
    assert _motif_decision_projection(n["direct_source_motif"]) == [
        (
            "G8701A",
            False,
            "absent or non-callable in the primary four; retained as source provenance "
            "without enlarging the runtime marker denominator",
        ),
        ("C9540T", True, None),
        (
            "G10398A",
            False,
            "omitted because downstream N lineages model reversions to m.10398G; "
            "emitting m.10398A on N would conflict before descendant traversal",
        ),
        (
            "C10873T",
            False,
            "absent or non-callable in the primary four; retained as source provenance "
            "without enlarging the runtime marker denominator",
        ),
        (
            "A15301G!",
            False,
            "omitted because downstream B models the m.15301A reversion; emitting "
            "m.15301G on N would conflict before descendant traversal",
        ),
    ]
    assert [
        (marker["pos"], marker["motif_owner"], marker["array_coverage"])
        for marker in n["emitted_snps"]
    ] == [
        (
            9540,
            "N",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
    ]

    flattened_reason = (
        "Flattened in issue 1899: Build 17 places N1'5 between N and N1. Its "
        "primary-callable G1719A event is emitted on N1 with N1'5 ownership so "
        "downstream N1 lineages retain the source-backed state."
    )
    n1 = _MT_SOURCE["nodes"]["N1"]
    assert n1["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "N",
        "source_parent": "N1'5",
        "flattened_source_path": [
            {
                "source_node": "N1'5",
                "source_parent": "N",
                "reason": flattened_reason,
                "direct_source_motif": [
                    {
                        "notation": "G1719A",
                        "mutation_type": "substitution",
                        "pos": 1719,
                        "ancestral_allele": "G",
                        "derived_allele": "A",
                        "emitted": True,
                    }
                ],
            }
        ],
    }
    assert _MT_SOURCE["omitted_nodes"]["N1'5"] == {
        "type": "flattened_source_intermediate",
        "reason": flattened_reason,
    }
    assert _motif_decision_projection(n1["direct_source_motif"]) == [
        ("T10238C", True, None),
        ("G12501A", True, None),
    ]
    assert [
        (marker["pos"], marker["motif_owner"], marker["array_coverage"])
        for marker in n1["emitted_snps"]
    ] == [
        (
            position,
            owner,
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        )
        for position, owner in [(1719, "N1'5"), (10238, "N1"), (12501, "N1")]
    ]

    n1a = _MT_SOURCE["nodes"]["N1a"]
    assert n1a["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "N1",
        "source_parent": "N1",
        "flattened_source_path": [],
    }
    assert _motif_decision_projection(n1a["direct_source_motif"]) == [
        ("T204C", True, None),
        ("A13780G", True, None),
    ]
    assert [(marker["pos"], marker["array_coverage"]) for marker in n1a["emitted_snps"]] == [
        (
            204,
            {
                "cohort_id": "historical_five_23andme_including_2014",
                "position_present_in": ["pgp_1050"],
                "callable_snv_in": ["pgp_1050"],
            },
        ),
        (
            13780,
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
    ]

    names = {"N", "N1", "N1a"}
    migration = _MT_SOURCE["migration"]
    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])


def test_issue_1899_old_n_spine_marker_sets_fail_the_exact_tree_guard() -> None:
    """The legacy N1/N1a markers cannot re-enter the source-locked tree."""
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)
    inventory.by_name["N1"].node["defining_snps"] = [
        {"rsid": "i5006365", "pos": 6365, "allele": "C"},
        {"rsid": "i5010398", "pos": 10398, "allele": "G"},
    ]
    inventory.by_name["N1a"].node["defining_snps"] = [
        {"rsid": "i5000152", "pos": 152, "allele": "C"},
        {"rsid": "i5006365", "pos": 6365, "allele": "C"},
        {"rsid": "i5010398", "pos": 10398, "allele": "G"},
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, inventory))
    assert "Marker-exact mtDNA node N1 has markers" in text
    assert "Marker-exact mtDNA node N1a has markers" in text


def test_issue_1899_n1_flattened_motif_owner_is_fail_closed() -> None:
    """The inherited m.1719 marker cannot be relabeled as a direct N1 event."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["N1"]["emitted_snps"][0]["motif_owner"] = "N1"

    text = _issues_text(_validate_mt_source_schema(source))
    assert "does not match its source mutation direction" in text
    assert "emitted markers do not match every source emission decision" in text


def test_issue_1899_i_record_and_flattened_source_path_are_exact() -> None:
    """I retains its literal Build 17 path, sparse motif, ownership, and coverage."""
    record = _MT_SOURCE["nodes"]["I"]
    assert record["source_node"] == "I"
    assert record["emitted_parent"] == "N1a"
    assert record["source_motif_status"] == "exact"

    path = record["source_topology"]["flattened_source_path"]
    assert record["source_topology"]["status"] == "exact"
    assert record["source_topology"]["emitted_parent_source_node"] == "N1a"
    assert record["source_topology"]["source_parent"] == "N1a1b"
    assert [(step["source_node"], step["source_parent"]) for step in path] == [
        ("N1a1'2", "N1a"),
        ("N1a1", "N1a1'2"),
        ("N1a1b", "N1a1"),
    ]
    assert [_motif_decision_projection(step["direct_source_motif"]) for step in path] == [
        [
            (
                "T199C",
                False,
                "not part of the retained sparse I motif; emitting it would expand "
                "classifier behavior beyond this provenance migration",
            )
        ],
        [
            (
                "573.XC",
                False,
                "non-substitution event is unsupported by the substitution-only caller",
            ),
            (
                "A10398G!",
                False,
                "recurrent reversion is not part of the retained sparse I motif; m.15043 "
                "supplies the selected intermediate evidence without adding non-specific "
                "m.10398 evidence",
            ),
            ("G15043A", True, None),
        ],
        [
            (
                notation,
                False,
                "source-provenance only; not part of the retained sparse I motif",
            )
            for notation in ["T250C", "A4529t", "G8251A", "A15924G", "G16391A"]
        ],
    ]
    assert [_MT_SOURCE["omitted_nodes"][step["source_node"]] for step in path] == [
        {"type": "flattened_source_intermediate", "reason": step["reason"]} for step in path
    ]
    assert _motif_decision_projection(record["direct_source_motif"]) == [
        ("T10034C", True, None),
        ("G16129A!", True, None),
    ]
    assert [
        (
            marker["rsid"],
            marker["pos"],
            marker["ancestral_allele"],
            marker["allele"],
            marker["motif_owner"],
            marker["array_coverage"],
        )
        for marker in record["emitted_snps"]
    ] == [
        (
            "i5010034",
            10034,
            "T",
            "C",
            "I",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
        (
            "i5015043",
            15043,
            "G",
            "A",
            "N1a1",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
        (
            "i5016129",
            16129,
            "G",
            "A",
            "I",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": ["pgp_4162", "pgp_4187", "pgp_huA08F4D"],
            },
        ),
    ]

    migration = _MT_SOURCE["migration"]
    assert "I" not in _MT_SOURCE["pending_nodes"]
    assert "I" in _MT_SOURCE["direct_source_motif_states"]["exact_nodes"]
    assert "I" in migration["locked_exact_nodes"]
    assert "I" in migration["locked_direct_motif_exact_nodes"]
    assert "I" not in migration["baseline_exact_nodes"]
    assert "I" not in migration["baseline_direct_motif_exact_nodes"]
    assert "I" in migration["initial_pending_nodes"]


def test_issue_1899_old_i_marker_set_fails_the_exact_tree_guard() -> None:
    """The former duplicated m.1719 marker cannot re-enter I's source-locked node."""
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)
    inventory.by_name["I"].node["defining_snps"] = [
        {"rsid": "i5001719", "pos": 1719, "allele": "A"},
        {"rsid": "i5010034", "pos": 10034, "allele": "C"},
        {"rsid": "i5015043", "pos": 15043, "allele": "A"},
        {"rsid": "i5016129", "pos": 16129, "allele": "A"},
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, inventory))
    assert "Marker-exact mtDNA node I has markers" in text


def test_issue_1899_i_topology_and_flattened_owner_are_fail_closed() -> None:
    """I cannot bypass N1a or relabel N1a1's selected m.15043 event."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["I"]["source_topology"]["emitted_parent_source_node"] = "N"
    source["nodes"]["I"]["emitted_snps"][1]["motif_owner"] = "I"

    text = _issues_text(_validate_mt_source_schema(source))
    assert "Exact source topology for mtDNA node I breaks adjacency" in text
    assert "does not match its source mutation direction" in text
    assert "emitted markers do not match every source emission decision" in text


def test_issue_1814_s_child_records_and_frontiers_are_exact() -> None:
    """S1/S2 use direct Build 17 motifs with their observed cohort membership."""
    expected = {
        "S1": {
            "motif": [
                ("G14384c", 14384, "G", "C"),
                ("T16075C", 16075, "T", "C"),
            ],
            "markers": [
                (
                    "i5014384",
                    14384,
                    "G",
                    "C",
                    "historical_five_23andme_including_2014",
                    ["pgp_1050"],
                ),
                (
                    "i5016075",
                    16075,
                    "T",
                    "C",
                    "historical_five_23andme_including_2014",
                    ["pgp_1050"],
                ),
            ],
        },
        "S2": {
            "motif": [
                ("C2380T", 2380, "C", "T"),
                ("G3438A", 3438, "G", "A"),
                ("T6167C", 6167, "T", "C"),
            ],
            "markers": [
                ("i5002380", 2380, "C", "T", "primary_four_23andme", PRIMARY_EXPORTS),
                ("i5003438", 3438, "G", "A", "primary_four_23andme", PRIMARY_EXPORTS),
                ("i5006167", 6167, "T", "C", "primary_four_23andme", PRIMARY_EXPORTS),
            ],
        },
    }

    for name, expected_record in expected.items():
        record = _MT_SOURCE["nodes"][name]
        assert record["source_node"] == name
        assert record["emitted_parent"] == "S"
        assert record["source_motif_status"] == "exact"
        assert record["source_topology"] == {
            "status": "exact",
            "emitted_parent_source_node": "S",
            "source_parent": "S",
            "flattened_source_path": [],
        }
        assert [
            (
                mutation["notation"],
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
            )
            for mutation in record["direct_source_motif"]
        ] == expected_record["motif"]
        assert all(
            mutation["mutation_type"] == "substitution"
            for mutation in record["direct_source_motif"]
        )
        assert all(mutation["emitted"] is True for mutation in record["direct_source_motif"])
        assert [
            (
                marker["rsid"],
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["array_coverage"]["cohort_id"],
                marker["array_coverage"]["position_present_in"],
            )
            for marker in record["emitted_snps"]
        ] == expected_record["markers"]
        assert all(marker["motif_owner"] == name for marker in record["emitted_snps"])
        assert all(
            marker["array_coverage"]["callable_snv_in"]
            == marker["array_coverage"]["position_present_in"]
            for marker in record["emitted_snps"]
        )

    names = {"S1", "S2"}
    migration = _MT_SOURCE["migration"]
    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])


def test_issue_1849_h1_parent_and_h1a_records_are_exact() -> None:
    """H1 supplies the audited parent identity for H1a's exact source edge."""
    h1 = _MT_SOURCE["nodes"]["H1"]
    assert h1 == {
        "source_node": "H1",
        "emitted_parent": "H",
        "source_motif_status": "exact",
        "source_topology": {
            "status": "exact",
            "emitted_parent_source_node": "H",
            "source_parent": "H",
            "flattened_source_path": [],
        },
        "direct_source_motif": [
            {
                "notation": "G3010A",
                "mutation_type": "substitution",
                "pos": 3010,
                "ancestral_allele": "G",
                "derived_allele": "A",
                "emitted": True,
            }
        ],
        "emitted_snps": [
            {
                "rsid": "i5003010",
                "pos": 3010,
                "ancestral_allele": "G",
                "allele": "A",
                "motif_owner": "H1",
                "array_coverage": {
                    "cohort_id": "primary_four_23andme",
                    "position_present_in": PRIMARY_EXPORTS,
                    "callable_snv_in": PRIMARY_EXPORTS,
                },
            }
        ],
    }

    h1a = _MT_SOURCE["nodes"]["H1a"]
    assert h1a["source_node"] == "H1a"
    assert h1a["emitted_parent"] == "H1"
    assert h1a["source_motif_status"] == "exact"
    assert h1a["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "H1",
        "source_parent": "H1",
        "flattened_source_path": [],
    }
    assert [
        (
            mutation["notation"],
            mutation["pos"],
            mutation["ancestral_allele"],
            mutation["derived_allele"],
            mutation["emitted"],
        )
        for mutation in h1a["direct_source_motif"]
    ] == [
        ("A73G!", 73, "A", "G", True),
        ("A16162G", 16162, "A", "G", True),
    ]
    assert [
        (
            marker["rsid"],
            marker["pos"],
            marker["ancestral_allele"],
            marker["allele"],
            marker["motif_owner"],
            marker["array_coverage"],
        )
        for marker in h1a["emitted_snps"]
    ] == [
        (
            "i5000073",
            73,
            "A",
            "G",
            "H1a",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
                "callable_snv_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
            },
        ),
        (
            "i5016162",
            16162,
            "A",
            "G",
            "H1a",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
    ]

    names = {"H1", "H1a"}
    migration = _MT_SOURCE["migration"]
    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])


def test_issue_1834_w_records_and_flattened_w3_topology_are_exact() -> None:
    """W/W3 retain their Build 17 motifs while Batch 07 closes W's N2 path."""
    w = _MT_SOURCE["nodes"]["W"]
    assert w["source_node"] == "W"
    assert w["emitted_parent"] == "N"
    assert w["source_motif_status"] == "exact"
    assert w["source_topology"]["status"] == "exact"
    assert w["source_topology"]["emitted_parent_source_node"] == "N"
    assert w["source_topology"]["source_parent"] == "N2"
    assert [step["source_node"] for step in w["source_topology"]["flattened_source_path"]] == [
        "N2"
    ]
    assert [
        (
            mutation["notation"],
            mutation["pos"],
            mutation["ancestral_allele"],
            mutation["derived_allele"],
            mutation["emitted"],
        )
        for mutation in w["direct_source_motif"]
    ] == [
        ("T195C!", 195, "T", "C", False),
        ("T204C", 204, "T", "C", False),
        ("G207A", 207, "G", "A", True),
        ("T1243C", 1243, "T", "C", True),
        ("A3505G", 3505, "A", "G", True),
        ("G5460A", 5460, "G", "A", True),
        ("G8251A", 8251, "G", "A", True),
        ("G8994A", 8994, "G", "A", True),
        ("A11947G", 11947, "A", "G", True),
        ("G15884c", 15884, "G", "C", True),
        ("C16292T", 16292, "C", "T", True),
    ]
    assert w["direct_source_motif"][0]["omission_reason"] == (
        "Absent from all four modern and the historical 2014 export exemplars."
    )
    assert w["direct_source_motif"][1]["omission_reason"] == (
        "Absent from all four modern export exemplars and callable only in the "
        "historical 2014 export."
    )
    assert [
        (
            marker["rsid"],
            marker["pos"],
            marker["ancestral_allele"],
            marker["allele"],
            marker["array_coverage"]["position_present_in"],
            marker["array_coverage"]["callable_snv_in"],
        )
        for marker in w["emitted_snps"]
    ] == [
        (
            "i5000207",
            207,
            "G",
            "A",
            ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
            ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
        ),
        ("i5001243", 1243, "T", "C", PRIMARY_EXPORTS, PRIMARY_EXPORTS),
        ("i5003505", 3505, "A", "G", PRIMARY_EXPORTS, PRIMARY_EXPORTS),
        ("i5005460", 5460, "G", "A", PRIMARY_EXPORTS, PRIMARY_EXPORTS),
        ("i5008251", 8251, "G", "A", ["pgp_4139", "pgp_4187", "pgp_huA08F4D"], ["pgp_4187"]),
        ("i5008994", 8994, "G", "A", PRIMARY_EXPORTS, PRIMARY_EXPORTS),
        (
            "i5011947",
            11947,
            "A",
            "G",
            ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
            ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
        ),
        ("i5015884", 15884, "G", "C", PRIMARY_EXPORTS, PRIMARY_EXPORTS),
        ("i5016292", 16292, "C", "T", PRIMARY_EXPORTS, ["pgp_4162", "pgp_4187", "pgp_huA08F4D"]),
    ]
    assert all(marker["motif_owner"] == "W" for marker in w["emitted_snps"])
    assert all(
        marker["array_coverage"]["cohort_id"] == "primary_four_23andme"
        for marker in w["emitted_snps"]
    )

    w3 = _MT_SOURCE["nodes"]["W3"]
    assert w3["source_node"] == "W3"
    assert w3["emitted_parent"] == "W"
    assert w3["source_motif_status"] == "exact"
    assert w3["direct_source_motif"] == [
        {
            "notation": "T1406C",
            "mutation_type": "substitution",
            "pos": 1406,
            "ancestral_allele": "T",
            "derived_allele": "C",
            "emitted": True,
        }
    ]
    assert w3["emitted_snps"] == [
        {
            "rsid": "i5001406",
            "pos": 1406,
            "ancestral_allele": "T",
            "allele": "C",
            "motif_owner": "W3",
            "array_coverage": {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        }
    ]
    flattened_reason = (
        "Flattened mutation-only Build 17 intermediate between W and W3: its sole "
        "direct C194T event is absent from all four primary export exemplars and "
        "callable only in the historical 2014 export."
    )
    assert w3["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "W",
        "source_parent": "W+194",
        "flattened_source_path": [
            {
                "source_node": "W+194",
                "source_parent": "W",
                "reason": flattened_reason,
                "direct_source_motif": [
                    {
                        "notation": "C194T",
                        "mutation_type": "substitution",
                        "pos": 194,
                        "ancestral_allele": "C",
                        "derived_allele": "T",
                        "emitted": False,
                        "omission_reason": (
                            "Absent from all four primary export exemplars and "
                            "callable only in the historical 2014 export."
                        ),
                    }
                ],
            }
        ],
    }
    assert _MT_SOURCE["omitted_nodes"]["W+194"] == {
        "type": "flattened_source_intermediate",
        "reason": flattened_reason,
    }

    names = {"W", "W3"}
    migration = _MT_SOURCE["migration"]
    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])


def _motif_decision_projection(motif: list[dict[str, Any]]) -> list[tuple[str, bool, str | None]]:
    """Project a motif to its literal notation, emission, and omission contract."""
    return [
        (mutation["notation"], mutation["emitted"], mutation.get("omission_reason"))
        for mutation in motif
    ]


def _root_l0_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect every batch-01 occurrence of one flattened source identity."""
    return [
        (name, step)
        for name in ROOT_L0_NODES
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_01_root_l0_records_are_literal_and_coverage_locked() -> None:
    """Lock all 16 exact records to reviewed motifs, topology, and array coverage."""
    assert ROOT_L0_NODES
    assert set(ROOT_L0_DIRECT_MOTIFS) == set(ROOT_L0_NODES)
    assert set(ROOT_L0_TOPOLOGY) == set(ROOT_L0_NODES)
    assert set(ROOT_L0_EMITTED_MARKER_POSITIONS) == set(ROOT_L0_NODES)
    assert set(ROOT_L0_EMITTED_MARKER_SHA256) == set(ROOT_L0_NODES)

    tree_inventory = _index_mt_tree(build_mt_tree())
    for name in ROOT_L0_NODES:
        record = _MT_SOURCE["nodes"][name]
        emitted_parent, parent_source, source_parent, path = ROOT_L0_TOPOLOGY[name]

        assert record["source_node"] == name
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert record["source_topology"]["status"] == "exact"
        assert record["source_topology"]["emitted_parent_source_node"] == parent_source
        assert record["source_topology"]["source_parent"] == source_parent
        assert [
            step["source_node"] for step in record["source_topology"]["flattened_source_path"]
        ] == path
        assert (
            _motif_decision_projection(record["direct_source_motif"])
            == (ROOT_L0_DIRECT_MOTIFS[name])
        )

        markers = record["emitted_snps"]
        assert [marker["pos"] for marker in markers] == ROOT_L0_EMITTED_MARKER_POSITIONS[name]
        assert _canonical_sha256(markers) == ROOT_L0_EMITTED_MARKER_SHA256[name]
        expected_owners = (
            ["L0a'g", "L0a", "L0a", "L0a"] if name == "L0a" else [name] * len(markers)
        )
        assert [marker["motif_owner"] for marker in markers] == expected_owners
        assert all(
            marker["array_coverage"]["cohort_id"] == "primary_four_23andme" for marker in markers
        )
        assert all(marker["array_coverage"]["callable_snv_in"] for marker in markers)
        assert all(
            set(marker["array_coverage"]["callable_snv_in"])
            <= set(marker["array_coverage"]["position_present_in"])
            <= set(PRIMARY_EXPORTS)
            for marker in markers
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")} for marker in markers
        ] == tree_inventory.by_name[name].node["defining_snps"]

    assert _MT_SOURCE["nodes"]["L0a"]["emitted_snps"][0] == {
        "rsid": "i5011176",
        "pos": 11176,
        "ancestral_allele": "G",
        "allele": "A",
        "motif_owner": "L0a'g",
        "array_coverage": {
            "cohort_id": "primary_four_23andme",
            "position_present_in": PRIMARY_EXPORTS,
            "callable_snv_in": ["pgp_4139", "pgp_4162", "pgp_4187"],
        },
    }


def test_issue_1798_batch_01_flattened_identities_and_shared_bytes_are_exact() -> None:
    """Lock all 11 flattened identities and their repeated source bytes."""
    expected_occurrence_counts = {
        "L0a'b'f'g'k": 4,
        "L0a'b'f'g": 3,
        "L0a'b'g": 2,
        "L0a'g": 1,
        "L0a1'4": 1,
        "L0d1'2": 2,
        "L1'2'3'4'5'6": 6,
        "L2'3'4'5'6": 5,
        "L2'3'4'6": 4,
        "L3'4'6": 3,
        "L3'4": 2,
    }
    assert ROOT_L0_FLATTENED_STEPS
    assert set(ROOT_L0_FLATTENED_STEPS) == set(expected_occurrence_counts)

    for identity, expected in ROOT_L0_FLATTENED_STEPS.items():
        occurrences = _root_l0_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == expected_occurrence_counts[identity]
        assert _MT_SOURCE["omitted_nodes"][identity] == {
            "type": expected["type"],
            "reason": expected["reason"],
        }
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}

        for _, step in occurrences:
            assert step["source_node"] == identity
            assert step["source_parent"] == expected["source_parent"]
            assert step["reason"] == expected["reason"]
            assert _motif_decision_projection(step["direct_source_motif"]) == expected["motif"]

    serialized = json.dumps(_MT_SOURCE, sort_keys=True, separators=(",", ":"))
    assert "L2'3'4'6+" not in serialized
    assert [
        (mutation["notation"], mutation["emitted"])
        for expected in ROOT_L0_FLATTENED_STEPS.values()
        for mutation in (
            {"notation": notation, "emitted": emitted}
            for notation, emitted, _ in expected["motif"]
        )
        if mutation["emitted"]
    ] == [("G11176A", True)]


def test_issue_1798_batch_01_advances_only_live_frontiers() -> None:
    """Advance live locks without rewriting immutable migration baselines."""
    names = set(ROOT_L0_NODES)
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


@pytest.mark.parametrize("name", ROOT_L0_NODES)
def test_issue_1798_batch_01_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject every pre-audit marker set for a promoted batch-01 node."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in ROOT_L0_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", ROOT_L0_NODES)
def test_issue_1798_batch_01_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject an allele-direction mutation in every promoted direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert f"mtDNA substitution {name}:" in _issues_text(issues)
    assert "notation disagrees with its declared allele direction" in _issues_text(issues)


@pytest.mark.parametrize("name", ROOT_L0_NODES)
def test_issue_1798_batch_01_topology_mutations_fail_closed(name: str) -> None:
    """Reject a source-parent mutation in every promoted exact topology."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(ROOT_L0_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif"])
def test_issue_1798_batch_01_flattened_path_mutations_fail_closed(
    identity: str, mutation: str
) -> None:
    """Reject adjacency, reason, and motif drift for each flattened identity."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _root_l0_flattened_occurrences(source, identity)[0]
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    else:
        source_mutation = next(
            item for item in step["direct_source_motif"] if item["mutation_type"] == "substitution"
        )
        source_mutation["derived_allele"] = (
            "A" if source_mutation["derived_allele"] != "A" else "C"
        )
        expected = f"mtDNA substitution {identity}:"
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert expected in _issues_text(issues)


def _batch02_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect each byte-identical batch-02 occurrence of a flattened identity."""
    return [
        (name, step)
        for name in BATCH02_RECORD_SHA256
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_02_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 19 direct motifs, topology paths, marker decisions, and coverage."""
    assert set(BATCH02_RECORD_SHA256) == set(BATCH02_TOPOLOGY)
    inventory = _index_mt_tree(build_mt_tree())

    for name, (motif_sha256, marker_sha256) in BATCH02_RECORD_SHA256.items():
        record = _MT_SOURCE["nodes"][name]
        emitted_parent, parent_source, source_parent, flattened_path = BATCH02_TOPOLOGY[name]
        topology = record["source_topology"]

        assert record["source_node"] == name
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert _canonical_sha256(record["direct_source_motif"]) == motif_sha256
        assert _canonical_sha256(record["emitted_snps"]) == marker_sha256

        emitted_decisions = [
            mutation for mutation in record["direct_source_motif"] if mutation["emitted"] is True
        ]
        assert all(mutation["mutation_type"] == "substitution" for mutation in emitted_decisions)
        assert [
            (mutation["pos"], mutation["ancestral_allele"], mutation["derived_allele"])
            for mutation in emitted_decisions
        ] == [
            (marker["pos"], marker["ancestral_allele"], marker["allele"])
            for marker in record["emitted_snps"]
        ]
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )
        assert all(marker["motif_owner"] == name for marker in record["emitted_snps"])
        assert all(
            marker["array_coverage"]["cohort_id"] == "primary_four_23andme"
            and marker["array_coverage"]["callable_snv_in"]
            and set(marker["array_coverage"]["callable_snv_in"])
            <= set(marker["array_coverage"]["position_present_in"])
            <= set(PRIMARY_EXPORTS)
            for marker in record["emitted_snps"]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_02_flattened_identities_are_exact_and_source_only() -> None:
    """Lock all ten omitted helper identities and every repeated source byte."""
    for identity, (source_parent, motif, count, omission_type) in BATCH02_FLATTENED_STEPS.items():
        occurrences = _batch02_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == count
        assert _MT_SOURCE["omitted_nodes"][identity]["type"] == omission_type
        assert _MT_SOURCE["omitted_nodes"][identity]["reason"] == occurrences[0][1]["reason"]
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}
        for _, step in occurrences:
            assert step["source_parent"] == source_parent
            assert tuple(item["notation"] for item in step["direct_source_motif"]) == motif
            assert all(item["emitted"] is False for item in step["direct_source_motif"])
            assert all(item["omission_reason"] for item in step["direct_source_motif"])


def test_issue_1798_batch_02_advances_only_live_frontiers() -> None:
    """Promote the batch without changing any immutable migration baseline."""
    names = set(BATCH02_RECORD_SHA256)
    promoted_names = names - {"L2c"}
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names & set(migration["baseline_exact_nodes"]) == {"L2c"}
    assert names & set(migration["baseline_direct_motif_exact_nodes"]) == {"L2c"}
    assert promoted_names <= set(migration["initial_pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256


def test_issue_1798_batch_02_reversion_and_recurrence_policies_are_explicit() -> None:
    """Pin the L2 reversal safeguard without deleting valid recurrent L5a evidence."""
    l2_m146 = next(
        mutation
        for mutation in _MT_SOURCE["nodes"]["L2"]["direct_source_motif"]
        if mutation["pos"] == 146
    )
    assert l2_m146 == {
        "notation": "T146C!",
        "mutation_type": "substitution",
        "pos": 146,
        "ancestral_allele": "T",
        "derived_allele": "C",
        "emitted": False,
        "omission_reason": (
            "omitted because the downstream L2a2'3'4 source path reverses m.146; "
            "emitting the upstream L2 state would conflict before L2a2 traversal"
        ),
    }
    assert 146 not in {marker["pos"] for marker in _MT_SOURCE["nodes"]["L2"]["emitted_snps"]}
    l5a_m16362 = next(
        mutation
        for mutation in _MT_SOURCE["nodes"]["L5a"]["direct_source_motif"]
        if mutation["pos"] == 16362
    )
    assert l5a_m16362["notation"] == "T16362C"
    assert l5a_m16362["emitted"] is True
    assert any(marker["pos"] == 16362 for marker in _MT_SOURCE["nodes"]["L5a"]["emitted_snps"])


@pytest.mark.parametrize("name", list(BATCH02_OLD_MARKERS))
def test_issue_1798_batch_02_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject every replaced hand-curated marker set in the promoted batch."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH02_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", list(BATCH02_RECORD_SHA256))
def test_issue_1798_batch_02_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every batch-02 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert f"mtDNA substitution {name}:" in _issues_text(issues)
    assert "notation disagrees with its declared allele direction" in _issues_text(issues)


@pytest.mark.parametrize("name", list(BATCH02_RECORD_SHA256))
def test_issue_1798_batch_02_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every batch-02 node."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(BATCH02_FLATTENED_STEPS))
def test_issue_1798_batch_02_flattened_path_mutations_fail_closed(identity: str) -> None:
    """Reject a source adjacency change for each batch-02 flattened helper."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch02_flattened_occurrences(source, identity)[0]
    step["source_parent"] = "wrong-parent"
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert f"breaks adjacency at {identity}" in _issues_text(issues)


def _batch03_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect each byte-identical batch-03 occurrence of a flattened identity."""
    return [
        (name, step)
        for name in BATCH03_RECORD_SHA256
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_03_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 15 motifs, topology paths, marker decisions, ownership, and coverage."""
    assert BATCH03_RECORD_SHA256
    assert BATCH03_TOPOLOGY
    assert set(BATCH03_RECORD_SHA256) == set(BATCH03_TOPOLOGY)
    inventory = _index_mt_tree(build_mt_tree())

    for name, (motif_sha256, marker_sha256) in BATCH03_RECORD_SHA256.items():
        record = _MT_SOURCE["nodes"][name]
        emitted_parent, parent_source, source_parent, flattened_path = BATCH03_TOPOLOGY[name]
        topology = record["source_topology"]

        assert record["source_node"] == name
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert _canonical_sha256(record["direct_source_motif"]) == motif_sha256
        assert _canonical_sha256(record["emitted_snps"]) == marker_sha256

        owned_mutations = [
            (step["source_node"], mutation)
            for step in topology["flattened_source_path"]
            for mutation in step["direct_source_motif"]
        ] + [(name, mutation) for mutation in record["direct_source_motif"]]
        emitted_decisions = [
            (owner, mutation) for owner, mutation in owned_mutations if mutation["emitted"] is True
        ]
        assert all(
            mutation["mutation_type"] == "substitution" for _, mutation in emitted_decisions
        )
        assert sorted(
            (
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
                owner,
            )
            for owner, mutation in emitted_decisions
        ) == sorted(
            (
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["motif_owner"],
            )
            for marker in record["emitted_snps"]
        )
        assert all(
            mutation.get("omission_reason")
            for _, mutation in owned_mutations
            if mutation["emitted"] is False
        )
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_03_flattened_identities_are_exact_and_source_only() -> None:
    """Lock six omitted helper identities, repeated bytes, and emitted ownership decisions."""
    assert BATCH03_FLATTENED_STEPS
    for identity, (source_parent, motif, count, omission_type) in BATCH03_FLATTENED_STEPS.items():
        occurrences = _batch03_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == count
        assert _MT_SOURCE["omitted_nodes"][identity]["type"] == omission_type
        assert _MT_SOURCE["omitted_nodes"][identity]["reason"] == occurrences[0][1]["reason"]
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}
        for _, step in occurrences:
            assert step["source_parent"] == source_parent
            assert (
                tuple((item["notation"], item["emitted"]) for item in step["direct_source_motif"])
                == motif
            )
            assert all(
                item.get("omission_reason")
                for item in step["direct_source_motif"]
                if item["emitted"] is False
            )


def test_issue_1798_batch_03_advances_only_live_frontiers() -> None:
    """Promote 12 pending nodes and G's motif without rewriting immutable baselines."""
    names = set(BATCH03_RECORD_SHA256)
    marker_promotions = names - {"G", "M1", "M8"}
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names & set(migration["baseline_exact_nodes"]) == {"G", "M1", "M8"}
    assert names & set(migration["baseline_direct_motif_exact_nodes"]) == {"M1", "M8"}
    assert marker_promotions <= set(migration["initial_pending_nodes"])
    assert {"G"} <= set(migration["initial_direct_motif_pending_nodes"])
    assert marker_promotions.isdisjoint(migration["initial_direct_motif_pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )


@pytest.mark.parametrize("name", list(BATCH03_OLD_MARKERS))
def test_issue_1798_batch_03_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject all 13 changed pre-batch marker sets, including threshold-preserving subsets."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH03_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", list(BATCH03_RECORD_SHA256))
def test_issue_1798_batch_03_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every batch-03 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    text = _issues_text(issues)
    assert f"mtDNA substitution {name}:" in text
    assert "notation disagrees with its declared allele direction" in text


@pytest.mark.parametrize("name", list(BATCH03_RECORD_SHA256))
def test_issue_1798_batch_03_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every batch-03 node."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(BATCH03_FLATTENED_STEPS))
def test_issue_1798_batch_03_flattened_path_mutations_fail_closed(identity: str) -> None:
    """Reject a source adjacency change for each batch-03 flattened helper."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch03_flattened_occurrences(source, identity)[0]
    step["source_parent"] = "wrong-parent"
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert f"breaks adjacency at {identity}" in _issues_text(issues)


def _batch04_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect every batch-04 occurrence of a flattened D-subtree identity."""
    return [
        (name, step)
        for name in BATCH04_RECORD_SHA256
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_04_records_are_exact_covered_and_tree_locked() -> None:
    """Lock the seven D-subtree records, topology, marker ownership, and coverage."""
    assert set(BATCH04_RECORD_SHA256) == set(BATCH04_TOPOLOGY)
    inventory = _index_mt_tree(build_mt_tree())

    for name, (motif_sha256, marker_sha256) in BATCH04_RECORD_SHA256.items():
        record = _MT_SOURCE["nodes"][name]
        emitted_parent, parent_source, source_parent, flattened_path = BATCH04_TOPOLOGY[name]
        topology = record["source_topology"]

        assert record["source_node"] == name
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert _canonical_sha256(record["direct_source_motif"]) == motif_sha256
        assert _canonical_sha256(record["emitted_snps"]) == marker_sha256

        owned_mutations = [
            (step["source_node"], mutation)
            for step in topology["flattened_source_path"]
            for mutation in step["direct_source_motif"]
        ] + [(name, mutation) for mutation in record["direct_source_motif"]]
        emitted_decisions = [
            (owner, mutation) for owner, mutation in owned_mutations if mutation["emitted"] is True
        ]
        assert all(
            mutation["mutation_type"] == "substitution" for _, mutation in emitted_decisions
        )
        assert sorted(
            (
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
                owner,
            )
            for owner, mutation in emitted_decisions
        ) == sorted(
            (
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["motif_owner"],
            )
            for marker in record["emitted_snps"]
        )
        assert all(
            mutation.get("omission_reason")
            for _, mutation in owned_mutations
            if mutation["emitted"] is False
        )
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_04_flattened_prefixes_are_exact_and_source_only() -> None:
    """Lock all six D-prefix helpers and keep their non-specific events out of runtime."""
    observed_positions: dict[str, set[int]] = {}

    for identity, (source_parent, motif, count, omission_type) in BATCH04_FLATTENED_STEPS.items():
        occurrences = _batch04_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == count
        assert _MT_SOURCE["omitted_nodes"][identity]["type"] == omission_type
        assert _MT_SOURCE["omitted_nodes"][identity]["reason"] == occurrences[0][1]["reason"]
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}
        for owner, step in occurrences:
            assert step["source_parent"] == source_parent
            assert (
                tuple((item["notation"], item["emitted"]) for item in step["direct_source_motif"])
                == motif
            )
            assert all(item["emitted"] is False for item in step["direct_source_motif"])
            assert all(item["omission_reason"] for item in step["direct_source_motif"])
            observed_positions.setdefault(owner, set()).update(
                item["pos"] for item in step["direct_source_motif"]
            )

    assert observed_positions == BATCH04_FLATTENED_PREFIX_POSITIONS
    inventory = _index_mt_tree(build_mt_tree())
    for owner, prefix_positions in observed_positions.items():
        source_positions = {marker["pos"] for marker in _MT_SOURCE["nodes"][owner]["emitted_snps"]}
        tree_positions = {
            marker["pos"] for marker in inventory.by_name[owner].node["defining_snps"]
        }
        assert prefix_positions.isdisjoint(source_positions)
        assert prefix_positions.isdisjoint(tree_positions)


def test_issue_1798_batch_04_advances_only_live_frontiers() -> None:
    """Promote six D records and D2 topology without rewriting immutable baselines."""
    names = set(BATCH04_RECORD_SHA256)
    newly_promoted = names - {"D2"}
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["baseline_direct_motif_exact_nodes"])
    assert names <= set(migration["initial_pending_nodes"])
    assert names.isdisjoint(migration["initial_direct_motif_pending_nodes"])
    assert newly_promoted == {"D1", "D3", "D4", "D4a", "D4b", "D5"}
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256


@pytest.mark.parametrize("name", list(BATCH04_OLD_MARKERS))
def test_issue_1798_batch_04_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject all four replaced pre-batch D marker sets."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH04_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", list(BATCH04_RECORD_SHA256))
def test_issue_1798_batch_04_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every batch-04 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    text = _issues_text(issues)
    assert f"mtDNA substitution {name}:" in text
    assert "notation disagrees with its declared allele direction" in text


@pytest.mark.parametrize("name", list(BATCH04_RECORD_SHA256))
def test_issue_1798_batch_04_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every batch-04 node."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(BATCH04_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif"])
def test_issue_1798_batch_04_flattened_path_mutations_fail_closed(
    identity: str, mutation: str
) -> None:
    """Reject adjacency, omission-reason, and motif drift for each D-prefix helper."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch04_flattened_occurrences(source, identity)[0]
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    else:
        source_mutation = next(
            item for item in step["direct_source_motif"] if item["mutation_type"] == "substitution"
        )
        source_mutation["derived_allele"] = (
            "A" if source_mutation["derived_allele"] != "A" else "C"
        )
        expected = f"mtDNA substitution {identity}:"
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert expected in _issues_text(issues)


def _batch05_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect every Batch 05 occurrence of a flattened M-subtree identity."""
    return [
        (name, step)
        for name in BATCH05_NAMES
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_05_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 14 Batch 05 records plus changed M7 to reviewed source bytes."""
    assert set(BATCH05_RECORD_SHA256) == set(BATCH05_NAMES) == set(BATCH05_TOPOLOGY)
    inventory = _index_mt_tree(build_mt_tree())

    for name, (motif_sha256, marker_sha256) in BATCH05_RECORD_SHA256.items():
        record = _MT_SOURCE["nodes"][name]
        emitted_parent, parent_source, source_parent, flattened_path = BATCH05_TOPOLOGY[name]
        topology = record["source_topology"]

        assert record["source_node"] == name
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert _canonical_sha256(record["direct_source_motif"]) == motif_sha256
        assert _canonical_sha256(record["emitted_snps"]) == marker_sha256

        owned_mutations = [
            (step["source_node"], mutation)
            for step in topology["flattened_source_path"]
            for mutation in step["direct_source_motif"]
        ] + [(name, mutation) for mutation in record["direct_source_motif"]]
        emitted_decisions = [
            (owner, mutation) for owner, mutation in owned_mutations if mutation["emitted"] is True
        ]
        assert all(
            mutation["mutation_type"] == "substitution" for _, mutation in emitted_decisions
        )
        assert sorted(
            (
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
                owner,
            )
            for owner, mutation in emitted_decisions
        ) == sorted(
            (
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["motif_owner"],
            )
            for marker in record["emitted_snps"]
        )
        assert all(
            mutation.get("omission_reason")
            for _, mutation in owned_mutations
            if mutation["emitted"] is False
        )
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_05_flattened_owners_are_exact_and_reused_bytes_match() -> None:
    """Lock helper ownership while keeping all helpers except G2a'c source-only."""
    source_only_positions: dict[str, set[int]] = {}

    for identity, (source_parent, motif, count, omission_type) in BATCH05_FLATTENED_STEPS.items():
        occurrences = _batch05_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == count
        assert _MT_SOURCE["omitted_nodes"][identity]["type"] == omission_type
        assert _MT_SOURCE["omitted_nodes"][identity]["reason"] == occurrences[0][1]["reason"]
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}
        for owner, step in occurrences:
            assert step["source_parent"] == source_parent
            assert (
                tuple((item["notation"], item["emitted"]) for item in step["direct_source_motif"])
                == motif
            )
            if identity == "G2a'c":
                assert all(item["emitted"] is True for item in step["direct_source_motif"])
            else:
                assert all(item["emitted"] is False for item in step["direct_source_motif"])
                assert all(item["omission_reason"] for item in step["direct_source_motif"])
                source_only_positions.setdefault(owner, set()).update(
                    item["pos"] for item in step["direct_source_motif"]
                )

    assert source_only_positions == BATCH05_SOURCE_ONLY_PREFIX_POSITIONS
    inventory = _index_mt_tree(build_mt_tree())
    for owner, prefix_positions in source_only_positions.items():
        source_positions = {marker["pos"] for marker in _MT_SOURCE["nodes"][owner]["emitted_snps"]}
        tree_positions = {
            marker["pos"] for marker in inventory.by_name[owner].node["defining_snps"]
        }
        assert prefix_positions.isdisjoint(source_positions)
        assert prefix_positions.isdisjoint(tree_positions)
    source_only_helpers = {"CZ", "M7b'c", "Z+152"}
    assert not [
        (name, marker)
        for name, record in _MT_SOURCE["nodes"].items()
        for marker in record["emitted_snps"]
        if marker["motif_owner"] in source_only_helpers
    ]


def test_issue_1798_batch_05_g2ac_marker_is_emitted_once_with_source_ownership() -> None:
    """Keep callable shared G2a'c evidence specific to the G2a runtime record."""
    source_occurrences = [
        (owner, mutation)
        for owner, step in _batch05_flattened_occurrences(_MT_SOURCE, "G2a'c")
        for mutation in step["direct_source_motif"]
        if mutation["emitted"] is True
    ]
    assert [(owner, mutation["notation"]) for owner, mutation in source_occurrences] == [
        ("G2a", "G9575A")
    ]

    marker_occurrences = [
        (name, marker)
        for name, record in _MT_SOURCE["nodes"].items()
        for marker in record["emitted_snps"]
        if marker["motif_owner"] == "G2a'c"
    ]
    assert [
        (
            name,
            marker["rsid"],
            marker["pos"],
            marker["ancestral_allele"],
            marker["allele"],
        )
        for name, marker in marker_occurrences
    ] == [("G2a", "i5009575", 9575, "G", "A")]

    inventory = _index_mt_tree(build_mt_tree())
    tree_occurrences = [
        (name, marker)
        for name, occurrence in inventory.by_name.items()
        for marker in occurrence.node["defining_snps"]
        if marker["pos"] == 9575
    ]
    assert tree_occurrences == [("G2a", {"rsid": "i5009575", "pos": 9575, "allele": "A"})]


def test_issue_1798_batch_05_non_substitution_events_are_literal_and_source_only() -> None:
    """Preserve Build 17 deletion/insertion bytes without exposing unscoreable markers."""
    c1_deletion = next(
        mutation
        for mutation in _MT_SOURCE["nodes"]["C1"]["direct_source_motif"]
        if mutation["mutation_type"] == "deletion"
    )
    c5_insertion = next(
        mutation
        for mutation in _MT_SOURCE["nodes"]["C5"]["direct_source_motif"]
        if mutation["mutation_type"] == "insertion"
    )
    _, cz_step = _batch05_flattened_occurrences(_MT_SOURCE, "CZ")[0]
    cz_deletion = cz_step["direct_source_motif"][0]

    assert {
        key: c1_deletion[key]
        for key in ("notation", "mutation_type", "pos", "deleted_sequence", "emitted")
    } == {
        "notation": "290-291d",
        "mutation_type": "deletion",
        "pos": 290,
        "deleted_sequence": "AA",
        "emitted": False,
    }
    assert {
        key: c5_insertion[key]
        for key in ("notation", "mutation_type", "pos", "inserted_sequence", "emitted")
    } == {
        "notation": "595.1C",
        "mutation_type": "insertion",
        "pos": 595,
        "inserted_sequence": "C",
        "emitted": False,
    }
    assert {
        key: cz_deletion[key]
        for key in ("notation", "mutation_type", "pos", "deleted_sequence", "emitted")
    } == {
        "notation": "A249d",
        "mutation_type": "deletion",
        "pos": 249,
        "deleted_sequence": "A",
        "emitted": False,
    }
    assert all(
        mutation["omission_reason"] for mutation in (c1_deletion, c5_insertion, cz_deletion)
    )


def test_issue_1798_batch_05_advances_only_live_frontiers() -> None:
    """Promote eight records while preserving all immutable issue-level baselines."""
    names = set(BATCH05_NAMES)
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names & set(migration["baseline_exact_nodes"]) == {
        "C",
        "G1",
        "G2",
        "M8a",
        "Z",
        "Z1",
    }
    assert names & set(migration["baseline_direct_motif_exact_nodes"]) == {
        "C",
        "G1",
        "G2",
        "M8a",
        "Z",
        "Z1",
    }
    assert BATCH05_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH05_PROMOTIONS.isdisjoint(migration["baseline_exact_nodes"])
    assert names.isdisjoint(migration["initial_direct_motif_pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256


@pytest.mark.parametrize("name", list(BATCH05_OLD_MARKERS))
def test_issue_1798_batch_05_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject every changed pre-batch marker row, including strict old subsets."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH05_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", BATCH05_NAMES)
def test_issue_1798_batch_05_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every Batch 05 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    text = _issues_text(issues)
    assert f"mtDNA substitution {name}:" in text
    assert "notation disagrees with its declared allele direction" in text


@pytest.mark.parametrize(
    ("name", "mutation_type", "sequence_field"),
    [
        ("C1", "deletion", "deleted_sequence"),
        ("C5", "insertion", "inserted_sequence"),
    ],
)
def test_issue_1798_batch_05_non_substitution_sequence_drift_fails_closed(
    name: str, mutation_type: str, sequence_field: str
) -> None:
    """Reject semantically altered deletion and insertion sequences via live locks."""
    source = deepcopy(_MT_SOURCE)
    mutation = next(
        item
        for item in source["nodes"][name]["direct_source_motif"]
        if item["mutation_type"] == mutation_type
    )
    mutation[sequence_field] += "A"

    text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_exact_semantic_sha256 does not match its registry projection" in text


@pytest.mark.parametrize(
    ("name", "mutation_type"),
    [("C1", "deletion"), ("C5", "insertion")],
)
def test_issue_1798_batch_05_non_substitution_emission_fails_closed(
    name: str, mutation_type: str
) -> None:
    """Reject attempts to emit structural events through the substitution-only caller."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = next(
        item for item in record["direct_source_motif"] if item["mutation_type"] == mutation_type
    )
    mutation["emitted"] = True
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert (
        f"mtDNA {mutation_type} {name}:{mutation['pos']} cannot be emitted by the "
        "substitution-only classifier"
    ) in _issues_text(issues)


@pytest.mark.parametrize("name", BATCH05_NAMES)
def test_issue_1798_batch_05_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every Batch 05 record."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(BATCH05_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif"])
def test_issue_1798_batch_05_flattened_path_mutations_fail_closed(
    identity: str, mutation: str
) -> None:
    """Reject adjacency, reason, and event drift for all four Batch 05 helpers."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch05_flattened_occurrences(source, identity)[0]
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    else:
        source_mutation = step["direct_source_motif"][0]
        if source_mutation["mutation_type"] == "substitution":
            source_mutation["derived_allele"] = (
                "A" if source_mutation["derived_allele"] != "A" else "C"
            )
            expected = f"mtDNA substitution {identity}:"
        else:
            source_mutation["emitted"] = True
            expected = (
                f"mtDNA {source_mutation['mutation_type']} {identity}:{source_mutation['pos']} "
                "cannot be emitted by the substitution-only classifier"
            )
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert expected in _issues_text(issues)


def _batch06_flattened_step(source: dict[str, Any], identity: str) -> dict[str, Any]:
    """Return one of A2's two reviewed mutation-only Build 17 path steps."""
    return next(
        step
        for step in source["nodes"]["A2"]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    )


def test_issue_1798_batch_06_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 11 Batch 06 records to literal source, topology, and coverage bytes."""
    assert set(BATCH06_RECORD_SHA256) == set(BATCH06_NAMES)
    assert set(BATCH06_DIRECT_MOTIFS) == set(BATCH06_NAMES)
    assert set(BATCH06_TOPOLOGY) == set(BATCH06_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH06_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH06_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH06_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH06_DIRECT_MOTIFS[name]
        )

        owned_mutations = [
            (step["source_node"], mutation)
            for step in topology["flattened_source_path"]
            for mutation in step["direct_source_motif"]
        ] + [(source_node, mutation) for mutation in record["direct_source_motif"]]
        assert sorted(
            (
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
                owner,
            )
            for owner, mutation in owned_mutations
            if mutation["emitted"] is True
        ) == sorted(
            (
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["motif_owner"],
            )
            for marker in record["emitted_snps"]
        )
        assert all(
            mutation.get("omission_reason")
            for _, mutation in owned_mutations
            if mutation["emitted"] is False
        )
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_06_a2_mutation_only_path_and_helper_ownership_are_exact() -> None:
    """Keep historical m.152 source-only and own emitted m.16362 by its exact helper."""
    record = _MT_SOURCE["nodes"]["A2"]
    assert len(record["source_topology"]["flattened_source_path"]) == 2

    for identity, (source_parent, motif, omission_type) in BATCH06_FLATTENED_STEPS.items():
        step = _batch06_flattened_step(_MT_SOURCE, identity)
        assert step["source_parent"] == source_parent
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in step["direct_source_motif"]
            )
            == motif
        )
        assert _MT_SOURCE["omitted_nodes"][identity] == {
            "type": omission_type,
            "reason": step["reason"],
        }

    assert not _batch06_flattened_step(_MT_SOURCE, "A+152")["direct_source_motif"][0]["emitted"]
    helper = _batch06_flattened_step(_MT_SOURCE, "A+152+16362")["direct_source_motif"][0]
    assert helper["notation"] == "T16362C"
    assert helper["emitted"] is True
    helper_markers = [
        marker for marker in record["emitted_snps"] if marker["motif_owner"] == "A+152+16362"
    ]
    assert helper_markers == [
        {
            "rsid": "i5016362",
            "pos": 16362,
            "ancestral_allele": "T",
            "allele": "C",
            "motif_owner": "A+152+16362",
            "array_coverage": {
                "cohort_id": "primary_four_23andme",
                "position_present_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
                "callable_snv_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
            },
        }
    ]
    assert len(helper_markers) / len(record["emitted_snps"]) == pytest.approx(0.2)


def test_issue_1798_batch_06_preserves_reviewed_n1_n1a_i_provenance_bytes() -> None:
    """Sibling work cannot rewrite prior N1/N1a/I motifs, helpers, or topology records."""
    assert {
        name: _canonical_sha256(
            {
                key: _MT_SOURCE["nodes"][name][key]
                for key in ("source_topology", "direct_source_motif", "emitted_snps")
            }
        )
        for name in BATCH06_PRESERVED_PROVENANCE_SHA256
    } == BATCH06_PRESERVED_PROVENANCE_SHA256


def test_issue_1798_batch_06_y_alias_and_n9_gateway_are_exact() -> None:
    """Map runtime Y_mt to source Y beneath N9 and keep both Y children beneath the alias."""
    inventory = _index_mt_tree(build_mt_tree())
    y = _MT_SOURCE["nodes"]["Y_mt"]

    assert y["source_node"] == "Y"
    assert y["emitted_parent"] == "N9"
    assert y["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "N9",
        "source_parent": "N9",
        "flattened_source_path": [],
    }
    assert inventory.by_name["Y_mt"].parent == "N9"
    assert [inventory.by_name[name].parent for name in ("Y1", "Y2")] == ["Y_mt", "Y_mt"]
    for name in ("Y1", "Y2"):
        assert _MT_SOURCE["nodes"][name]["source_topology"] == {
            "status": "exact",
            "emitted_parent_source_node": "Y",
            "source_parent": "Y",
            "flattened_source_path": [],
        }
    assert [(marker["pos"], marker["motif_owner"]) for marker in y["emitted_snps"]] == [
        (8392, "Y"),
        (10398, "Y"),
        (14178, "Y"),
        (14693, "Y"),
        (16126, "Y"),
        (16223, "Y"),
        (16231, "Y"),
    ]
    assert [
        (marker["pos"], marker["motif_owner"])
        for marker in _MT_SOURCE["nodes"]["Y1"]["emitted_snps"]
    ] == [(3834, "Y1")]


def test_issue_1798_batch_06_advances_only_live_frontiers() -> None:
    """Promote six pending nodes, close three legacy motifs, and retire only A4."""
    names = set(BATCH06_NAMES)
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert names & set(migration["baseline_exact_nodes"]) == {"N9", "Y1", "Y2", "Y_mt"}
    assert names & set(migration["baseline_direct_motif_exact_nodes"]) == {"N9"}
    assert BATCH06_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH06_PROMOTIONS.isdisjoint(migration["baseline_exact_nodes"])
    assert {"Y1", "Y2", "Y_mt"} <= set(migration["initial_direct_motif_pending_nodes"])
    assert BATCH06_DIRECT_MOTIF_PROMOTIONS.isdisjoint(
        _MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"]
    )
    assert "A4" not in _MT_SOURCE["nodes"]
    assert "A4" not in _MT_SOURCE["pending_nodes"]
    assert set(_MT_SOURCE["retired_emitted_nodes"]) == {"A4"}
    assert "A4" in migration["initial_pending_nodes"]
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


@pytest.mark.parametrize("name", list(BATCH06_OLD_MARKERS))
def test_issue_1798_batch_06_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject every replaced Batch 06 marker row, including N's historical denominator."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH06_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize("name", BATCH06_NAMES)
def test_issue_1798_batch_06_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every Batch 06 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = record["direct_source_motif"][0]
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    text = _issues_text(issues)
    assert f"mtDNA substitution {record['source_node']}:" in text
    assert "notation disagrees with its declared allele direction" in text


@pytest.mark.parametrize("name", BATCH06_NAMES)
def test_issue_1798_batch_06_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every Batch 06 record."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize(
    "name",
    ["N", "A", "A2", "N1b", "N9a", "N9b", "Y1", "Y2"],
)
def test_issue_1798_batch_06_omission_reason_drift_fails_live_lock(name: str) -> None:
    """Reject nonblank policy drift in every Batch 06 record with an omitted event."""
    source = deepcopy(_MT_SOURCE)
    mutation = next(
        item for item in source["nodes"][name]["direct_source_motif"] if not item["emitted"]
    )
    mutation["omission_reason"] += " Test-only drift."

    text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_exact_semantic_sha256 does not match its registry projection" in text
    assert "locked_direct_motif_semantic_sha256 does not match its registry projection" in text


@pytest.mark.parametrize("identity", list(BATCH06_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif"])
def test_issue_1798_batch_06_a2_helper_drift_fails_closed(identity: str, mutation: str) -> None:
    """Reject adjacency, reason, and motif drift in both mutation-only A2 helpers."""
    source = deepcopy(_MT_SOURCE)
    step = _batch06_flattened_step(source, identity)
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    else:
        source_mutation = step["direct_source_motif"][0]
        source_mutation["derived_allele"] = (
            "A" if source_mutation["derived_allele"] != "A" else "C"
        )
        expected = f"mtDNA substitution {identity}:"
    issues: list[str] = []

    _mt_validate_exact_record(
        "A2",
        source["nodes"]["A2"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert expected in _issues_text(issues)


def test_issue_1798_batch_06_a2_helper_owner_drift_fails_closed() -> None:
    """The flattened m.16362 event cannot be relabeled as a direct A2 mutation."""
    source = deepcopy(_MT_SOURCE)
    marker = next(
        marker for marker in source["nodes"]["A2"]["emitted_snps"] if marker["pos"] == 16362
    )
    marker["motif_owner"] = "A2"
    issues: list[str] = []

    _mt_validate_exact_record(
        "A2",
        source["nodes"]["A2"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert "emitted markers do not match every source emission decision" in _issues_text(issues)


def _batch07_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, dict[str, Any]]]:
    """Collect every Batch 07 occurrence of one flattened source identity."""
    return [
        (name, step)
        for name in BATCH07_NAMES
        for step in source["nodes"][name]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]


def test_issue_1798_batch_07_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 11 S/W/X records to reviewed motif, topology, and coverage bytes."""
    assert set(BATCH07_RECORD_SHA256) == set(BATCH07_NAMES)
    assert set(BATCH07_DIRECT_MOTIFS) == set(BATCH07_NAMES)
    assert set(BATCH07_TOPOLOGY) == set(BATCH07_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH07_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH07_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH07_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH07_DIRECT_MOTIFS[name]
        )

        owned_mutations = [
            (step["source_node"], mutation)
            for step in topology["flattened_source_path"]
            for mutation in step["direct_source_motif"]
        ] + [(source_node, mutation) for mutation in record["direct_source_motif"]]
        assert sorted(
            (
                mutation["pos"],
                mutation["ancestral_allele"],
                mutation["derived_allele"],
                owner,
            )
            for owner, mutation in owned_mutations
            if mutation["emitted"] is True
        ) == sorted(
            (
                marker["pos"],
                marker["ancestral_allele"],
                marker["allele"],
                marker["motif_owner"],
            )
            for marker in record["emitted_snps"]
        )
        assert all(
            mutation.get("omission_reason")
            for _, mutation in owned_mutations
            if mutation["emitted"] is False
        )
        assert all(marker["motif_owner"] == source_node for marker in record["emitted_snps"])
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]


def test_issue_1798_batch_07_flattened_events_are_exact_shared_and_source_only() -> None:
    """Lock all seven omitted intermediates and prohibit helper-only caller evidence."""
    flattened_owners = set(BATCH07_FLATTENED_STEPS)
    for identity, (source_parent, motif, expected_count) in BATCH07_FLATTENED_STEPS.items():
        occurrences = _batch07_flattened_occurrences(_MT_SOURCE, identity)
        assert len(occurrences) == expected_count
        assert _MT_SOURCE["omitted_nodes"][identity] == {
            "type": "flattened_source_intermediate",
            "reason": occurrences[0][1]["reason"],
        }
        assert {
            json.dumps(step, sort_keys=True, separators=(",", ":")) for _, step in occurrences
        } == {json.dumps(occurrences[0][1], sort_keys=True, separators=(",", ":"))}
        for _, step in occurrences:
            assert step["source_parent"] == source_parent
            assert (
                tuple(
                    (mutation["notation"], mutation["emitted"])
                    for mutation in step["direct_source_motif"]
                )
                == motif
            )
            assert all(mutation["emitted"] is False for mutation in step["direct_source_motif"])
            assert all(mutation["omission_reason"] for mutation in step["direct_source_motif"])

    assert {
        marker["motif_owner"]
        for name in BATCH07_NAMES
        for marker in _MT_SOURCE["nodes"][name]["emitted_snps"]
    }.isdisjoint(flattened_owners)


def test_issue_1798_batch_07_x_promotions_and_corrected_directions_are_exact() -> None:
    """Replace X1's old marker and normalize both legacy-partial X2 motifs."""
    assert [
        (marker["pos"], marker["allele"], marker["motif_owner"])
        for marker in _MT_SOURCE["nodes"]["X1"]["emitted_snps"]
    ] == [(5302, "C", "X1"), (15654, "C", "X1"), (16104, "T", "X1")]
    assert 6253 not in {marker["pos"] for marker in _MT_SOURCE["nodes"]["X1"]["emitted_snps"]}
    assert [
        (
            mutation["notation"],
            mutation["ancestral_allele"],
            mutation["derived_allele"],
        )
        for mutation in _MT_SOURCE["nodes"]["X2"]["direct_source_motif"]
    ] == [("T195C!", "T", "C"), ("G1719A", "G", "A")]
    assert [
        (
            mutation["notation"],
            mutation["ancestral_allele"],
            mutation["derived_allele"],
        )
        for mutation in _MT_SOURCE["nodes"]["X2a"]["direct_source_motif"]
    ] == [
        ("A200G", "A", "G"),
        ("A8913G", "A", "G"),
        ("T14502C", "T", "C"),
        ("G16213A", "G", "A"),
    ]
    assert [marker["pos"] for marker in _MT_SOURCE["nodes"]["X2b"]["emitted_snps"]] == [8393]
    assert 13708 not in {marker["pos"] for marker in _MT_SOURCE["nodes"]["X2b"]["emitted_snps"]}


def test_issue_1798_batch_07_advances_only_live_frontiers() -> None:
    """Promote only X1's marker state and the three reviewed X direct motifs."""
    names = set(BATCH07_NAMES)
    migration = _MT_SOURCE["migration"]

    assert names.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert names <= set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    assert names <= set(migration["locked_exact_nodes"])
    assert names <= set(migration["locked_direct_motif_exact_nodes"])
    assert BATCH07_MARKER_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH07_MARKER_PROMOTIONS.isdisjoint(migration["baseline_exact_nodes"])
    assert {"X2", "X2a"} <= set(migration["baseline_exact_nodes"])
    assert {"X2", "X2a"} <= set(migration["initial_direct_motif_pending_nodes"])
    assert BATCH07_DIRECT_MOTIF_PROMOTIONS.isdisjoint(
        _MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"]
    )
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_07_old_x1_marker_cannot_be_restored() -> None:
    """Reject X1's unsupported pre-audit m.6253 marker."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name["X1"].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH07_OLD_MARKERS["X1"]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Marker-exact mtDNA node X1 has markers" in text


@pytest.mark.parametrize(
    ("name", "mutations"),
    [
        ("X2", ((0, "T195C", "T", "C"),)),
        ("X2a", ((0, "T200C", "T", "C"), (3, "T16213C", "T", "C"))),
    ],
)
def test_issue_1798_batch_07_legacy_x_directions_cannot_be_restored(
    name: str, mutations: tuple[tuple[int, str, str, str], ...]
) -> None:
    """Reject the internally consistent but source-incorrect legacy X2 rows."""
    source = deepcopy(_MT_SOURCE)
    for index, notation, ancestral, derived in mutations:
        mutation = source["nodes"][name]["direct_source_motif"][index]
        mutation["notation"] = notation
        mutation["ancestral_allele"] = ancestral
        mutation["derived_allele"] = derived

    text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_exact_semantic_sha256 does not match its registry projection" in text
    assert "locked_direct_motif_semantic_sha256 does not match its registry projection" in text


@pytest.mark.parametrize("name", BATCH07_NAMES)
def test_issue_1798_batch_07_direct_motif_mutations_fail_closed(name: str) -> None:
    """Reject one wrong source allele direction in every Batch 07 direct motif."""
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"][name]
    mutation = record["direct_source_motif"][0]
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    issues: list[str] = []

    _mt_validate_exact_record(
        name,
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    text = _issues_text(issues)
    assert f"mtDNA substitution {record['source_node']}:" in text
    assert "notation disagrees with its declared allele direction" in text


@pytest.mark.parametrize("name", BATCH07_NAMES)
def test_issue_1798_batch_07_topology_mutations_fail_closed(name: str) -> None:
    """Reject a wrong emitted-parent source declaration for every Batch 07 record."""
    source = deepcopy(_MT_SOURCE)
    source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = "wrong-parent"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in text


@pytest.mark.parametrize("identity", list(BATCH07_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif"])
def test_issue_1798_batch_07_flattened_path_mutations_fail_closed(
    identity: str, mutation: str
) -> None:
    """Reject adjacency, reason, and motif drift in every Batch 07 helper."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch07_flattened_occurrences(source, identity)[0]
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    else:
        source_mutation = step["direct_source_motif"][0]
        source_mutation["derived_allele"] = (
            "A" if source_mutation["derived_allele"] != "A" else "C"
        )
        expected = f"mtDNA substitution {identity}:"
    issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert expected in _issues_text(issues)


@pytest.mark.parametrize("identity", ["X1'2'3", "X2+225"])
def test_issue_1798_batch_07_shared_helper_bytes_cannot_diverge(identity: str) -> None:
    """Both emitted paths must retain identical shared-prefix provenance bytes."""
    source = deepcopy(_MT_SOURCE)
    occurrences = _batch07_flattened_occurrences(source, identity)
    assert len(occurrences) == 2
    occurrences[1][1]["direct_source_motif"][0]["omission_reason"] += " Test-only drift."

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Flattened mtDNA source node {identity} has inconsistent provenance" in text


@pytest.mark.parametrize("identity", list(BATCH07_FLATTENED_STEPS))
def test_issue_1798_batch_07_source_only_helper_emission_fails_live_lock(identity: str) -> None:
    """No Batch 07 flattened event can silently become runtime evidence."""
    source = deepcopy(_MT_SOURCE)
    owner, step = _batch07_flattened_occurrences(source, identity)[0]
    step["direct_source_motif"][0]["emitted"] = True
    step["direct_source_motif"][0].pop("omission_reason")
    local_issues: list[str] = []

    _mt_validate_exact_record(
        owner,
        source["nodes"][owner],
        source["omitted_nodes"],
        source["array_cohorts"],
        local_issues,
    )

    assert "emitted markers do not match every source emission decision" in _issues_text(
        local_issues
    )
    assert "locked_exact_semantic_sha256 does not match its registry projection" in _issues_text(
        _validate_mt_source_schema(source)
    )


def _batch08_flattened_step(source: dict[str, Any], identity: str) -> dict[str, Any]:
    """Return the sole reviewed occurrence of one Batch 08 flattened identity."""
    owner = BATCH08_FLATTENED_STEPS[identity][3]
    category = "structural_exceptions" if owner == "H5" else "nodes"
    matches = [
        step
        for step in source[category][owner]["source_topology"]["flattened_source_path"]
        if step["source_node"] == identity
    ]
    assert len(matches) == 1
    return matches[0]


def test_issue_1798_batch_08_marker_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 11 regular R/H records to reviewed source, topology, and coverage."""
    assert BATCH08_NAMES
    assert set(BATCH08_RECORD_SHA256) == set(BATCH08_NAMES)
    assert set(BATCH08_DIRECT_MOTIFS) == set(BATCH08_NAMES)
    assert set(BATCH08_TOPOLOGY) == set(BATCH08_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH08_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH08_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH08_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH08_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert record["emitted_snps"]
        for marker in record["emitted_snps"]:
            assert marker["motif_owner"] == name
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )


def test_issue_1798_batch_08_structural_records_are_exact_and_markerless() -> None:
    """R0, HV, and H5 retain complete exact source records without caller markers."""
    assert BATCH08_STRUCTURAL_MOTIFS
    assert set(BATCH08_STRUCTURAL_SHA256) == set(BATCH08_STRUCTURAL_MOTIFS)
    assert set(BATCH08_STRUCTURAL_TOPOLOGY) == set(BATCH08_STRUCTURAL_MOTIFS)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH08_STRUCTURAL_MOTIFS:
        record = _MT_SOURCE["structural_exceptions"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH08_STRUCTURAL_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH08_STRUCTURAL_SHA256[name]
        assert record["type"] == "markerless_passthrough"
        assert record["source_status"] == "exact"
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["emitted_snps"] == []
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH08_STRUCTURAL_MOTIFS[name]
        )
        assert all(
            mutation["emitted"] is False and mutation["omission_reason"]
            for mutation in record["direct_source_motif"]
        )
        assert inventory.by_name[name].parent == emitted_parent
        assert inventory.by_name[name].node["defining_snps"] == []


def test_issue_1798_batch_08_flattened_helpers_are_exact_and_source_only() -> None:
    """Lock H5'36 and H+195 type, adjacency, reason, and literal event bytes."""
    assert BATCH08_FLATTENED_STEPS
    assert set(BATCH08_OMITTED_SHA256) == set(BATCH08_FLATTENED_STEPS)
    for identity, (source_parent, omission_type, motif, _owner) in BATCH08_FLATTENED_STEPS.items():
        step = _batch08_flattened_step(_MT_SOURCE, identity)
        omitted = _MT_SOURCE["omitted_nodes"][identity]

        assert _canonical_sha256(omitted) == BATCH08_OMITTED_SHA256[identity]
        assert omitted == {"type": omission_type, "reason": step["reason"]}
        assert step["source_parent"] == source_parent
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in step["direct_source_motif"]
            )
            == motif
        )
        assert all(
            mutation["emitted"] is False and mutation["omission_reason"]
            for mutation in step["direct_source_motif"]
        )


def test_issue_1798_batch_08_literal_notation_and_h11_path_are_preserved() -> None:
    """Parentheses, lowercase transversion, reversions, and H11 ancestry stay literal."""
    assert [
        mutation["notation"] for mutation in _MT_SOURCE["nodes"]["H6"]["direct_source_motif"]
    ] == ["T239C", "T16362C", "(A16482G)"]
    assert [
        mutation["notation"] for mutation in _MT_SOURCE["nodes"]["H10"]["direct_source_motif"]
    ] == ["T14470a"]
    assert [
        mutation["notation"] for mutation in _MT_SOURCE["nodes"]["H11"]["direct_source_motif"]
    ] == ["T8448C", "G13759A", "T16311C!"]
    assert [
        mutation["notation"]
        for mutation in _batch08_flattened_step(_MT_SOURCE, "H+195")["direct_source_motif"]
    ] == ["T195C!"]
    assert _MT_SOURCE["nodes"]["H11"]["emitted_parent"] == "H"
    assert _MT_SOURCE["nodes"]["H11"]["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "H",
        "source_parent": "H+195",
        "flattened_source_path": [_batch08_flattened_step(_MT_SOURCE, "H+195")],
    }


def test_issue_1798_batch_08_advances_only_reviewed_live_frontiers() -> None:
    """Promote seven marker records, eight direct motifs, and three structural records."""
    migration = _MT_SOURCE["migration"]
    exact = set(_MT_SOURCE["nodes"])
    direct = set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"])

    assert BATCH08_MARKER_PROMOTIONS <= exact
    assert BATCH08_MARKER_PROMOTIONS.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert BATCH08_MARKER_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH08_MARKER_PROMOTIONS.isdisjoint(migration["baseline_exact_nodes"])
    assert BATCH08_DIRECT_MOTIF_PROMOTIONS <= direct
    assert BATCH08_DIRECT_MOTIF_PROMOTIONS.isdisjoint(legacy)
    assert {"R0", "HV", "H5"} <= set(_MT_SOURCE["structural_exceptions"])
    assert "H5a" in migration["initial_pending_nodes"]
    assert "H5a" in exact
    assert "H5a" not in _MT_SOURCE["pending_nodes"]
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


@pytest.mark.parametrize("name", BATCH08_NAMES)
def test_issue_1798_batch_08_regular_source_mutations_fail_closed(name: str) -> None:
    """Reject one wrong literal direction and topology edge in every regular record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = motif_source["nodes"][name]["direct_source_motif"][0]
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )


@pytest.mark.parametrize("name", list(BATCH08_STRUCTURAL_MOTIFS))
def test_issue_1798_batch_08_structural_source_mutations_fail_closed(name: str) -> None:
    """Structural motifs cannot become caller evidence or drift from their exact edge."""
    emitted_source = deepcopy(_MT_SOURCE)
    mutation = emitted_source["structural_exceptions"][name]["direct_source_motif"][0]
    mutation["emitted"] = True
    mutation.pop("omission_reason")
    emitted_text = _issues_text(
        haplogroup_builder._validate_mt_structural_records(
            emitted_source,
            _index_mt_tree(build_mt_tree()),
        )
    )
    assert f"Markerless structural mtDNA node {name} has an emitted source decision" in (
        emitted_text
    )
    schema_text = _issues_text(_validate_mt_source_schema(emitted_source))
    assert "state_partition_sha256 does not match its registry projection" in schema_text

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["structural_exceptions"][name]["source_topology"][
        "emitted_parent_source_node"
    ] = "wrong-parent"
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )


@pytest.mark.parametrize("identity", list(BATCH08_FLATTENED_STEPS))
@pytest.mark.parametrize("mutation", ["path", "reason", "motif", "emission"])
def test_issue_1798_batch_08_flattened_mutations_fail_closed(identity: str, mutation: str) -> None:
    """No Batch 08 helper can drift in adjacency, reason, direction, or emission state."""
    source = deepcopy(_MT_SOURCE)
    step = _batch08_flattened_step(source, identity)
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = f"breaks adjacency at {identity}"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = f"source path node {identity} disagrees with its omission reason"
    elif mutation == "motif":
        step["direct_source_motif"][0]["derived_allele"] = "A"
        expected = f"mtDNA substitution {identity}:"
    else:
        step["direct_source_motif"][0]["emitted"] = True
        step["direct_source_motif"][0].pop("omission_reason")
        expected = (
            "emitted source decision"
            if BATCH08_FLATTENED_STEPS[identity][3] == "H5"
            else "emitted markers do not match every source emission decision"
        )

    owner = BATCH08_FLATTENED_STEPS[identity][3]
    if owner == "H5":
        detailed_issues = haplogroup_builder._validate_mt_structural_records(
            source,
            _index_mt_tree(build_mt_tree()),
        )
        lock_expected = "state_partition_sha256 does not match its registry projection"
    else:
        detailed_issues = []
        _mt_validate_exact_record(
            owner,
            source["nodes"][owner],
            source["omitted_nodes"],
            source["array_cohorts"],
            detailed_issues,
        )
        lock_expected = "locked_exact_semantic_sha256 does not match its registry projection"
    text = _issues_text(detailed_issues)
    assert expected in text
    assert lock_expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize("name", list(BATCH08_OLD_MARKERS))
def test_issue_1798_batch_08_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Reject each changed pre-audit marker set in the promoted batch."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH08_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert f"Marker-exact mtDNA node {name} has markers" in text


@pytest.mark.parametrize(
    ("name", "markers"),
    [
        ("HV", [("i5014766", 14766, "C")]),
        ("H5", [("i5000456", 456, "T"), ("i5016304", 16304, "C")]),
        ("H5a", [("i5004336", 4336, "C"), ("i5016304", 16304, "C")]),
        ("H11", [("i5008448", 8448, "C"), ("i5013101", 13101, "A")]),
    ],
)
def test_issue_1798_batch_08_removed_runtime_markers_fail_tree_lock(
    name: str, markers: list[tuple[str, int, str]]
) -> None:
    """Historical, helper, duplicated, and unsupported legacy rows cannot return."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": rsid, "pos": pos, "allele": allele} for rsid, pos, allele in markers
    ]

    text = _issues_text(_validate_mt_source(_MT_SOURCE, tree))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text


def _batch09_flattened_step(source: dict[str, Any]) -> dict[str, Any]:
    """Return the reviewed H1+16189 occurrence on H1b's source path."""
    matches = [
        step
        for step in source["nodes"]["H1b"]["source_topology"]["flattened_source_path"]
        if step["source_node"] == "H1+16189"
    ]
    assert len(matches) == 1
    return matches[0]


def test_issue_1798_batch_09_regular_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all 12 regular H-descendant records to source, topology, and coverage."""
    assert BATCH09_REGULAR_NAMES
    assert set(BATCH09_RECORD_SHA256) == set(BATCH09_REGULAR_NAMES)
    assert set(BATCH09_DIRECT_MOTIFS) == set(BATCH09_REGULAR_NAMES)
    assert set(BATCH09_TOPOLOGY) == set(BATCH09_REGULAR_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH09_REGULAR_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH09_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH09_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH09_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert record["emitted_snps"]
        for marker in record["emitted_snps"]:
            assert marker["motif_owner"] == name
            coverage = marker["array_coverage"]
            cohort_exports = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert (
                set(coverage["callable_snv_in"])
                <= set(coverage["position_present_in"])
                <= cohort_exports
            )
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )


def test_issue_1798_batch_09_h2a2_is_exact_and_markerless() -> None:
    """Historical-only m.750 remains source-only on the internal H2a2 gateway."""
    record = _MT_SOURCE["structural_exceptions"]["H2a2"]
    topology = record["source_topology"]
    inventory = _index_mt_tree(build_mt_tree())
    source_node, emitted_parent, parent_source, source_parent, flattened_path = (
        BATCH09_STRUCTURAL_TOPOLOGY["H2a2"]
    )

    assert _canonical_sha256(record) == BATCH09_STRUCTURAL_SHA256["H2a2"]
    assert record["type"] == "markerless_passthrough"
    assert record["source_status"] == "exact"
    assert record["source_node"] == source_node
    assert record["emitted_parent"] == emitted_parent
    assert record["emitted_snps"] == []
    assert topology["status"] == "exact"
    assert topology["emitted_parent_source_node"] == parent_source
    assert topology["source_parent"] == source_parent
    assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
        flattened_path
    )
    assert (
        tuple(
            (mutation["notation"], mutation["emitted"])
            for mutation in record["direct_source_motif"]
        )
        == BATCH09_STRUCTURAL_MOTIFS["H2a2"]
    )
    assert all(
        mutation["emitted"] is False and mutation["omission_reason"]
        for mutation in record["direct_source_motif"]
    )
    assert inventory.by_name["H2a2"].parent == "H2a"
    assert inventory.by_name["H2a2"].node["defining_snps"] == []


def test_issue_1798_batch_09_h1_helper_is_exact_and_source_only() -> None:
    """Lock H1+16189 type, adjacency, reason, and unavailable reversion bytes."""
    step = _batch09_flattened_step(_MT_SOURCE)
    omitted = _MT_SOURCE["omitted_nodes"]["H1+16189"]
    source_parent, omission_type, motif, owner = BATCH09_FLATTENED_STEPS["H1+16189"]

    assert owner == "H1b"
    assert _canonical_sha256(omitted) == BATCH09_OMITTED_SHA256["H1+16189"]
    assert omitted == {"type": omission_type, "reason": step["reason"]}
    assert step["source_parent"] == source_parent
    assert (
        tuple(
            (mutation["notation"], mutation["emitted"]) for mutation in step["direct_source_motif"]
        )
        == motif
    )
    assert all(
        mutation["emitted"] is False and mutation["omission_reason"]
        for mutation in step["direct_source_motif"]
    )


def test_issue_1798_batch_09_h1a_and_h1b_remain_source_siblings() -> None:
    """The explicit H1+16189 edge must not be misread as descent through H1a."""
    inventory = _index_mt_tree(build_mt_tree())
    h1a = _MT_SOURCE["nodes"]["H1a"]
    h1b = _MT_SOURCE["nodes"]["H1b"]
    helper = _batch09_flattened_step(_MT_SOURCE)

    assert inventory.by_name["H1a"].parent == "H1"
    assert inventory.by_name["H1b"].parent == "H1"
    assert h1a["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "H1",
        "source_parent": "H1",
        "flattened_source_path": [],
    }
    assert h1b["emitted_parent"] == "H1"
    assert h1b["source_topology"] == {
        "status": "exact",
        "emitted_parent_source_node": "H1",
        "source_parent": "H1+16189",
        "flattened_source_path": [helper],
    }
    assert helper["source_parent"] == "H1"
    assert [mutation["notation"] for mutation in helper["direct_source_motif"]] == ["T16189C!"]


def test_issue_1798_batch_09_advances_only_reviewed_live_frontiers() -> None:
    """Promote nine records, one structural gateway, and two edge-only records."""
    migration = _MT_SOURCE["migration"]
    exact = set(_MT_SOURCE["nodes"])
    direct = set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"])
    initial_pending = set(migration["initial_pending_nodes"])

    assert BATCH09_MARKER_PROMOTIONS <= exact
    assert BATCH09_MARKER_PROMOTIONS.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert BATCH09_MARKER_PROMOTIONS <= initial_pending
    assert BATCH09_MARKER_PROMOTIONS.isdisjoint(migration["baseline_exact_nodes"])
    assert BATCH09_DIRECT_MOTIF_PROMOTIONS <= direct
    assert BATCH09_DIRECT_MOTIF_PROMOTIONS.isdisjoint(legacy)
    assert BATCH09_STRUCTURAL_PROMOTIONS <= set(_MT_SOURCE["structural_exceptions"])
    assert BATCH09_STRUCTURAL_PROMOTIONS <= initial_pending
    assert BATCH09_PREEXISTING_EXACT <= exact
    assert BATCH09_EDGE_ONLY_RECORDS <= set(migration["baseline_exact_nodes"])
    assert BATCH09_EDGE_ONLY_RECORDS <= exact
    assert (
        BATCH09_MARKER_PROMOTIONS
        | BATCH09_STRUCTURAL_PROMOTIONS
        | BATCH09_PREEXISTING_EXACT
        | BATCH09_EDGE_ONLY_RECORDS
    ).isdisjoint(_MT_SOURCE["pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_09_promotions_have_independent_authoritative_evidence() -> None:
    """Validate promoted exact records against schema-v3's shared global references."""
    source_metadata = _MT_SOURCE["source"]
    references_by_doi = {reference["doi"]: reference for reference in _MT_SOURCE["references"]}

    assert source_metadata["version"] == "Build 17"
    assert source_metadata["archive_url"] == (
        "https://www.phylotree.org/builds/mtDNA_tree_Build_17.zip"
    )
    assert source_metadata["archive_sha256"] == (
        "3fe8cf00a15e1ccb09235091016eef1af3a68f44dd9355dd2b7666f8f767b146"
    )
    assert source_metadata["accessed"] == "2026-07-12"
    assert set(BATCH09_PROMOTED_RECORD_EVIDENCE) == BATCH09_PROMOTED_RECORDS

    for name, citations in BATCH09_PROMOTED_RECORD_EVIDENCE.items():
        if name in BATCH09_STRUCTURAL_PROMOTIONS:
            record = _MT_SOURCE["structural_exceptions"][name]
            assert record["source_status"] == "exact"
        else:
            record = _MT_SOURCE["nodes"][name]
            assert record["source_motif_status"] == "exact"
            assert record["source_topology"]["status"] == "exact"

        assert len(citations) == 2
        for citation in citations:
            reference = references_by_doi[citation["doi"]]
            assert {key: reference[key] for key in citation} == citation


@pytest.mark.parametrize("name", BATCH09_REGULAR_NAMES)
def test_issue_1798_batch_09_regular_source_mutations_fail_closed(name: str) -> None:
    """Reject one wrong literal direction and topology edge in every regular record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = motif_source["nodes"][name]["direct_source_motif"][0]
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )


def test_issue_1798_batch_09_h2a2_source_mutations_fail_closed() -> None:
    """H2a2's historical event cannot become caller evidence or drift in topology."""
    emitted_source = deepcopy(_MT_SOURCE)
    mutation = emitted_source["structural_exceptions"]["H2a2"]["direct_source_motif"][0]
    mutation["emitted"] = True
    mutation.pop("omission_reason")
    emitted_text = _issues_text(
        haplogroup_builder._validate_mt_structural_records(
            emitted_source,
            _index_mt_tree(build_mt_tree()),
        )
    )
    assert "Markerless structural mtDNA node H2a2 has an emitted source decision" in (emitted_text)
    assert "state_partition_sha256 does not match its registry projection" in _issues_text(
        _validate_mt_source_schema(emitted_source)
    )

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["structural_exceptions"]["H2a2"]["source_topology"][
        "emitted_parent_source_node"
    ] = "wrong-parent"
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert "Exact source topology for mtDNA node H2a2 names emitted-parent source" in (
        topology_text
    )


@pytest.mark.parametrize("mutation", ["path", "reason", "motif", "emission"])
def test_issue_1798_batch_09_h1_helper_mutations_fail_closed(mutation: str) -> None:
    """H1+16189 cannot drift in adjacency, reason, direction, or emission state."""
    source = deepcopy(_MT_SOURCE)
    step = _batch09_flattened_step(source)
    if mutation == "path":
        step["source_parent"] = "wrong-parent"
        expected = "breaks adjacency at H1+16189"
    elif mutation == "reason":
        step["reason"] = "test-only altered source reason"
        expected = "source path node H1+16189 disagrees with its omission reason"
    elif mutation == "motif":
        step["direct_source_motif"][0]["derived_allele"] = "A"
        expected = "mtDNA substitution H1+16189:"
    else:
        step["direct_source_motif"][0]["emitted"] = True
        step["direct_source_motif"][0].pop("omission_reason")
        expected = "emitted markers do not match every source emission decision"

    detailed_issues: list[str] = []
    _mt_validate_exact_record(
        "H1b",
        source["nodes"]["H1b"],
        source["omitted_nodes"],
        source["array_cohorts"],
        detailed_issues,
    )
    assert expected in _issues_text(detailed_issues)
    assert "locked_exact_semantic_sha256 does not match its registry projection" in (
        _issues_text(_validate_mt_source_schema(source))
    )


@pytest.mark.parametrize("name", list(BATCH09_OLD_MARKERS))
def test_issue_1798_batch_09_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Duplicated, historical, and non-source Batch 09 rows stay out of runtime."""
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)
    inventory.by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH09_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text
    if name == "H2a2":
        assert "Structural mtDNA pass-through H2a2 must be markerless" in text
    else:
        assert f"Marker-exact mtDNA node {name} has markers" in text


def _batch10_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """Collect every exact Batch 10 reference to one flattened source identity."""
    occurrences: list[tuple[str, str, dict[str, Any]]] = []
    for category in ("nodes", "structural_exceptions"):
        for owner, record in source[category].items():
            for step in record.get("source_topology", {}).get("flattened_source_path", []):
                if step["source_node"] == identity:
                    occurrences.append((category, owner, step))
    return occurrences


def test_issue_1798_batch_10_regular_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all sixteen reportable Batch 10 records to direct source evidence."""
    assert set(BATCH10_RECORD_SHA256) == set(BATCH10_REGULAR_NAMES)
    assert set(BATCH10_DIRECT_MOTIFS) == set(BATCH10_REGULAR_NAMES)
    assert set(BATCH10_TOPOLOGY) == set(BATCH10_REGULAR_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH10_REGULAR_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH10_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH10_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH10_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert record["emitted_snps"]
        for marker in record["emitted_snps"]:
            assert marker["motif_owner"] == name
            coverage = marker["array_coverage"]
            cohort = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            assert coverage["callable_snv_in"]
            assert set(coverage["callable_snv_in"]) <= set(coverage["position_present_in"])
            assert set(coverage["position_present_in"]) <= cohort
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )


def test_issue_1798_batch_10_b_alias_is_exact_markerless_and_deletion_defined() -> None:
    """Runtime B preserves source alias B4'5 without inventing a substitution marker."""
    record = _MT_SOURCE["structural_exceptions"]["B"]
    topology = record["source_topology"]
    source_node, emitted_parent, parent_source, source_parent, flattened_path = (
        BATCH10_STRUCTURAL_TOPOLOGY["B"]
    )
    inventory = _index_mt_tree(build_mt_tree())

    assert _canonical_sha256(record) == BATCH10_STRUCTURAL_SHA256["B"]
    assert record["type"] == "markerless_passthrough"
    assert record["source_status"] == "exact"
    assert record["source_node"] == source_node == "B4'5"
    assert record["emitted_parent"] == emitted_parent == "R"
    assert record["emitted_snps"] == []
    assert topology["status"] == "exact"
    assert topology["emitted_parent_source_node"] == parent_source
    assert topology["source_parent"] == source_parent
    assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
        flattened_path
    )
    assert (
        tuple(
            (mutation["notation"], mutation["emitted"])
            for mutation in record["direct_source_motif"]
        )
        == BATCH10_STRUCTURAL_MOTIFS["B"]
    )
    deletion = record["direct_source_motif"][0]
    assert deletion["mutation_type"] == "deletion"
    assert deletion["deleted_sequence"] == "CCCCCTCTA"
    assert deletion["omission_reason"]
    assert inventory.by_name["B"].parent == "R"
    assert inventory.by_name["B"].node["defining_snps"] == []


def test_issue_1798_batch_10_flattened_helpers_are_exact_and_source_only() -> None:
    """Lock all seven corrected helper identities, owners, literals, and omission types."""
    assert set(BATCH10_OMITTED_SHA256) == set(BATCH10_FLATTENED_STEPS)

    for identity, (source_parent, omission_type, motif, owners) in BATCH10_FLATTENED_STEPS.items():
        omitted = _MT_SOURCE["omitted_nodes"][identity]
        occurrences = _batch10_flattened_occurrences(_MT_SOURCE, identity)

        assert _canonical_sha256(omitted) == BATCH10_OMITTED_SHA256[identity]
        assert tuple(owner for _category, owner, _step in occurrences) == owners
        assert len(occurrences) == 1
        _category, _owner, step = occurrences[0]
        assert omitted == {"type": omission_type, "reason": step["reason"]}
        assert step["source_parent"] == source_parent
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in step["direct_source_motif"]
            )
            == motif
        )
        assert all(
            mutation["emitted"] is False and mutation["omission_reason"]
            for mutation in step["direct_source_motif"]
        )

    assert _batch10_flattened_occurrences(_MT_SOURCE, "R9")[0][1] == "F"
    assert _batch10_flattened_occurrences(_MT_SOURCE, "R+16189")[0][1] == "B"
    assert _batch10_flattened_occurrences(_MT_SOURCE, "F1a'c'f")[0][1] == "F1a"
    assert _batch10_flattened_occurrences(_MT_SOURCE, "F1+16189")[0][1] == "F1b"
    b4a_step = _batch10_flattened_occurrences(_MT_SOURCE, "B4+16261")[0][2]
    assert [mutation["notation"] for mutation in b4a_step["direct_source_motif"]] == ["C16261T"]


def test_issue_1798_batch_10_advances_only_reviewed_live_frontiers() -> None:
    """Promote sixteen regular records and deletion-defined structural B."""
    migration = _MT_SOURCE["migration"]
    exact = set(_MT_SOURCE["nodes"])
    direct = set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"])
    initial_pending = set(migration["initial_pending_nodes"])

    assert BATCH10_MARKER_PROMOTIONS <= exact
    assert BATCH10_MARKER_PROMOTIONS <= initial_pending
    assert BATCH10_MARKER_PROMOTIONS.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert BATCH10_DIRECT_MOTIF_PROMOTIONS <= direct
    assert BATCH10_DIRECT_MOTIF_PROMOTIONS.isdisjoint(legacy)
    assert BATCH10_STRUCTURAL_PROMOTIONS <= set(_MT_SOURCE["structural_exceptions"])
    assert BATCH10_STRUCTURAL_PROMOTIONS <= initial_pending
    assert BATCH10_STRUCTURAL_PROMOTIONS.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_10_promotions_have_authoritative_global_evidence() -> None:
    """Every promoted record is covered by the shared Build 17 paper references."""
    references_by_doi = {reference["doi"]: reference for reference in _MT_SOURCE["references"]}
    assert set(BATCH10_PROMOTED_RECORD_EVIDENCE) == BATCH10_PROMOTED_RECORDS

    for name, citations in BATCH10_PROMOTED_RECORD_EVIDENCE.items():
        record = (
            _MT_SOURCE["structural_exceptions"][name]
            if name in BATCH10_STRUCTURAL_PROMOTIONS
            else _MT_SOURCE["nodes"][name]
        )
        assert record.get("source_status", record.get("source_motif_status")) == "exact"
        assert len(citations) == 2
        for citation in citations:
            reference = references_by_doi[citation["doi"]]
            assert {key: reference[key] for key in citation} == citation


@pytest.mark.parametrize("name", BATCH10_REGULAR_NAMES)
def test_issue_1798_batch_10_regular_mutations_and_edges_fail_closed(name: str) -> None:
    """Reject direction drift and emitted-parent source drift in each regular record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = next(
        item
        for item in motif_source["nodes"][name]["direct_source_motif"]
        if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )


def test_issue_1798_batch_10_b_cannot_emit_deletion_or_substitution_markers() -> None:
    """B remains markerless even if its deletion decision or old markers are restored."""
    emitted_source = deepcopy(_MT_SOURCE)
    deletion = emitted_source["structural_exceptions"]["B"]["direct_source_motif"][0]
    deletion["emitted"] = True
    deletion.pop("omission_reason")
    emitted_text = _issues_text(
        haplogroup_builder._validate_mt_structural_records(
            emitted_source,
            _index_mt_tree(build_mt_tree()),
        )
    )
    assert "mtDNA deletion B4'5:8281 cannot be emitted" in emitted_text

    tree = build_mt_tree()
    _index_mt_tree(tree).by_name["B"].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH10_OLD_MARKERS["B"]
    ]
    restored_text = _issues_text(
        _validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree))
    )
    assert "Structural mtDNA pass-through B must be markerless" in restored_text
    assert "mtDNA emitted tree differs from its live locked fingerprint" in restored_text


@pytest.mark.parametrize("identity", list(BATCH10_FLATTENED_STEPS))
def test_issue_1798_batch_10_flattened_helpers_fail_closed(identity: str) -> None:
    """A helper cannot drift in adjacency or silently become runtime evidence."""
    path_source = deepcopy(_MT_SOURCE)
    _category, _owner, path_step = _batch10_flattened_occurrences(path_source, identity)[0]
    path_step["source_parent"] = "wrong-parent"
    if identity == "R+16189":
        path_issues = haplogroup_builder._validate_mt_structural_records(
            path_source,
            _index_mt_tree(build_mt_tree()),
        )
    else:
        path_issues = _validate_mt_source(path_source, build_mt_tree())
    path_text = _issues_text(path_issues)
    assert f"breaks adjacency at {identity}" in path_text

    emitted_source = deepcopy(_MT_SOURCE)
    _category, _owner, emitted_step = _batch10_flattened_occurrences(emitted_source, identity)[0]
    emitted_step["direct_source_motif"][0]["emitted"] = True
    emitted_step["direct_source_motif"][0].pop("omission_reason")
    if identity == "R+16189":
        emitted_issues = haplogroup_builder._validate_mt_structural_records(
            emitted_source,
            _index_mt_tree(build_mt_tree()),
        )
    else:
        emitted_issues = _validate_mt_source(emitted_source, build_mt_tree())
    emitted_text = _issues_text(emitted_issues)
    if identity == "R+16189":
        assert "Markerless structural mtDNA node B has an emitted source decision" in (
            emitted_text
        )
    else:
        assert "emitted markers do not match every source emission decision" in emitted_text
        assert "locked_exact_semantic_sha256 does not match its registry projection" in (
            emitted_text
        )


@pytest.mark.parametrize("name", list(BATCH10_OLD_MARKERS))
def test_issue_1798_batch_10_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Unsupported, inherited, helper, and historical Batch 10 marker sets stay out."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH10_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text
    if name == "B":
        assert "Structural mtDNA pass-through B must be markerless" in text
    else:
        assert f"Marker-exact mtDNA node {name} has markers" in text


def _batch11_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """Collect every exact Batch 11 reference to one flattened source identity."""
    occurrences: list[tuple[str, str, dict[str, Any]]] = []
    for category in ("nodes", "structural_exceptions"):
        for owner, record in source[category].items():
            for step in record.get("source_topology", {}).get("flattened_source_path", []):
                if step["source_node"] == identity:
                    occurrences.append((category, owner, step))
    return occurrences


def test_issue_1798_batch_11_jt_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all eighteen J/T records to their exact motifs, paths, and runtime markers."""
    assert set(BATCH11_RECORD_SHA256) == set(BATCH11_REGULAR_NAMES)
    assert set(BATCH11_DIRECT_MOTIFS) == set(BATCH11_REGULAR_NAMES)
    assert set(BATCH11_TOPOLOGY) == set(BATCH11_REGULAR_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH11_REGULAR_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH11_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH11_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH11_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert record["emitted_snps"]
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )

    assert [
        (marker["pos"], marker["allele"])
        for marker in inventory.by_name["J1d"].node["defining_snps"]
    ] == [(7963, "G")]
    assert [
        (marker["pos"], marker["allele"])
        for marker in inventory.by_name["T2a"].node["defining_snps"]
    ] == [(13965, "C")]


def test_issue_1798_batch_11_flattened_helpers_are_exact_and_source_only() -> None:
    """Lock all four corrected helper identities, owners, literals, and omission types."""
    assert set(BATCH11_OMITTED_SHA256) == set(BATCH11_FLATTENED_STEPS)

    for identity, (source_parent, omission_type, motif, owners) in BATCH11_FLATTENED_STEPS.items():
        omitted = _MT_SOURCE["omitted_nodes"][identity]
        occurrences = _batch11_flattened_occurrences(_MT_SOURCE, identity)

        assert _canonical_sha256(omitted) == BATCH11_OMITTED_SHA256[identity]
        assert tuple(owner for _category, owner, _step in occurrences) == owners
        assert len(occurrences) == 1
        _category, _owner, step = occurrences[0]
        assert omitted == {"type": omission_type, "reason": step["reason"]}
        assert step["source_parent"] == source_parent
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in step["direct_source_motif"]
            )
            == motif
        )
        assert all(
            mutation["emitted"] is False and mutation["omission_reason"]
            for mutation in step["direct_source_motif"]
        )


def test_issue_1798_batch_11_advances_only_reviewed_jt_frontiers() -> None:
    """Promote sixteen pending records and complete two pre-existing topology edges."""
    migration = _MT_SOURCE["migration"]
    exact = set(_MT_SOURCE["nodes"])
    direct = set(_MT_SOURCE["direct_source_motif_states"]["exact_nodes"])
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"])
    initial_pending = set(migration["initial_pending_nodes"])

    assert BATCH11_MARKER_PROMOTIONS <= exact
    assert BATCH11_MARKER_PROMOTIONS <= initial_pending
    assert BATCH11_MARKER_PROMOTIONS.isdisjoint(_MT_SOURCE["pending_nodes"])
    assert BATCH11_DIRECT_MOTIF_PROMOTIONS <= direct
    assert BATCH11_DIRECT_MOTIF_PROMOTIONS.isdisjoint(legacy)
    assert BATCH11_EDGE_ONLY_RECORDS <= exact
    assert BATCH11_EDGE_ONLY_RECORDS <= direct
    assert BATCH11_EDGE_ONLY_RECORDS.isdisjoint(initial_pending)
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == BASELINE_EXACT_NAMES_SHA256
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_11_promotions_have_authoritative_global_evidence() -> None:
    """Every promoted record is covered by the shared Build 17 paper references."""
    references_by_doi = {reference["doi"]: reference for reference in _MT_SOURCE["references"]}
    assert set(BATCH11_PROMOTED_RECORD_EVIDENCE) == BATCH11_MARKER_PROMOTIONS

    for name, citations in BATCH11_PROMOTED_RECORD_EVIDENCE.items():
        assert _MT_SOURCE["nodes"][name]["source_motif_status"] == "exact"
        assert len(citations) == 2
        for citation in citations:
            reference = references_by_doi[citation["doi"]]
            assert {key: reference[key] for key in citation} == citation


def test_issue_1798_batch_11_t2_gateway_keeps_parenthesized_16296_source_only() -> None:
    """Preserve 1/2 T2 gateway reachability for pgp_4162 instead of blocking at 1/3."""
    record = _MT_SOURCE["nodes"]["T2"]
    decision = next(
        mutation for mutation in record["direct_source_motif"] if mutation["pos"] == 16296
    )

    assert decision["notation"] == "(C16296T)"
    assert decision["emitted"] is False
    assert "pgp_4162" in decision["omission_reason"]
    assert "1/3" in decision["omission_reason"]
    assert "1/2" in decision["omission_reason"]
    assert [(marker["pos"], marker["allele"]) for marker in record["emitted_snps"]] == [
        (11812, "G"),
        (14233, "G"),
    ]


def test_issue_1798_batch_11_historical_leaf_markers_keep_five_export_scope() -> None:
    """T1a and T2f retain their callable historical markers without widening scope."""
    for name, position in (("T1a", 16186), ("T2f", 8270)):
        marker = _MT_SOURCE["nodes"][name]["emitted_snps"][0]
        coverage = marker["array_coverage"]
        assert marker["pos"] == position
        assert coverage["cohort_id"] == "historical_five_23andme_including_2014"
        assert coverage["position_present_in"] == ["pgp_1050"]
        assert coverage["callable_snv_in"] == ["pgp_1050"]


@pytest.mark.parametrize("name", BATCH11_REGULAR_NAMES)
def test_issue_1798_batch_11_mutations_and_edges_fail_closed(name: str) -> None:
    """Reject one literal-direction drift and topology drift in every J/T record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = next(
        item
        for item in motif_source["nodes"][name]["direct_source_motif"]
        if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )


@pytest.mark.parametrize("identity", list(BATCH11_FLATTENED_STEPS))
def test_issue_1798_batch_11_flattened_helpers_fail_closed(identity: str) -> None:
    """A helper cannot drift in adjacency or silently become runtime evidence."""
    path_source = deepcopy(_MT_SOURCE)
    _category, _owner, path_step = _batch11_flattened_occurrences(path_source, identity)[0]
    path_step["source_parent"] = "wrong-parent"
    path_text = _issues_text(_validate_mt_source(path_source, build_mt_tree()))
    assert f"breaks adjacency at {identity}" in path_text

    emitted_source = deepcopy(_MT_SOURCE)
    _category, _owner, emitted_step = _batch11_flattened_occurrences(emitted_source, identity)[0]
    emitted_step["direct_source_motif"][0]["emitted"] = True
    emitted_step["direct_source_motif"][0].pop("omission_reason")
    emitted_text = _issues_text(_validate_mt_source(emitted_source, build_mt_tree()))
    assert "emitted markers do not match every source emission decision" in emitted_text
    assert "locked_exact_semantic_sha256 does not match its registry projection" in (emitted_text)


@pytest.mark.parametrize("name", list(BATCH11_OLD_MARKERS))
def test_issue_1798_batch_11_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Unsupported, inherited, helper, and historical J/T marker sets stay out."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH11_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text
    assert f"Marker-exact mtDNA node {name} has markers" in text


def _batch12_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """Collect every exact Batch 12 reference to one flattened source identity."""
    occurrences: list[tuple[str, str, dict[str, Any]]] = []
    for category in ("nodes", "structural_exceptions"):
        for owner, record in source[category].items():
            for step in record.get("source_topology", {}).get("flattened_source_path", []):
                if step["source_node"] == identity:
                    occurrences.append((category, owner, step))
    return occurrences


def test_issue_1798_batch_12_u_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all eighteen U records to exact motifs, paths, coverage, and markers."""
    expected_names = set(BATCH12_REGULAR_NAMES)
    assert set(BATCH12_RECORD_SHA256) == expected_names
    assert set(BATCH12_DIRECT_MOTIFS) == expected_names
    assert set(BATCH12_TOPOLOGY) == expected_names
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH12_REGULAR_NAMES:
        record = _MT_SOURCE["nodes"][name]
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH12_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH12_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record["source_motif_status"] == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH12_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert record["emitted_snps"]
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            present = set(coverage["position_present_in"])
            callable_snv = set(coverage["callable_snv_in"])
            assert callable_snv <= present <= cohort
            assert present


def test_issue_1798_batch_12_flattened_helpers_are_exact_and_source_only() -> None:
    """Lock five U helpers across every owner without granting runtime credit."""
    assert set(BATCH12_OMITTED_SHA256) == set(BATCH12_FLATTENED_STEPS)

    for identity, (source_parent, omission_type, motif, owners) in BATCH12_FLATTENED_STEPS.items():
        omitted = _MT_SOURCE["omitted_nodes"][identity]
        occurrences = _batch12_flattened_occurrences(_MT_SOURCE, identity)

        assert _canonical_sha256(omitted) == BATCH12_OMITTED_SHA256[identity]
        assert tuple(owner for _category, owner, _step in occurrences) == owners
        assert len(occurrences) == len(owners)
        assert all(step == occurrences[0][2] for _category, _owner, step in occurrences)
        for _category, _owner, step in occurrences:
            assert omitted == {"type": omission_type, "reason": step["reason"]}
            assert step["source_parent"] == source_parent
            assert (
                tuple(
                    (mutation["notation"], mutation["emitted"])
                    for mutation in step["direct_source_motif"]
                )
                == motif
            )
            assert all(
                mutation["emitted"] is False and mutation["omission_reason"]
                for mutation in step["direct_source_motif"]
            )

    assert "U2b" not in _MT_SOURCE["omitted_nodes"]
    assert BATCH12_TOPOLOGY["U2e"][3:] == ("U2+152", ("U2+152",))


def test_issue_1798_batch_12_advances_only_reviewed_u_frontiers() -> None:
    """Reverse Batch 12 and reproduce every exact pre-batch partition digest."""
    migration = _MT_SOURCE["migration"]
    # Normalize the completed Batch 13 state back to the Batch 12 endpoint
    # before replaying this historical frontier assertion.
    exact = set(migration["locked_exact_nodes"]) - BATCH13_MARKER_PROMOTIONS
    direct = set(migration["locked_direct_motif_exact_nodes"]) - (
        BATCH13_MARKER_PROMOTIONS | BATCH13_LEGACY_UPGRADES
    )
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"]) | (
        BATCH13_LEGACY_UPGRADES
    )
    pending = set(_MT_SOURCE["pending_nodes"]) | BATCH13_PENDING_PROMOTIONS
    omitted = set(_MT_SOURCE["omitted_nodes"]) - set(BATCH13_FLATTENED_STEPS)
    helpers = set(BATCH12_FLATTENED_STEPS)

    assert set(BATCH12_REGULAR_NAMES) == (
        BATCH12_MARKER_PROMOTIONS | BATCH12_LEGACY_UPGRADES | BATCH12_PREEXISTING_REPAIRS
    )
    assert BATCH12_MARKER_PROMOTIONS <= exact
    assert BATCH12_MARKER_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH12_MARKER_PROMOTIONS.isdisjoint(pending)
    assert BATCH12_LEGACY_UPGRADES <= direct
    assert BATCH12_LEGACY_UPGRADES.isdisjoint(legacy)
    assert BATCH12_PREEXISTING_REPAIRS <= exact & direct
    assert BATCH12_PREEXISTING_REPAIRS.isdisjoint(migration["initial_pending_nodes"])

    assert _canonical_sha256(sorted(exact - BATCH12_MARKER_PROMOTIONS)) == (
        BATCH12_PREVIOUS_EXACT_NAMES_SHA256
    )
    assert (
        _canonical_sha256(sorted(direct - BATCH12_MARKER_PROMOTIONS - BATCH12_LEGACY_UPGRADES))
        == BATCH12_PREVIOUS_DIRECT_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(legacy | BATCH12_LEGACY_UPGRADES)) == (
        BATCH12_PREVIOUS_LEGACY_PARTIAL_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(pending | BATCH12_MARKER_PROMOTIONS)) == (
        BATCH12_PREVIOUS_PENDING_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(omitted - helpers)) == (BATCH12_PREVIOUS_OMITTED_NAMES_SHA256)
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_12_promotions_have_authoritative_global_evidence() -> None:
    """Each newly exact U record is covered by both pinned Build 17 references."""
    references_by_doi = {reference["doi"]: reference for reference in _MT_SOURCE["references"]}
    assert set(BATCH12_PROMOTED_RECORD_EVIDENCE) == (
        BATCH12_MARKER_PROMOTIONS | BATCH12_LEGACY_UPGRADES
    )

    for name, citations in BATCH12_PROMOTED_RECORD_EVIDENCE.items():
        assert _MT_SOURCE["nodes"][name]["source_motif_status"] == "exact"
        assert len(citations) == 2
        for citation in citations:
            reference = references_by_doi[citation["doi"]]
            assert {key: reference[key] for key in citation} == citation


def test_issue_1798_batch_12_repairs_lowercase_transversion_and_preserves_u2e_markers() -> None:
    """C4640a remains a source-only C>A substitution; prior U2e markers stay byte-locked."""
    c4640 = next(
        mutation
        for mutation in _MT_SOURCE["nodes"]["U3b"]["direct_source_motif"]
        if mutation["notation"] == "C4640a"
    )
    assert c4640["mutation_type"] == "substitution"
    assert (c4640["ancestral_allele"], c4640["derived_allele"], c4640["emitted"]) == (
        "C",
        "A",
        False,
    )

    markers = {marker["pos"]: marker for marker in _MT_SOURCE["nodes"]["U2e"]["emitted_snps"]}
    for position, expected_sha256 in BATCH12_U2E_PRESERVED_MARKER_SHA256.items():
        assert _canonical_sha256(markers[position]) == expected_sha256


@pytest.mark.parametrize("name", BATCH12_REGULAR_NAMES)
def test_issue_1798_batch_12_mutations_topology_and_coverage_fail_closed(name: str) -> None:
    """Reject one motif, parent, and export-coverage drift in every U record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = next(
        item
        for item in motif_source["nodes"][name]["direct_source_motif"]
        if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )

    coverage_source = deepcopy(_MT_SOURCE)
    coverage_source["nodes"][name]["emitted_snps"][0]["array_coverage"]["callable_snv_in"].append(
        "unknown-export"
    )
    coverage_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        coverage_source["nodes"][name],
        coverage_source["omitted_nodes"],
        coverage_source["array_cohorts"],
        coverage_issues,
    )
    assert "callable-SNV exports outside its cohort" in _issues_text(coverage_issues)


@pytest.mark.parametrize("identity", list(BATCH12_FLATTENED_STEPS))
def test_issue_1798_batch_12_flattened_helpers_fail_closed(identity: str) -> None:
    """A U helper cannot drift in adjacency or silently become runtime evidence."""
    path_source = deepcopy(_MT_SOURCE)
    _category, _owner, path_step = _batch12_flattened_occurrences(path_source, identity)[0]
    path_step["source_parent"] = "wrong-parent"
    path_text = _issues_text(_validate_mt_source(path_source, build_mt_tree()))
    assert f"breaks adjacency at {identity}" in path_text

    emitted_source = deepcopy(_MT_SOURCE)
    _category, _owner, emitted_step = _batch12_flattened_occurrences(emitted_source, identity)[0]
    emitted_step["direct_source_motif"][0]["emitted"] = True
    emitted_step["direct_source_motif"][0].pop("omission_reason")
    emitted_text = _issues_text(_validate_mt_source(emitted_source, build_mt_tree()))
    assert "emitted markers do not match every source emission decision" in emitted_text
    assert "locked_exact_semantic_sha256 does not match its registry projection" in emitted_text


@pytest.mark.parametrize("name", list(BATCH12_OLD_MARKERS))
def test_issue_1798_batch_12_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Inherited, helper, historical, and stale U marker sets remain outside runtime."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH12_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text
    assert f"Marker-exact mtDNA node {name} has markers" in text


def _batch13_record(source: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a Batch 13 regular or exact markerless record."""
    category = "structural_exceptions" if name == "U5" else "nodes"
    return source[category][name]


def _batch13_flattened_occurrences(
    source: dict[str, Any], identity: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """Collect every exact Batch 13 reference to one flattened source identity."""
    occurrences: list[tuple[str, str, dict[str, Any]]] = []
    for category in ("nodes", "structural_exceptions"):
        for owner, record in source[category].items():
            for step in record.get("source_topology", {}).get("flattened_source_path", []):
                if step["source_node"] == identity:
                    occurrences.append((category, owner, step))
    return occurrences


def test_issue_1798_batch_13_records_are_exact_covered_and_tree_locked() -> None:
    """Lock all sixteen final records to exact motifs, paths, coverage, and markers."""
    assert set(BATCH13_RECORD_SHA256) == set(BATCH13_NAMES)
    assert set(BATCH13_DIRECT_MOTIFS) == set(BATCH13_NAMES)
    assert set(BATCH13_TOPOLOGY) == set(BATCH13_NAMES)
    inventory = _index_mt_tree(build_mt_tree())

    for name in BATCH13_NAMES:
        record = _batch13_record(_MT_SOURCE, name)
        source_node, emitted_parent, parent_source, source_parent, flattened_path = (
            BATCH13_TOPOLOGY[name]
        )
        topology = record["source_topology"]

        assert _canonical_sha256(record) == BATCH13_RECORD_SHA256[name]
        assert record["source_node"] == source_node
        assert record["emitted_parent"] == emitted_parent
        assert record.get("source_motif_status", record.get("source_status")) == "exact"
        assert topology["status"] == "exact"
        assert topology["emitted_parent_source_node"] == parent_source
        assert topology["source_parent"] == source_parent
        assert tuple(step["source_node"] for step in topology["flattened_source_path"]) == (
            flattened_path
        )
        assert (
            tuple(
                (mutation["notation"], mutation["emitted"])
                for mutation in record["direct_source_motif"]
            )
            == BATCH13_DIRECT_MOTIFS[name]
        )
        assert [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in record["emitted_snps"]
        ] == inventory.by_name[name].node["defining_snps"]
        assert all(
            mutation.get("omission_reason")
            for mutation in record["direct_source_motif"]
            if mutation["emitted"] is False
        )

        if name == "U5":
            assert record["type"] == "markerless_passthrough"
            assert record["emitted_snps"] == []
            continue

        assert record["emitted_snps"]
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort = set(_MT_SOURCE["array_cohorts"][coverage["cohort_id"]]["export_ids"])
            present = set(coverage["position_present_in"])
            callable_snv = set(coverage["callable_snv_in"])
            assert callable_snv <= present <= cohort
            assert present

    assert BATCH13_TOPOLOGY["K"][1:] == ("U8b", "U8b", "U8b", ())


def test_issue_1798_batch_13_u5_is_an_exact_markerless_gateway() -> None:
    """U5 keeps its markerless gateway with a source-backed ancestral conflict guard."""
    record = _MT_SOURCE["structural_exceptions"]["U5"]
    inventory = _index_mt_tree(build_mt_tree())

    assert tuple(mutation["notation"] for mutation in record["direct_source_motif"]) == (
        "C16192T",
        "C16270T",
    )
    assert not any(mutation["emitted"] for mutation in record["direct_source_motif"])
    assert inventory.by_name["U5"].node["defining_snps"] == []
    assert inventory.by_name["U5"].node["optional_conflict_snps"] == [
        {"rsid": "i5016270", "pos": 16270, "allele": "T"}
    ]
    assert record["optional_conflict_snps"] == [
        {
            "rsid": "i5016270",
            "pos": 16270,
            "ancestral_allele": "C",
            "allele": "T",
            "motif_owner": "U5",
            "array_coverage": {
                "cohort_id": "primary_four_23andme",
                "position_present_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
                "callable_snv_in": ["pgp_4187", "pgp_huA08F4D"],
            },
        }
    ]
    assert [child["haplogroup"] for child in inventory.by_name["U5"].node["children"]] == [
        "U5a",
        "U5b",
    ]

    tree = build_mt_tree()
    _find_node(tree, "U5")["defining_snps"].append(
        {"rsid": "i5016270", "pos": 16270, "allele": "T"}
    )
    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Structural mtDNA pass-through U5 must be markerless" in text
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text


def test_issue_2165_u5_conflict_evidence_packet_is_source_bound() -> None:
    """Keep the public evidence packet bound to the exact U5 Build 17 edge."""
    inventory_path = U5_CONFLICT_EVIDENCE_PACKET / "source-inventory.json"
    extract_path = U5_CONFLICT_EVIDENCE_PACKET / "raw/phylotree-build17-u5-source-extract.json"
    response_index_path = U5_CONFLICT_EVIDENCE_PACKET / "source-response-index.json"
    pubmed_path = U5_CONFLICT_EVIDENCE_PACKET / "pubmed-esummary.json"
    readme_path = U5_CONFLICT_EVIDENCE_PACKET / "README.md"

    for path in (
        inventory_path,
        extract_path,
        response_index_path,
        pubmed_path,
        readme_path,
    ):
        assert path.is_file(), path

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    extract = json.loads(extract_path.read_text(encoding="utf-8"))
    response_index = json.loads(response_index_path.read_text(encoding="utf-8"))
    pubmed = json.loads(pubmed_path.read_text(encoding="utf-8"))
    source_u5 = _MT_SOURCE["structural_exceptions"]["U5"]
    source_metadata = _MT_SOURCE["source"]

    assert inventory["issue"] == 2165
    assert inventory["accessed"] == "2026-08-03"
    assert inventory["implementation"]["commit"] == ("aa3acc8df76f782de3ade41a95d6e5a1d9f96da4")
    assert inventory["source_archive"]["version"] == source_metadata["version"]
    assert inventory["source_archive"]["archive_url"] == source_metadata["archive_url"]
    assert inventory["source_archive"]["archive_sha256"] == source_metadata["archive_sha256"]
    assert inventory["source_archive"]["archive_accessed"] == source_metadata["accessed"]
    assert inventory["source_archive"]["license_or_terms"]["status"] == (
        "not_stated_on_inspected_official_pages"
    )
    expected_extract_source_archive = {
        "name": source_metadata["name"],
        "version": source_metadata["version"],
        "archive_url": source_metadata["archive_url"],
        "archive_sha256": source_metadata["archive_sha256"],
        "archive_accessed": source_metadata["accessed"],
    }
    assert extract["source_archive"] == expected_extract_source_archive
    assert extract["license_or_terms"]["status"] == ("not_stated_on_inspected_official_pages")

    expected_motif = [
        {
            "notation": mutation["notation"],
            "pos": mutation["pos"],
            "ancestral_allele": mutation["ancestral_allele"],
            "derived_allele": mutation["derived_allele"],
            "emitted": mutation["emitted"],
        }
        for mutation in source_u5["direct_source_motif"]
    ]
    assert inventory["u5_guard"]["direct_source_motif"] == expected_motif
    assert extract["u5"]["direct_source_motif"] == expected_motif
    assert inventory["u5_guard"]["optional_conflict_snp"] == source_u5["optional_conflict_snps"][0]
    assert extract["u5"]["optional_conflict_snp"] == source_u5["optional_conflict_snps"][0]
    assert source_u5["emitted_snps"] == []

    assert {(record["pmid"], record["doi"]) for record in pubmed["records"]} == {
        ("18853457", "10.1002/humu.20921"),
        ("34072215", "10.3390/ijms22115747"),
        ("10712215", "10.1086/302802"),
        # NCBI emits no DOI for this record; the packet records that rather than
        # inventing an identifier, so ``None`` here is the asserted state.
        ("11032788", None),
    }
    pubmed_by_pmid = {record["pmid"]: record for record in pubmed["records"]}
    assert pubmed_by_pmid["11032788"]["publisher_item_identifier"] == "S0002-9297(07)62954-1", (
        "the DOI-less record must keep a durable identifier of its own"
    )
    assert all(
        record["correction_check"]["comments_corrections_list_emitted"] is False
        for record in pubmed["records"]
    )
    assert (
        "https://www.ncbi.nlm.nih.gov/books/NBK25497/"
        in pubmed["license_or_terms"]["official_policy_urls"]
    )

    # C5 is the limiting claim: m.16270 back-mutates inside U5, so the guard is
    # withholding rather than exclusion. Both supporting records must stay in the
    # packet, must carry a paraphrase instead of retained source text, and must
    # not be accompanied by an invented false-veto rate.
    c5_records = [record for record in pubmed["records"] if "C5" in record.get("supports", [])]
    assert {record["pmid"] for record in c5_records} == {"10712215", "11032788"}
    for record in c5_records:
        assert record["supporting_statement"]["location"] == "Abstract"
        assert record["supporting_statement"]["verbatim_text_retained"] is False
        assert record["supporting_statement"]["paraphrase"].strip()
    assert "C5" in pubmed["claim_ids"]

    readme = readme_path.read_text(encoding="utf-8")
    assert "implementation-level source-conflict rule" in readme
    assert "clinical, phenotypic, population, ancestry, or forensic conclusion" in readme
    # The exclusion-flavoured wording an earlier revision used must stay retired.
    assert "incompatible with descent" not in readme
    assert "it does not assert that the sample is not U5" in readme.replace("\n", " ")
    assert "PMID:10712215" in readme
    assert "PMID:11032788" in readme
    known_false_veto = inventory["u5_guard"]["known_false_veto"]
    assert known_false_veto["rate_estimated"] is False
    assert inventory["u5_guard"]["runtime_decision"]["semantics"] == "withholding, not exclusion"
    assert inventory["u5_guard"]["runtime_decision"]["merged_flag_only_ambiguity_sentinel"]

    assert {entry["service"] for entry in response_index["entries"]} == {
        "repository source-audited registry",
        "NCBI Entrez",
    }
    # Without this the per-entry payload/hash loop below would pass vacuously on
    # an empty list, and the packet could silently stop being source-bound.
    assert response_index["entries"]
    for entry in response_index["entries"]:
        payload_path = Path(__file__).resolve().parents[2] / entry["payload_path"]
        assert payload_path.is_file(), payload_path
        assert hashlib.sha256(payload_path.read_bytes()).hexdigest() == entry["sanitized_sha256"]

    services = {entry["service"]: entry for entry in response_index["discovery_services"]}
    assert set(services) == {"Consensus", "Scite"}
    required_service_fields = {
        "service",
        "invoked_on",
        "sanitized_query",
        "purpose",
        "provider_output_retained",
        "provider_output_used_as_evidence",
        "primary_source_ids_checked_independently",
        "documentation_url",
        "terms_url",
    }
    # Closed vocabulary: a discovery-service entry may record a fallback or an
    # excluded provider finding, and nothing else. Anything outside this set
    # would be undeclared provider content leaking into the packet.
    optional_service_fields = {"unavailable_or_quota_events", "excluded_provider_findings"}
    for service in services.values():
        assert required_service_fields <= set(service)
        assert set(service) <= required_service_fields | optional_service_fields
        assert service["provider_output_retained"] is False
        assert service["provider_output_used_as_evidence"] is False
        assert {
            "PMID:18853457",
            "PMID:34072215",
            "DOI:10.1002/humu.20921",
            "DOI:10.3390/ijms22115747",
        } <= set(service["primary_source_ids_checked_independently"])
        for event in service.get("unavailable_or_quota_events", []):
            assert event["error"] and event["action"]
        for excluded in service.get("excluded_provider_findings", []):
            assert excluded["used_as_evidence"] is False
            assert excluded["excluded_because"].strip()
    # Retention policy must describe what the files actually hold. Every URL the
    # packet keeps is one of the three enumerated exceptions, so the README and
    # the index cannot claim a blanket exclusion they do not honour.
    retained_url_kinds = response_index["sanitization"]["retained"]
    assert any("source-archive URL" in kind for kind in retained_url_kinds)
    assert any("licence or reuse terms" in kind for kind in retained_url_kinds)
    assert any("documentation and terms URLs" in kind for kind in retained_url_kinds)
    assert "`source_archive.archive_url`" in readme
    assert "explicit, enumerated exceptions" in readme
    # The rate-limit and result-size fallbacks are recorded, not silently dropped.
    assert services["Consensus"]["unavailable_or_quota_events"]
    assert services["Scite"]["unavailable_or_quota_events"]
    # Likewise the excluded corroborating passage. The loop over this list above
    # would pass vacuously if the field were emptied or dropped, and the test
    # would quietly stop checking that the exclusion stays documented.
    assert services["Scite"]["excluded_provider_findings"]
    assert {"PMID:10712215", "PMID:11032788"} <= set(
        services["Scite"]["primary_source_ids_checked_independently"]
    )
    assert not (U5_CONFLICT_EVIDENCE_PACKET / "raw/consensus-search-fetch-sanitized.json").exists()
    assert not (
        U5_CONFLICT_EVIDENCE_PACKET / "raw/scite-targeted-doi-responses-sanitized.json"
    ).exists()
    assert not any(
        provider in path.name.lower()
        for path in (U5_CONFLICT_EVIDENCE_PACKET / "raw").iterdir()
        for provider in ("consensus", "scite")
    )


def test_issue_1798_batch_13_flattened_helpers_are_exact_and_source_only() -> None:
    """Lock both final U helpers across every owner without granting runtime credit."""
    assert set(BATCH13_OMITTED_SHA256) == set(BATCH13_FLATTENED_STEPS)

    for identity, (source_parent, motif, owners) in BATCH13_FLATTENED_STEPS.items():
        omitted = _MT_SOURCE["omitted_nodes"][identity]
        occurrences = _batch13_flattened_occurrences(_MT_SOURCE, identity)

        assert _canonical_sha256(omitted) == BATCH13_OMITTED_SHA256[identity]
        assert omitted["type"] == "flattened_source_intermediate"
        assert tuple(owner for _category, owner, _step in occurrences) == owners
        assert len(occurrences) == len(owners)
        assert all(step == occurrences[0][2] for _category, _owner, step in occurrences)
        for _category, _owner, step in occurrences:
            assert omitted["reason"] == step["reason"]
            assert step["source_parent"] == source_parent
            assert (
                tuple(
                    (mutation["notation"], mutation["emitted"])
                    for mutation in step["direct_source_motif"]
                )
                == motif
            )
            assert all(
                mutation["emitted"] is False and mutation["omission_reason"]
                for mutation in step["direct_source_motif"]
            )


def test_issue_1798_batch_13_closes_only_the_final_reviewed_frontiers() -> None:
    """Reverse Batch 13 and reproduce every exact Batch 12 endpoint digest."""
    migration = _MT_SOURCE["migration"]
    exact = set(migration["locked_exact_nodes"])
    direct = set(migration["locked_direct_motif_exact_nodes"])
    legacy = set(_MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"])
    pending = set(_MT_SOURCE["pending_nodes"])
    omitted = set(_MT_SOURCE["omitted_nodes"])

    assert set(BATCH13_NAMES) == (
        BATCH13_PENDING_PROMOTIONS | BATCH13_LEGACY_UPGRADES | BATCH13_TOPOLOGY_COMPANIONS
    )
    assert BATCH13_MARKER_PROMOTIONS <= exact & direct
    assert BATCH13_PENDING_PROMOTIONS <= set(migration["initial_pending_nodes"])
    assert BATCH13_LEGACY_UPGRADES <= exact & direct
    assert BATCH13_TOPOLOGY_COMPANIONS <= exact & direct
    assert not pending
    assert not legacy
    assert _canonical_sha256(sorted(exact - BATCH13_MARKER_PROMOTIONS)) == (
        BATCH13_PREVIOUS_EXACT_NAMES_SHA256
    )
    assert (
        _canonical_sha256(sorted(direct - BATCH13_MARKER_PROMOTIONS - BATCH13_LEGACY_UPGRADES))
        == BATCH13_PREVIOUS_DIRECT_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(legacy | BATCH13_LEGACY_UPGRADES)) == (
        BATCH13_PREVIOUS_LEGACY_PARTIAL_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(pending | BATCH13_PENDING_PROMOTIONS)) == (
        BATCH13_PREVIOUS_PENDING_NAMES_SHA256
    )
    assert _canonical_sha256(sorted(omitted - set(BATCH13_FLATTENED_STEPS))) == (
        BATCH13_PREVIOUS_OMITTED_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == INITIAL_PENDING_NAMES_SHA256
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )


def test_issue_1798_batch_13_completion_is_ready_and_fully_audited() -> None:
    """The final source state satisfies the schema's independent completion predicate."""
    inventory = _index_mt_tree(build_mt_tree())

    assert _MT_SOURCE["migration"]["status"] == "complete"
    assert _MT_SOURCE["pending_nodes"] == {}
    assert _MT_SOURCE["direct_source_motif_states"]["legacy_partial_nodes"] == []
    assert set(_MT_SOURCE["migration"]["locked_exact_nodes"]) == set(_MT_SOURCE["nodes"])
    assert set(_MT_SOURCE["migration"]["locked_direct_motif_exact_nodes"]) == set(
        _MT_SOURCE["nodes"]
    )
    assert (
        "Completed full emitted-tree PhyloTree Build 17 provenance audit"
        in (_MT_SOURCE["audit_scope"])
    )
    assert _mt_migration_complete_ready(_MT_SOURCE, inventory)
    assert _validate_mt_source(_MT_SOURCE, build_mt_tree()) == []


def test_issue_1798_batch_13_promotions_have_authoritative_global_evidence() -> None:
    """Every final promotion or legacy upgrade is covered by both pinned references."""
    references_by_doi = {reference["doi"]: reference for reference in _MT_SOURCE["references"]}
    reviewed = BATCH13_PENDING_PROMOTIONS | BATCH13_LEGACY_UPGRADES

    for name in reviewed:
        record = _batch13_record(_MT_SOURCE, name)
        assert record.get("source_motif_status", record.get("source_status")) == "exact"
        for citation in BATCH09_AUTHORITATIVE_CITATIONS:
            reference = references_by_doi[citation["doi"]]
            assert {key: reference[key] for key in citation} == citation


def test_issue_1798_batch_13_preserves_k1b_primary_coverage_bytes() -> None:
    """The topology repair cannot broaden K1b's previously audited primary coverage."""
    marker = _MT_SOURCE["nodes"]["K1b"]["emitted_snps"][0]

    assert _canonical_sha256(marker) == BATCH13_K1B_MARKER_SHA256
    assert marker["array_coverage"] == {
        "cohort_id": "primary_four_23andme",
        "position_present_in": ["pgp_4139", "pgp_4187"],
        "callable_snv_in": [],
    }


@pytest.mark.parametrize("name", BATCH13_REGULAR_NAMES)
def test_issue_1798_batch_13_mutations_topology_and_coverage_fail_closed(name: str) -> None:
    """Reject one motif, parent, and export-coverage drift in every regular final record."""
    motif_source = deepcopy(_MT_SOURCE)
    mutation = next(
        item
        for item in motif_source["nodes"][name]["direct_source_motif"]
        if item["mutation_type"] == "substitution"
    )
    mutation["derived_allele"] = "A" if mutation["derived_allele"] != "A" else "C"
    motif_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        motif_source["nodes"][name],
        motif_source["omitted_nodes"],
        motif_source["array_cohorts"],
        motif_issues,
    )
    assert f"mtDNA substitution {name}:" in _issues_text(motif_issues)

    topology_source = deepcopy(_MT_SOURCE)
    topology_source["nodes"][name]["source_topology"]["emitted_parent_source_node"] = (
        "wrong-parent"
    )
    topology_text = _issues_text(
        _validate_mt_registry_against_tree(topology_source, _index_mt_tree(build_mt_tree()))
    )
    assert f"Exact source topology for mtDNA node {name} names emitted-parent source" in (
        topology_text
    )

    coverage_source = deepcopy(_MT_SOURCE)
    coverage_source["nodes"][name]["emitted_snps"][0]["array_coverage"]["callable_snv_in"].append(
        "unknown-export"
    )
    coverage_issues: list[str] = []
    _mt_validate_exact_record(
        name,
        coverage_source["nodes"][name],
        coverage_source["omitted_nodes"],
        coverage_source["array_cohorts"],
        coverage_issues,
    )
    assert "callable-SNV exports outside its cohort" in _issues_text(coverage_issues)


def test_issue_1798_batch_13_u5_direct_motif_drift_fails_closed() -> None:
    """The markerless gateway remains locked to its literal Build 17 directions."""
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["U5"]["direct_source_motif"][0]["derived_allele"] = "A"

    text = _issues_text(_validate_mt_source(source, build_mt_tree()))
    assert "state_partition_sha256 does not match its registry projection" in text
    assert "state_partition_sha256 differs from the locked baseline" in text


@pytest.mark.parametrize("identity", list(BATCH13_FLATTENED_STEPS))
def test_issue_1798_batch_13_flattened_helpers_fail_closed(identity: str) -> None:
    """A final helper cannot drift in adjacency or silently become runtime evidence."""
    path_source = deepcopy(_MT_SOURCE)
    _category, _owner, path_step = _batch13_flattened_occurrences(path_source, identity)[0]
    path_step["source_parent"] = "wrong-parent"
    path_text = _issues_text(_validate_mt_source(path_source, build_mt_tree()))
    assert f"breaks adjacency at {identity}" in path_text

    emitted_source = deepcopy(_MT_SOURCE)
    _category, _owner, emitted_step = _batch13_flattened_occurrences(emitted_source, identity)[0]
    emitted_step["direct_source_motif"][0]["emitted"] = True
    emitted_step["direct_source_motif"][0].pop("omission_reason")
    emitted_text = _issues_text(_validate_mt_source(emitted_source, build_mt_tree()))
    assert "emitted markers do not match every source emission decision" in emitted_text
    assert "locked_exact_semantic_sha256 does not match its registry projection" in emitted_text


@pytest.mark.parametrize("name", list(BATCH13_OLD_MARKERS))
def test_issue_1798_batch_13_old_marker_sets_cannot_be_restored(name: str) -> None:
    """Shared, historical, and stale U5/U8 marker sets remain outside runtime."""
    tree = build_mt_tree()
    _index_mt_tree(tree).by_name[name].node["defining_snps"] = [
        {"rsid": f"i5{pos:06d}", "pos": pos, "allele": allele}
        for pos, allele in BATCH13_OLD_MARKERS[name]
    ]

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "mtDNA emitted tree differs from its live locked fingerprint" in text
    expected = (
        "Structural mtDNA pass-through U5 must be markerless"
        if name == "U5"
        else f"Marker-exact mtDNA node {name} has markers"
    )
    assert expected in text


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        (
            [*PRIMARY_EXPORTS, "pgp_ancestry_4190"],
            "includes the Ancestry comparator",
        ),
        ([*PRIMARY_EXPORTS, "pgp_4139"], "repeats an export"),
        ([*PRIMARY_EXPORTS, "unknown-export"], "names an unknown export"),
    ],
)
def test_cohort_membership_rejects_ancestry_duplicates_and_unknown_exports(
    members: list[str], expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    source["array_cohorts"]["primary_four_23andme"]["export_ids"] = members

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_marker_coverage_rejects_outside_and_callable_beyond_present_members() -> None:
    outside = deepcopy(_MT_SOURCE)
    outside["nodes"]["K1b"]["emitted_snps"][0]["array_coverage"]["position_present_in"].append(
        "pgp_1050"
    )
    assert "position-present exports outside its cohort" in _issues_text(
        _validate_mt_source_schema(outside)
    )

    callable_absent = deepcopy(_MT_SOURCE)
    callable_absent["nodes"]["K1b"]["emitted_snps"][0]["array_coverage"]["callable_snv_in"].append(
        "pgp_4162"
    )
    assert "callable where its position is absent" in _issues_text(
        _validate_mt_source_schema(callable_absent)
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("type", "omitted source node CZ has an invalid omission type"),
        ("reason", "omitted source node CZ has no reason"),
        ("overlap", "source nodes are both omitted and emitted: G"),
    ],
)
def test_omissions_require_typed_reasons_and_cannot_overlap_emitted_nodes(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "type":
        source["omitted_nodes"]["CZ"]["type"] = "hand_waved"
    elif mutation == "reason":
        source["omitted_nodes"]["CZ"]["reason"] = " "
    else:
        source["omitted_nodes"]["G"] = {
            "type": "unreportable_source_node",
            "reason": "test-only overlap",
        }

    assert expected in _issues_text(_validate_mt_source_schema(source))


def _a4_retirement_tombstone() -> dict[str, Any]:
    return {
        "type": "retired_unmapped_emitted_node",
        "former_emitted_parent": "A",
        "former_defining_snps": [
            {"rsid": "i5009347", "pos": 9347, "allele": "G"},
            {"rsid": "i5014308", "pos": 14308, "allele": "A"},
        ],
        "reason": (
            "Retired in issue #1798 batch 06 because emitted A4 has no exact PhyloTree "
            "Build 17 source identity."
        ),
    }


def test_retired_node_accepts_exact_tombstone_with_nonempty_former_markers() -> None:
    tombstone = _MT_SOURCE["retired_emitted_nodes"]["A4"]

    assert tombstone == _a4_retirement_tombstone()
    assert tombstone["former_defining_snps"]
    assert all(
        set(marker) == {"rsid", "pos", "allele"} for marker in tombstone["former_defining_snps"]
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("tombstone-fields", "Retired mtDNA node A4 has invalid fields"),
        ("missing-fields", "Retired mtDNA node A4 has invalid fields"),
        ("type", "Retired mtDNA node A4 has an invalid retirement type"),
        ("parent", "Retired mtDNA node A4 has an invalid former emitted parent"),
        ("reason", "Retired mtDNA node A4 has no reason"),
        ("empty-markers", "Retired mtDNA node A4 has no former defining markers"),
        ("non-object-marker", "Retired mtDNA node A4 has a non-object former marker"),
        ("marker-fields", "Retired mtDNA node A4 has a former marker with invalid fields"),
        ("rsid", "Retired mtDNA node A4 has an invalid former marker identifier"),
        ("position", "Retired mtDNA node A4 has an invalid former marker position"),
        ("boolean-position", "Retired mtDNA node A4 has an invalid former marker position"),
        ("allele", "Retired mtDNA node A4 has an invalid former marker allele"),
        ("duplicate-rsid", "Retired mtDNA node A4 has an invalid former marker identifier"),
        ("duplicate-position", "Retired mtDNA node A4 has an invalid former marker position"),
    ],
)
def test_retired_node_rejects_malformed_tombstone(mutation: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    tombstone = source["retired_emitted_nodes"]["A4"]
    markers = tombstone["former_defining_snps"]
    if mutation == "tombstone-fields":
        tombstone["unreviewed_field"] = True
    elif mutation == "missing-fields":
        tombstone.pop("former_emitted_parent")
    elif mutation == "type":
        tombstone["type"] = "retired_without_review"
    elif mutation == "parent":
        tombstone["former_emitted_parent"] = "A4"
    elif mutation == "reason":
        tombstone["reason"] = " "
    elif mutation == "empty-markers":
        tombstone["former_defining_snps"] = []
    elif mutation == "non-object-marker":
        markers.append("not-a-marker")
    elif mutation == "marker-fields":
        markers[0]["motif_owner"] = "A4"
    elif mutation == "rsid":
        markers[0]["rsid"] = " "
    elif mutation == "position":
        markers[0]["pos"] = 0
    elif mutation == "boolean-position":
        markers[0]["pos"] = True
    elif mutation == "allele":
        markers[0]["allele"] = "N"
    elif mutation == "duplicate-rsid":
        markers[1]["rsid"] = markers[0]["rsid"]
    else:
        markers[1]["pos"] = markers[0]["pos"]

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            "parent",
            "Retired mtDNA node A4 former emitted parent differs from its locked historical "
            "baseline",
        ),
        (
            "markers",
            "Retired mtDNA node A4 former defining markers differ from its locked historical "
            "baseline",
        ),
    ],
)
def test_retired_node_must_preserve_locked_historical_record(mutation: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    tombstone = source["retired_emitted_nodes"]["A4"]
    if mutation == "parent":
        tombstone["former_emitted_parent"] = "never-existed"
    else:
        tombstone["former_defining_snps"][0] = {
            "rsid": "fabricated-marker",
            "pos": 9348,
            "allele": "G",
        }

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("name", "tombstone"),
    [
        ("", _a4_retirement_tombstone()),
        ("A4", "not-a-tombstone"),
    ],
)
def test_retired_node_requires_named_object_tombstone(name: str, tombstone: Any) -> None:
    source = deepcopy(_MT_SOURCE)
    source["retired_emitted_nodes"] = {name: tombstone}

    assert "has no typed tombstone" in _issues_text(_validate_mt_source_schema(source))


def test_retired_node_cannot_overlap_current_or_omitted_state() -> None:
    current_overlap = deepcopy(_MT_SOURCE)
    current_overlap["pending_nodes"]["A4"] = {"emitted_parent": "A"}
    assert "retired-emitted state overlaps current states: A4" in _issues_text(
        _validate_mt_source_schema(current_overlap)
    )

    omitted_overlap = deepcopy(_MT_SOURCE)
    omitted_overlap["omitted_nodes"]["A4"] = {
        "type": "unreportable_source_node",
        "reason": "Test-only source omission must remain distinct from emitted retirement.",
    }
    assert "retired-emitted state overlaps omitted source nodes: A4" in _issues_text(
        _validate_mt_source_schema(omitted_overlap)
    )


def test_retired_node_must_come_from_initial_emitted_pending_frontier() -> None:
    source = deepcopy(_MT_SOURCE)
    source["retired_emitted_nodes"]["never-emitted"] = _a4_retirement_tombstone()

    assert (
        "retired emitted nodes were not in the initial pending frontier: never-emitted"
        in _issues_text(_validate_mt_source_schema(source))
    )
    assert "Retired mtDNA node never-emitted has no locked historical baseline" in _issues_text(
        _validate_mt_source_schema(source)
    )


def test_omitted_source_node_cannot_dispose_of_initial_emitted_frontier() -> None:
    source = deepcopy(_MT_SOURCE)
    source["retired_emitted_nodes"].pop("A4")
    source["omitted_nodes"]["A4"] = {
        "type": "unreportable_source_node",
        "reason": "Test-only source omission cannot retire a formerly emitted identity.",
    }

    assert "initial pending frontier contains nodes with no current disposition" in _issues_text(
        _validate_mt_source_schema(source)
    )


def test_retired_node_is_rejected_while_it_is_still_emitted() -> None:
    tree = build_mt_tree()
    _find_node(tree, "A")["children"].append(
        {
            "haplogroup": "A4",
            "defining_snps": deepcopy(
                _MT_SOURCE["retired_emitted_nodes"]["A4"]["former_defining_snps"]
            ),
            "children": [],
        }
    )

    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Retired mtDNA nodes are still emitted in the tree: A4" in text
    assert "provenance partition differs from the emitted tree; missing=['A4']" in text


def test_live_a4_removal_is_covered_by_its_exact_retirement_tombstone() -> None:
    tree = build_mt_tree()

    assert "A4" not in _index_mt_tree(tree).by_name
    assert _MT_SOURCE["retired_emitted_nodes"] == {"A4": _a4_retirement_tombstone()}
    assert _validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)) == []


def test_retired_node_nonblank_reason_drift_fails_the_live_partition_lock() -> None:
    source = deepcopy(_MT_SOURCE)
    source["retired_emitted_nodes"]["A4"]["reason"] = "Altered but nonblank reason."

    text = _issues_text(_validate_mt_source_schema(source))
    assert "state_partition_sha256 does not match its registry projection" in text
    assert "state_partition_sha256 differs from the locked baseline" in text


def test_migration_status_cannot_remain_complete_when_pending_state_returns() -> None:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"]["K"] = {"emitted_parent": "U8b"}

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "migration status must be 'in_progress' for its live provenance state" in text


def _completion_tree(*children: str) -> dict[str, Any]:
    return {
        "haplogroup": "mt-MRCA",
        "defining_snps": [],
        "children": [
            {
                "haplogroup": name,
                "defining_snps": [{"rsid": f"test-{name}", "pos": pos, "allele": "G"}],
            }
            for pos, name in enumerate(children or ("child",), start=1)
        ],
    }


def _completion_source(*children: str) -> dict[str, Any]:
    names = list(children or ("child",))
    return {
        "pending_nodes": {},
        "nodes": {
            name: {
                "source_topology": {
                    "status": "exact",
                    "flattened_source_path": [],
                }
            }
            for name in names
        },
        "structural_exceptions": {
            "mt-MRCA": {
                "type": "root",
                "emitted_parent": None,
                "source_status": "synthetic",
                "source_topology_anchor": "mt-MRCA",
            }
        },
        "direct_source_motif_states": {
            "exact_nodes": names,
            "legacy_partial_nodes": [],
        },
        "omitted_nodes": {},
        "retired_emitted_nodes": {},
    }


def test_migration_completion_requires_every_direct_source_motif_to_be_exact() -> None:
    inventory = _index_mt_tree(_completion_tree())
    source = _completion_source()
    source["direct_source_motif_states"] = {
        "exact_nodes": [],
        "legacy_partial_nodes": ["child"],
    }

    assert not _mt_migration_complete_ready(source, inventory)
    source["direct_source_motif_states"] = {
        "exact_nodes": ["child"],
        "legacy_partial_nodes": [],
    }
    assert _mt_migration_complete_ready(source, inventory)
    source["structural_exceptions"]["mt-MRCA"]["source_topology_anchor"] = "wrong-root"
    assert not _mt_migration_complete_ready(source, inventory)


def test_migration_completion_rejects_only_retired_identities_still_live() -> None:
    inventory = _index_mt_tree(_completion_tree())
    source = _completion_source()
    source["retired_emitted_nodes"]["formerly-emitted"] = {}

    assert _mt_migration_complete_ready(source, inventory)
    source["retired_emitted_nodes"]["child"] = {}
    assert not _mt_migration_complete_ready(source, inventory)


@pytest.mark.parametrize(
    "omission_type",
    [
        "flattened_source_intermediate",
        "flattened_unreportable_source_intermediate",
    ],
)
def test_migration_completion_requires_flattened_omissions_on_an_exact_path(
    omission_type: str,
) -> None:
    inventory = _index_mt_tree(_completion_tree())
    source = _completion_source()
    source["omitted_nodes"]["middle"] = {
        "type": omission_type,
        "reason": "test-only flattened source intermediate",
    }

    assert not _mt_migration_complete_ready(source, inventory)
    source["nodes"]["child"]["source_topology"]["flattened_source_path"] = [
        {"source_node": "middle"}
    ]
    assert _mt_migration_complete_ready(source, inventory)


def test_migration_completion_ignores_ordinary_unreportable_omissions() -> None:
    source = _completion_source()
    source["omitted_nodes"]["pruned"] = {
        "type": "unreportable_source_node",
        "reason": "test-only ordinary omission",
    }

    assert _mt_migration_complete_ready(source, _index_mt_tree(_completion_tree()))


@pytest.mark.parametrize(
    "omission_type",
    [
        "flattened_source_intermediate",
        "flattened_unreportable_source_intermediate",
    ],
)
def test_migration_completion_allows_consistently_shared_flattened_reference(
    omission_type: str,
) -> None:
    shared_step = {
        "source_node": "middle",
        "source_parent": "mt-MRCA",
        "reason": "test-only shared flattened source intermediate",
        "direct_source_motif": [],
    }
    source = _completion_source("left", "right")
    for record in source["nodes"].values():
        record["source_topology"]["flattened_source_path"] = [deepcopy(shared_step)]
    source["omitted_nodes"]["middle"] = {
        "type": omission_type,
        "reason": shared_step["reason"],
    }

    assert _mt_migration_complete_ready(source, _index_mt_tree(_completion_tree("left", "right")))


@pytest.mark.parametrize(
    "omission_type",
    [
        "flattened_source_intermediate",
        "flattened_unreportable_source_intermediate",
    ],
)
def test_claimed_complete_migration_rejects_orphan_flattened_omission(
    omission_type: str,
) -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["status"] = "complete"
    source["omitted_nodes"]["test-orphan"] = {
        "type": omission_type,
        "reason": "test-only orphan flattened source identity",
    }

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert (
        "flattened source intermediates that are not referenced by an exact flattened path: "
        "test-orphan" in text
    )


def test_complete_migration_retains_reviewed_cz_flattening() -> None:
    inventory = _index_mt_tree(build_mt_tree())

    assert _MT_SOURCE["migration"]["status"] == "complete"
    assert _MT_SOURCE["omitted_nodes"]["CZ"]["type"] == (
        "flattened_unreportable_source_intermediate"
    )
    assert _mt_migration_complete_ready(_MT_SOURCE, inventory)
    assert _validate_mt_registry_against_tree(_MT_SOURCE, inventory) == []


def test_reintroducing_pending_state_after_completion_fails_closed() -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"].pop("U5a")
    source["direct_source_motif_states"]["exact_nodes"].remove("U5a")
    source["pending_nodes"]["U5a"] = {"emitted_parent": "U5"}
    source["migration"]["status"] = "in_progress"

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "locked exact frontier does not equal the live marker-exact nodes" in schema_text
    assert "state_partition_sha256 does not match its registry projection" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "parent whose source identity is still pending" in registry_text


def test_source_aware_recurrence_allows_same_direction_for_distinct_exact_owners() -> None:
    m8a_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["M8a"]["emitted_snps"] if marker["pos"] == 14470
    )
    x_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["X"]["emitted_snps"] if marker["pos"] == 14470
    )

    assert m8a_marker["rsid"] == x_marker["rsid"] == "i5014470"
    assert m8a_marker["motif_owner"] == "M8a"
    assert x_marker["motif_owner"] == "X"
    assert {"M8a", "X"} <= set(DIRECT_MOTIF_EXACT_NODES)
    assert all(
        any(
            mutation["notation"] == "T14470C"
            for mutation in _MT_SOURCE["nodes"][owner]["direct_source_motif"]
        )
        for owner in ("M8a", "X")
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("motif_owner", "M8a", "outside motif owner 'M8a'"),
        ("allele", "A", "does not match its source mutation direction"),
    ],
)
def test_recurrent_marker_rejects_wrong_owner_and_direction(
    field: str, value: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    marker = next(item for item in source["nodes"]["X"]["emitted_snps"] if item["pos"] == 14470)
    marker[field] = value

    assert expected in _issues_text(_validate_mt_source_schema(source))


def _flattened_g1_source() -> dict[str, Any]:
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"]["G1"]
    flattened_mutation = record["direct_source_motif"].pop(0)
    record["emitted_snps"][0]["motif_owner"] = "G-flat"
    reason = "test-only source intermediate omitted from the emitted tree"
    source["omitted_nodes"]["G-flat"] = {
        "type": "flattened_source_intermediate",
        "reason": reason,
    }
    record["source_topology"] = {
        "status": "exact",
        "emitted_parent_source_node": "G",
        "source_parent": "G-flat",
        "flattened_source_path": [
            {
                "source_node": "G-flat",
                "source_parent": "G",
                "reason": reason,
                "direct_source_motif": [flattened_mutation],
            }
        ],
    }
    return source


def test_flattened_source_path_accepts_ordered_adjacency_and_marker_ownership() -> None:
    source = _flattened_g1_source()
    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        source["nodes"]["G1"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert issues == []
    # The topology-only registry guard can validate this state without changing
    # the locked emitted-tree fingerprint; schema digest locks intentionally remain.
    assert _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree())) == []


def test_flattened_unreportable_source_path_rejects_emitted_source_decision() -> None:
    source = _flattened_g1_source()
    source["omitted_nodes"]["G-flat"]["type"] = "flattened_unreportable_source_intermediate"
    issues: list[str] = []

    _mt_validate_exact_record(
        "G1",
        source["nodes"]["G1"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert (
        "Flattened-unreportable mtDNA source node G-flat has an emitted source decision" in issues
    )


def test_general_flattened_source_path_can_omit_every_owned_decision() -> None:
    source = _flattened_g1_source()
    record = source["nodes"]["G1"]
    flattened_mutation = record["source_topology"]["flattened_source_path"][0][
        "direct_source_motif"
    ][0]
    flattened_mutation["emitted"] = False
    flattened_mutation["omission_reason"] = "Test-only explicit non-emission policy."
    record["emitted_snps"] = [marker for marker in record["emitted_snps"] if marker["pos"] != 8200]
    issues: list[str] = []

    _mt_validate_exact_record(
        "G1",
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert issues == []


@pytest.mark.parametrize(
    "omission_type",
    [
        "flattened_source_intermediate",
        "flattened_unreportable_source_intermediate",
    ],
)
def test_flattened_identity_cannot_also_be_an_emitted_direct_source(
    omission_type: str,
) -> None:
    source = _flattened_g1_source()
    source["omitted_nodes"]["G-flat"]["type"] = omission_type
    source["nodes"]["G2"]["source_node"] = "G-flat"

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "exact direct source nodes are also globally omitted: G-flat" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "flattens the direct source node G-flat of another emitted record" in registry_text


@pytest.mark.parametrize("mutation", ["source_parent", "reason", "direct_source_motif"])
def test_shared_flattened_identity_requires_identical_path_provenance(mutation: str) -> None:
    source = _flattened_g1_source()
    shared_step = deepcopy(source["nodes"]["G1"]["source_topology"]["flattened_source_path"][0])
    source["nodes"]["G2"]["source_topology"] = {
        "status": "exact",
        "emitted_parent_source_node": "G",
        "source_parent": "G-flat",
        "flattened_source_path": [shared_step],
    }
    if mutation == "source_parent":
        shared_step["source_parent"] = "wrong-parent"
    elif mutation == "reason":
        shared_step["reason"] = "A conflicting shared-path reason."
    else:
        shared_step["direct_source_motif"][0]["notation"] = "T8200A"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Flattened mtDNA source node G-flat has inconsistent provenance" in text


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("topology", "source topology for mtDNA node G1 has invalid fields"),
        ("path", "path step 0 has invalid fields"),
    ],
)
def test_unknown_exact_topology_fields_fail_closed(target: str, expected: str) -> None:
    source = _flattened_g1_source()
    topology = source["nodes"]["G1"]["source_topology"]
    if target == "topology":
        topology["unreviewed_field"] = True
    else:
        topology["flattened_source_path"][0]["unreviewed_field"] = True

    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        source["nodes"]["G1"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )
    assert expected in _issues_text(issues)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("adjacency", "breaks adjacency at G-flat"),
        ("owner", "outside motif owner 'not-on-path'"),
        ("direction", "does not match its source mutation direction"),
    ],
)
def test_flattened_source_path_rejects_bad_adjacency_owner_and_direction(
    mutation: str, expected: str
) -> None:
    source = _flattened_g1_source()
    record = source["nodes"]["G1"]
    if mutation == "adjacency":
        record["source_topology"]["flattened_source_path"][0]["source_parent"] = "wrong-parent"
    elif mutation == "owner":
        record["emitted_snps"][0]["motif_owner"] = "not-on-path"
    else:
        record["emitted_snps"][0]["allele"] = "A"

    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )
    assert expected in _issues_text(issues)


def test_derived_provenance_metadata_and_bundle_compatibility_are_exact() -> None:
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)
    summary = _summarize_mt_provenance(_MT_SOURCE, inventory)

    assert summary["migration_status"] == "complete"
    assert summary["emitted_nodes"] == 193
    assert summary["marker_bearing_nodes"] == 186
    assert summary["marker_exact_nodes"]["count"] == 186
    assert summary["direct_source_motif_nodes"] == {
        "exact": {"count": 186, "names": DIRECT_MOTIF_EXACT_NODES},
        "legacy_partial": {"count": 0, "names": DIRECT_MOTIF_LEGACY_PARTIAL_NODES},
    }
    assert (
        "All 186 marker-bearing emitted nodes have exact direct motifs"
        in (_MT_SOURCE["audit_scope"])
    )
    assert "No pending or legacy-partial live record remains" in _MT_SOURCE["audit_scope"]
    assert summary["structural_nodes"] == {
        "count": 7,
        "names": ["B", "H2a2", "H5", "HV", "R0", "U5", "mt-MRCA"],
    }
    assert summary["pending_nodes"] == {"count": 0, "names": []}
    assert summary["retired_emitted_nodes"] == {"count": 1, "names": ["A4"]}
    assert summary["marker_records"] == {
        "emitted": 634,
        "marker_exact": 634,
        "marker_exact_by_cohort": {
            "historical_five_23andme_including_2014": 15,
            "primary_four_23andme": 619,
        },
    }
    assert summary["source_mutation_decisions"] == {
        "total": 1016,
        "emitted": 634,
        "omitted": 382,
        "direct_motif_exact": 839,
        "direct_motif_legacy_partial": 0,
        "recurrent_or_uncertain_events": 17,
        "reversion_events": 84,
        "reversion_marks": 88,
    }
    assert summary["emitted_parent_edges"] == {
        "total": 192,
        "validated_declarations": 192,
    }
    assert summary["source_parent_edges"] == {"validated": 192, "pending": 0}
    assert summary["omitted_source_nodes"] == {
        "count": 72,
        "names": [
            "A+152",
            "A+152+16362",
            "B4+16261",
            "B4b'd'e'j",
            "CZ",
            "D+16189",
            "D4b1",
            "D4b1c",
            "D4e",
            "D4e1",
            "D4e1'3",
            "F1+16189",
            "F1a'c'f",
            "G2a'c",
            "H+195",
            "H1+16189",
            "H5'36",
            "HV0a",
            "J1+16193",
            "K1c",
            "L0a'b'f'g",
            "L0a'b'f'g'k",
            "L0a'b'g",
            "L0a'g",
            "L0a1'4",
            "L0d1'2",
            "L1'2'3'4'5'6",
            "L1b2'3",
            "L1c1'2'4'5'6",
            "L1c1'2'4'6",
            "L1c2'4",
            "L2'3'4'5'6",
            "L2'3'4'6",
            "L2a'b'c'd",
            "L2a1'2'3'4",
            "L2a2'3",
            "L2a2'3'4",
            "L2b'c",
            "L2b'c'd",
            "L3'4",
            "L3'4'6",
            "L3b'f",
            "L3c'd",
            "L3e'i'k'x",
            "M1'20'51",
            "M12'G",
            "M7b'c",
            "M80'D",
            "N1'5",
            "N1a1",
            "N1a1'2",
            "N1a1b",
            "N2",
            "R+16189",
            "R2'JT",
            "R9",
            "T2+150",
            "T2+16189",
            "U2'3'4'7'8'9",
            "U2+152",
            "U3a'c",
            "U4'9",
            "U5a'b",
            "U6a'b'd",
            "U8b'c",
            "W+194",
            "X1'2'3",
            "X1'3",
            "X2+225",
            "X2a'j",
            "X2b'd",
            "Z+152",
        ],
        "by_type": {
            "flattened_source_intermediate": 60,
            "flattened_unreportable_source_intermediate": 11,
            "unreportable_source_node": 1,
        },
    }
    assert summary["arrays"] == {"exports": 6, "cohorts": 2}
    assert summary["locked_exact_frontier"] == {
        "count": 186,
        "sha256": LOCKED_EXACT_NAMES_SHA256,
    }
    assert summary["locked_direct_motif_frontier"] == {
        "count": 186,
        "sha256": LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256,
    }
    assert summary["digests"]["baseline_snapshot_sha256"] == BASELINE_SNAPSHOT_SHA256
    assert summary["digests"]["baseline_emitted_tree_sha256"] == (BASELINE_EMITTED_TREE_SHA256)
    assert summary["digests"]["locked_emitted_tree_sha256"] == LOCKED_EMITTED_TREE_SHA256

    bundle = build_bundle()
    mt_audit = bundle["sources"]["mt"]["audit"]
    assert bundle["version"] == "1.1.30"
    assert bundle["stats"]["mt_haplogroups"] == 193
    assert bundle["stats"]["mt_defining_snps"] == 634
    assert bundle["stats"]["mt_unique_snps"] == 515
    assert bundle["stats"]["total_defining_snps"] == 788
    assert bundle["stats"]["total_unique_snps"] == 669
    assert mt_audit["schema_version"] == 3
    assert mt_audit["audited_nodes"] == sorted(_MT_SOURCE["nodes"])
    assert mt_audit["omitted_nodes"] == {
        name: record["reason"] for name, record in sorted(_MT_SOURCE["omitted_nodes"].items())
    }
    assert mt_audit["retired_emitted_nodes"] == _MT_SOURCE["retired_emitted_nodes"]
    assert mt_audit["provenance"] == summary
    assert bundle["trees"]["mt"] == tree
