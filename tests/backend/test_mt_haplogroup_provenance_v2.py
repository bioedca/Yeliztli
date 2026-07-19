"""Fail-closed tests for the schema-v3 mtDNA provenance frontier."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
LOCKED_EXACT_NAMES_SHA256 = "639d66e7a6dd546ea22d325634f8d145100a3d660960307da04f1ffd170b51ab"
LOCKED_EXACT_SEMANTIC_SHA256 = "cd606fbe34d01f98ffa57607d911ff99352da5454eb5c3555fd13ed489a343ac"
LOCKED_EXACT_COVERAGE_SHA256 = "a2b304ab71d0608d6bef05fa704af2486ede36c380d33c71a833a89fbfd0735c"
BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "0dc2cc812e511bc89b76fca6ed13614d8ddb75a6ebe6321bde670096c44fba61"
)
LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "f6b1e9c729805aa912d763a37632c7a9e19032dfec01750463f35597a2f5d194"
)
BASELINE_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256 = (
    "ecc1dbf4c93872031e102ee166eac50e31d6468395e5d0053357af44f8a9785a"
)
LOCKED_DIRECT_MOTIF_EXACT_SEMANTIC_SHA256 = (
    "57dc1a9989215c6252350eb3460987a7c78185fec776adb8df99e01fafb717ae"
)
INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256 = (
    "7b4848980e34ca1eff9739f964906d68eb4acdbbcd5e93227e17ece79296aefb"
)
INITIAL_PENDING_NAMES_SHA256 = "996c2c96c22d37a2aa7edf1f4639d626ccc5199ecc5eb35984aa84204e05a591"
ARRAY_MANIFEST_SHA256 = "42de22517a4644884596e36b0499a4fc45f264986c63f6fb239452b88719f977"
SOURCE_METADATA_SHA256 = "5b3a3578fc208c91f6c3fdcc6d772f5071851b3604762b9e81994cf2632deb3d"
STATE_PARTITION_SHA256 = "75366394e97c1cab9911865bbffdf55af7e4b03470c0c61dff5e037c73ab3aeb"
BASELINE_EMITTED_TREE_SHA256 = "02a40be2096dd8c60e6e2934ba68a813f07478117a749e60e94e0608bed21914"
LOCKED_EMITTED_TREE_SHA256 = "f824922f37bfabcf8a34181041ee9df86772cc09c10f380647d4e266027be608"

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
    "C",
    "D",
    "D1",
    "D2",
    "D3",
    "D4",
    "D4a",
    "D4b",
    "D5",
    "G",
    "G1",
    "G2",
    "H1",
    "H10",
    "H13",
    "H13a",
    "H1a",
    "H6a",
    "I",
    "J1d",
    "K1b",
    "K2",
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
    "M8",
    "M8a",
    "M9",
    "N",
    "N1",
    "N1a",
    "N9",
    "S",
    "S1",
    "S2",
    "T2a",
    "U2",
    "U3a",
    "U3b",
    "U5b2",
    "W",
    "W1",
    "W3",
    "X",
    "X2b",
    "Z",
    "Z1",
]
DIRECT_MOTIF_LEGACY_PARTIAL_NODES = [
    "H6",
    "K",
    "K1",
    "K1a",
    "K2a",
    "U2e",
    "X2",
    "X2a",
    "Y1",
    "Y2",
    "Y_mt",
]

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
        "bf5db784480ba53cc0d724b33c947e46c0f577ca663416865bfea16cbe4e7c4e",
        "9f856f7081a09155950c2be11e9546dc711500ad122b19ab13cee4a1ea54a7bd",
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
        projection.append(
            {
                "node": node["haplogroup"],
                "parent": parent,
                "defining_snps": node.get("defining_snps", []),
            }
        )
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

    assert len(inventory.occurrences) == 194
    assert len(inventory.by_name) == 194
    assert not inventory.duplicates
    assert len(inventory.marker_bearing_names) == 192
    assert len(inventory.markerless_names) == 2
    assert inventory.marker_count == 580
    assert inventory.edge_count == 193
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["structural_exceptions"]) | set(
        _MT_SOURCE["pending_nodes"]
    ) == set(inventory.by_name)
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["pending_nodes"]) == set(
        inventory.marker_bearing_names
    )
    assert set(_MT_SOURCE["structural_exceptions"]) == set(inventory.markerless_names)
    assert _MT_SOURCE["schema_version"] == 3
    assert _MT_SOURCE["retired_emitted_nodes"] == {}
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
        ("missing", "missing=['A']"),
        ("overlap", "marker-exact and pending states overlap: G"),
        ("orphan", "extra=['not-an-emitted-node']"),
    ],
)
def test_partition_rejects_missing_overlap_and_orphan(mutation: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    inventory = _index_mt_tree(build_mt_tree())

    if mutation == "missing":
        source["pending_nodes"].pop("A")
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
        ("overlap", "exact and legacy-partial direct-source motif states overlap: H6"),
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
        states["exact_nodes"].append("H6")
        states["exact_nodes"].sort()
    elif mutation == "missing":
        states["legacy_partial_nodes"].remove("H6")
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
        ("pending_nodes", "A", "M", "Pending mtDNA node A declares parent 'M'"),
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
    assert _MT_SOURCE["structural_exceptions"] == {
        "mt-MRCA": {
            "type": "root",
            "emitted_parent": None,
            "source_status": "synthetic",
            "source_topology_anchor": "mt-MRCA",
            "reason": "Synthetic tree-walk root; it emits no defining marker.",
        },
        "R0": {
            "type": "markerless_passthrough",
            "emitted_parent": "R",
            "source_status": "pending",
            "reason": (
                "Retained to preserve the emitted tree path while its direct Build-17 "
                "motif and source topology remain pending; it emits no defining marker."
            ),
        },
    }

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
        ("G8701A", True, None),
        ("C9540T", True, None),
        (
            "G10398A",
            False,
            "omitted because downstream N lineages model reversions to m.10398G; "
            "emitting m.10398A on N would conflict before descendant traversal",
        ),
        ("C10873T", True, None),
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
            8701,
            "N",
            {
                "cohort_id": "historical_five_23andme_including_2014",
                "position_present_in": ["pgp_1050"],
                "callable_snv_in": ["pgp_1050"],
            },
        ),
        (
            9540,
            "N",
            {
                "cohort_id": "primary_four_23andme",
                "position_present_in": PRIMARY_EXPORTS,
                "callable_snv_in": PRIMARY_EXPORTS,
            },
        ),
        (
            10873,
            "N",
            {
                "cohort_id": "historical_five_23andme_including_2014",
                "position_present_in": ["pgp_1050"],
                "callable_snv_in": ["pgp_1050"],
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
        "source_topology": {"status": "pending"},
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
    """W/W3 retain their Build 17 motifs and the unreportable W+194 hop."""
    w = _MT_SOURCE["nodes"]["W"]
    assert w["source_node"] == "W"
    assert w["emitted_parent"] == "N"
    assert w["source_motif_status"] == "exact"
    assert w["source_topology"] == {"status": "pending"}
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
        "type": "flattened_unreportable_source_intermediate",
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
    a4 = _find_node(build_mt_tree(), "A4")
    return {
        "type": "retired_unmapped_emitted_node",
        "former_emitted_parent": "A",
        "former_defining_snps": [
            {key: marker[key] for key in ("rsid", "pos", "allele")}
            for marker in a4["defining_snps"]
        ],
        "reason": "Test-only retirement after finding no Build 17 source identity.",
    }


def _source_with_a4_retired() -> dict[str, Any]:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"].pop("A4")
    source["retired_emitted_nodes"]["A4"] = _a4_retirement_tombstone()
    return source


def test_retired_node_accepts_exact_tombstone_with_nonempty_former_markers() -> None:
    source = _source_with_a4_retired()
    tombstone = source["retired_emitted_nodes"]["A4"]

    assert set(tombstone) == {
        "type",
        "former_emitted_parent",
        "former_defining_snps",
        "reason",
    }
    assert tombstone["former_defining_snps"]
    assert all(
        set(marker) == {"rsid", "pos", "allele"} for marker in tombstone["former_defining_snps"]
    )
    assert _validate_mt_source_schema(source) == [
        "mtDNA migration state_partition_sha256 does not match its registry projection",
        "mtDNA migration state_partition_sha256 differs from the locked baseline",
    ]


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
    source = _source_with_a4_retired()
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
    source = _source_with_a4_retired()
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
    source["pending_nodes"].pop("A4")
    source["retired_emitted_nodes"] = {name: tombstone}

    assert "has no typed tombstone" in _issues_text(_validate_mt_source_schema(source))


def test_retired_node_cannot_overlap_current_or_omitted_state() -> None:
    current_overlap = deepcopy(_MT_SOURCE)
    current_overlap["retired_emitted_nodes"]["A4"] = _a4_retirement_tombstone()
    assert "retired-emitted state overlaps current states: A4" in _issues_text(
        _validate_mt_source_schema(current_overlap)
    )

    omitted_overlap = _source_with_a4_retired()
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
    source["pending_nodes"].pop("A4")
    source["omitted_nodes"]["A4"] = {
        "type": "unreportable_source_node",
        "reason": "Test-only source omission cannot retire a formerly emitted identity.",
    }

    assert "initial pending frontier contains nodes with no current disposition" in _issues_text(
        _validate_mt_source_schema(source)
    )


def test_retired_node_is_rejected_while_it_is_still_emitted() -> None:
    source = _source_with_a4_retired()

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Retired mtDNA nodes are still emitted in the tree: A4" in text
    assert "provenance partition differs from the emitted tree; missing=['A4']" in text


def test_future_a4_removal_is_covered_by_its_retirement_tombstone() -> None:
    source = _source_with_a4_retired()
    future_tree = build_mt_tree()
    a = _find_node(future_tree, "A")
    a["children"] = [child for child in a["children"] if child["haplogroup"] != "A4"]

    assert _validate_mt_registry_against_tree(source, _index_mt_tree(future_tree)) == [
        "mtDNA emitted tree differs from its live locked fingerprint",
        "mtDNA emitted tree differs from the review-locked live tree",
    ]


def test_migration_status_cannot_claim_complete_with_pending_nodes() -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["status"] = "complete"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "migration status must be 'in_progress'" in text


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
    source["omitted_nodes"]["CZ"]["type"] = omission_type

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert (
        "flattened source intermediates that are not referenced by an exact flattened path: CZ"
        in text
    )


def test_in_progress_migration_allows_predeclared_cz_flattening() -> None:
    assert _MT_SOURCE["migration"]["status"] == "in_progress"
    assert _MT_SOURCE["omitted_nodes"]["CZ"]["type"] == (
        "flattened_unreportable_source_intermediate"
    )
    assert _validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(build_mt_tree())) == []


def test_clearing_pending_map_without_migrating_nodes_fails_closed() -> None:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"].clear()
    source["migration"]["status"] = "in_progress"

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "initial pending frontier contains nodes with no current disposition" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "provenance partition differs from the emitted tree" in registry_text
    assert "marker-bearing nodes do not equal exact plus pending states" in registry_text


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

    assert summary["migration_status"] == "in_progress"
    assert summary["emitted_nodes"] == 194
    assert summary["marker_bearing_nodes"] == 192
    assert summary["marker_exact_nodes"]["count"] == 102
    assert summary["direct_source_motif_nodes"] == {
        "exact": {"count": 91, "names": DIRECT_MOTIF_EXACT_NODES},
        "legacy_partial": {
            "count": 11,
            "names": DIRECT_MOTIF_LEGACY_PARTIAL_NODES,
        },
    }
    marker_exact_count = summary["marker_exact_nodes"]["count"]
    direct_exact_count = summary["direct_source_motif_nodes"]["exact"]["count"]
    assert f"{marker_exact_count} marker-exact nodes" in _MT_SOURCE["audit_scope"]
    assert f"classifies {direct_exact_count} direct motifs as exact" in _MT_SOURCE["audit_scope"]
    assert summary["structural_nodes"] == {
        "count": 2,
        "names": ["R0", "mt-MRCA"],
    }
    assert summary["pending_nodes"]["count"] == 90
    assert summary["retired_emitted_nodes"] == {"count": 0, "names": []}
    assert summary["marker_records"] == {
        "emitted": 580,
        "marker_exact": 421,
        "marker_exact_by_cohort": {
            "historical_five_23andme_including_2014": 14,
            "primary_four_23andme": 407,
        },
    }
    assert summary["source_mutation_decisions"] == {
        "total": 682,
        "emitted": 421,
        "omitted": 261,
        "direct_motif_exact": 526,
        "direct_motif_legacy_partial": 35,
        "recurrent_or_uncertain_events": 5,
        "reversion_events": 48,
        "reversion_marks": 50,
    }
    assert summary["emitted_parent_edges"] == {
        "total": 193,
        "validated_declarations": 193,
    }
    assert summary["source_parent_edges"] == {"validated": 65, "pending": 128}
    assert summary["omitted_source_nodes"] == {
        "count": 40,
        "names": [
            "CZ",
            "D+16189",
            "D4b1",
            "D4b1c",
            "D4e",
            "D4e1",
            "D4e1'3",
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
            "M80'D",
            "N1'5",
            "N1a1",
            "N1a1'2",
            "N1a1b",
            "W+194",
        ],
        "by_type": {
            "flattened_source_intermediate": 33,
            "flattened_unreportable_source_intermediate": 6,
            "unreportable_source_node": 1,
        },
    }
    assert summary["arrays"] == {"exports": 6, "cohorts": 2}
    assert summary["locked_exact_frontier"] == {
        "count": 102,
        "sha256": LOCKED_EXACT_NAMES_SHA256,
    }
    assert summary["locked_direct_motif_frontier"] == {
        "count": 91,
        "sha256": LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256,
    }
    assert summary["digests"]["baseline_snapshot_sha256"] == BASELINE_SNAPSHOT_SHA256
    assert summary["digests"]["baseline_emitted_tree_sha256"] == (BASELINE_EMITTED_TREE_SHA256)
    assert summary["digests"]["locked_emitted_tree_sha256"] == LOCKED_EMITTED_TREE_SHA256

    bundle = build_bundle()
    mt_audit = bundle["sources"]["mt"]["audit"]
    assert bundle["version"] == "1.1.20"
    assert bundle["stats"]["mt_haplogroups"] == 194
    assert bundle["stats"]["mt_defining_snps"] == 580
    assert bundle["stats"]["mt_unique_snps"] == 472
    assert bundle["stats"]["total_defining_snps"] == 734
    assert bundle["stats"]["total_unique_snps"] == 626
    assert mt_audit["schema_version"] == 3
    assert mt_audit["audited_nodes"] == sorted(_MT_SOURCE["nodes"])
    assert mt_audit["omitted_nodes"] == {
        name: record["reason"] for name, record in sorted(_MT_SOURCE["omitted_nodes"].items())
    }
    assert mt_audit["retired_emitted_nodes"] == {}
    assert mt_audit["provenance"] == summary
    assert bundle["trees"]["mt"] == tree
