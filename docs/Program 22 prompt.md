## Program annotation task

You are a hepatocyte biologist. Interpret Program 22, a consensus gene expression program in mouse hepatocytes, in the context of aging and MASLD.

### Task
- Provide a specific, evidence-anchored interpretation of Program 22.
- Primary evidence: program genes and research-evidence modules
- Supporting evidence: top KEGG/GO enrichment, regulator perturbation evidence, gene summaries, cell-type enrichment, and hepatocyte, liver, aging, MASLD context.
- Refine final module labels, boundaries, and gene membership using all supplied evidence, with primary evidence carrying the most weight.

### Primary evidence
#### Program genes

Top-loading genes:
Intu, Twist1, Me1, Pklr, Lias, Gstm7, Aldh2, Mt2, Cyb5r3, Acly, Ugdh, Gstp1, Mt1, Zfp729b, Khk, Fasn, Stap1, Qdpr, Gstp3, Aldh1a7

Unique genes:
Zfp709, Ampd2, Sema3e, Utp3, Nob1, Lama1, Bahcc1, Phlda3, Vps36, Ccng1

#### Research-evidence modules

Use these as high-priority evidence, not fixed final module boundaries. Each module carries a verification status (supported/partial/unsupported).

Module 1: De novo lipogenesis and fatty acid synthesis
- Supporting genes: Fasn, Acly, Me1, Pklr, Khk, Ugdh
- Supporting evidence (PMID/DOI): PMID:37055547, DOI:10.1038/s41574-023-00809-4, PMID:35675800, DOI:10.1016/j.cmet.2022.05.004, PMID:16885160, DOI:10.1074/jbc.m601576200, PMID:37681411, DOI:10.1172/jci.insight.161282, PMID:36822479, DOI:10.1016/j.jhep.2023.02.010, PMID:37230214, DOI:10.1016/j.metabol.2023.155591, PMID:39103107, DOI:10.1016/j.jnutbio.2024.109717, PMID:32461046, DOI:10.1016/j.cca.2020.05.042, PMID:32749667, DOI:10.1002/1873-3468.13895, PMID:34981123, DOI:10.1042/bsr20212248, PMID:32214246, DOI:10.1038/s41586-020-2101-7, PMID:31550253, DOI:10.1371/journal.pone.0222558
- Literature summary: This program coordinates the complete de novo lipogenesis pathway, converting carbohydrates (including glucose and fructose) into fatty acids and triglycerides. ChREBP (Mlxipl) serves as the master carbohydrate-responsive transcription factor that activates lipogenic genes. The pathway proceeds through glycolysis (Pklr) and fructose metabolism (Khk), generating acetyl-CoA via ACLY, which is then converted to malonyl-CoA and subsequently to fatty acids by FASN. ME1 provides essential NADPH for reductive biosynthesis. Insig1 regulates SREBP processing to control lipogenic gene expression, while Dgat2 catalyzes the final step of triglyceride synthesis. This integrated lipogenic program is highly relevant to MASLD pathogenesis.

Module 2: Xenobiotic metabolism and cellular detoxification
- Supporting genes: Gstm7, Gstp1, Gstp3, Aldh2, Aldh1a7, Mt1, Mt2, Cyb5r3, Qdpr
- Supporting evidence (PMID/DOI): PMID:33035518, DOI:10.1016/j.cbi.2020.109284, PMID:28483492, DOI:10.1016/j.tox.2017.05.002, PMID:16157696, DOI:10.1124/mol.105.013680, PMID:23743293, DOI:10.1016/j.freeradbiomed.2013.05.028, PMID:31792171, DOI:10.1073/pnas.1908137116, PMID:25392542, DOI:10.1161/jaha.114.001329, PMID:29239069, DOI:10.1111/jvh.12845, PMID:2885099, DOI:10.1007/bf00052847
- Literature summary: This program expresses a comprehensive detoxification system in hepatocytes, including multiple glutathione S-transferases (Gstm7, Gstp1, Gstp3) that catalyze glutathione conjugation of electrophilic xenobiotics and endogenous toxic compounds, aldehyde dehydrogenases (Aldh2, Aldh1a7) that detoxify reactive aldehydes including those from lipid peroxidation and alcohol metabolism, and metallothioneins (Mt1, Mt2) that bind heavy metals and scavenge free radicals. This detoxification machinery is essential for hepatocyte protection against oxidative stress and xenobiotic insults, particularly relevant in the context of metabolic stress and aging.

