"""Two compact scientific composites from actual ParaView images and native-channel eyes."""
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('MPLCONFIGDIR','/tmp/palace-eye-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def compose(root):
    report=json.loads((root/'eye-summary.json').read_text())
    condition=report['conditions'];ui=1/condition['bit_rate_hz']
    ranked=sorted(report['cases'],key=lambda name:report['cases'][name]['metrics']['switching_minus_quiet_peak_v'])
    for name,r in report['cases'].items():
        w=np.genfromtxt(root/f'{name}-waveforms.csv',delimiter=',',names=True)
        fig,axes=plt.subplots(1,3,figsize=(15,4),layout='constrained',facecolor='white')
        for ax,kind,title in zip(axes[:2],['mesh','field'],['Gmsh mesh','ParaView ·1GHz']):
            ax.imshow(plt.imread(root/f'{name}-{kind}.png'));ax.axis('off');ax.set_title(title,fontsize=13)
        ax=axes[2];phase=np.linspace(0,2,801)
        for activity,color in [('quiet','#356f9c'),('switching','#d24f2e')]:
            for i in range(condition['discard_bits'],condition['bits']-condition['end_margin_bits']):
                origin=2e-9+(i-.5)*ui+condition['fixed_eye_delay_s']
                ax.plot(phase,np.interp(origin+phase*ui,w['time_s'],w[f'victim_{activity}_v']),color=color,alpha=.14,lw=.65)
            ax.plot([],[],color=color,label=activity)
        ax.set(xlim=(0,2),ylim=(-.08,.83),xlabel='Time (UI)',ylabel='Victim (V)',title=f'Victim eye ·{condition["bit_rate_hz"]/1e9:g}Gb/s')
        ax.legend(loc='upper right',frameon=False,fontsize=9);ax.grid(alpha=.12)
        label='Weaker measured coupling' if name==ranked[0] else 'Stronger measured coupling'
        fig.suptitle(f'{label} ·{r["gap_mm"]:g}mm gap',fontsize=15)
        c=r['checks'];state='passed' if c['status']=='passed' and c['full_complex_channel_mesh_status']=='passed' and c['truncation_convergence']=='passed' else 'failed'
        noise=r['metrics']['switching_minus_quiet_peak_v']*1000
        fig.text(.5,.005,f'Added noise {noise:.1f}mV ·Convergence {state} ·Assumed PEC fixture ·Field: unit incident power',ha='center',fontsize=9)
        role='weaker' if name==ranked[0] else 'stronger'
        fig.savefig(root/f'{role}-coupling.png',dpi=150,facecolor='white');plt.close(fig)
    html='<!doctype html><meta charset="utf-8"><title>Crosstalk: mesh, field, eye</title><style>body{max-width:1300px;margin:32px auto;font:17px system-ui;color:#253340;background:white}img{width:100%}table{width:100%;text-align:left}td,th{padding:8px}p{line-height:1.5}</style>'
    for role in ['weaker','stronger']:html+=f'<img src="{role}-coupling.png" alt="Actual Gmsh mesh, ParaView field and victim eye for {role} measured coupling">'
    html+='<table><tr><th>Gap</th><th>Added noise</th><th>Quiet opening</th><th>Switching opening</th><th>Mesh /domain /frequency</th></tr>'
    for name in ranked:
        r=report['cases'][name];m=r['metrics'];c=r['checks']
        html+=f'<tr><td>{r["gap_mm"]:g}mm</td><td>{m["switching_minus_quiet_peak_v"]*1000:.2f}mV</td><td>{m["quiet"]["observed_center_opening_v"]*1000:.2f}mV</td><td>{m["switching"]["observed_center_opening_v"]*1000:.2f}mV</td><td>{c["full_complex_channel_mesh_status"]} /{c["truncation_convergence"]} /{c["frequency_status"]}</td></tr>'
    html+='</table><p>Preliminary synthetic testbench. Quiet and switching use the same physical channel, victim bits and loads. Field shows a slice inside true3D geometry; eyes use broadband complex channel data and a causal loaded FIR. No BER, DDR or routing qualification.</p><p><a href="eye-summary.json">Numerical checks and assumptions</a> ·<a href="render-settings.json">Shared camera and field scale</a></p>'
    (root/'index.html').write_text(html)
    print(json.dumps({'images':[str(root/f'{role}-coupling.png') for role in ['weaker','stronger']],'report':str(root/'index.html')}))


if __name__=='__main__':compose(Path(sys.argv[1]).resolve())
