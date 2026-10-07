"""Finite-pattern eyes from native Palace channels and explicit matched sources.

A regularized, real causal FIR approximates TWO loaded transfer functions in
the solved band. Its DC constraint follows the verified ideal-PEC topology.
It is not a broadband passive macromodel or an IBIS/circuit simulator.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import sys

os.environ.setdefault('MPLCONFIGDIR', '/tmp/palace-eye-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from scipy.signal import fftconvolve


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def channel(directory):
    result = json.loads((directory/'result.json').read_text())
    summary = json.loads((directory/'summary.json').read_text())
    model = json.loads((directory/'model.json').read_text())
    if result['status'] != 'native_passed' or summary['checks_status'] != 'passed':
        raise ValueError('Eyes require passed native channel outputs, never a fallback')
    samples = sorted(summary['s_parameters'], key=lambda x:x['frequency_hz'])
    f = np.array([x['frequency_hz'] for x in samples])
    s = np.array([np.array(x['real'])+1j*np.array(x['imag']) for x in samples])
    if len(f)<17 or f[0]>1e7 or not np.allclose(np.diff(f[1:]), np.diff(f[1:])[0]):
        raise ValueError('Use a checked near-DC sample plus a dense uniform broadband grid')
    dc = np.zeros((4,4))
    dc[0,1]=dc[1,0]=dc[2,3]=dc[3,2]=1
    dc_error = float(np.max(abs(s[0]-dc)))
    if dc_error > .005:
        raise ValueError('Native low-frequency sample does not support the explicit ideal-PEC DC limit')
    if model['setup']['port_resistance_ohms'] != 50 or model['setup']['conductor_model'] != 'pec':
        raise ValueError('This educational loaded-channel example requires explicit PEC and matched 50 ohm ports')
    return f, s, model, {'source_csv':str(directory/'postpro/port-S.csv'),
        'source_csv_sha256':hashlib.sha256((directory/'postpro/port-S.csv').read_bytes()).hexdigest(),
        'circuit_json_sha256':hashlib.sha256((directory/'circuit.json').read_bytes()).hexdigest(),
        'dc_model':'Ideal PEC connects P1-P2 and P3-P4; distinct signal conductors remain electrically isolated at DC',
        'dc_anchor_max_complex_difference_at_lowest_native_frequency':dc_error,
        'native_frequency_count':len(f), 'independent_converged_excitations':summary['independent_converged_excitations'],
        'native_maximum_singular_value':max(x['maximum_singular_value'] for x in samples),
        'native_maximum_reciprocity_residual':max(x['maximum_reciprocity_residual'] for x in samples)}


def causal_fit(f, target, dt, duration=4e-10):
    """Real taps at nonnegative times; ridge controls unconstrained out-of-band gain.

    Eliminating the first tap enforces EXACT DC [.5,0]. Factor .5 is the
    matched Thevenin-source voltage divider, not unit-power field scaling.
    No time advance, absolute value of S, or fabricated frequency sample.
    """
    t = np.arange(round(duration/dt)+1)*dt
    dc = np.array([.5, 0.])
    basis = np.exp(-2j*np.pi*f[:,None]*t[None,1:])-1
    ridge = 1e-3
    a = np.vstack([basis.real, basis.imag, np.sqrt(ridge)*np.eye(len(t)-1),
                   -np.sqrt(ridge)*np.ones((1,len(t)-1))])
    rhs = np.vstack([(target-dc).real, target.imag, np.zeros((len(t)-1,2)), -np.sqrt(ridge)*dc[None,:]])
    c = np.linalg.lstsq(a,rhs,rcond=None)[0]
    taps = np.vstack([dc-c.sum(axis=0),c])
    fitted = np.exp(-2j*np.pi*f[:,None]*t[None,:])@taps
    dense = np.linspace(0,f[-1],1001)
    response = np.exp(-2j*np.pi*dense[:,None]*t[None,:])@taps
    error = float(np.max(abs(fitted-target)))
    norm = float(np.max(np.linalg.norm(response,axis=1)))
    if error>.002 or norm>.501:
        raise ValueError(f'Loaded causal fit failed: max native-band error={error:g}, row norm={norm:g}')
    return taps, {'method':'Real nonnegative-lag regularized FIR; exact static DC; no proportional/unstable poles',
        'time_step_s':dt,'support_s':duration,'regularization':ridge,
        'maximum_native_loaded_complex_fit_error':error,'maximum_dense_in_band_loaded_row_norm':norm,
        'passivity_scope':'Raw full native matrices checked; only loaded two-input row checked between samples. FIR outside solved band is not qualified.',
        'causality_scope':'FIR is causal by construction; fit residual checks consistency with sampled channel, not an independent global causality proof.'}


def pattern(seed, count):
    rng = random.Random(seed)
    return np.array([rng.getrandbits(1) for _ in range(count)])


def source(t, bits, condition, phase_ui):
    ui = 1/condition['bit_rate_hz']
    start = 2e-9+phase_ui*ui
    edge = condition['rise_fall_s']
    # Ramps begin at each bit boundary; before the pattern the source is low.
    knots, levels = [0., start], [condition['driver_low_v']]*2
    previous = condition['driver_low_v']
    for i,bit in enumerate(bits):
        when = start+i*ui
        value = condition['driver_high_v'] if bit else condition['driver_low_v']
        knots.extend([when,when+edge]); levels.extend([previous,value]); previous=value
    knots.extend([start+len(bits)*ui, start+len(bits)*ui+edge, t[-1]+edge])
    levels.extend([previous,condition['driver_low_v'],condition['driver_low_v']])
    return np.interp(t,knots,levels)


def waveforms(f, s, condition, dt=None, support=4e-10):
    dt = dt or condition['time_step_s']
    vbits = pattern(condition['victim_seed'],condition['bits'])
    abits = pattern(condition['aggressor_seed'],condition['bits'])
    ui = 1/condition['bit_rate_hz']
    t = np.arange(0,2e-9+(condition['bits']+4)*ui,dt)
    # P3->P4 victim, P2->P4 opposing-direction aggressor: near-end coupling.
    taps, fit = causal_fit(f,.5*s[:,3,[2,1]],dt,support)
    victim = source(t,vbits,condition,0)
    aggressor = source(t,abits,condition,condition['aggressor_phase_ui'])
    quiet = fftconvolve(victim,taps[:,0],mode='full')[:len(t)]
    noise = fftconvolve(aggressor,taps[:,1],mode='full')[:len(t)]
    switching = quiet+noise
    # Independent response check: causal convolution cannot precede source onset.
    pre_input = float(max(np.max(abs(quiet[t<2e-9])),np.max(abs(noise[t<2e-9]))))
    if pre_input>1e-10: raise ValueError('Time-domain response precedes input')
    fit['pre_input_voltage_residual_v']=pre_input
    return {'t':t,'quiet':quiet,'switching':switching,'noise':noise,
            'victim_source':victim,'aggressor_source':aggressor,'victim_bits':vbits,'aggressor_bits':abits,'fit':fit}


def difference(a,b):
    return float(max(np.max(abs(a[k]-np.interp(a['t'],b['t'],b[k]))) for k in ['quiet','switching','noise']))


def mesh_refinement(fine, coarse):
    # Threshold size grows from near to far over transition_mm. A shorter
    # transition coarsens the interior even when the edge minimum is smaller.
    return (fine['near_mm'] <= coarse['near_mm'] and
            fine['far_mm'] <= coarse['far_mm'] and
            fine['transition_mm'] >= coarse['transition_mm'] and
            fine != coarse)


def metrics(w, condition):
    ui = 1/condition['bit_rate_hz']
    indices = np.arange(condition['discard_bits'],condition['bits']-condition['end_margin_bits'])
    centers = 2e-9+(indices+.5)*ui+condition['fixed_eye_delay_s']
    data = {}
    for name in ['quiet','switching']:
        values = np.interp(centers,w['t'],w[name])
        low = float(np.max(values[w['victim_bits'][indices]==0]))
        high = float(np.min(values[w['victim_bits'][indices]==1]))
        data[name] = {'maximum_low_at_fixed_center_v':low,'minimum_high_at_fixed_center_v':high,
                      'observed_center_opening_v':high-low}
    data['switching_minus_quiet_peak_v']=float(np.max(abs(w['noise'])))
    data['opening_reduction_v']=data['quiet']['observed_center_opening_v']-data['switching']['observed_center_opening_v']
    data['definition']='Finite deterministic pattern at one fixed sampling phase; not a statistical BER, eye mask or DDR compliance result'
    return data


def layout(model, output, label, ax=None):
    own_figure=ax is None
    if own_figure: fig,ax=plt.subplots(figsize=(11,5),layout='constrained')
    else: fig=ax.figure
    x0,y0,x1,y1=model['board']['bounds_mm']
    ax.add_patch(Rectangle((x0,y0),x1-x0,y1-y0,facecolor='#f3f5f2',edgecolor='#44505b',lw=2))
    a,b,c,d=model['reference']['bounds_mm']
    ax.add_patch(Rectangle((a,b),c-a,d-b,facecolor='#dcebdd',edgecolor='#769a78',ls='--'))
    colors=['#d96439','#287bc1']
    for i,s in enumerate(model['signals']):
        lo=min(s['x_min_mm']-s['width_mm']/2,*[p['x_mm']-p['width_mm']/2 for p in s['pads']])
        hi=max(s['x_max_mm']+s['width_mm']/2,*[p['x_mm']+p['width_mm']/2 for p in s['pads']])
        ax.add_patch(Rectangle((lo,s['y_mm']-s['width_mm']/2),hi-lo,s['width_mm'],facecolor=colors[i]))
        for p in s['pads']:
            ax.add_patch(Rectangle((p['x_mm']-p['width_mm']/2,p['y_mm']-p['height_mm']/2),p['width_mm'],p['height_mm'],facecolor=colors[i],edgecolor='#354452'))
    for i,s in enumerate(model['signals']):
        y=s['y_mm']; direction='Aggressor P2 → P1' if i==0 else 'Victim P3 → P4'
        dy=-.32 if i==0 else .32
        ax.annotate(direction,xy=(.1,y),xytext=(.3,y+dy),color=colors[i],arrowprops={'arrowstyle':'-', 'color':colors[i]},fontsize=12)
        inset=model['setup']['port_inset_mm']
        for end,x in enumerate([s['x_min_mm']+inset,s['x_max_mm']-inset]):
            n=2*i+end+1
            ax.plot(x,y,'o',color='white',mec='#354452',ms=5)
            ax.text(x,y+(-.22 if i==0 else .22),f'P{n}',ha='center',fontsize=10)
    ax.set_xlim(x0-.2,x1+.2);ax.set_ylim(y0-.1,y1+.1);ax.set_aspect('equal')
    ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)')
    signal=model['signals'][0]
    length=signal['x_max_mm']-signal['x_min_mm']
    ax.set_title(f"{label} · edge gap {model['gap_mm']:g} mm\nActual TSX-rendered copper: {length:g} mm × {signal['width_mm']:g} mm traces, {x1-x0:g} × {y1-y0:g} mm board",fontsize=13)
    ax.text(a+.1,b+.1,f'Actual bottom GND pour: {c-a:g} × {d-b:g} mm',fontsize=10,color='#45684a')
    if own_figure:
        fig.savefig(output,dpi=150);plt.close(fig)


def layout_pair(models,output):
    fig,axes=plt.subplots(2,1,figsize=(11,8),layout='constrained')
    for ax,(name,model) in zip(axes,models.items()):layout(model,None,name.capitalize()+' spacing',ax)
    fig.savefig(output,dpi=150);plt.close(fig)


def process(root):
    config=json.loads((root/'eye-input.json').read_text())
    condition=config['conditions']
    if condition['source_resistance_ohms']!=50 or condition['load_resistance_ohms']!=50 or condition['quiet_aggressor_voltage_v']!=0:
        raise ValueError('Only the declared matched 50 ohm / quiet 0 V testbench is implemented')
    reports, waves, models = {}, {}, {}
    for name,paths in config['cases'].items():
        fine=Path(paths['channel']); coarse=Path(paths['coarse'])
        f,s,model,provenance=channel(fine)
        fc,sc,mc,pc=channel(coarse)
        if provenance['circuit_json_sha256']!=pc['circuit_json_sha256'] or not np.array_equal(f,fc):
            raise ValueError('Refinement comparison requires identical physical input and frequency grid')
        fine_setup=dict(model['setup']); coarse_setup=dict(mc['setup'])
        fm=fine_setup.pop('mesh'); cm=coarse_setup.pop('mesh')
        if fine_setup!=coarse_setup or not mesh_refinement(fm,cm):
            raise ValueError('Refinement must tighten mesh without changing other analysis choices')
        fine_tets=json.loads((fine/'mesh-report.json').read_text())['field_tetrahedra']
        coarse_tets=json.loads((coarse/'mesh-report.json').read_text())['field_tetrahedra']
        if fine_tets<=coarse_tets: raise ValueError('Refined mesh did not add actual field tetrahedra')
        w=waveforms(f,s,condition); wc=waveforms(fc,sc,condition)
        m=metrics(w,condition); mcoarse=metrics(wc,condition)
        mesh_change=difference(w,wc)
        opening_change=max(abs(m[k]['observed_center_opening_v']-mcoarse[k]['observed_center_opening_v']) for k in ['quiet','switching'])
        half=waveforms(f,s,condition,dt=condition['time_step_s']/2)
        sparse=np.r_[0,np.arange(2,len(f),2)]
        band=f<=f[-1]*.8
        sampling_change=difference(w,half)
        grid_change=difference(w,waveforms(f[sparse],s[sparse],condition))
        bandwidth_change=difference(w,waveforms(f[band],s[band],condition))
        support_change=difference(w,waveforms(f,s,condition,support=6e-10))
        domain=Path(paths['domain'])
        fd,sd,md,pd=channel(domain)
        base_setup=dict(model['setup']); domain_setup=dict(md['setup'])
        base_air=base_setup.pop('air_padding_mm'); domain_air=domain_setup.pop('air_padding_mm')
        if pd['circuit_json_sha256']!=provenance['circuit_json_sha256'] or not np.array_equal(fd,f) or base_setup!=domain_setup or domain_air<=base_air:
            raise ValueError('Domain sensitivity requires identical physical input/setup except increased air padding')
        wd=waveforms(fd,sd,condition)
        domain_voltage_change=difference(w,wd)
        domain_s_change=float(np.max(abs(sd-s)))
        domain_coupling_change=float(np.max(abs(sd[:,3,[1,0]]-s[:,3,[1,0]])/np.maximum(abs(s[:,3,[1,0]]),1e-4)))
        checks={'loaded_waveform_mesh_change_v':mesh_change,'fixed_center_opening_mesh_change_v':opening_change,
                'time_step_halving_change_v':sampling_change,'frequency_grid_decimation_change_v':grid_change,
                'bandwidth_80_percent_change_v':bandwidth_change,'causal_fir_support_extension_change_v':support_change,
                'maximum_all_complex_S_mesh_change':float(np.max(abs(s-sc))),
                'maximum_selected_complex_coupling_relative_mesh_change':float(np.max(abs(s[:,3,[1,0]]-sc[:,3,[1,0]])/np.maximum(abs(sc[:,3,[1,0]]),1e-4))),
                'relative_coupling_denominator_floor':1e-4,
                'mesh_field_tetrahedra':[coarse_tets,fine_tets],
                'voltage_tolerance_v':condition['waveform_tolerance_v'],
                'status':'passed' if max(mesh_change,opening_change,sampling_change,grid_change,bandwidth_change,support_change)<=condition['waveform_tolerance_v'] else 'failed',
                'scope':'Local educational loaded-voltage checks; not full complex coupling convergence, asymptotic error bound or global SI qualification',
                'domain_loaded_waveform_change_v':domain_voltage_change,
                'domain_maximum_all_complex_S_change':domain_s_change,
                'domain_maximum_selected_complex_coupling_relative_change':domain_coupling_change,
                'air_padding_mm':[base_air,domain_air],
                'truncation_convergence':'passed' if domain_s_change<=.01 and domain_coupling_change<=.05 and domain_voltage_change<=condition['waveform_tolerance_v'] else 'failed',
                'frequency_status':'passed' if max(grid_change,bandwidth_change,support_change,sampling_change)<=condition['waveform_tolerance_v'] else 'failed'}
        checks['full_complex_channel_mesh_status']='passed' if checks['maximum_all_complex_S_mesh_change']<=.01 and checks['maximum_selected_complex_coupling_relative_mesh_change']<=.05 else 'failed'
        checks['full_complex_channel_mesh_criteria']={'maximum_all_S_absolute':.01,'maximum_selected_coupling_relative':.05}
        zero=waveforms(f,s,{**condition,'driver_low_v':0,'driver_high_v':0})
        checks['zero_drive_control_v']=float(max(np.max(abs(zero[k])) for k in ['quiet','switching','noise']))
        checks['quiet_control']='Same native coupled channel, aggressor source held at0V; linear superposition'
        reports[name]={'gap_mm':model['gap_mm'],'metrics':m,'fit':w['fit'],'checks':checks,'provenance':provenance}
        waves[name]=w;models[name]=model
        layout(model,root/f'{name}-layout.png',name.capitalize()+' spacing')
        np.savetxt(root/f'{name}-waveforms.csv',np.column_stack([w[k] for k in ['t','victim_source','aggressor_source','quiet','switching','noise']]),delimiter=',',header='time_s,victim_source_v,aggressor_source_v,victim_quiet_v,victim_switching_v,added_noise_v',comments='')
    # Physical comparison is spacing only; no widths, lengths, materials or loads vary.
    a,b=models.values()
    for key in ['board','stackup','reference','setup']:
        if a[key]!=b[key]: raise ValueError('Physical comparison changed common model/setup')
    def dimensions(model):
        return [(x['x_min_mm'],x['x_max_mm'],x['width_mm'],[(p['width_mm'],p['height_mm']) for p in x['pads']]) for x in model['signals']]
    if dimensions(a)!=dimensions(b): raise ValueError('Physical comparison changed signal dimensions')
    layout_pair(models,root/'layouts.png')
    ui=1/condition['bit_rate_hz']
    fig,axes=plt.subplots(2,2,figsize=(12,8),sharex=True,sharey=True,layout='constrained')
    names=list(waves)
    for row,name in enumerate(names):
        w=waves[name]
        for col,activity in enumerate(['quiet','switching']):
            ax=axes[row,col]; phase=np.linspace(0,2,801)
            for i in range(condition['discard_bits'],condition['bits']-condition['end_margin_bits']):
                origin=2e-9+(i-.5)*ui+condition['fixed_eye_delay_s']
                ax.plot(phase,np.interp(origin+phase*ui,w['t'],w[activity]),color='#527a97' if col==0 else '#c4503a',alpha=.14,lw=.8)
            ax.axvline(1,color='#a1a9b1',ls=':',lw=.8)
            ax.set_title(f"{name.capitalize()} ({reports[name]['gap_mm']:g} mm gap) · aggressor {activity}")
            opening=reports[name]['metrics'][activity]['observed_center_opening_v']*1000
            ax.text(.04,.06,f'Fixed-center opening: {opening:.1f} mV',transform=ax.transAxes,fontsize=10,
                    bbox={'facecolor':'white','edgecolor':'none','alpha':.9})
            ax.set_ylim(-.08,.83);ax.set_xlim(0,2);ax.grid(alpha=.18)
            ax.set_xlabel('Time (UI), fixed common alignment');ax.set_ylabel('Victim P4 voltage (V)')
    fig.suptitle('Victim eyes from actual Palace broadband channels\nSame drivers, loads and victim bits; quiet retains physical coupling',fontsize=15)
    fig.savefig(root/'eyes.png',dpi=150);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4),layout='constrained')
    for name in names:
        w=waves[name]; ax.plot((w['t']-2e-9)/ui,w['noise']*1000,label=f"{name.capitalize()}: {reports[name]['gap_mm']:g} mm",lw=1)
    ax.set_xlim(8,24);ax.set_xlabel('Time after pattern start (UI)');ax.set_ylabel('Switching − quiet at victim P4 (mV)')
    ax.set_title('Added crosstalk noise · identical victim bits and fixed aggressor timing');ax.legend();ax.grid(alpha=.2)
    fig.savefig(root/'added-noise.png',dpi=150);plt.close(fig)
    report={'status':'convergence_checks_passed' if all(r['checks']['status']=='passed' and r['checks']['full_complex_channel_mesh_status']=='passed' and r['checks']['truncation_convergence']=='passed' for r in reports.values()) else 'convergence_checks_failed',
            'conditions':condition,'cases':reports,'actual_bits':{k:waves[names[0]][k].tolist() for k in ['victim_bits','aggressor_bits']},
            'limits':['Synthetic assumed PEC/lossless fixture; IC/package/PDN and random effects omitted.',
                      'Causal loaded FIR fit only qualified in solved band; out-of-band behavior and full passive macromodel not established.',
                      'Quiet is an inactive aggressor on the SAME coupled geometry, not zero physical coupling.',
                      'Finite-pattern eyes at one selected phase; no BER/compliance, optimized routing or actual DDR qualification.',
                      'Two-mesh/two-domain sensitivities do not establish an asymptotic error bound; failed gates remain explicit.']}
    report['spacing_comparison']={'measured_lower_added_noise_case':min(reports,key=lambda n:reports[n]['metrics']['switching_minus_quiet_peak_v']),
         'measured_smaller_opening_reduction_case':min(reports,key=lambda n:reports[n]['metrics']['opening_reduction_v']),
         'interpretation':'Measured outcomes for this declared testbench; no universal good/bad or routing claim. Compare switching-minus-quiet within each layout to isolate aggressor activity.'}
    save(root/'eye-summary.json',report)
    from timing import save_timing
    save_timing(root)
    html='<!doctype html><meta charset="utf-8"><title>Two-layout crosstalk demo</title><style>body{font:18px system-ui;max-width:1100px;margin:40px auto;color:#273744;background:white}img{width:100%}p{line-height:1.5}.warning{padding:16px;background:#fff3da}</style>'
    html+='<h1>Two layouts, the same victim signal</h1><p>Orange: aggressor travels right → left. Blue: victim travels left → right. Only spacing changes.</p>'
    for name in names:html+=f'<h2>{name.capitalize()} spacing</h2><img src="{name}-layout.png" alt="Actual rendered PCB geometry">'
    html+='<h2>What reaches the victim receiver?</h2><p>Each layout is tested with its aggressor quiet and switching. The axes, victim bits, loads and sampling reference are identical.</p><img src="eyes.png" alt="Actual channel-derived victim eyes"><img src="added-noise.png" alt="Switching minus quiet voltage">'
    html+='<img src="reference-timing.png" alt="Saved victim voltages and eye relative to an ideal reference DQS; illustrative voltage windows, no setup or hold margin">'
    html+='<table style="border-collapse:collapse;width:100%"><tr><th align="left">Measured example</th><th>Added-noise peak</th><th>Quiet opening</th><th>Switching opening</th><th>Opening lost</th></tr>'
    for name,r in reports.items():
        m=r['metrics']
        html+=f'<tr><td>{name.capitalize()} ({r["gap_mm"]:g} mm gap)</td><td align="center">{m["switching_minus_quiet_peak_v"]*1000:.1f} mV</td><td align="center">{m["quiet"]["observed_center_opening_v"]*1000:.1f} mV</td><td align="center">{m["switching"]["observed_center_opening_v"]*1000:.1f} mV</td><td align="center">{m["opening_reduction_v"]*1000:.1f} mV</td></tr>'
    html+='</table>'
    full_channel_passed=all(r['checks']['full_complex_channel_mesh_status']=='passed' for r in reports.values())
    full_channel_text='Full complex-channel mesh checks passed.' if full_channel_passed else 'Full complex-channel mesh checks failed; the eye-voltage check does not qualify the complete channel.'
    html+=f'<p class="warning">{report["status"].replace("_"," ")}. {full_channel_text} Domain sensitivity: '+', '.join(f'{n}: {r["checks"]["truncation_convergence"]}' for n,r in reports.items())+f'. Preliminary synthetic fixture; no DDR/BER or routing qualification.</p><p>{condition["bit_rate_hz"]/1e9:g} Gb/s; {condition["driver_low_v"]:g}–{condition["driver_high_v"]:g} V sources; {condition["rise_fall_s"]*1e12:g} ps ramps; 50 Ω source/load; receiver nominal high {condition["driver_high_v"]/2:g} V. Native channel plus causal loaded FIR approximation, not a Palace transient solve.</p>'
    (root/'index.html').write_text(html)
    print(json.dumps({'status':report['status'],'report':str(root/'index.html'),'summary':str(root/'eye-summary.json')}))


if __name__=='__main__':
    if sys.argv[1]=='layout':
        layout(json.loads(Path(sys.argv[2]).read_text()),Path(sys.argv[3]),sys.argv[4])
    elif sys.argv[1]=='layouts':
        layout_pair({'close':json.loads(Path(sys.argv[2]).read_text()),'separated':json.loads(Path(sys.argv[3]).read_text())},Path(sys.argv[4]))
    else: process(Path(sys.argv[1]).resolve())
