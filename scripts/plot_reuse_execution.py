"""Draw all sealed E3 conditions; no inference, imputation or outcome selection.

Counts are descriptive, not independent-task estimates. Cost is a component
account including allocations, NOT observed production end-to-end latency.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('M1', 'M2', 'M3', 'M4', 'M5', 'M6')
PRICES = ('1', '10', '20')
USES = ('1', '10', '100')
mpl.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'DejaVu Sans'],
    'font.size': 7, 'axes.labelsize': 7, 'axes.titlesize': 8,
    'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5, 'legend.fontsize': 6.5,
    'svg.fonttype': 'none', 'pdf.fonttype': 42,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': .6, 'xtick.major.width': .6, 'ytick.major.width': .6,
    'legend.frameon': False,
})


def validate_record(d):
    if (d['status'] != 'E3_COMPLETED_FROZEN_SCORE_REPRODUCED'
            or not d['source_rescoring_repeated'] or not d['all_services_closed']
            or d['logical_cells'] != 432 or d['bases'] != 12 or d['templates'] != 12
            or set(d['groups']) != set(METHODS)):
        raise ValueError('Complete frozen E3 record required')
    for method in METHODS:
        if set(d['groups'][method]) != set(PRICES):
            raise ValueError('Missing price')
        for price in PRICES:
            r = d['groups'][method][price]
            if (r['n'], r['positive_n'], r['negative_n']) != (24, 12, 12):
                raise ValueError('Wrong fixed denominator')
            counts = outcome_counts(r)
            if any(type(x) is not int or x < 0 for x in counts) or sum(counts) != 24:
                raise ValueError('Invalid mutually exclusive outcome counts')
            if r['negative_snapshot_lucky_cache_matches'] != 0:
                raise ValueError('Figure must distinguish unsafe lucky cache matches')
            if r['positive_cache_first_correct'] != r['cache_first_correct']:
                raise ValueError('Unexpected cache-first successes')
            if sum(r['first_statuses'].values()) != 24:
                raise ValueError('Missing first-execution outcomes')
            costs = r['mean_modeled_total_seconds_by_cache_uses']
            if set(costs) != set(USES):
                raise ValueError('Missing amortization choice')
            if any(not math.isfinite(v) or v <= 0 for v in costs.values()):
                raise ValueError('Log scale requires strictly positive finite costs')
            if not costs['1'] >= costs['10'] >= costs['100']:
                raise ValueError('Unexpected amortization order')


def outcome_counts(row):
    return (row['cache_first_correct'], row['final_correct'] - row['cache_first_correct'],
            row['n'] - row['final_correct'] - row['no_final_answer'], row['no_final_answer'])


def main(output):
    records = []
    sources = {}
    for family, title in (('qwen', 'Qwen2.5-7B'), ('mistral', 'Mistral-7B-v0.3')):
        path = ROOT / 'configs/run_records' / f'reuse_e3_{family}_20261027.json'
        data = path.read_bytes()
        record = json.loads(data)
        validate_record(record)
        if record['model_family'] != family:
            raise ValueError('Model mismatch')
        records.append((title, record))
        sources[path.name] = hashlib.sha256(data).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'source_data.json').open('x', encoding='utf-8') as stream:
        json.dump({'records': {t: d for t, d in records}, 'source_sha256': sources},
                  stream, indent=2)
        stream.write('\n')
    fig = plt.figure(figsize=(7.2047244094, 7.2834645669))
    axes_top = [fig.add_axes([.10 + .49 * i, .54, .37, .39]) for i in range(2)]
    axes_cost = [fig.add_axes([.10 + .49 * i, .18, .37, .22]) for i in range(2)]
    outcome_colors = ('#5D999D', '#CBD0D5', '#C58B54', '#795B79')
    hatches = ('', '', '///', 'xxx')
    outcome_names = ('Correct cache result', 'Correct via recompute / fallback',
                     'Incorrect final answer', 'No final answer')
    price_colors = ('#337C91', '#60676E', '#B57A37')
    markers = ('o', 's', '^')
    for col, (title, d) in enumerate(records):
        ax = axes_top[col]
        ys, names = [], []
        for m, method in enumerate(METHODS):
            for p, price in enumerate(PRICES):
                y = 4 * m + p
                ys.append(y)
                names.append(method + ' / ' + price)
                row = d['groups'][method][price]
                start = 0
                for category, count in enumerate(outcome_counts(row)):
                    ax.barh(y, count, height=.76, left=start,
                            color=outcome_colors[category], edgecolor='white',
                            linewidth=.35, hatch=hatches[category])
                    if count and category != 1:
                        ax.text(start + count / 2, y, str(count), ha='center',
                                va='center', fontsize=6, color='white' if category == 3 else '#172D38',
                                bbox={'facecolor': outcome_colors[category], 'edgecolor': 'none', 'pad': .15})
                    start += count
                ax.text(24.6, y, str(row['final_correct']) + '/24',
                        va='center', ha='left', fontsize=6)
        ax.set(xlim=(0, 29), ylim=(22.8, -.9), xticks=[0, 6, 12, 18, 24],
               yticks=ys, yticklabels=names, xlabel='Context count (24 per condition)',
               title=title + ' | actual outcomes')
        ax.set_ylabel('Method / declared cache price', labelpad=3)
        ax.spines['right'].set_visible(False)
        ax.text(-.22, 1.06, 'ab'[col], transform=ax.transAxes, weight='bold', fontsize=8)
        ax.text(24.5, -1.1, 'Final\ncorrect', fontsize=6, va='bottom')

        ax = axes_cost[col]
        for m, method in enumerate(METHODS):
            for p, price in enumerate(PRICES):
                costs = d['groups'][method][price]['mean_modeled_total_seconds_by_cache_uses']
                xs = [m + (p - 1) * .24 + (u - 1) * .055 for u in range(3)]
                vals = [costs[u] for u in USES]
                ax.plot(xs, vals, color=price_colors[p], linewidth=.55, alpha=.7)
                for u, (x, value) in enumerate(zip(xs, vals)):
                    ax.plot(x, value, marker=markers[u], color=price_colors[p],
                            markerfacecolor=price_colors[p] if u == 0 else 'white',
                            markeredgewidth=.65, markersize=3.0, linestyle='none')
        ax.set_yscale('log')
        ax.set(xlim=(-.5, 5.5), ylim=(.0002, 10), xticks=range(6), xticklabels=METHODS,
               ylabel='Mean accounted time (s / context; log)',
               title=title + ' | component-cost account')
        ax.set_yticks([.001, .01, .1, 1, 10], labels=['0.001', '0.01', '0.1', '1', '10'])
        ax.minorticks_off()
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#E4E6E8', linewidth=.45)
        ax.text(-.22, 1.08, 'cd'[col], transform=ax.transAxes, weight='bold', fontsize=8)
    fig.legend(handles=[Patch(facecolor=c, hatch=h, edgecolor='white', label=n)
                        for c, h, n in zip(outcome_colors, hatches, outcome_names)],
               loc='center', bbox_to_anchor=(.53, .465), ncols=2, columnspacing=1.4)
    fig.legend(handles=[Line2D([], [], color=c, marker='o', linestyle='none',
                              markersize=3, label='Price ' + p) for c, p in zip(price_colors, PRICES)],
               loc='center', bbox_to_anchor=(.53, .115), ncols=3, columnspacing=2.5)
    fig.legend(handles=[Line2D([], [], color='#3C4145', marker=m, linestyle='none',
                              markerfacecolor='#3C4145' if i == 0 else 'white',
                              markersize=3, label=u + ' cache use' + ('s' if i else ''))
                        for i, (m, u) in enumerate(zip(markers, USES))],
               loc='center', bbox_to_anchor=(.53, .079), ncols=3, columnspacing=1.8)
    fig.text(.5, .032, '12 fixed bases/model; correct Q supplied. Allocated cost is not production latency.',
             ha='center', fontsize=6.5)
    fig.savefig(output / 'execution.svg', facecolor='white')
    fig.savefig(output / 'execution.pdf', facecolor='white')
    fig.savefig(output / 'execution.png', dpi=600, facecolor='white')
    fig.savefig(output / 'execution.tiff', dpi=600, facecolor='white')
    fig.savefig(output / 'preview.png', dpi=300, facecolor='white')
    plt.close(fig)
    qa = {'backend': 'python', 'width_mm': 183, 'height_mm': 185,
          'source_sha256': sources, 'logical_cells_before': 864, 'logical_cells_after': 864,
          'excluded_cells': 0, 'methods': list(METHODS), 'prices': list(PRICES),
          'cache_uses': list(USES), 'observations': '12 fixed bases/model, not864 independent tasks',
          'statistics': 'Descriptive counts and recorded component means; no error bars or tests',
          'image_integrity': 'No artwork, no image adjustments, no synthetic plotting values',
          'validation': 'Fixed counts, finite positive cost, all outcomes/prices/amortizations checked',
          'visual_review': 'See separate QA_REVIEW after rendered inspection'}
    with (output / 'qa.json').open('x', encoding='utf-8') as stream:
        json.dump(qa, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': 'RENDERED_PENDING_VISUAL_QA', 'logical_cells': 864}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
