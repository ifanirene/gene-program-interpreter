## Program 10 annotation

**Brief Summary:** Program 10 represents a hepatocyte-intrinsic oxidative metabolic capacity program encompassing mitochondrial electron transport, TCA-linked redox shuttling, and fasting-state carbon flux, actively repressed by insulin/mTOR signaling and modulated by sterol and bile-acid detoxification stress in aging and MASLD contexts.

**Program label:** Insulin-repressed hepatocyte oxidative-fasting metabolic capacity

---

## 1. High-level overview

Program 10 is anchored by a dense cluster of mitochondrial oxidative phosphorylation genes (Ndufa6, Ndufv1, Ndufs6, Sdhb, Cox7a2, Atp5a1, Atp5o, Etfb), TCA/malate-aspartate shuttle components (Got2, Mdh1, Suclg1, Ak3), and fasting gluconeogenic/glycolytic flux enzymes (Fbp1, Pgm1, Tpi1). The program is consistently activated when insulin receptor (Insr), mTOR (Mtor), or Irs1 signaling is disrupted—across both young and aged conditions—establishing a strong insulin/mTOR-repressed oxidative-fasting state. In MASLD-relevant perturbations, lipogenic regulators Dgat2 and Scd1 suppress this program in young hepatocytes, while sterol (Sqle, Abcg8) and endocytic (Ap2a2) regulators modulate it in aged hepatocytes, consistent with age-dependent remodeling of hepatic oxidative capacity under metabolic stress.

---

## 2. Functional modules and mechanisms

```
Module 1: Mitochondrial electron transport and oxidative phosphorylation capacity
1-sentence summary: Core ETC complexes I–V and outer mitochondrial membrane channel support hepatocyte ATP synthesis capacity that is suppressed by insulin/mTOR and reduced in MASLD/MASH.
Key genes: Ndufa6, Ndufv1, Ndufs6, Sdhb, Cox7a2, Atp5a1, Atp5o, Etfb, Vdac1
Supporting PMIDs: 27346353, 35000203, 31081165, 15096593
Evidence used: genes and literature.
Proposed mechanism: Ndufa6, Ndufv1, and Ndufs6 (Complex I), Sdhb (Complex II), Cox7a2 (Complex IV), Atp5a1/Atp5o (Complex V/ATP synthase), and Etfb (electron transfer to ETC from fatty-acid β-oxidation) constitute the hepatocyte mitochondrial respiratory axis. Vdac1 gates metabolite flux across the outer mitochondrial membrane to sustain this capacity. Insr repression (young: log2FC=+1.158; aged: log2FC=+0.518) and Mtor repression (young: log2FC=+0.770) strongly activate this program, consistent with the established model that hepatic insulin receptor loss stimulates oxidative metabolism while mTORC1 activation suppresses it (PMID:27346353). Human NASH is associated with reduced mitochondrial fatty-acid oxidation and increased mitochondrial ROS (PMID:35000203), positioning this module as a capacity metric inversely related to MASLD severity.
```

```
Module 2: TCA-coupled malate-aspartate redox shuttle and fasting gluconeogenic carbon flux
1-sentence summary: Mitochondrial and cytosolic redox-transfer enzymes linked to gluconeogenesis, glycogen-carbon handling, and nucleotide energy buffering define an insulin-suppressed fasting metabolic state.
Key genes: Got2, Mdh1, Suclg1, Fbp1, Pgm1, Tpi1, Ak3, Adk
Supporting PMIDs: 20133650, 27708333, 18590692, 22517657, 31805015, 27346353
Evidence used: genes and literature.
Proposed mechanism: Got2 (mitochondrial aspartate aminotransferase) and Mdh1 (malic enzyme/malate dehydrogenase) execute the malate-aspartate shuttle linking mitochondrial TCA redox to cytosolic NADH; Suclg1 supports succinyl-CoA/GTP production in the TCA cycle. Fbp1 (fructose-1,6-bisphosphatase), Pgm1 (phosphoglucomutase), and Tpi1 (triosephosphate isomerase) provide gluconeogenic and glycogen-carbon flux capacity, while Ak3 and Adk buffer mitochondrial and cytosolic adenylate energy charge. Irs1 repression (young: log2FC=+0.741) and Insr repression co-activate these genes, consistent with insulin/IRS1/mTOR normally suppressing gluconeogenesis while promoting lipogenesis; selective insulin resistance in obesity/NAFLD uncouples these branches (PMIDs:20133650, 27708333), and loss of this fasting program characterizes hepatic metabolic inflexibility in MASLD.
```

```
Module 3: Sterol/bile-acid detoxification and HSC70-mediated proteostatic buffering of lipid stress
1-sentence summary: Sult2a1-mediated sterol sulfation, Akr7a5 reactive-aldehyde detoxification, and Hspa8/HSC70-driven chaperone-mediated autophagy jointly buffer hepatocyte lipotoxic and proteostatic stress, with age-specific modulation by cholesterol flux regulators.
Key genes: Hspa8, Sult2a1, Akr7a5, Gamt, Pcbd1, Inmt, Tle5
Supporting PMIDs: 18690243, 25043815, 25620427, 25961502, 33647280, 12208868, 29287776
Evidence used: genes and literature.
Proposed mechanism: Hspa8/HSC70 is the obligate substrate-recognition chaperone for chaperone-mediated autophagy (CMA); hepatic CMA deficiency causes steatosis, glycogen depletion, and accelerated aging-linked proteostasis failure, while CMA restoration improves hepatic function (PMIDs:18690243, 25043815, 25620427), and CMA promotes lipid-droplet protein turnover to buffer steatosis (PMID:25961502). Sult2a1 sulfates bile acids and sterols for excretion, providing a detoxification link to the aged-specific perturbation evidence: Sqle knockdown reduces program activity (aged activator; log2FC=−0.228), consistent with SQLE driving cholesterol accumulation and steatohepatitis in NASH (PMID:33647280), whereas Abcg8 knockdown increases program activity (aged repressor; log2FC=+0.240) through impaired biliary sterol excretion. Akr7a5 detoxifies reactive aldehydes generated under lipid peroxidation stress. Gamt, Pcbd1, Inmt, and Tle5 represent ancillary metabolic/transcriptional functions with weaker MASLD-specific support but consistent hepatocyte expression.
```

