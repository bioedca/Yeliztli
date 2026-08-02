# Query log — issue #2164

All requests used public, sanitized inputs on 2026-08-02.

| Source | Request | Result |
| --- | --- | --- |
| Consensus | `protein disulfide bond connectivity paired cysteine residues nonadjacent endpoints` | Returned a broad consistency result. It was not retained as evidence and was not used for any claim or implementation decision. |
| Scite | DOI lookup for `10.4137/EBO.S25349` and `10.1371/journal.pone.0112883` | Endpoint returned `INVALID_ARGUMENT` for both valid DOI requests; no Scite result was used. |
| UniProt REST | `GET /uniprotkb/P01308?format=json` | Saved the reviewed P01308 JSON response as `uniprot-p01308.json`; entry version 283; `EXACT` `Disulfide bond` locations are 31–96, 43–109, and 95–100. |
| PubMed E-utilities | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=26523116&retmode=xml` | Saved the sanitized citation-metadata derivative `pubmed-26523116.xml`; it identifies DOI `10.4137/EBO.S25349` and contains no correction/retraction relation in the fetched metadata. See `pubmed-26523116-sanitization.md` for request, DTD, hashes, removals, and reuse boundary. |
| Context7 | Three EBI Nightingale library-resolution attempts | Returned only an unrelated `/ccfos/nightingale` package. No Context7 documentation was used; the pinned official upstream source was inspected instead. |
| Nightingale upstream | [`config.ts`](https://github.com/ebi-webcomponents/nightingale/blob/a4a65eccbf03fe5290adb1ec171cb5a43e8a3d83/packages/nightingale-track/src/config.ts#L69-L75) and [`FeatureShape.ts`](https://github.com/ebi-webcomponents/nightingale/blob/a4a65eccbf03fe5290adb1ec171cb5a43e8a3d83/packages/nightingale-track/src/FeatureShape.ts#L101-L135) | `DISULFID` maps to `bridge`; `bridge` is marked non-continuous by the renderer. |
