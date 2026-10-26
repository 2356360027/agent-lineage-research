"""Paired, template-cluster-aware summaries and pre-outcome size sensitivity.

These constructed templates are purposively selected. Bootstrap bands describe
resampling sensitivity, not guaranteed coverage of arbitrary agent workloads.
"""
from collections import defaultdict
import math
import random
from statistics import mean


def sensitivity():
    rows=[]
    for n in (48,96,144,192):
        # 12 structural templates; changing n here changes worlds per template,
        # not the number of genuinely different semantic structures.
        m=n/12
        for rho in (0,0.10,0.25,0.50):
            design_effect=1+(m-1)*rho
            effective=n/design_effect
            for discordance in (0.10,0.25,0.50,1.0):
                se=math.sqrt(discordance/effective)
                rows.append({'bases':n,'templates':12,'instances_per_template':m,
                    'assumed_within_template_rho':rho,'assumed_paired_discordance':discordance,
                    'effective_n_approx':effective,
                    'normal_95_halfwidth_at_zero_difference':1.959963984540054*se,
                    'normal_80_power_effect_approx':(1.959963984540054+0.8416212335729143)*se})
    return {'kind':'PRE_CONFIRMATION_PLANNING_APPROXIMATION','rows':rows,
        'limitations':'Normal/design-effect approximation, not an exact power calculation. '
                     'No observed model outcomes used. Twelve purposive templates do not establish '
                     'a random population sample; rho and discordance are assumptions. Values above '
                     'one imply an infeasible difference, not a reason to tune or replace tasks.'}


def paired_price_rows(observations, method, expected_bases, low=1, high=20):
    """Input: one label per base x cache-kind x price x method.

    Only the prespecified insufficient-cache contrast is selected. Missing or
    duplicate cells fail, rather than silently changing the planned denominator.
    """
    indexed={}
    for row in observations:
        if row['method']!=method or row['sufficient'] or row['price'] not in (low,high):
            continue
        key=(row['base_id'],row['price'])
        if key in indexed: raise ValueError('Duplicate paired cell')
        indexed[key]=row
    bases=sorted(expected_bases)
    if len(set(bases))!=len(bases): raise ValueError('Duplicate planned base')
    if {b for b,p in indexed}-set(bases): raise ValueError('Unplanned paired base')
    result=[]
    for base in bases:
        if (base,low) not in indexed or (base,high) not in indexed:
            raise ValueError('Incomplete price pair: '+base)
        a,b=indexed[base,low],indexed[base,high]
        if (a['template'],a['family'])!=(b['template'],b['family']):
            raise ValueError('Pair identity drift')
        left,right=a['admissibility'],b['admissibility']
        statuses=('applicable','inapplicable','unknown','invalid','missing')
        if left not in statuses or right not in statuses:
            raise ValueError('Unrecognized semantic status')
        # Invalid/unknown are not successful judgments. They are separate from
        # observed false accepts; conservative bounds treat them as unresolved.
        la,lb=left=='applicable',right=='applicable'
        ua,ub=left in ('unknown','invalid','missing'),right in ('unknown','invalid','missing')
        result.append({'base_id':base,'template':a['template'],'family':a['family'],
            'value':int(la)-int(lb),'low_false_applicable':int(la),'high_false_applicable':int(lb),
            'low_unresolved':int(ua),'high_unresolved':int(ub),
            'conservative_difference_min':int(la)-int(lb or ub),
            'conservative_difference_max':int(la or ua)-int(lb)})
    return result


def quantile(values,p):
    ordered=sorted(values)
    if not ordered: raise ValueError('Empty statistic')
    x=(len(ordered)-1)*p
    lo=int(math.floor(x)); hi=int(math.ceil(x))
    return ordered[lo]+(ordered[hi]-ordered[lo])*(x-lo)


def clustered_difference(rows, reps=2000, seed=20261028):
    if not rows: raise ValueError('No paired bases')
    if len({r['base_id'] for r in rows})!=len(rows):
        raise ValueError('A base must contribute exactly one paired difference')
    clusters=defaultdict(lambda:defaultdict(list))
    for r in rows:
        if not -1<=r['value']<=1: raise ValueError('Difference outside [-1,1]')
        clusters[r['family']][r['template']].append(r['value'])
    # Equal-family, equal-template weights; within-template bases equally weighted.
    point=mean(mean(mean(v) for v in ts.values()) for ts in clusters.values())
    rng=random.Random(seed); samples=[]
    for _ in range(reps):
        families=[]
        for family in sorted(clusters):
            templates=clusters[family]; names=sorted(templates); draws=[]
            for name in rng.choices(names,k=len(names)):
                values=templates[name]
                draws.append(mean(rng.choices(values,k=len(values))))
            families.append(mean(draws))
        samples.append(mean(families))
    interval=[quantile(samples,.025),quantile(samples,.975)]
    return {'paired_bases':len(rows),'template_clusters':sum(map(len,clusters.values())),
        'families':len(clusters),'weighted_difference':point,
        'bootstrap_95_resampling_band':interval,'bootstrap_replicates':reps,'seed':seed,
        'degenerate_band':interval[0]==interval[1],
        'family_differences':{f:mean(mean(v) for v in ts.values()) for f,ts in clusters.items()},
        'template_differences':{t:mean(v) for ts in clusters.values() for t,v in ts.items()},
        'claim_limit':'Sensitivity to resampling observed purposive templates and bases, not a '
                      'population safety guarantee. A [0,0] band with no observed errors does NOT '
                      'prove zero error risk or equivalence. No independent n from price variants.'}
