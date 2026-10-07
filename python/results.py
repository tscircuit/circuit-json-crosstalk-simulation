"""Read and plot actual Palace outputs; no synthetic S/field fallback."""
import csv
import itertools
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

os.environ.setdefault('MPLCONFIGDIR', str(Path(sys.argv[1])/'matplotlib-cache') if len(sys.argv)>1 else '/tmp/palace-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import LogNorm
import meshio
import numpy as np


def read_s(path):
    with path.open() as f:
        rows = [{k.strip():v.strip() for k,v in row.items()} for row in csv.DictReader(f)]
    if not rows: raise ValueError('No native S samples')
    samples = []
    for row in rows:
        indices = [tuple(map(int,m.groups())) for key in row if (m:=re.fullmatch(r'\|S\[(\d+)\]\[(\d+)\]\| \(dB\)',key))]
        if set(indices) != {(i,j) for i in range(1,5) for j in range(1,5)}: raise ValueError('Native S matrix is incomplete')
        s = np.zeros((4,4),dtype=complex)
        for i,j in indices:
            magnitude = 10**(float(row[f'|S[{i}][{j}]| (dB)'])/20)
            phase = float(row[f'arg(S[{i}][{j}]) (deg.)'])
            s[i-1,j-1] = magnitude*np.exp(1j*np.deg2rad(phase))
        if not np.isfinite(s).all(): raise ValueError('Nonfinite native S values')
        samples.append((float(row['f (GHz)'])*1e9,s))
    return samples


def plot_field(directory, frequency_hz):
    # The bounded fixture saves one field frequency; extension to multiple saved
    # cycles is explicitly rejected until a PVD frequency mapping is implemented.
    field_root = directory/'postpro/paraview/driven/excitation_1'
    files = sorted(field_root.rglob('proc000000.vtu'))
    if len(files) != 1: raise ValueError('Field plot requires exactly one saved excitation-1 cycle')
    source = files[0]
    pvd = list(field_root.glob('*.pvd'))
    datasets = ET.parse(pvd[0]).findall('.//DataSet') if len(pvd) == 1 else []
    if len(datasets) != 1 or abs(float(datasets[0].attrib['timestep'])*1e9-frequency_hz) > max(1,frequency_hz*1e-8):
        raise ValueError('Native PVD field frequency does not match requested saved frequency')
    raw = source.read_bytes()
    cache = directory/'visualization-cache'
    cache.mkdir(exist_ok=True)
    derived = cache/'reader-compatible.vtu'
    # Meshio 5.3.5 rejects MFEM's 2.2 marker. Change only that marker in a derived
    # reader copy; preserve the original native arrays/file.
    derived.write_bytes(raw.replace(b'version="2.2"',b'version="1.0"',1))
    mesh = meshio.read(derived)
    field = mesh.point_data['E_real'] + 1j*mesh.point_data['E_imag']
    if field.shape != mesh.points.shape or not np.isfinite(field).all(): raise ValueError('Invalid native complex E array')
    # Cross-section halfway along the two routes. Native coordinates stay mm.
    model = json.loads((directory/'model.json').read_text())
    plane = (model['signals'][0]['x_min_mm']+model['signals'][0]['x_max_mm'])/2
    patches, magnitudes = [], []
    for block in mesh.cells:
        if block.type not in ['tetra','tetra10','VTK_LAGRANGE_TETRAHEDRON']: raise ValueError('Unsupported native field cell')
        for cell in block.data:
            positions, values = mesh.points[cell[:4]], field[cell[:4]]
            distance = positions[:,0]-plane
            if distance.min()>0 or distance.max()<0: continue
            intersections = []
            for i,j in itertools.combinations(range(4),2):
                if abs(distance[i])<1e-9: intersections.append((positions[i],values[i]))
                if distance[i]*distance[j]<0:
                    t = distance[i]/(distance[i]-distance[j])
                    intersections.append((positions[i]+t*(positions[j]-positions[i]),values[i]+t*(values[j]-values[i])))
            unique = {tuple(np.round(p,7)):(p,v) for p,v in intersections}
            if len(unique)<3: continue
            points = np.array([p[[1,2]] for p,_ in unique.values()])
            values = np.array([v for _,v in unique.values()])
            center = points.mean(axis=0)
            order = np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))
            patches.append(points[order]); magnitudes.append(float(np.linalg.norm(values.mean(axis=0))))
    if not patches or not np.isfinite(magnitudes).all(): raise ValueError('Native field section is empty/nonfinite')
    positive = np.asarray(magnitudes)[np.asarray(magnitudes)>0]
    if not len(positive): raise ValueError('Native field section is identically zero')
    limits = np.quantile(positive,[.02,.995])
    fig,ax = plt.subplots(figsize=(10,5),layout='constrained')
    collection = PolyCollection(patches,array=np.asarray(magnitudes),cmap='inferno',norm=LogNorm(vmin=max(limits[0],1e-12),vmax=limits[1]),edgecolors='none')
    ax.add_collection(collection);ax.autoscale_view();ax.set_aspect('equal')
    ax.set_xlabel('Y (mm)');ax.set_ylabel('Z (mm)')
    ax.set_title(f'Native Palace |E|; {frequency_hz/1e9:g} GHz; excitation 1; X={plane:g} mm\nUnit incident power normalization, not an IC voltage drive')
    fig.colorbar(collection,ax=ax,label='|E| (V/m), logarithmic percentile-clipped scale')
    fig.savefig(directory/'electric-field.png',dpi=150);plt.close(fig)
    return {'source':str(source.relative_to(directory)),
            'reader_conversion':'Derived VTU version marker 2.2 -> 1.0 only; arrays unchanged',
            'frequency_hz':frequency_hz,'excitation':1,'normalization':'unit incident power','x_plane_mm':plane,'color_limits_v_per_m':limits.tolist(),
            'interpolation':'Linear tetrahedron corner samples; section polygon mean complex vector',
            'section_polygons':len(patches),'quantity':'norm(E_real + i E_imag)','unit':'V/m','solved_data':True}


