"""Actual sealed E1/E2 aggregates; Python-only editable scientific figure.

Fixed denominators retain invalid/unknown labels. No artificial observations,
inferred confidence bars, missing-value deletion or effect-selected methods.
"""
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT=Path(__file__).resolve().parents[1]
mpl.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],
    'font.size':7,'axes.titlesize':8,'axes.labelsize':7,'xtick.labelsize':7,'ytick.labelsize':7,
    'legend.fontsize':7,'svg.fonttype':'none','pdf.fonttype':42,
    'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.65,
    'xtick.major.width':.65,'ytick.major.width':.65,'legend.frameon':False})


def main(output):
    output.mkdir(parents=True,exist_ok=False)
    models=[];hashes={}
    for family,title in (('qwen','Qwen2.5-7B'),('mistral','Mistral-7B-v0.3')):
        path=ROOT/'configs/run_records'/f'reuse_confirmation_{family}_20261027.json'
        d=json.loads(path.read_bytes())
        if d['responses']!=1920 or d['missing']!=0 or not d['source_rescoring_repeated']:
            raise ValueError('Sealed complete verified record required')
        hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest();models.append((title,d))
    (output/'source_data.json').write_text(json.dumps({t:d for t,d in models},indent=2)+'\n',encoding='utf-8')
    fig,axes=plt.subplots(2,2,figsize=(7.2047244094,5.5118110236),
        gridspec_kw={'height_ratios':[1,1.12]})
    fig.subplots_adjust(left=.085,right=.985,bottom=.19,top=.94,wspace=.26,hspace=.52)
    colors={'M1':'#60676E','M2':'#337C91'}
    for col,(title,d) in enumerate(models):
        ax=axes[0,col]
        for method,marker,style in (('M1','o','-'),('M2','s','--')):
            rows=[d['labels'][method][str(p)] for p in (1,10,20)]
            if any(r['negative_n']!=96 for r in rows):raise ValueError('Wrong negative denominator')
            rates=[100*r['false_applicable']/r['negative_n'] for r in rows]
            ax.plot([1,10,20],rates,marker=marker,linestyle=style,color=colors[method],
                linewidth=1.15,markersize=3.8,label=method)
            for x,y in zip((1,10,20),rates):
                other=d['labels']['M2' if method=='M1' else 'M1'][str(x)]
                other_rate=100*other['false_applicable']/other['negative_n']
                ax.annotate(f'{y:.1f}',(x,y),xytext=(0,6 if y>=other_rate else -11),
                    textcoords='offset points',ha='center',fontsize=6.5,color=colors[method])
        ax.set(xlim=(-1,22),ylim=(0,112),xticks=[1,10,20],yticks=[0,25,50,75,100],
            xlabel='Declared cache price (recompute = 10)',ylabel='False-applicable labels (%)',title=title)
        ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=0))
        ax.text(-.16,1.06,'ab'[col],transform=ax.transAxes,weight='bold',fontsize=8)
        ax.legend(loc='lower left',ncols=2,handlelength=2.0)
        ax.set_axisbelow(True);ax.grid(axis='y',color='#E1E4E7',linewidth=.5)

        ax=axes[1,col]
        for i,method in enumerate(('M1','M2','M3','M4','M5','M6')):
            r=d['low_price_actions'][method]
            if r['negative_n']!=96 or r['positive_n']!=96:raise ValueError('Wrong action denominators')
            unsafe=100*r['unsafe_reuse']/96;useful=100*r['useful_reuse']/96
            ax.bar(i-.18,unsafe,width=.33,color='#C5834D',edgecolor='#855429',linewidth=.5,
                hatch='///',label='Unsafe reuse' if i==0 else None)
            ax.bar(i+.18,useful,width=.33,color='#6AA6AB',edgecolor='#326B72',linewidth=.5,
                label='Useful reuse' if i==0 else None)
            for x,v in ((i-.18,unsafe),(i+.18,useful)):
                ax.text(x,v+2,f'{v:.0f}',ha='center',va='bottom',fontsize=6)
        ax.set(ylim=(0,113),yticks=[0,25,50,75,100],xticks=list(range(6)),
            xticklabels=['M1','M2','M3','M4','M5','M6'],ylabel='Low-price reuse rate (%)',
            title=title+' | safety and retained reuse')
        ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=0))
        ax.text(-.16,1.06,'cd'[col],transform=ax.transAxes,weight='bold',fontsize=8)
        ax.set_axisbelow(True);ax.grid(axis='y',color='#E1E4E7',linewidth=.5)
    handles,labels=axes[1,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.115),ncols=2)
    fig.text(.085,.078,'Each rate: n = 96 negative or 96 positive contexts; 12 templates × 8 paired base instances.',fontsize=7)
    fig.text(.085,.047,'Strict-format invalid outputs retained: Qwen 0/1920; Mistral 128/1920. No execution success inferred.',fontsize=7)
    fig.savefig(output/'confirmation.svg',facecolor='white')
    fig.savefig(output/'confirmation.pdf',facecolor='white')
    fig.savefig(output/'confirmation.png',dpi=600,facecolor='white')
    fig.savefig(output/'preview.png',dpi=300,facecolor='white')
    fig.savefig(output/'confirmation.tiff',dpi=600,facecolor='white')
    plt.close(fig)
    qa={'backend':'python','width_mm':183,'height_mm':140,'source_hashes':hashes,
        'n_base_per_model':96,'n_templates':12,'n_instances_per_template':8,
        'before_after_counts':'All1920 physical responses/model retained in source records; no exclusions.',
        'panel_scope':'a/b all prices of prespecified M1/M2; c/d all six methods at prespecified price1.',
        'missing_or_invalid':'Retained in denominator, not counted as false-applicable nor as valid semantic judgment.',
        'error_bars':'None: descriptive rates. Frozen paired clustered contrasts reported separately.',
        'no_external_artwork':True,'no_image_manipulation':True,
        'source_validation_and_visual_inspection':'Pending separate preflight and rendered inspection.'}
    (output/'qa.json').write_text(json.dumps(qa,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    main(p.parse_args().output)