Module 3: Carbohydrate metabolism and glycolytic flux
- Supporting genes: Pklr, Khk, Ugdh
- Supporting evidence (PMID/DOI): PMID:34512869, DOI:10.1155/2021/7182914, PMID:39418102, DOI:10.1172/jci.insight.184396, PMID:40278833, DOI:10.1002/advs.202417355, PMID:33812059, DOI:10.1016/j.molmet.2021.101227, PMID:37719384, DOI:10.1016/j.apsb.2023.06.014, PMID:28483921, DOI:10.1074/jbc.m117.786525
- Literature summary: This program regulates hepatic carbohydrate metabolism through glycolysis and gluconeogenesis. Pklr (pyruvate kinase liver isoform) catalyzes the final rate-limiting step of glycolysis, converting phosphoenolpyruvate to pyruvate. Khk (ketohexokinase) mediates the first step of fructose metabolism, which feeds into the lipogenic pathway. Regulators include G6pc (glucose-6-phosphatase), the rate-limiting enzyme of gluconeogenesis that opposes glycolysis, and Gckr (glucokinase regulatory protein), which modulates glucose phosphorylation. Ugdh generates UDP-glucuronic acid for conjugation reactions. Together, these genes coordinate hepatic glucose sensing, glycolytic flux, and the balance between glucose storage, utilization, and production.
Context: liver tissue; hepatocyte biology spanning metabolic zonation, xenobiotic and drug metabolism, bile acid metabolism, gluconeogenesis and glycogen storage, nitrogen and urea metabolism, oxidative and mitochondrial metabolism, membrane transport and solute carriers, lipid metabolism; with attention to aging and MASLD.
### Supporting evidence

