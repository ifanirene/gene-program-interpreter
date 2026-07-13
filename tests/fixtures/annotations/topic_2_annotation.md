## Program 2 annotation

**Brief Summary:** Program 2 captures a hepatocyte-autonomous transcriptional state defined by HNF4A-driven bile acid export/detoxification, insulin-PI3K-SREBP-suppressed anti-steatotic hepatokine signaling, and aldehyde-glutathione redox buffering, collectively maintaining metabolic identity and resisting lipotoxic injury in the context of aging and MASLD.

**Program label:** HNF4A-bile acid-IGFBP2 anti-steatotic hepatocyte identity

---

### 1. High-level overview

Program 2 is anchored by three converging hepatocyte-specific axes. First, HNF4A coordinates canalicular bile acid export (Abcb11) and sulfation-based detoxification (Sult2a5), maintaining differentiated hepatocyte identity. Second, insulin-PI3K-SREBP signaling, evidenced by the dominant Insr/Irs1/Insig1 repressor triad across both ages, suppresses an anti-steatotic hepatokine program centered on Igfbp2 and Glud1. Third, Aldh2 and Gclm provide mitochondrial aldehyde and glutathione-mediated redox buffering against lipotoxic injury. The age-attenuated Insr/Irs1 repressor coefficients suggest insulin-coupled suppression of this program weakens with aging, potentially enabling a partially compensatory anti-steatotic state in aged hepatocytes.

---

### 2. Functional modules and mechanisms

```
Module 1: HNF4A-directed canalicular bile acid export and sulfation-based detoxification
1-sentence summary: HNF4A maintains differentiated hepatocyte identity by driving
canalicular bile salt efflux via Abcb11 and SULT2A-family sulfation of bile acids
and steroids via Sult2a5, a circuit disrupted in cholestatic and steatotic liver disease.
Key genes: Hnf4a, Abcb11, Sult2a5, Ugdh, Wdtc1
Supporting PMIDs: 14570929, 22619174, 33098092, 31877412, 19131563, 33872606, 37934943
Evidence used: genes (Hnf4a, Abcb11, Sult2a5 as top-loading program genes); literature
module 1 PMIDs; Abcb11 gene summary confirming major canalicular bile salt transporter
role; Sult2a5 gene summary confirming sulfotransferase activity and SULT2A1 orthology;
Ugdh gene summary confirming UDP-glucuronate biosynthesis supporting conjugation
capacity; Wdtc1 gene summary confirming insulin-responsive histone deacetylase binding
and negative regulation of biosynthesis consistent with HNF4A metabolic identity
maintenance; Acvr2a as a young-dominant activator (log2FC=-0.692, adjP=1.30e-03)
consistent with activin-modulated hepatocyte differentiation state.
Proposed mechanism: HNF4A, a master hepatocyte transcription factor, directly
transactivates Abcb11 to sustain canalicular bile salt secretion and drives Sult2a5
expression for bile acid sulfation-based elimination; Ugdh supplies UDP-glucuronate
for parallel glucuronidation, and the young-dominant Acvr2a activator signal suggests
activin-A/SMAD signaling reinforces this bile acid identity axis in young but not aged
hepatocytes, consistent with age-related decline of HNF4A-dependent differentiation
in MASLD progression.
```

