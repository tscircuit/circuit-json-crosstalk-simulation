"""Explain saved victim waveforms against an ideal reference DQS.

No new clock channel, receiver specification, setup/hold margin or solver run.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('MPLCONFIGDIR', '/tmp/palace-timing-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from eyes import pattern


def save_timing(root):
    root = Path(root).resolve()
    target = root/'reference-timing.png'
    receipt_path = root/'reference-timing.json'
    if target.exists() or receipt_path.exists():
        raise ValueError('Timing output already exists; preserve it or choose a separate run directory')
    config_path = root/'eye-input.json'
    config = json.loads(config_path.read_text())
    condition = config['conditions']
    if condition['driver_low_v'] != 0:
        raise ValueError('This explanatory logic-window view requires the fixture low level of 0 V')
    ui = 1/condition['bit_rate_hz']
    origin = 2e-9 + condition['fixed_eye_delay_s']
    bits = pattern(condition['victim_seed'], condition['bits'])
    # Explicit explanatory limits, not a receiver/DDR specification. Matched
    # source/load nominal plateau is used only to choose visible logic bands.
    nominal = condition['driver_high_v'] * condition['load_resistance_ohms'] / (
        condition['source_resistance_ohms'] + condition['load_resistance_ohms'])
    low, high = .2*nominal, .8*nominal
    first, last = condition['discard_bits'], condition['bits']-condition['end_margin_bits']
    shown = (first, first+8)
    colors = {'close':'#c65429', 'separated':'#2678a3'}
    summary_path = root/'eye-summary.json'
    summary = json.loads(summary_path.read_text())
    mesh_status = 'PASSED' if all(v['checks']['full_complex_channel_mesh_status']=='passed' for v in summary['cases'].values()) else 'FAILED'
    fig = plt.figure(figsize=(14, 7), facecolor='white', layout='constrained')
    grid = fig.add_gridspec(4, 2, height_ratios=[.55, 2.2, .55, 2.2], width_ratios=[2.4,1])
    receipt = {
        'clock_model':'Ideal reference DQS; both edges sample once per data UI. No clock/DQS channel was simulated.',
        'clock_alignment':f'Same fixed {condition["fixed_eye_delay_s"]*1e12:g} ps receiver alignment already used by saved eyes; no per-layout or waveform optimization.',
        'ui_s':ui, 'reference_delay_s':condition['fixed_eye_delay_s'],
        'illustrative_valid_voltage_criterion':{'maximum_low_v':low,'minimum_high_v':high,
            'definition':'Expected finite-pattern bit must meet these explanatory voltage limits. These are not device input thresholds.'},
        'interpretation':'Qualitative bus-timing illustration, not measured setup/hold, jitter, DDR timing, eye-mask or compliance margin.',
        'mesh_status':mesh_status,
        'new_native_solves':0, 'source_sha256':{'eye-input.json':hashlib.sha256(config_path.read_bytes()).hexdigest(),
            'eye-summary.json':hashlib.sha256(summary_path.read_bytes()).hexdigest()},
        'layouts':{},
    }
    for row, name in enumerate(('close','separated')):
        model_path = Path(config['cases'][name]['channel'])/'model.json'
        model = json.loads(model_path.read_text())
        label = f'{model["gap_mm"]:g} mm gap'
        receipt['source_sha256'][f'{name}/channel/model.json'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
        path = root/f'{name}-waveforms.csv'
        w = np.genfromtxt(path, delimiter=',', names=True)
        required = {'time_s','victim_quiet_v','victim_switching_v','added_noise_v'}
        if not required <= set(w.dtype.names or ()) or not np.isfinite(w['time_s']).all():
            raise ValueError('Saved native-channel-derived waveform columns are missing/nonfinite')
        if np.any(np.diff(w['time_s']) <= 0):
            raise ValueError('Saved waveform time must increase')
        quiet, switching = w['victim_quiet_v'], w['victim_switching_v']
        if not np.isfinite(quiet).all() or not np.isfinite(switching).all() or np.max(abs(switching-quiet-w['added_noise_v']))>1e-12:
            raise ValueError('Saved quiet/switching/noise identity failed')
        x = (w['time_s']-origin)/ui
        clock_ax = fig.add_subplot(grid[2*row,0])
        data_ax = fig.add_subplot(grid[2*row+1,0], sharex=clock_ax)
        eye_clock = fig.add_subplot(grid[2*row,1])
        eye_ax = fig.add_subplot(grid[2*row+1,1], sharex=eye_clock)
        for ax in (clock_ax, eye_clock):
            ax.set_ylim(-.15,1.3)
            ax.set_yticks([])
            ax.spines[['top','right','left']].set_visible(False)
            ax.tick_params(labelbottom=False, bottom=False)
        clk_x = np.linspace(shown[0],shown[1],4001)
        clock = (np.floor(clk_x+.5).astype(int)%2 == 0).astype(float)
        clock_ax.plot(clk_x,clock,color='#40454a',lw=1.5,drawstyle='steps-post')
        sample_x = np.arange(shown[0],shown[1])+.5
        clock_ax.scatter(sample_x,np.full(len(sample_x),1.16),marker='v',color='#713b86',s=24)
        clock_ax.set_title(f'{label} — ideal reference DQS, both edges sample',loc='left',fontsize=10)
        data_ax.plot(x,quiet,color='#747a80',ls='--',lw=1.3,label='aggressor quiet')
        data_ax.plot(x,switching,color=colors[name],lw=1.5,label='aggressor switching')
        within = (x>=shown[0]) & (x<=shown[1])
        bit_index = np.clip(np.floor(x).astype(int),0,len(bits)-1)
        valid = np.where(bits[bit_index]==1,switching>=high,switching<=low) & within
        data_ax.fill_between(x,0,nominal,where=valid,color='#93c89b',alpha=.2,linewidth=0)
        for sample in sample_x:
            data_ax.axvline(sample,color='#713b86',ls=':',lw=.8)
        for i in range(shown[0],shown[1]):
            data_ax.text(i+.5,nominal*1.06,str(bits[i]),ha='center',fontsize=9,color='#40454a')
        data_ax.set_xlim(*shown)
        data_ax.set_ylim(-.06,nominal*1.15)
        data_ax.set_ylabel('Victim P4 (V)')
        data_ax.set_xlabel('Data time (UI), shared reference alignment')
        data_ax.legend(loc='lower right',fontsize=8,framealpha=.9)
        data_ax.grid(alpha=.15)
        phase = np.linspace(0,2,501)
        traces = {'quiet':[], 'switching':[]}
        for i in range(first,last):
            times = origin+(i-.5+phase)*ui
            for mode, values in [('quiet',quiet),('switching',switching)]:
                values_at_phase = np.interp(times,w['time_s'],values)
                traces[mode].append(values_at_phase)
                eye_ax.plot(phase,values_at_phase,color='#747a80' if mode=='quiet' else colors[name],
                            lw=.5,alpha=.12 if mode=='quiet' else .22)
        traces = {k:np.asarray(v) for k,v in traces.items()}
        expected = bits[first:last]
        max_low = traces['switching'][expected==0].max(axis=0)
        min_high = traces['switching'][expected==1].min(axis=0)
        valid_eye = (max_low<=low) & (min_high>=high) & (phase>=.5) & (phase<=1.5)
        eye_ax.fill_between(phase,0,nominal,where=valid_eye,color='#93c89b',alpha=.22,linewidth=0)
        for ax in (data_ax,eye_ax):
            ax.axhline(low,color='#4a9360',ls=':',lw=.6)
            ax.axhline(high,color='#4a9360',ls=':',lw=.6)
        eye_ax.axvline(1,color='#713b86',ls=':',lw=1.2)
        eye_clock.plot([0,1,1,2],[0,0,1,1],color='#40454a',lw=1.5)
        eye_clock.scatter([1],[1.16],marker='v',color='#713b86',s=24)
        eye_clock.set_title('Clock-aligned victim eye',loc='left',fontsize=10)
        eye_ax.set_xlim(0,2)
        eye_ax.set_ylim(-.06,nominal*1.15)
        eye_ax.set_xlabel('Eye phase (UI); sample at 1')
        eye_ax.set_ylabel('Victim P4 (V)')
        eye_ax.grid(alpha=.15)
        receipt['source_sha256'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt['layouts'][name] = {'source':path.name,'eye_traces_per_mode':last-first,
            'saved_waveform_identity_max_error_v':float(np.max(abs(switching-quiet-w['added_noise_v']))),
            'green_window':'Saved switching voltage meets illustrative limits; the eye requires all observed high/low bits to meet them.',
            'clock_is_synthetic_reference':True}
    fig.suptitle('Data relative to a reference sampling clock',fontsize=17)
    fig.supxlabel(f'Green = illustrative voltage-valid window (low ≤ {low:g} V, high ≥ {high:g} V). Ideal clock; no setup/hold or DDR margin. Mesh qualification {mesh_status}.',fontsize=9)
    fig.savefig(target,dpi=150,facecolor='white')
    plt.close(fig)
    receipt['image_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'image':str(target),'receipt':str(receipt_path),'new_native_solves':0}))


if __name__=='__main__':
    save_timing(sys.argv[1])