#### Gene Summaries (Harmonizome):
- Acly: ATP citrate lyase is the primary enzyme responsible for the synthesis of cytosolic acetyl-CoA in many tissues. The enzyme is a tetramer (relative molecular weight approximately 440,000) of apparently identical subunits. It catalyzes the formation of acetyl-CoA and oxaloacetate from citrate and CoA with a concomitant hydrolysis of ATP to ADP and phosphate. The product, acetyl-CoA, serves several important biosynthetic pathways, including lipogenesis and cholesterogenesis. In nervous tissue, ATP citrate-lyase may be involved in the biosynthesis of acetylcholine. Multiple transcript variants encoding distinct isoforms have been identified for this gene.
- Aldh2: This protein belongs to the aldehyde dehydrogenase family of proteins. Aldehyde dehydrogenase is the second enzyme of the major oxidative pathway of alcohol metabolism. Two major liver isoforms of aldehyde dehydrogenase, cytosolic and mitochondrial, can be distinguished by their electrophoretic mobilities, kinetic properties, and subcellular localizations. Most Caucasians have two major isozymes, while approximately 50% of East Asians have the cytosolic isozyme but not the mitochondrial isozyme. A remarkably higher frequency of acute alcohol intoxication among East Asians than among Caucasians could be related to the absence of a catalytically active form of the mitochondrial isozyme. The increased exposure to acetaldehyde in individuals with the catalytically inactive form may also confer greater susceptibility to many types of cancer. This gene encodes a mitochondrial isoform, which has a low Km for acetaldehydes, and is localized in mitochondrial matrix. Alternative splicing results in multiple transcript variants encoding distinct isoforms.
- Ampd2: The protein encoded by this gene is important in purine metabolism by converting AMP to IMP. The encoded protein, which acts as a homotetramer, is one of three AMP deaminases found in mammals. Several transcript variants encoding different isoforms have been found for this gene.
- Bahcc1: Predicted to enable chromatin binding activity. Predicted to act upstream of or within chromatin organization; locomotory behavior; and neuron differentiation.
- Ccng1: The eukaryotic cell cycle is governed by cyclin-dependent protein kinases (CDKs) whose activities are regulated by cyclins and CDK inhibitors. The protein encoded by this gene is a member of the cyclin family and contains the cyclin box. The encoded protein lacks the protein destabilizing (PEST) sequence that is present in other family members. Transcriptional activation of this gene can be induced by tumor protein p53. Two transcript variants encoding the same protein have been identified for this gene.
- Cyb5r3: This gene encodes cytochrome b5 reductase, which includes a membrane-bound form in somatic cells (anchored in the endoplasmic reticulum, mitochondrial and other membranes) and a soluble form in erythrocytes. The membrane-bound form exists mainly on the cytoplasmic side of the endoplasmic reticulum and functions in desaturation and elongation of fatty acids, in cholesterol biosynthesis, and in drug metabolism. The erythrocyte form is located in a soluble fraction of circulating erythrocytes and is involved in methemoglobin reduction. The membrane-bound form has both membrane-binding and catalytic domains, while the soluble form has only the catalytic domain. Alternate splicing results in multiple transcript variants. Mutations in this gene cause methemoglobinemias.
- Fasn: The enzyme encoded by this gene is a multifunctional protein. Its main function is to catalyze the synthesis of palmitate from acetyl-CoA and malonyl-CoA, in the presence of NADPH, into long-chain saturated fatty acids. In some cancer cell lines, this protein has been found to be fused with estrogen receptor-alpha (ER-alpha), in which the N-terminus of FAS is fused in-frame with the C-terminus of ER-alpha.
- Gstp1: Glutathione S-transferases (GSTs) are a family of enzymes that play an important role in detoxification by catalyzing the conjugation of many hydrophobic and electrophilic compounds with reduced glutathione. Based on their biochemical, immunologic, and structural properties, the soluble GSTs are categorized into 4 main classes: alpha, mu, pi, and theta. This GST family member is a polymorphic gene encoding active, functionally different GSTP1 variant proteins that are thought to function in xenobiotic metabolism and play a role in susceptibility to cancer, and other diseases.
- Intu: Predicted to enable phosphatidylinositol binding activity. Involved in embryonic digit morphogenesis; roof of mouth development; and tongue morphogenesis. Located in ciliary basal body and motile cilium. Implicated in asphyxiating thoracic dystrophy and orofaciodigital syndrome XVII.
- Khk: This gene encodes ketohexokinase that catalyzes conversion of fructose to fructose-1-phosphate. The product of this gene is the first enzyme with a specialized pathway that catabolizes dietary fructose. Alternatively spliced transcript variants encoding different isoforms have been identified.
- Lama1: This gene encodes one of the alpha 1 subunits of laminin. The laminins are a family of extracellular matrix glycoproteins that have a heterotrimeric structure consisting of an alpha, beta and gamma chain. These proteins make up a major component of the basement membrane and have been implicated in a wide variety of biological processes including cell adhesion, differentiation, migration, signaling, neurite outgrowth and metastasis. Mutations in this gene may be associated with Poretti-Boltshauser syndrome.
- Lias: The protein encoded by this gene belongs to the biotin and lipoic acid synthetases family. Localized in the mitochondrion, this iron-sulfur enzyme catalyzes the final step in the de novo pathway for the biosynthesis of lipoic acid, a potent antioxidant. The deficient expression of this enzyme has been linked to conditions such as diabetes, atherosclerosis and neonatal-onset epilepsy. Alternative splicing occurs at this locus, and several transcript variants encoding distinct isoforms have been identified.
- Me1: This gene encodes a cytosolic, NADP-dependent enzyme that generates NADPH for fatty acid biosynthesis. The activity of this enzyme, the reversible oxidative decarboxylation of malate, links the glycolytic and citric acid cycles. The regulation of expression for this gene is complex. Increased expression can result from elevated levels of thyroid hormones or by higher proportions of carbohydrates in the diet.
- Mt1: Predicted to enable zinc ion binding activity. Involved in cellular response to cadmium ion and cellular response to zinc ion. Located in cytoplasm and nucleus.
- Mt2: This gene is a member of the metallothionein family of genes. Proteins encoded by this gene family are low in molecular weight, are cysteine-rich, lack aromatic residues, and bind divalent heavy metal ions, altering the intracellular concentration of heavy metals in the cell. These proteins act as anti-oxidants, protect against hydroxyl free radicals, are important in homeostatic control of metal in the cell, and play a role in detoxification of heavy metals. The encoded protein interacts with the protein encoded by the homeobox containing 1 gene in some cell types, controlling intracellular zinc levels, affecting apoptotic and autophagy pathways. Some polymorphisms in this gene are associated with an increased risk of cancer.
- Nob1: In yeast, over 200 protein and RNA cofactors are required for ribosome assembly, and these are generally conserved in eukaryotes. These factors orchestrate modification and cleavage of the initial 35S precursor rRNA transcript into the mature 18S, 5.8S, and 25S rRNAs, folding of the rRNA, and binding of ribosomal proteins and 5S RNA. Nob1 is involved in pre-rRNA processing. In a late cytoplasmic processing step, Nob1 cleaves a 20S rRNA intermediate at cleavage site D to produce the mature 18S rRNA (Lamanna and Karbstein, 2009 [PubMed 19706509]).[supplied by OMIM, Nov 2010]
- Phlda3: Enables phosphatidylinositol phosphate binding activity and phosphatidylinositol-3,4-bisphosphate binding activity. Involved in intrinsic apoptotic signaling pathway in response to DNA damage by p53 class mediator; negative regulation of phosphatidylinositol 3-kinase/protein kinase B signal transduction; and positive regulation of apoptotic process. Located in plasma membrane.
- Pklr: The protein encoded by this gene is a pyruvate kinase that catalyzes the transphosphorylation of phohsphoenolpyruvate into pyruvate and ATP, which is the rate-limiting step of glycolysis. Defects in this enzyme, due to gene mutations or genetic variations, are the common cause of chronic hereditary nonspherocytic hemolytic anemia (CNSHA or HNSHA). Multiple transcript variants encoding different isoforms have been found for this gene.
- Qdpr: This gene encodes the enzyme dihydropteridine reductase, which catalyzes the NADH-mediated reduction of quinonoid dihydrobiopterin. This enzyme is an essential component of the pterin-dependent aromatic amino acid hydroxylating systems. Mutations in this gene resulting in QDPR deficiency include aberrant splicing, amino acid substitutions, insertions, or premature terminations. Dihydropteridine reductase deficiency presents as atypical phenylketonuria due to insufficient production of biopterin, a cofactor for phenylalanine hydroxylase.
- Sema3e: Semaphorins are a large family of conserved secreted and membrane associated proteins which possess a semaphorin (Sema) domain and a PSI domain (found in plexins, semaphorins and integrins) in the N-terminal extracellular portion. Based on sequence and structural similarities, semaphorins are put into eight classes: invertebrates contain classes 1 and 2, viruses have class V, and vertebrates contain classes 3-7. Semaphorins serve as axon guidance ligands via multimeric receptor complexes, some (if not all) containing plexin proteins. This gene encodes a class 4 semaphorin. This gene encodes a class 3 semaphorin. Multiple transcript variants encoding different isoforms have been found for this gene.
- Stap1: The protein encoded by this gene contains a proline-rich region, a pleckstrin homology (PH) domain, and a region in the carboxy terminal half with similarity to the Src Homology 2 (SH2) domain. This protein is a substrate of tyrosine-protein kinase Tec, and its interaction with tyrosine-protein kinase Tec is phosphorylation-dependent. This protein is thought to participate in a positive feedback loop by upregulating the activity of tyrosine-protein kinase Tec. Variants of this gene have been associated with autosomal-dominant hypercholesterolemia (ADH), which is characterized by elevated low-density lipoprotein cholesterol levels and in increased risk of coronary vascular disease. Alternative splicing results in multiple transcript variants.
- Twist1: This gene encodes a basic helix-loop-helix (bHLH) transcription factor that plays an important role in embryonic development. The encoded protein forms both homodimers and heterodimers that bind to DNA E box sequences and regulate the transcription of genes involved in cranial suture closure during skull development. This protein may also regulate neural tube closure, limb development and brown fat metabolism. This gene is hypermethylated and overexpressed in multiple human cancers, and the encoded protein promotes tumor cell invasion and metastasis, as well as metastatic recurrence. Mutations in this gene cause Saethre-Chotzen syndrome in human patients, which is characterized by craniosynostosis, ptosis and hypertelorism.
- Ugdh: The protein encoded by this gene converts UDP-glucose to UDP-glucuronate and thereby participates in the biosynthesis of glycosaminoglycans such as hyaluronan, chondroitin sulfate, and heparan sulfate. These glycosylated compounds are common components of the extracellular matrix and likely play roles in signal transduction, cell migration, and cancer growth and metastasis. The expression of this gene is up-regulated by transforming growth factor beta and down-regulated by hypoxia. Alternative splicing results in multiple transcript variants.
- Utp3: Enables RNA binding activity. Involved in ribosomal small subunit biogenesis. Located in nucleolus. Part of small-subunit processome.
- Vps36: This gene encodes a protein that is a subunit of the endosomal sorting complex required for transport II (ESCRT-II). This protein complex functions in sorting of ubiquitinated membrane proteins during endocytosis. A similar protein complex in rat is associated with RNA polymerase elongation factor II.