```
Module 2: Insulin-PI3K-SREBP-suppressed anti-steatotic hepatokine and anaplerotic signaling
1-sentence summary: Insulin receptor/IRS1 and INSIG1-SREBP signaling tonically
repress an anti-steatotic hepatokine program centered on secreted IGFBP2 and
mitochondrial anaplerotic Glud1, with age-attenuated repression suggesting
partial derepression as a compensatory hepatocyte response in aging-associated
metabolic dysfunction.
Key genes: Igfbp2, Glud1, Hnf4a, Serpine2, Slc6a6
Supporting PMIDs: 39299533, 34524971, 37146585, 15711641, 27708333, 33722690,
40378221, 29266543
Evidence used: Insr repressor dominance across both ages (aged: log2FC=+0.366,
adjP=1.95e-46; young: log2FC=+0.809, adjP=5.80e-15) as the single strongest
perturbation signal in the program; Irs1 repressor in aged condition
(log2FC=+0.150, adjP=1.09e-06); Insig1 repressor in both conditions (aged:
log2FC=+0.113, adjP=3.85e-05; young: log2FC=+0.406, adjP=2.48e-04); Igfbp2
as a top-loading gene and literature-confirmed anti-steatotic hepatokine
(PMIDs 39299533, 34524971, 37146585); Glud1 gene summary confirming
mitochondrial glutamate dehydrogenase activity, TCA anaplerosis, and positive
regulation of insulin secretion; GO fatty acid metabolic process (FDR=8.60e-04)
and KEGG FoxO signaling (FDR=1.70e-04) including Pck1 and Pik3ca confirming
insulin-lipid metabolic coupling; Irs1 interactor network including Ptpn11, Pik3ca,
Pck1, Scd1, Cebpa supporting insulin axis; Ppp1r3b aged activator (log2FC=-0.150)
supporting glycogen-triglyceride partitioning context.
Proposed mechanism: Hepatic insulin receptor signaling through IRS1-PI3K suppresses
this anti-steatotic program, while INSIG1-mediated SREBP retention provides a
parallel lipogenic brake; knockdown of Insr or Insig1 derepresses Igfbp2 and Glud1,
indicating that in states of insulin resistance or INSIG1 loss (as in MASLD), this
program may be aberrantly derepressed or fail to compensate, and the smaller
aged Insr/Irs1 coefficients suggest blunted insulin-coupled repression with aging
consistent with selective hepatic insulin resistance.
```

```
Module 3: Mitochondrial aldehyde-glutathione redox buffering and ER secretory stress
adaptation against lipotoxic injury
1-sentence summary: Aldh2, Gclm, Mia2, and Mia3 form a hepatocyte lipotoxic-injury
buffering axis coupling mitochondrial aldehyde detoxification and glutathione
biosynthesis with ER-to-Golgi cargo secretion remodeling under steatohepatitic stress.
Key genes: Aldh2, Gclm, Mia2, Mia3, Mars1
Supporting PMIDs: 34439969, 24492981, 20548286, 39034312, 12586826, 28039913,
34711912, 29287776
Evidence used: Aldh2, Gclm, Mia2, Mia3 as top-loading program genes; literature
module 3 PMIDs; Aldh2 gene summary confirming mitochondrial aldehyde dehydrogenase
and link to liver disease; Gclm gene summary confirming glutamate-cysteine ligase
modifier subunit and glutathione biosynthesis; Mia2 gene summary confirming ER-to-Golgi
trafficking and cholesterol metabolism regulation; Mia3 gene summary confirming ER
membrane cargo receptor activity; GO response to oxidative stress (FDR=3.70e-04)
including Gclm and Sod2; GO small molecule catabolic process (FDR=1.60e-03) including
Aldh2; Flcn as strong young activator (log2FC=-0.587, adjP=2.98e-07) connecting
to TFEB/autophagy-dependent stress adaptation; Mars1 as a top-loading
aminoacyl-tRNA synthetase gene implicated in integrated stress response/UPR
translation initiation consistent with ER secretory stress context.
Proposed mechanism: Toxic lipid-derived aldehydes generated during steatosis are
detoxified by mitochondrial ALDH2, while GCLM-dependent glutathione synthesis
neutralizes reactive oxygen species; simultaneously, MIA2 and MIA3 remodel ER
secretory capacity to manage proteostatic and lipotoxic ER stress, and the
young-dominant Flcn activator signal implicates TFEB-driven autophagy as an
upstream enabler of this buffering program that weakens with age, potentially
contributing to age-associated vulnerability to steatohepatitic progression.
```

---

### 3. Distinctive features

The most distinctive feature of Program 2 is the co-occurrence of a potent, age-attenuated Insr/Irs1 repressor axis (the strongest perturbation signal in the dataset, young log2FC=+0.809) operating on the secreted anti-steatotic hepatokine Igfbp2 alongside a canonical HNF4A-Abcb11 bile acid identity circuit—linking two hepatocyte-autonomous programs that are jointly eroded in MASLD-associated insulin resistance and HNF4A loss. The unique gene Wrnip1 (DNA replication/repair helicase) and Spata13 (GEF for cell migration) load onto this program without clear hepatocyte-MASLD anchoring, indicating that Program 2 also captures a minor genotoxic/cytoskeletal stress signature whose biological integration with the dominant metabolic axes remains uncertain with current evidence.