---

## 3. Distinctive features

Program 10 is distinctively defined by the convergence of mitochondrial ETC genes (Ndufa6, Ndufv1, Ndufs6, Sdhb, Etfb) with fasting gluconeogenic flux enzymes (Fbp1, Pgm1, Tpi1) under a shared insulin/mTOR repressor logic—evidenced by Insr being the top repressor in both young (log2FC=+1.158) and aged (log2FC=+0.518) conditions, with Mtor and Irs1 as additional young-specific repressors. The unique genes Adk, Suclg1, and Vdac1 specifically extend this to mitochondrial adenylate energy buffering and outer membrane metabolite gating, while Sult2a1 and the aged-specific Sqle/Abcg8 perturbation effects add a sterol-detoxification dimension not captured by generic oxidative phosphorylation programs, distinguishing Program 10 as a hepatocyte-specific insulin-repressed oxidative-fasting state relevant to MASLD metabolic inflexibility.

---

## 4. Regulator analysis

```
Insr (repressor in both aged and young; aged log2FC=+0.518, adjP=2.06e-24; young log2FC=+1.158, adjP=1.25e-11): [Confidence: High]
Proposed mechanistic hypothesis: Insulin receptor signaling normally suppresses hepatic gluconeogenesis and promotes lipogenesis via IRS1/PI3K/AKT; knockdown of Insr releases this suppression, activating the fasting oxidative program encompassing Got2, Fbp1, Tpi1 (gluconeogenic flux), and ETC genes Ndufa6, Sdhb, Etfb (oxidative capacity). The consistent top-repressor status across both age conditions, with the strongest effect in young hepatocytes (log2FC=+1.158), indicates Insr is the primary gatekeeper of this program; co-activation of Igf1 and Hras in Insr perturbation target lists further implicates insulin/IGF-PI3K axis withdrawal in the program's release. This is mechanistically supported by evidence that hepatic insulin receptor loss stimulates oxidative metabolism (PMID:27346353).
```

```
Mtor (repressor in young only; log2FC=+0.770, adjP=2.38e-03): [Confidence: High]
Proposed mechanistic hypothesis: mTORC1 promotes lipogenesis via SREBP1c and suppresses oxidative catabolism; its knockdown in young hepatocytes activates the oxidative-fasting program, mirroring Insr knockdown effects. Mtor perturbation co-activates Hsp90ab1, Sqstm1, and Eif4a1, suggesting concurrent relief of translational suppression and autophagy induction alongside the oxidative program. The absence of Mtor as a top repressor in aged hepatocytes suggests age-related decoupling of mTOR from proximal insulin receptor control, consistent with reported mTOR dysregulation in hepatic aging (PMIDs:27346353, 20133650).
```

```
Sqle (activator in aged; log2FC=−0.228, adjP=4.78e-11): [Confidence: Medium]
Proposed mechanistic hypothesis: SQLE (squalene epoxidase) catalyzes a rate-limiting step in cholesterol biosynthesis; its knockdown reduces program activity in aged hepatocytes, suggesting that squalene/sterol pathway flux supports or is co-regulated with this oxidative program in aging. Mechanistically, SQLE overexpression drives cholesterol accumulation and NF-κB-mediated steatohepatitis in NASH (PMID:33647280), and co-activation of Sdhb and Hmgcs2 in the Sqle perturbation target list directly links sterol metabolism to ETC and ketogenic capacity. The aged-specificity of Sqle as an activator, absent in young, implicates age-dependent cholesterol-stress coupling to the oxidative fasting program via Sult2a1-mediated sterol detoxification.
```

---

## 5. Pathway Enrichment

```
- GO Process: Generation of precursor metabolites and energy (FDR=6.65e-36) - member genes: Sdhb, Etfb, Ndufa6 (Ndufa11 ortholog context), Ndufs6, Mdh1 (Mdh2 context), Atp5a1, Atp5o
- GO Process: Energy derivation by oxidation of organic compounds (FDR=1.85e-33) - member genes: Sdhb, Etfb, Ndufs6, Ndufv1, Suclg1
- GO Process: Cellular respiration (FDR=1.23e-30) - member genes: Sdhb, Etfb, Ndufa6, Ndufs6, Ndufv1, Cox7a2, Atp5a1
- GO Process: Aerobic respiration (FDR=5.03e-30) - member genes: Sdhb, Ndufs6, Ndufv1, Suclg1, Mdh1
- GO Process: Small molecule catabolic process (FDR=2.16e-32) - member genes: Etfb, Tpi1, Got2, Sult2a1, Gamt, Adk
- KEGG: Parkinson disease (FDR=2.47e-30) - member genes: Sdhb, Ndufs6, Ndufv1, Hspa8, Vdac1, Atp5a1
- KEGG: Prion disease (FDR=5.28e-30) - member genes: Sdhb, Ndufs6, Ndufv1, Hspa8, Atp5a1
```