Perform literature research on this in vivo mouse hepatocyte Perturb-seq gene program and propose literature-supported functional modules suitable for downstream LLM annotation.

Program ID:
10

Program genes for research:
Rhbg, Lhpp, Gulo, Glul, Lect2, Oat, Slc22a1, Slc1a2, Cyp2e1, Slc13a3, Cyp1a2, Slco1b2, Gstm2, Tbx3, Stx6, Axin2, Slc22a3, Pon1, Ang, Rnase4, Nkd1, Cd44, Rasgef1a, Erich2, Brd7, Lgr5, Sp5, Rpain, Lpxn, Car2, Dgat2, Insig1, Scap, Trib1

Context terms for research:
Aging, MASLD/MASH/NAFLD, steatosis, lipid metabolism, insulin resistance, cellular stress/senescence, inflammation, and fibrosis.

Perturbation context (secondary):
Young activators: Insig1 (-1.775), Trib1 (-1.081), Fgfr4 (-0.765)
Young repressors: Dgat2 (+1.831), Scap (+1.215), Mlxipl (+1.113)
Aged activators: Insig1 (-0.698), Trib1 (-0.399), Tmprss6 (-0.176)
Aged repressors: Dgat2 (+0.359), Scap (+0.225), Mlxipl (+0.201)

Instructions:
Perform literature research and use the literature and your biological knowledge to first understand what liver/hepatocyte functions are represented across the program genes. Prefer direct hepatocyte, liver in vivo, or liver disease evidence with PMIDs, but do not ignore coherent pathway-level biology when direct gene-specific liver literature is sparse; label weaker support as indirect or inferential.

After considering the evidence across the whole gene set, identify the strongest recurring biological themes, ranked from strongest to weakest. Modules should be coherent hepatocyte functions supported by multiple genes, not labels driven by one or two famous genes. Return the top 1-3 strongest themes as modules. Assign genes only after choosing the modules; order supporting_genes from strongest/most defining support to weaker or more inferential support.

Use perturbation regulators only when they reinforce or prioritize the same broader module biology. Mention young-vs-aged differences in regulators when there's clear evidence. Use disease or aging terms only when they are directly relevant to the shared hepatocyte function.

For each module, briefly distinguish direct PMID-supported evidence from weaker pathway-level or inferential support. Put genes with unclear fit, very limited interpretable biology, or only generic/non-liver evidence in genes_with_limited_hepatocyte_literature.

Include claim-relevant PMIDs for each module, ranked by importance.

Return download link to the JSON file only:

{
  "program_id": 10,
  "functional_modules": [
    {
      "module_name": "",
      "supporting_genes": [],
      "supporting_pmids": [],
      "evidence_type": "direct hepatocyte|direct liver disease|mixed|indirect",
      "literature_summary": "2-4 sentences. Explain why this is one of the strongest themes, which genes drive it, which genes are weaker or inferential support, and whether perturbation/context evidence reinforces it."
    }
  ],
  "genes_with_limited_hepatocyte_literature": []
}