---

### 4. Regulator analysis

```
Insr (repressor, log2FC=+0.366 aged / +0.809 young): [Confidence: High]
Mechanistic hypothesis: Insulin receptor signaling through IRS1-PI3K (evidenced by
Irs1 as co-repressor and shared interactors Ptpn11, Pik3ca) tonically suppresses
Program 2 activity, likely by phosphorylating and inactivating FOXO transcription
factors (supported by KEGG FoxO pathway FDR=1.70e-04 including Pck1, Pik3ca) that
would otherwise drive Igfbp2, Hnf4a, and Abcb11 expression; the larger young
coefficient (+0.809 vs +0.366) indicates that insulin-coupled repression is
substantially blunted in aged hepatocytes, consistent with age-progressive hepatic
insulin resistance and reduced suppression of this anti-steatotic program.
```

```
Insig1 (repressor, log2FC=+0.113 aged / +0.406 young): [Confidence: High]
Mechanistic hypothesis: INSIG1 retains SCAP/SREBP in the ER, preventing lipogenic
SREBP target activation; Insig1 knockdown derepresses Program 2, suggesting that
SREBP-driven lipogenic reprogramming antagonizes the HNF4A-Igfbp2 anti-steatotic
state, and the larger young coefficient implies INSIG1-mediated SREBP repression of
Program 2 is more effective in young hepatocytes, with loss of this brake in aging
or MASLD (where INSIG1 is dysregulated) contributing to steatotic progression;
Insig1 interactors Scd1 and Acsl1 further link SREBP to fatty acid
desaturation/activation circuits opposing the program.
```

```
Acvr2a (activator, log2FC=-0.692 young / -0.151 aged): [Confidence: Medium]
Mechanistic hypothesis: ACVR2A-mediated activin signaling through SMAD4 (top
interactor, score=905) supports Program 2 activity—likely by sustaining HNF4A
expression or Abcb11 transcription in young hepatocytes, as activin-A has been
linked to hepatocyte differentiation and MASLD biology; the dramatically attenuated
aged coefficient (-0.151 vs -0.692) suggests age-associated decline in hepatocyte
activin responsiveness contributes to erosion of the bile acid identity module,
though a direct hepatocyte-autonomous ACVR2A-to-Abcb11/Hnf4a transcriptional link
requires further resolution.
```

---

### 5. Pathway Enrichment

```
- KEGG: FoxO signaling pathway (FDR=1.70e-04) - member genes: Raf1, Sod2, Crebbp, Smad4, Pck1, Pik3ca, Prkaa2, Gabarapl1, S1pr1, Foxo4
- GO Process: Response to oxidative stress (FDR=3.70e-04) - member genes: Sod2, Ppp2cb, Naprt, Agap3, Als2, Map1lc3a, Gclm, Psip1, Prkaa2, Pink1
- GO Process: Fatty acid metabolic process (FDR=8.60e-04) - member genes: Crot, Gstm1, Elovl2, Hacl1, Ehhadh, Pck1, Hao2, Prkaa2, Cyp4a14, Acsl1
- GO Process: Small molecule catabolic process (FDR=1.60e-03) - member genes: Crot, Pfkl, Glud1, Hacl1, Ehhadh, Csad, Cyp26a1, Pck1, Cyp4a14, Aldh2
- GO Process: Response to carbohydrate (FDR=1.80e-03) - member genes: Raf1, Sod2, Hnf4a, Pfkl, Smad4, Pck1, Pik3ca, Gclm, Prkaa2, Gclc
- GO Process: Cerebellum development (FDR=2.00e-04) - member genes: Glud1, Serpine2, Aars, Arcn1, Atxn2, Ptpn11, Usp9x, Ogdh, Zbtb18, Nfix
- GO Process: Cerebellar cortex development (FDR=3.90e-04) - member genes: Serpine2, Aars, Arcn1, Atxn2, Ptpn11, Usp9x, Ogdh, Nfix, Ulk1
```