#### Top KEGG/GO enrichment

- GO Process: Generation of precursor metabolites and energy (FDR=6.84e-36) - member genes: Cox5a, Sdhd, Crot, Adh1, Ndufs3, Adh5, Pgam1, Ndufs2, Ndufa2, Atp5e
- GO Process: Ribose phosphate metabolic process (FDR=1.18e-31) - member genes: Gnai3, Sdhd, Crot, Ndufs3, Pgam1, Ndufs2, Ndufa2, Atp5e, Nudt4, Sdha
- GO Process: Small molecule catabolic process (FDR=2.13e-31) - member genes: Haao, Crot, Adh1, Adh5, Acat2, Pten, Qdpr, Echdc1, Arg1, Hacl1
- GO Process: Ribonucleotide metabolic process (FDR=2.91e-30) - member genes: Gnai3, Sdhd, Crot, Ndufs3, Pgam1, Ndufs2, Ndufa2, Atp5e, Nudt4, Sdha
- GO Process: Purine ribonucleotide metabolic process (FDR=2.66e-29) - member genes: Gnai3, Sdhd, Crot, Ndufs3, Pgam1, Ndufs2, Ndufa2, Atp5e, Nudt4, Sdha
- GO Process: Nucleotide metabolic process (FDR=4.44e-29) - member genes: Gnai3, Sdhd, Haao, Crot, Ndufs3, Pgam1, Ndufs2, Ndufa2, Atp5e, Nudt4
- KEGG: Carbon metabolism (FDR=1.43e-26) - member genes: Sdhd, Adh5, Acat2, Pgam1, Sdha, Pdhb, Tkt, Ehhadh, Echs1, Taldo1
#### Cell-type enrichment: Not available.
#### Regulator perturbation evidence
(Top 3 activators and 3 repressors per condition; duplicate guides collapsed and ranked by adjusted p-value.)

