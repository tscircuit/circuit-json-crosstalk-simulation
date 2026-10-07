"""Diagnose the saved straight-pair example. Never mesh, solve or alter inputs."""
import hashlib
import json
from pathlib import Path
import re
import sys

import meshio
import numpy as np
from eyes import mesh_refinement, metrics, waveforms
from results import read_s


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixed_inputs(paths):
    """Fail before comparison if CAD, apertures, materials or setup changed."""
    models = {k: json.loads((p/'model.json').read_text()) for k,p in paths.items()}
    shared = {}
    for name in ['circuit.json', 'board/board.brep', 'terminal-map.json']:
        hashes = {digest(p/name) for p in paths.values()}
        if len(hashes) != 1:
            raise ValueError(f'Physical comparison changed {name}')
        shared[name] = hashes.pop()
    if digest(paths['coarse']/'geometry.brep') != digest(paths['channel']/'geometry.brep'):
        raise ValueError('Mesh comparison changed extended CAD including apertures/air')
    shared['coarse_fine_extended_CAD'] = digest(paths['channel']/'geometry.brep')
    if any(m['stackup'] != models['channel']['stackup'] for m in models.values()):
        raise ValueError('Resolved material data changed')
    fine = dict(models['channel']['setup']); coarse = dict(models['coarse']['setup'])
    fm = fine.pop('mesh'); cm = coarse.pop('mesh')
    if fine != coarse or set(fm) != set(cm) or not mesh_refinement(fm,cm):
        raise ValueError('Refinement must tighten mesh only, with no coarser setting')
    domain = dict(models['domain']['setup']); base = dict(models['channel']['setup'])
    expanded = domain.pop('air_padding_mm'); original = base.pop('air_padding_mm')
    if domain != base or expanded <= original:
        raise ValueError('Domain comparison must increase air padding only')
    return models, shared


def point_change(a,b):
    phase = float(np.rad2deg(np.angle(b*np.conj(a)))) if abs(a)*abs(b) else None
    radial = float(abs(abs(b)-abs(a)))
    angular = float(2*np.sqrt(abs(a)*abs(b))*abs(np.sin(np.deg2rad(phase)/2))) if phase is not None else 0.0
    return {'before_real':float(a.real),'before_imag':float(a.imag),
            'after_real':float(b.real),'after_imag':float(b.imag),
            'before_magnitude':float(abs(a)),'after_magnitude':float(abs(b)),
            'absolute_complex_change':float(abs(b-a)),
            'radial_magnitude_difference':radial,'angular_component':angular,
            'phase_difference_deg':phase}


def term_change(f,a,b):
    delta = abs(b-a); relative = delta/np.maximum(abs(a),1e-4)
    i,j = int(np.argmax(delta)), int(np.argmax(relative))
    return {'maximum_absolute_change':float(delta[i]),'absolute_worst_frequency_hz':float(f[i]),
            'at_absolute_worst':point_change(a[i],b[i]),
            'maximum_relative_change':float(relative[j]),'relative_worst_frequency_hz':float(f[j]),
            'at_relative_worst':point_change(a[j],b[j]),'relative_denominator_floor':1e-4}


def matrix_change(f,a,b):
    k,i,j = np.unravel_index(np.argmax(abs(b-a)),a.shape)
    return {'maximum_all_S_absolute_change':float(abs(b[k,i,j]-a[k,i,j])),
            'worst_term':f'S{i+1}{j+1}','worst_frequency_hz':float(f[k]),
            'worst_term_components':point_change(a[k,i,j],b[k,i,j]),
            'NEXT_S42':term_change(f,a[:,3,1],b[:,3,1]),
            'FEXT_S41':term_change(f,a[:,3,0],b[:,3,0]),
            'through_S43':term_change(f,a[:,3,2],b[:,3,2])}


def local_mesh(path,model):
    mesh = meshio.read(path/'model.msh')
    cells = np.concatenate([b.data[mesh.cell_data['gmsh:physical'][i]==2]
                            for i,b in enumerate(mesh.cells) if b.type=='tetra'])
    xyz = mesh.points[cells]; cent = xyz.mean(axis=1)
    signals = sorted(model['signals'],key=lambda s:s['y_mm'])
    y0 = signals[0]['y_mm']+signals[0]['width_mm']/2
    y1 = signals[1]['y_mm']-signals[1]['width_mm']/2
    x0,x1 = signals[0]['x_min_mm'],signals[0]['x_max_mm']
    middle = (x0+x1)/2; half = (x1-x0)/4
    thickness = model['stackup']['layers'][1]['thickness_mm']
    selected = (abs(cent[:,0]-middle)<half)&(cent[:,1]>y0)&(cent[:,1]<y1)&(cent[:,2]>0)&(cent[:,2]<thickness)
    if not selected.any():
        raise ValueError('No dielectric cells in central gap probe')
    local = xyz[selected]
    longest = np.max([np.linalg.norm(local[:,a]-local[:,b],axis=1)
                      for a,b in [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]],axis=0)
    return {'centroid_probe_bounds_mm':[middle-half,y0,0,middle+half,y1,thickness],
            'selected_tetrahedra':int(selected.sum()),
            'longest_edge_mm_p10_median_p90':np.quantile(longest,[.1,.5,.9]).tolist(),
            'xyz_extent_mm_p10_median_p90':np.quantile(np.ptp(local,axis=1),[.1,.5,.9],axis=0).tolist(),
            'scope':'Centroid/edge statistics indicate resolution; they are not an EM error estimator.'}


