const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const packet = JSON.parse(fs.readFileSync(path.join(root, 'review', 'review_packet.json'), 'utf8'));
const items = new Map(packet.review_items.map((item) => [item.review_id, item]));

const A = [
  {
    id: 'RL-0a9ea963b058f961bd07', support: 'partial',
    studied: ['Acat2','Mvk','Idi1','Fdps','Fdft1','Cyp51','Msmo1','Dhcr7'],
    functional: ['Acat2','Mvk','Idi1','Fdps','Fdft1','Cyp51','Msmo1','Dhcr7'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Cholesterol biosynthesis-related genes, such as Mvk, Idi1, Fdps, Fdft1, Cyp51a1, Msmo1 and Dhcr7 were upregulated (Fig. 6a).',
    rationale: "Liver-specific Acat2 gain of function supports its thiolase-to-acetoacetyl-CoA role and shifts mouse hepatic cholesterol-pathway genes. The citation does not support the separate cholesterol-esterification statement.",
    flags: ['wrong_function']
  },
  {
    id: 'RL-242b5a9c5abc75abc94d', support: 'supports',
    studied: ['Hmgcs1','Cyp51','Fdps','Acat2','Mvk','Mvd','Idi1','Fdft1','Lss','Nsdhl','Dhcr7'],
    functional: ['Hmgcs1','Cyp51','Fdps','Acat2','Mvk','Mvd','Idi1','Fdft1','Lss','Nsdhl','Dhcr7'],
    directness: 'causal', direction: 'matches', context: 'partial', accuracy: 'overclaimed',
    span: 'Expression data from HepG2 cells also indicated that multiple enzymes in cholesterol biosynthesis and fatty acid synthesis pathways were significantly down regulated (Figure 3).',
    rationale: "TSA perturbation supports coordinated cholesterol-pathway regulation. HepG2 is a human hepatoma model, so the direct mouse-hepatocyte label is too strong.", flags: ['context_overclaim']
  },
  {
    id: 'RL-40098cc460cadd4e2943', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'SCAP-deficient mice showed an 80% reduction in basal rates of cholesterol and fatty acid synthesis in liver, owing to decreases in mRNAs encoding multiple biosynthetic enzymes.',
    rationale: 'Conditional mouse-liver Scap deficiency directly demonstrates the regulator requirement for hepatic lipid synthesis. Scap is not in the supplied-gene denominator.', flags: []
  },
  {
    id: 'RL-47cb3ccbf39d99c1b53e', support: 'partial', studied: ['Dhcr7','Sc5d'], functional: ['Dhcr7','Sc5d'],
    directness: 'causal', direction: 'matches', context: 'partial', accuracy: 'overclaimed',
    span: 'Experiments indicated that human sterol Δ(7)-reductase (DHCR7) is the major target of LK-980 (34-fold increase of 7-dehydrocholesterol), whereas human sterol Δ(14)-reductase (DHCR14), human sterol Δ(24)-reductase (DHCR24), and human sterol C5-desaturase (SC5DL) represent minor targets.',
    rationale: "The data support DHCR7 and SC5D as late cholesterol-pathway enzymes, but not the stronger claim that both are rate-limiting. Human hepatocytes are partial context.", flags: ['wrong_function','context_overclaim']
  },
  {
    id: 'RL-490fe81565b6e16d4d74', support: 'supports', studied: ['Fdft1'], functional: ['Fdft1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Overexpression of SS increased de novo cholesterol biosynthesis with increased 3-hydroxy-3-methyglutaryl-CoA (HMG-CoA) reductase activity, in spite of the downregulation of its own mRNA expression.',
    rationale: 'Mouse-liver overexpression of squalene synthase, the Fdft1 product, directly increases cholesterol synthesis.', flags: []
  },
  {
    id: 'RL-61540a022c6b44664286', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Absence of Insig1 in the liver (Figure 2A) resulted in a marked upregulation of SREBP1 and SREBP2 target genes, leading to enhanced lipid/cholesterol synthesis and remodelling (Figure 2B).',
    rationale: 'Insig1 loss in a mouse NASH model directly supports negative feedback on SREBP-driven hepatic cholesterol synthesis. Insig1 is a regulator.', flags: []
  },
  {
    id: 'RL-6dcde1c34bce3dbdd012', support: 'partial', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'We demonstrated that inflammatory stress increased hepatic cholesterol accumulation and enhanced sterol regulatory element binding protein 2 (SREBP2), low-density lipoprotein receptor (LDLr) and HMGCoA-r mRNA and protein expression in livers of C57BL/6J mice and in HepG2 cells.',
    rationale: 'The study supports hepatic SREBP2-LDLR-HMGCR feedback, but does not test Ldlr knockout or establish the claimed explanation for its perturbation fold change.', flags: ['wrong_function']
  },
  {
    id: 'RL-8341775104beac531474', support: 'partial', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'CTRP12 treatment also downregulates the expression of hepatocyte nuclear factor-4α (HNF-4α) and its target gene microsomal triglyceride transfer protein (MTTP), leading to reduced very-low-density lipoprotein (VLDL)-triglyceride export from hepatocytes.',
    rationale: 'The paper supports the hepatocyte DGAT-MTTP triglyceride-export arm, but neither Acss2 nor cholesterol synthesis, so only part of the proposed coupling is supported.', flags: ['wrong_function']
  },
  {
    id: 'RL-85775391149b8f392d5d', support: 'supports',
    studied: [], functional: [],
    directness: 'secondary', direction: 'not_applicable', context: 'indirect', accuracy: 'overclaimed',
    span: 'Taken together, many genes play a central role in cholesterol synthesis, including HMGCR, SQLE, HMGCS1, FDFT1, LSS, MVK, PMK, MVD, FDPS, CYP51, TM7SF2, LBR, MSMO1, NSDHL, HSD17B7, DHCR24, EBP, SC5D, DHCR7, IDI1/2 (Fig.',
    rationale: 'The pathway-focused review maps nearly all supplied enzymes to cholesterol synthesis, but only as a pathway list; this supports the mechanism without counting genes as substantively studied. It is not direct mouse-hepatocyte evidence.', flags: ['context_overclaim']
  },
  {
    id: 'RL-b2c271d4500bf0a67774', support: 'supports', studied: ['Hmgcs1'], functional: ['Hmgcs1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'We showed that IKK-mediated activation of hepatocyte NF-κB induces de novo lipogenesis (DNL) and cholesterol synthesis, leading to hepatic accumulation of triglycerides and cholesterol, accompanied by high plasma cholesterol levels.',
    rationale: 'A hepatocyte-specific mouse perturbation directly measures increased lipid and cholesterol synthesis; HMGCS1 is measured as a pathway protein.', flags: []
  },
  {
    id: 'RL-cfde5c855118d7be3a63', support: 'supports', studied: ['Fdft1'], functional: ['Fdft1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Other SREBP2 downstream targets, such as Farnesyl-diphosphate farnesyltransferase 1 (FDFT1) and 3-hydroxy-3-methylglutaryl-CoA reductase (HMGCR), were also upregulated by KLF2 in the liver of C57BL/6J mice fed a high-fat diet after being treated with Ad-ALB-Klf2 for 1 week (supplemental Fig.',
    rationale: 'Liver-specific Klf2 overexpression activates SREBP2 and its Fdft1 target in mouse liver.', flags: []
  },
  {
    id: 'RL-daf9675935894ab3741e', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'partial', accuracy: 'overclaimed',
    span: 'Similar to the regulation of extracellular VLDL and intracellular TG accumulation, the mRNA levels of the microsomal triglyceride transfer protein, forkhead box O1, and DGAT2 increased with treatment with 10 or 20 μg/ml of cholesterol, but decreased with treatment with 30 μg/ml of cholesterol (p < 0.05).',
    rationale: 'Cholesterol loading changes DGAT2/MTTP and VLDL-TG output in primary hepatocytes. Goose hepatocytes are partial mouse context and no supplied gene is studied.', flags: ['context_overclaim']
  },
  {
    id: 'RL-de544aa0b08392482e69', support: 'partial', studied: ['Acss2'], functional: ['Acss2'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Depletion of hepatic ACSS2 strongly suppressed the labeling of fatty acids in circulating lipids from 13C-fructose (Fig. 3d).',
    rationale: 'The study establishes Acss2 as an acetate-to-lipogenic acetyl-CoA node and shows acetate labeling of HMG-CoA, but does not directly measure cholesterol synthesis.', flags: ['wrong_function']
  },
  {
    id: 'RL-e02070e104a3f6abe459', support: 'supports', studied: ['Cyp51'], functional: ['Cyp51'],
    directness: 'secondary', direction: 'not_applicable', context: 'indirect', accuracy: 'overclaimed',
    span: 'It catalyzes the first step following cyclization in sterol biosynthesis such as removal of the 14 alpha-methyl group from lanosterol in the cholesterol biosynthetic pathway, leading to formation of the initial substrate in steroid hormone biosynthesis.',
    rationale: 'The CYP51-focused review supports the assigned demethylase function, but is general secondary evidence.', flags: ['context_overclaim']
  },
  {
    id: 'RL-ebff6126c1db18127944', support: 'supports',
    studied: ['Cyp51','Hmgcs1','Fdps','Msmo1','Nsdhl','Idi1','Fdft1','Tm7sf2','Sc5d','Hsd17b7','Dhcr7','Lss','Mvk','Pmvk','Mvd','Acat2'],
    functional: ['Cyp51','Hmgcs1','Fdps','Msmo1','Nsdhl','Idi1','Fdft1','Tm7sf2','Sc5d','Hsd17b7','Dhcr7','Lss','Mvk','Pmvk','Mvd','Acat2'],
    directness: 'secondary', direction: 'not_applicable', context: 'indirect', accuracy: 'overclaimed',
    span: 'The model of the cholesterol biosynthesis pathway presented in this work has been assembled using a variety of publicly available resources including the research findings of the LipidMaps consortium [8] and results obtained from thorough searches of the published literature that have been manually curated and validated by domain experts.',
    rationale: 'This expert-curated map substantively describes enzymatic steps for 16 supplied genes, but has no specific mouse-hepatocyte context.', flags: ['context_overclaim']
  },
  {
    id: 'RL-f7e99b9b10b42ca7b091', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'indirect', accuracy: 'overclaimed',
    span: 'These findings suggest that the INSIG-1 protein alters sterol balance by modulating SREBP processing jointly with SCAP.',
    rationale: 'Overexpression and RNAi experiments support INSIG1-SCAP control of SREBP processing. The unspecified cell system is indirect for mouse hepatocytes.', flags: ['context_overclaim']
  },
  {
    id: 'RL-1f0a922d711614ed5737', support: 'partial', studied: ['Hmgcs1'], functional: ['Hmgcs1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Using adenoviral-mediated silencing of hepatic Spring expression we found marked attenuation of SREBP2 signaling in the liver, with limited effect on the SREBP1 pathway.',
    rationale: 'The paper supports functional SCAP/SREBP machinery and hepatic Hmgcs regulation. SCAP mislocalization itself was shown mainly in engineered non-hepatocyte cells, not mouse hepatocytes as stated.', flags: ['wrong_function']
  },
  {
    id: 'RL-250f52f3f3d3826951e1', support: 'supports', studied: ['Hmgcs1'], functional: ['Hmgcs1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Furthermore, cholesterol synthesis in IKKβca;A20LKO mice was 2-fold higher compared to WT mice (Figure 5C).',
    rationale: 'The hepatocyte-specific mouse model directly measures increased cholesterol synthesis and HMGCS1 protein.', flags: []
  },
  {
    id: 'RL-48bcfac4b40852c81b72', support: 'supports',
    studied: ['Acat2','Cyp51','Fdps','Hmgcs1','Idi1','Lss','Msmo1','Mvk','Mvd','Nsdhl'],
    functional: ['Acat2','Cyp51','Fdps','Hmgcs1','Idi1','Lss','Msmo1','Mvk','Mvd','Nsdhl'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'In cholesterol biosynthesis signaling, the expression of 16 proteins (ACAA2, ACAT1, ACAT2, CYP51A1, EBP, FDPS, HADHA, HADHB, HMGCS1, IDI1, LBR, LSS, MSMO1, MVK, NSDHL, and SQLE) differed between two proteomes and eventually induced deactivation of the pathway (Figure 2E).',
    rationale: '27-hydroxycholesterol suppresses a broad set of P21 pathway proteins and transcripts in AML12 and primary mouse hepatocytes.', flags: []
  },
  {
    id: 'RL-6515a6465138462705ba', support: 'supports', studied: ['Hmgcs1'], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'We show that inhibition of Ser871 phosphorylation leads to increased cholesterol synthesis and cholesterol accumulation in the liver and, surprisingly, an elevated capacity for triglyceride synthesis.',
    rationale: 'A phosphorylation-resistant HMGCR knock-in connects AMPK-HMGCR control to hepatic cholesterol synthesis and steatosis. Hmgcs1 is measured but its specific function is not tested.', flags: []
  },
  {
    id: 'RL-7a812444f7281360d634', support: 'supports',
    studied: [], functional: [],
    directness: 'secondary', direction: 'not_applicable', context: 'indirect', accuracy: 'overclaimed',
    span: 'Taken together, many genes play a central role in cholesterol synthesis, including HMGCR, SQLE, HMGCS1, FDFT1, LSS, MVK, PMK, MVD, FDPS, CYP51, TM7SF2, LBR, MSMO1, NSDHL, HSD17B7, DHCR24, EBP, SC5D, DHCR7, IDI1/2 (Fig.',
    rationale: 'The review covers most supplied enzymes as a pathway list, supporting the mechanism but not gene-level study coverage. It is indirect rather than partial mouse-hepatocyte evidence.', flags: ['context_overclaim']
  },
  {
    id: 'RL-8313ab9a4140282ab449', support: 'supports', studied: ['Hmgcs1'], functional: ['Hmgcs1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'We demonstrate that the IKK:NF-κB axis controls DNL and cholesterol synthesis post-translationally by regulating the phosphorylation levels of AMPK and HMGCR and the protein levels of HMGCS1.',
    rationale: 'The mouse hepatocyte model connects cholesterol synthesis, HMGCS1 regulation, middle age, and steatotic liver pathology.', flags: []
  },
  {
    id: 'RL-833cf27c0cbeeeaff664', support: 'supports', studied: [], functional: [],
    directness: 'secondary', direction: 'not_applicable', context: 'partial', accuracy: 'accurate',
    span: 'Through these activities, Insig-1 and Insig-2 influence cholesterol metabolism, lipogenesis, and glucose homeostasis in diverse tissues such as adipose tissue and liver.',
    rationale: 'The review supports Insig-SCAP/HMGCR feedback and includes liver, but is secondary and not mouse-hepatocyte-specific.', flags: []
  },
  {
    id: 'RL-9229f992a4c27da67e36', support: 'partial', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Despite the simultaneous administration of the diet to mice, an additional 4-week fatostatin treatment significantly improved the oral glucose tolerance test and reduced body, adipose tissue and liver weights, fasting glycemia, alkaline phosphatase, total cholesterol, liver conjugated dienes, nitrites and triglycerides, steatosis, and histopathological total MASLD activity score.',
    rationale: 'SREBP1/2 inhibition improves cholesterol and MASLD outcomes, but the abstract does not directly measure elevated P21 sterol flux or name supplied genes.', flags: ['wrong_function']
  },
  {
    id: 'RL-a0025244798c67771989', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Furthermore, the liver of Cideb-null mice has lower rates of cholesterol biosynthesis and reduced expression levels of sterol response element-binding protein (SREBP) cleavage-activation protein (SCAP), and lower levels of nuclear form of SREBP2 and its downstream target genes in cholesterol biosynthesis pathway under a normal diet treatment.',
    rationale: "Cideb-null mouse liver links lower SCAP/nuclear SREBP2 to lower cholesterol synthesis. The abstract's ACAT is cholesterol acyltransferase and is not supplied Acat2 thiolase.", flags: []
  },
  {
    id: 'RL-b92d49a4066182657fed', support: 'supports', studied: ['Mvk','Pmvk','Fdft1'], functional: ['Mvk','Pmvk','Fdft1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Overall, these results highlight the importance of ERRα in mediating MVA/cholesterol biosynthetic pathway-induced cellular senescence in vitro in human cells and in vivo in mouse.',
    rationale: 'Perturbations of MVK, PMVK, and FDFT1 establish a cholesterol-branch senescence mechanism; HFD mouse-liver experiments support hepatic relevance.', flags: []
  },
  {
    id: 'RL-bbcd1c6f846751aa3d9b', support: 'supports', studied: ['Cyp51','Dhcr7'], functional: ['Cyp51','Dhcr7'],
    directness: 'causal', direction: 'matches', context: 'partial', accuracy: 'accurate',
    span: 'Inhibition of generation of the SREBPs active form by fatostatin or Scap siRNA in both in vivo and in vitro significantly decreased the expressions of de novo cholesterol biosynthetic enzymes, cholesterol accumulation, and progesterone (P4) production compared with the control group.',
    rationale: 'Mouse granulosa-cell experiments support conserved INSIG1-SCAP-SREBP control and Cyp51/Dhcr7 targets. Other tissue/cell type is correctly partial.', flags: []
  },
  {
    id: 'RL-c0149ee32fcc6767c368', support: 'supports', studied: ['Hmgcs1','Fdps','Fdft1'], functional: ['Hmgcs1','Fdps','Fdft1'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Consistently, knockdown of PAQR3 in primary hepatocytes significantly reduced the mRNA levels of critical cholesterol-synthesizing and uptaking genes including 3-hydroxy-3-methylglutaryl-CoA reductase (HMGCR), HMGC synthase (HMGCS), LDL receptor (LDLR) and squalene synthase (SS) (Fig.',
    rationale: 'Primary-hepatocyte and mouse-liver perturbations support PAQR3-SCAP/SREBP control and measure Hmgcs1, Fdps, and Fdft1 targets.', flags: []
  },
  {
    id: 'RL-c9f8ab00bf2d7955d073', support: 'no', studied: [], functional: [],
    directness: 'causal', direction: 'not_applicable', context: 'direct', accuracy: 'accurate', span: '',
    rationale: "This paper's ACAT2 is acyl-CoA:cholesterol acyltransferase 2 (legacy name for SOAT2), whereas supplied mouse Acat2 is cytosolic acetyl-CoA acetyltransferase/thiolase. The symbol collision does not support the supplied gene.",
    flags: ['wrong_gene']
  },
  {
    id: 'RL-cdef8d40fdbbef430a64', support: 'supports', studied: ['Cyp51'], functional: ['Cyp51'],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'These data indicate that propiconazole increases cell proliferation by increasing the levels of cholesterol biosynthesis intermediates presumably through a negative feedback mechanism within the pathway, a result of CYP51 inhibition.',
    rationale: 'CYP51 inhibition in AML12 mouse hepatocytes perturbs cholesterol intermediates and pathway feedback.', flags: []
  },
  {
    id: 'RL-dfa3085dcde3f76de26c', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'direct', accuracy: 'accurate',
    span: 'Specifically, miR-93 deficiency upregulated genes involved in fatty acid oxidation and downregulated genes associated with cholesterol biosynthesis.',
    rationale: 'MiR-93 knockout in mouse MASLD links cholesterol-biosynthesis transcription to steatosis, but the abstract names no supplied genes.', flags: []
  },
  {
    id: 'RL-fa25d9f81d5e685add1e', support: 'supports', studied: [], functional: [],
    directness: 'causal', direction: 'matches', context: 'indirect', accuracy: 'overclaimed',
    span: 'These findings suggest that the INSIG-1 protein alters sterol balance by modulating SREBP processing jointly with SCAP.',
    rationale: 'Cell experiments support INSIG1-SCAP control of SREBP processing, but the abstract has no liver or hepatocyte context.', flags: ['context_overclaim']
  }
];

const expected = packet.review_items.filter((item) => item.case_id === 'liver-p21');
if (A.length !== expected.length) throw new Error(`Expected ${expected.length} assessments, got ${A.length}`);
const expectedIds = new Set(expected.map((item) => item.review_id));
for (const a of A) {
  if (!expectedIds.delete(a.id)) throw new Error(`Unexpected or duplicate review id: ${a.id}`);
}
if (expectedIds.size) throw new Error(`Missing review ids: ${[...expectedIds].join(', ')}`);

const reviews = A.map((a) => {
  const item = items.get(a.id);
  const text = item.assessor_text.text;
  const start = a.span ? text.indexOf(a.span) : 0;
  if (a.span && start < 0) throw new Error(`Evidence span not found: ${a.id}`);
  return {
    review_id: a.id,
    assessor_id: 'primary-p21',
    support: a.support,
    studied_genes: a.studied,
    function_supported_genes: a.functional,
    directness: a.directness,
    direction: a.direction,
    assessed_context: a.context,
    context_label_accuracy: a.accuracy,
    evidence_span: a.span ? {
      text: a.span,
      start,
      end: start + a.span.length,
      source_type: item.assessor_text.text_type
    } : {
      text: '',
      start: null,
      end: null,
      source_type: ''
    },
    rationale: a.rationale,
    red_flags: a.flags
  };
});

const output = {
  schema_version: 1,
  assessor_type: 'model',
  assessor_id: 'primary-p21',
  reviews
};
fs.writeFileSync(path.join(__dirname, 'primary-p21.json'), `${JSON.stringify(output, null, 2)}\n`);