Aged condition
Activators (knockdown reduces program activity):
- Mlxipl (log2FC=-1.604; adjP=3.50e-103) -> Fasn(750), Acly(656), Pklr(588), Khk(579), Gpam(541), Acadl(409), Fabp1(405)
- Dgat2 (log2FC=-1.141; adjP=4.77e-46) -> Fasn(776), Gpam(741), Plin2(685), Plin3(615), Acly(590), Cpt2(532), Fabp1(528), Acadl(503)
- Insr (log2FC=-0.543; adjP=1.08e-17) -> Pten(641), Sqstm1(528), Cdc42(490), Fasn(427)
Repressors (knockdown increases program activity):
- Insig1 (log2FC=+1.251; adjP=1.24e-41) -> Vcp(721), Fasn(720), Stard4(624), Acly(542), Gpam(520), Hmgcs2(513), Acat2(409)
- Flcn (log2FC=+0.787; adjP=2.57e-24) -> Sdhd(469), Sdhc(416)
- G6pc (log2FC=+0.823; adjP=2.71e-23) -> Aldob(939), Tkt(842), Taldo1(824), Pcx(755), Pygl(751), Pklr(689), Cyp3a11(681), Fasn(678)

Young condition
Activators (knockdown reduces program activity):
- Mlxipl (log2FC=-1.697; adjP=2.16e-29) -> Fasn(750), Acly(656), Pklr(588), Khk(579), Gpam(541), Acadl(409), Fabp1(405)
- Dgat2 (log2FC=-1.583; adjP=4.25e-27) -> Fasn(776), Gpam(741), Plin2(685), Plin3(615), Acly(590), Cpt2(532), Fabp1(528), Acadl(503)
- Scap (log2FC=-0.994; adjP=6.16e-14) -> Fasn(641), Acly(502), Hmgcs2(412)
Repressors (knockdown increases program activity):
- Insig1 (log2FC=+2.049; adjP=4.01e-21) -> Vcp(721), Fasn(720), Stard4(624), Acly(542), Gpam(520), Hmgcs2(513), Acat2(409)
- Flcn (log2FC=+1.636; adjP=2.21e-18) -> Sdhd(469), Sdhc(416)
- Gckr (log2FC=+0.772; adjP=3.31e-04) -> Khk(633), Atp5d(461), Pygl(431), Pklr(423), Uox(421)

