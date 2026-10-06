"""Compare only completed native raw S exports; mesh convergence is a separate check."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--tight',required=True,type=Path)
parser.add_argument('--wide',required=True,type=Path)
parser.add_argument('--refined',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
os.environ['MPLCONFIGDIR'] = str(args.output/'matplotlib-cache')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'python'))
from results import read_s
import matplotlib.pyplot as plt
import numpy as np

cases = {}
for name,directory in [('tight',args.tight),('wide',args.wide),('refined',args.refined)]:
    result = json.loads((directory/'result.json').read_text())
    if result['status'] != 'native_passed': raise ValueError('Comparison requires completed passing native cases')
    samples = read_s(directory/'postpro/port-S.csv')
    if len(samples) != 1: raise ValueError('Bounded comparison requires one frequency')
    cases[name] = samples[0]
if len({f for f,_ in cases.values()}) != 1: raise ValueError('Comparison frequency mismatch')
setup = json.loads((args.tight/'setup.json').read_text())
refined_setup = json.loads((args.refined/'setup.json').read_text())
wide_setup = json.loads((args.wide/'setup.json').read_text())
models = {name:json.loads((directory/'model.json').read_text()) for name,directory in
          [('tight',args.tight),('wide',args.wide),('refined',args.refined)]}
if hashlib.sha256((args.tight/'circuit.json').read_bytes()).digest() != hashlib.sha256((args.refined/'circuit.json').read_bytes()).digest():
    raise ValueError('Mesh comparison requires identical physical Circuit JSON bytes')
if {k:v for k,v in setup.items() if k != 'mesh'} != {k:v for k,v in refined_setup.items() if k != 'mesh'}:
    raise ValueError('Mesh comparison may change only mesh settings, not the material/testbench/truncation')
before, after = setup['mesh'], refined_setup['mesh']
if after['near_mm']>before['near_mm'] or after['far_mm']>before['far_mm'] or after['transition_mm']<before['transition_mm'] or after == before:
    raise ValueError('Refinement requires tighter mesh settings with no coarser setting')
before_tets = json.loads((args.tight/'mesh-report.json').read_text())['field_tetrahedra']
after_tets = json.loads((args.refined/'mesh-report.json').read_text())['field_tetrahedra']
if after_tets <= before_tets: raise ValueError('Refinement must increase actual exported field tetrahedra')
if setup != wide_setup or any(models['tight'][key] != models['wide'][key] for key in ['board','stackup','reference','units']):
    raise ValueError('Spacing comparison requires common board/material/reference and analysis setup')
def signal_shape(signal):
    return {key:signal[key] for key in ['trace_id','source_trace_id','source_port_ids','x_min_mm','x_max_mm','width_mm']} | {
        'pads':[{key:pad[key] for key in ['id','x_mm','width_mm','height_mm']} for pad in signal['pads']]}
if [signal_shape(s) for s in models['tight']['signals']] != [signal_shape(s) for s in models['wide']['signals']]:
    raise ValueError('Spacing comparison must preserve signal lengths, widths, endpoint pads and identities')
if abs(sum(s['y_mm'] for s in models['tight']['signals'])-sum(s['y_mm'] for s in models['wide']['signals']))>1e-9 or models['wide']['gap_mm']<=models['tight']['gap_mm']:
    raise ValueError('Wide fixture must increase gap about the same pair centerline')
base, refined = cases['tight'][1], cases['refined'][1]
selected = [(2,0),(3,0),(0,2),(0,3),(2,1),(3,1),(1,2),(1,3)]
absolute = float(np.max(np.abs(refined-base)))
relative = max(float(abs(refined[i,j]-base[i,j])/max(abs(base[i,j]),1e-12)) for i,j in selected)
criteria = setup['checks']
passed = absolute <= criteria['convergence_absolute'] and relative <= criteria['convergence_relative']
summary = {'frequency_hz':cases['tight'][0],'source':'Completed raw native port-S.csv exports',
           'coupling_db':{name:{'near_s31':float(20*np.log10(abs(s[2,0]))),'far_s41':float(20*np.log10(abs(s[3,0])))} for name,(_,s) in cases.items()},
           'mesh_comparison':{'status':'passed' if passed else 'failed','maximum_absolute_all_s_change':absolute,
                              'maximum_relative_selected_coupling_change':relative,'criteria':criteria,
                              'field_tetrahedra_before':before_tets,'field_tetrahedra_after':after_tets,
                              'selected_entries_one_based':[[i+1,j+1] for i,j in selected]},
           'truncation_convergence':'not_evaluated',
           'interpretation':'One local mesh refinement is not general convergence or DDR qualification; wider spacing comparison is conditional on this explicit synthetic fixture.'}
(args.output/'comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
fig,axes = plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
for i,key in enumerate(['near_s31','far_s41']):
    axes[i].bar([f'{name.title()} {models[name]["gap_mm"]:.4g} mm gap' for name in ['tight','wide']],
                [summary['coupling_db'][n][key] for n in ['tight','wide']],color=['#b76f21','#298379'])
    axes[i].set_ylabel('Native coupling magnitude (dB)');axes[i].set_title(key.replace('_',' '));axes[i].set_ylim(-90,0)
fig.suptitle(f'Native Palace synthetic two-route fixture, {cases["tight"][0]/1e9:g} GHz\nExplicit PEC / constant dielectric / four internal {setup["port_resistance_ohms"]:g}Ω taps')
fig.savefig(args.output/'spacing-comparison.png',dpi=150);plt.close(fig)
print(json.dumps(summary,indent=2))
