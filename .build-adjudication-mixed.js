const fs = require('fs');

const taskPath = '/Volumes/IF_PHAGE/gene-program-interpreter/benchmarks/literature-v1/baseline-v4/reference_quality/adjudication_tasks/mixed.json';
const task = JSON.parse(fs.readFileSync(taskPath, 'utf8'));
const byId = new Map(task.items.map(item => [item.review_id, item]));

function decide(id, source, overrides = {}) {
  const item = byId.get(id);
  if (!item) throw new Error(`unknown adjudication id ${id}`);
  return {
    ...item[source],
    ...overrides,
    review_id: id,
    assessor_id: task.required_assessor_id,
  };
}

const reviews = [
  decide('RL-10c90de4214dc35aeb36', 'secondary', {
    direction: 'not_applicable',
    rationale: 'The review directly supports ALDH3A2 ER localization and broader peroxisome–ER lipid interplay, but not Hao2/Eci2 or mouse hepatocyte MASLD. The function is non-directional and the context is indirect.',
  }),
  decide('RL-1b94bd4f696f98c72ae7', 'secondary'),
  decide('RL-2031655117f8baca41ce', 'primary'),
  decide('RL-26961567be69d2771a0f', 'secondary'),
  decide('RL-48422b8ea27c2539bf2b', 'primary'),
  decide('RL-54dff275faab78dc35a5', 'secondary'),
  decide('RL-5551cbd4f12617c28768', 'secondary'),
  decide('RL-560b17922f0923a44c21', 'primary'),
  decide('RL-7b48bebde36f0916fddc', 'primary', {
    direction: 'unclear',
    rationale: 'The exact brain-endothelial review states that empirical support for ciliary mechanosensing is absent and favors nanotubules in BBB genesis. It therefore does not support the claimed ciliary function; the direction cannot be established.',
  }),
  decide('RL-8e8098d397b5ef463914', 'secondary'),
  decide('RL-bfd8007f2b08a42926ff', 'secondary'),
  decide('RL-e259e3212002092d0f1d', 'secondary', {
    direction: 'not_applicable',
    red_flags: ['wrong_gene'],
    rationale: 'The MASLD review directly supports dysregulated fatty-acid oxidation and DGAT2-driven lipid-droplet storage, but it does not study Fitm1, Hao2, or Eci2. This is useful pathway context, not supplied-gene evidence.',
  }),
];

const expected = task.items.map(item => item.review_id);
if (reviews.length !== expected.length || new Set(reviews.map(r => r.review_id)).size !== expected.length) {
  throw new Error('adjudication count or uniqueness mismatch');
}
const missing = expected.filter(id => !reviews.some(review => review.review_id === id));
if (missing.length) throw new Error(`missing adjudications: ${missing.join(', ')}`);

fs.writeFileSync(task.output_path, JSON.stringify({ reviews }, null, 2) + '\n');
console.log(`wrote ${reviews.length} adjudications to ${task.output_path}`);