### Interpretation rules
- Cite genes and supplied evidence for biological claims.
- Treat research-evidence modules as candidate modules, not fixed final boundaries.
- Add 1-2 de novo functional theme candidates when primary gene descriptions, regulators, or enrichments support them.
- Do not automatically select all research-evidence candidates; a de novo candidate may replace a research-evidence candidate when it is more specific or clearly supported by evidence.
- Do not refer to upstream labels such as "research-evidence Module 1", "research module", or "candidate module" anywhere in the final output, including the evidence used field; use genes, pathways, regulator evidence, and Supporting PMID/DOIs to trace evidence instead.
- Include all supplied PMIDs, only if a final module strongly overlaps a research-evidence module.
- Select 1-3 final modules from this candidate pool, ranked by specificity and reasoning; generic-theme-dominated modules should be down-weighted (refer to generic themes to down-weight section).
- Final program label should be decided after considering all selected modules and should be a coherent, human-readable biological phrase; If the selected modules are distinct and do not naturally relate, pick a label based on the most representative module. Avoid generic dictionary terms, cell-type filler such as "state/identity" unless necessary.

### Generic themes to down-weight
Not available.

### Output requirements (GitHub-flavored Markdown)
Start with: `## Program 22 annotation`

CRITICALLY, include the following two lines near the top, exactly with these bold labels:
- **Brief Summary:** <1-2 sentences>
- **Program label:** <=6 words

Then provide the following sections:

1. **High-level overview (<=120 words)**
   - Main theme(s) grounded in the primary evidence.
   - Connect to hepatocyte, liver, aging, MASLD context only when supported by the supplied genes and curated literature evidence.

2. **Functional modules and mechanisms**
   Group genes into 1-3 final modules. For each module, use this exact format:
   ```
   Module name
   1-sentence summary
   Key genes: list 2-10
   Supporting PMIDs: comma-separated PMIDs directly supporting this final module, or None
   evidence used: genes and literature, if any.
   Proposed mechanism: 1-2 sentences, backed by primary program genes, regulator evidence, gene summaries, pathway enrichment, or hepatocyte, liver, aging, MASLD context.
   ```

3. **Distinctive features**
   - Describe what is most distinctive about Program 22 in 1-2 sentences. Cite unique genes and provide reasoning.
   - If evidence is limited or mixed, say so explicitly.

4. **Regulator analysis**
   List 1-3 most prominent regulators from Perturb-seq, for each regulator use this exact format:
   ```
   regulator_name (role, log2FC=X): [Confidence: High/Medium/Low]
   Propose a mechanistic hypothesis: How might this regulator control the program's genes/pathways? Cite program genes and evidence.
   ```