def process(directory):
    setup = json.loads((directory/'setup.json').read_text())
    samples = read_s(directory/'postpro/port-S.csv')
    if len(samples) != len(setup['frequency_hz']) or any(abs(a-b)>max(1,a*1e-8) for a,b in zip(sorted(f for f,_ in samples),sorted(setup['frequency_hz']))):
        raise ValueError('Native frequencies do not match explicit setup')
    log = (directory/'palace.log').read_text()
    converged = re.findall(r'GMRES solver converged in (\d+) iterations?',log)
    if len(converged) != 4*len(samples) or re.search(r'(did not converge|diverged)',log,re.I):
        raise ValueError('Expected four converged independent native excitations per frequency')
    matrices = []
    for frequency,s in samples:
        singular = float(np.linalg.svd(s,compute_uv=False)[0])
        reciprocal = float(np.max(np.abs(s-s.T)))
        passed = singular <= 1+setup['checks']['passivity_tolerance'] and reciprocal <= setup['checks']['reciprocity_tolerance']
        matrices.append({'frequency_hz':frequency,'real':s.real.tolist(),'imag':s.imag.tolist(),
                         'maximum_singular_value':singular,'maximum_reciprocity_residual':reciprocal,
                         'column_powers':np.sum(np.abs(s)**2,axis=0).tolist(),'checks_status':'passed' if passed else 'failed'})
    frequency,s = samples[0]
    fig,ax = plt.subplots(figsize=(6,5),layout='constrained')
    image = ax.imshow(20*np.log10(np.maximum(np.abs(s),1e-12)),vmin=-80,vmax=0,cmap='viridis')
    for i in range(4):
        for j in range(4): ax.text(j,i,f'{20*np.log10(max(abs(s[i,j]),1e-12)):.1f}',ha='center',va='center',color='white')
    ax.set_xticks(range(4),['1 near','1 far','2 near','2 far']);ax.set_yticks(range(4),['1 near','1 far','2 near','2 far'])
    ax.set_xlabel('Excited port');ax.set_ylabel('Observed port');ax.set_title(f'Native Palace S magnitude (dB), {frequency/1e9:g} GHz')
    fig.colorbar(image,ax=ax);fig.savefig(directory/'s-matrix.png',dpi=150);plt.close(fig)
    fields = [plot_field(directory,setup['save_fields_at_hz'][0])] if setup['save_fields_at_hz'] else []
    # ParaView views of the actual extended Gmsh mesh, not a geometry drawing.
    mesh = meshio.read(directory/'model.msh')
    def vtk_subset(name, kind, attributes):
        cells = np.concatenate([b.data[np.isin(mesh.cell_data['gmsh:physical'][i],attributes)] for i,b in enumerate(mesh.cells) if b.type==kind])
        nodes = np.unique(cells)
        remap = np.full(len(mesh.points),-1,dtype=int);remap[nodes]=np.arange(len(nodes))
        meshio.write(directory/name,meshio.Mesh(mesh.points[nodes],[(kind,remap[cells])]))
    vtk_subset('dielectric-mesh.vtu','tetra',[2])
    vtk_subset('copper-surface.vtu','triangle',[11,12,14])
    summary = {'native_status':'completed','checks_status':'passed' if all(m['checks_status']=='passed' for m in matrices) else 'failed',
               'source_csv':'postpro/port-S.csv',
               'port_order':['signal_1_near','signal_1_far','signal_2_near','signal_2_far'],
               'independent_converged_excitations':len(converged),'gmres_iterations':list(map(int,converged)),
               's_parameters':matrices,'fields':fields,'mesh_convergence':'not_evaluated','truncation_convergence':'not_evaluated',
               'interpretation':'Native finite PEC fixture channel. This file alone is not an eye or IC/manufacturing/DDR timing qualification.'}
    (directory/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__': process(Path(sys.argv[1]).resolve())