def diagnose(root):
    config = json.loads((root/'eye-input.json').read_text())
    summary = json.loads((root/'eye-summary.json').read_text())
    condition = config['conditions']
    if condition != summary['conditions']:
        raise ValueError('Signal conditions differ from saved eye results')
    if (condition['source_resistance_ohms'] != 50 or condition['load_resistance_ohms'] != 50 or
        condition['quiet_aggressor_voltage_v'] != 0 or
        [condition['victim_source_port'],condition['aggressor_source_port'],condition['victim_receiver_port']] != [3,2,4]):
        raise ValueError('Diagnosis uses only the declared matched 50 ohm P3/P2 toP4 testbench')
    out = {'status':summary['status'],'new_solver_runs':0,'cases':{}}
    for name in ['close','separated']:
        paths = {k:root/name/k for k in ['coarse','channel','domain']}
        models,identity = fixed_inputs(paths)
        channels = {}
        solver = {}
        for kind,path in paths.items():
            receipt = json.loads((path/'native-receipt.json').read_text())
            if receipt['pipeline_status'] != 'passed':
                raise ValueError('Diagnosis requires completed native outputs')
            samples = read_s(path/'postpro/port-S.csv')
            f = np.array([x for x,_ in samples]); s = np.array([v for _,v in samples])
            if not np.array_equal(f,np.array(models[kind]['setup']['frequency_hz'])):
                raise ValueError('Raw frequency grid differs from configured grid')
            channels[kind] = f,s
            blocks = re.findall(r'Residual norms for GMRES solve(.*?)GMRES solver converged in (\d+) iterations?',(path/'palace.log').read_text(),re.S)
            if len(blocks) != 4*len(f):
                raise ValueError('Incomplete native convergence log')
            ratios = []
            for block,_ in blocks:
                values = list(map(float,re.findall(r'KSP residual norm ([\d.eE+-]+)',block)))
                ratios.append(values[-1]/values[0])
            solver[kind] = {'converged_excitations':len(blocks),'maximum_logged_initial_to_final_KSP_norm_ratio':max(ratios),
                            'scope':'Logged norm reduction; no independent true unpreconditioned-residual bound.'}
        f,a = channels['coarse']; ff,b = channels['channel']; fd,c = channels['domain']
        if not np.array_equal(f,ff) or not np.array_equal(f,fd):
            raise ValueError('Comparison frequency grids differ')
        variants = {k:metrics(waveforms(x,y,condition),condition) for k,(x,y) in channels.items()}
        sparse = np.r_[0,np.arange(2,len(f),2)]; band = f<=f[-1]*.8
        for label,x,y,options in [
            ('frequency_decimated',f[sparse],b[sparse],{}),
            ('bandwidth_80_percent',f[band],b[band],{}),
            ('time_step_halved',f,b,{'dt':condition['time_step_s']/2}),
            ('FIR_support_extended',f,b,{'support':6e-10}),
        ]:
            variants[label] = metrics(waveforms(x,y,condition,**options),condition)
        fine = variants['channel']
        if fine != summary['cases'][name]['metrics']:
            raise ValueError('Recomputed fine-eye metrics differ from original evidence')
        out['cases'][name] = {'input_hashes':identity,'mesh':matrix_change(f,a,b),
            'domain':matrix_change(f,b,c),'local_mesh':{k:local_mesh(paths[k],models[k]) for k in ['coarse','channel']},
            'eye_variants':variants,'original_checks':summary['cases'][name]['checks'],'solver':solver}
    rankings = {}
    for kind in out['cases']['close']['eye_variants']:
        close = out['cases']['close']['eye_variants'][kind]
        separated = out['cases']['separated']['eye_variants'][kind]
        rankings[kind] = {'close_added_noise_peak_v':close['switching_minus_quiet_peak_v'],
                         'separated_added_noise_peak_v':separated['switching_minus_quiet_peak_v'],
                         'added_noise_difference_v':close['switching_minus_quiet_peak_v']-separated['switching_minus_quiet_peak_v'],
                         'smaller_added_noise_case':'separated' if close['switching_minus_quiet_peak_v']>separated['switching_minus_quiet_peak_v'] else 'close',
                         'close_opening_reduction_v':close['opening_reduction_v'],
                         'separated_opening_reduction_v':separated['opening_reduction_v']}
    out['ranking'] = rankings
    out['interpretation'] = {
        'supported_cause':'Spatial discretization sensitivity with fixed physical CAD, apertures and testbench. Volume and port meshes changed together; their isolated errors and true residual remain unproven.',
        'why_eye_checks_pass':'The eye uses S43/S42, while the largest relative gate failure is S41. The 200ps ramp suppresses high-frequency drift. Stable loaded-voltage checks do not qualify the full channel.',
        'frequency_scope':'Resampling/truncating/refitting one native grid is a local sensitivity, not an independently finer-frequency solve or global error bound.',
        'ranking_scope':'Weaker/stronger refers to added-noise peak in this testbench. It is not universal good/bad: FEXT magnitude ranking can differ or reverse with refinement.',
        'next_probe_not_run':'Keep exact physical/port/setup inputs. Test a local volumetric size box covering both traces, gap and full dielectric thickness at0.25,1,10GHz before another sweep; audit port contacts and true residual. No result promotion from one sensitivity.',
        'qualification':'Both original full-channel mesh gates remain failed; thresholds, drivers and alignment are unchanged.',
    }
    return out


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'diagnosis.json'
    if destination.exists():
        raise FileExistsError('Preserve prior diagnosis; choose a new report path')
    report = diagnose(root)
    destination.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'diagnosis':str(destination),'status':report['status'],'new_solver_runs':0}))
