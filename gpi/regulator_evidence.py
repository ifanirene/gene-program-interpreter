"""Offline regulator coverage and recurrence, independent of annotation wording."""
import pandas as pd
from .column_mapper import collapse_regulator_guides


def complete_effects(data, program_id, genes):
    rows = []
    for condition, programs in data.items():
        frame = collapse_regulator_guides(programs.get(program_id, pd.DataFrame()),
                                         significant_only=False)
        for gene in sorted(set(genes)):
            matched = frame[frame.target_gene == gene] if not frame.empty else frame
            item = dict(gene=gene, condition=condition, available=not matched.empty)
            if not matched.empty:
                row = matched.iloc[0]
                item.update(guide=str(row.get('grna_target', gene)),
                            log2fc=float(row.log_2_fold_change),
                            significant=bool(row.significant) if 'significant' in row else None,
                            adj_pvalue=float(row.adj_p_value) if pd.notna(row.get('adj_p_value')) else None)
            rows.append(item)
    return rows


def recurrence(data):
    """Count gene-level significant programs and top slots across ALL input programs."""
    from .evidence_context import select_top_condition_regulators
    all_programs = {pid for programs in data.values() for pid in programs}
    significant, top = {}, {}
    for programs in data.values():
        for pid, frame in programs.items():
            for gene in frame.loc[frame.get('significant', pd.Series(False,index=frame.index)), 'target_gene']:
                significant.setdefault(gene, set()).add(pid)
    for pid in sorted(all_programs):
        for groups in select_top_condition_regulators(data, pid).values():
            for regs in groups.values():
                for reg in regs:
                    top.setdefault(reg['gene'], set()).add(pid)
    total = len(all_programs)
    return {gene: dict(significant_programs=len(significant.get(gene, set())),
                       top_programs=len(top.get(gene, set())), tested_programs=total,
                       broad_top_hit=total > 1 and len(top.get(gene, set())) / total >= 0.5)
            for gene in sorted(set(significant) | set(top))}


def audit_research_coverage(bundle, result):
    """Report explicit coverage; selection/identifier resolution alone is not research."""
    genes = {r['gene'] for regs in bundle.get('perturbation_regulators', {}).values() for r in regs}
    reported = {r['gene'].casefold():r for r in result.get('regulator_coverage', [])}
    return [{**reported.get(g.casefold(), dict(gene=g, status='not_researched', note='No explicit coverage record')),
             'selected_conditions': [c for c,regs in bundle.get('perturbation_regulators', {}).items()
                                     if any(r['gene']==g for r in regs)]}
            for g in sorted(genes)]
