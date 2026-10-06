"""Exact bounded rectangular copper union -> conforming Palace field mesh (mm)."""
import json
import sys
import time
from collections import Counter
from pathlib import Path

import gmsh
import meshio
import numpy as np
from cad_identity import mapped_cleanup, verify_port_contacts


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def box(bounds, z, height):
    a, b, c, d = bounds
    return (3, gmsh.model.occ.addBox(a, b, z, c - a, d - b, height))


def port_face(port):
    x, y0, y1 = port['x_mm'], port['y_min_mm'], port['y_max_mm']
    z0, z1 = port['reference_z_mm'], port['signal_z_mm']
    p = [gmsh.model.occ.addPoint(*xyz) for xyz in [(x,y0,z0),(x,y1,z0),(x,y1,z1),(x,y0,z1)]]
    edges = [gmsh.model.occ.addLine(p[i], p[(i + 1) % 4]) for i in range(4)]
    return (2, gmsh.model.occ.addPlaneSurface([gmsh.model.occ.addCurveLoop(edges)]))


def audit_exported_mesh(directory, ports):
    mesh = meshio.read(directory / 'model.msh')
    triangles, physical, tetrahedra = [], [], []
    for i, block in enumerate(mesh.cells):
        if block.type == 'triangle':
            triangles.extend(block.data)
            physical.extend(mesh.cell_data['gmsh:physical'][i])
        elif block.type == 'tetra':
            tetrahedra.extend(block.data)
    triangles, physical = np.asarray(triangles), np.asarray(physical)
    facets = Counter(tuple(sorted(face)) for tet in np.asarray(tetrahedra)
                     for face in [tet[[0,1,2]],tet[[0,1,3]],tet[[0,2,3]],tet[[1,2,3]]])
    rows = []
    for port in ports:
        aperture = triangles[physical == 20 + port['index']]
        if not len(aperture) or any(facets[tuple(sorted(t))] != 2 for t in aperture):
            raise ValueError('Port triangles must have two adjacent field tetrahedra')
        points = mesh.points[aperture]
        area = np.linalg.norm(np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0]),axis=1).sum()/2
        expected = port['width_mm'] * (port['signal_z_mm'] - port['reference_z_mm'])
        if abs(area - expected) > 1e-9:
            raise ValueError('Exported port area changed')
        nodes = np.unique(aperture)
        contacts = {}
        for side, z, attr in [('signal',port['signal_z_mm'],port['signal_attribute']),
                              ('reference',port['reference_z_mm'],14)]:
            ends = nodes[np.abs(mesh.points[nodes,2] - z) < 1e-8]
            pec = triangles[physical == attr]
            flat = pec[np.all(np.abs(mesh.points[pec,2] - z) < 1e-8, axis=1)]
            if len(ends) < 2 or not set(ends) <= set(flat.ravel()):
                raise ValueError('Port does not share literal nodes with the intended PEC conductor')
            xy = mesh.points[ends]
            if abs(xy[:,1].min()-port['y_min_mm']) > 1e-8 or abs(xy[:,1].max()-port['y_max_mm']) > 1e-8 or np.max(np.abs(xy[:,0]-port['x_mm'])) > 1e-8:
                raise ValueError('Port contact span changed')
            contacts[side] = {'attribute':attr,'shared_node_count':len(ends)}
        rows.append({'index':port['index'],'area_mm2':float(area),'triangles':len(aperture),
                     'field_tet_adjacency':2,**contacts})
    save(directory / 'mesh-port-audit.json', {'status':'passed','ports':rows})


def build(directory):
    started = time.monotonic()
    model = json.loads((directory / 'model.json').read_text())
    cfg, layers = model['setup'], model['stackup']['layers']
    bottom_t, dielectric_t, top_t = [layers[i]['thickness_mm'] for i in [2,1,0]]
    signal_z = round(bottom_t + dielectric_t, 12)
    height = model['board']['thickness_mm']
    ports = []
    for i, signal in enumerate(model['signals']):
        for x in [signal['x_min_mm']+cfg['port_inset_mm'],signal['x_max_mm']-cfg['port_inset_mm']]:
            ports.append({'index':len(ports)+1,'x_mm':x,'y_min_mm':signal['y_mm']-signal['width_mm']/2,
                          'y_max_mm':signal['y_mm']+signal['width_mm']/2,'width_mm':signal['width_mm'],
                          'signal_z_mm':signal_z,'reference_z_mm':bottom_t,
                          'signal_net':signal['source_trace_id'],'reference_net':model['reference']['net_id'],
                          'signal_attribute':11+i})
    save(directory / 'terminal-map.json', ports)
    gmsh.initialize()
    try:
        gmsh.model.add('circuit-json-straight-pair')
        gmsh.option.setNumber('General.NumThreads',1)
        substrate = box(model['board']['bounds_mm'], bottom_t, dielectric_t)
        copper = [box(model['reference']['bounds_mm'],0,bottom_t)]
        nets = [model['reference']['net_id']]
        expected_copper = gmsh.model.occ.getMass(*copper[0])
        for signal in model['signals']:
            lo = min(signal['x_min_mm']-signal['width_mm']/2,*[p['x_mm']-p['width_mm']/2 for p in signal['pads']])
            hi = max(signal['x_max_mm']+signal['width_mm']/2,*[p['x_mm']+p['width_mm']/2 for p in signal['pads']])
            entity = box([lo,signal['y_mm']-signal['width_mm']/2,hi,signal['y_mm']+signal['width_mm']/2],signal_z,top_t)
            copper.append(entity)
            nets.append(signal['source_trace_id'])
            expected_copper += gmsh.model.occ.getMass(*entity)
        expected_dielectric = gmsh.model.occ.getMass(*substrate)
        x0,y0,x1,y1 = model['board']['bounds_mm']
        pad = cfg['air_padding_mm']
        air = box([x0-pad,y0-pad,x1+pad,y1+pad],-pad,height+2*pad)
        apertures = [port_face(p) for p in ports]
        _, mapping = gmsh.model.occ.fragment([air],[substrate,*copper,*apertures])
        gmsh.model.occ.synchronize()
        def vols(row): return {tag for dim,tag in row if dim == 3}
        copper_nets = {net:vols(row) for net,row in zip(nets,mapping[2:2+len(copper)])}
        metal = set().union(*copper_nets.values())
        dielectric = vols(mapping[1]) - metal
        groups = {'air':vols(mapping[0])-dielectric-metal,'dielectric':dielectric,
                  **{'copper:'+net:tags for net,tags in copper_nets.items()}}
        port_groups = [{tag for dim,tag in row if dim == 2} for row in mapping[2+len(copper):]]
        groups, port_groups, identity = mapped_cleanup(groups,port_groups,audit_path=directory/'cad-identity-audit.json')
        save(directory/'cad-identity-audit.json',identity)
        copper_nets = {name[7:]:tags for name,tags in groups.items() if name.startswith('copper:')}
        metal = set().union(*copper_nets.values())
        actual_copper = sum(gmsh.model.occ.getMass(3,t) for t in metal)
        actual_dielectric = sum(gmsh.model.occ.getMass(3,t) for t in groups['dielectric'])
        if abs(actual_copper-expected_copper)>1e-7 or abs(actual_dielectric-expected_dielectric)>1e-7:
            raise ValueError('Fragmentation changed physical material volume')
        pec = set()
        for net, tags in copper_nets.items():
            faces = {t for d,t in gmsh.model.getBoundary([(3,t) for t in tags],combined=True,oriented=False) if d == 2}
            attr = 14 if net == model['reference']['net_id'] else 11 + [s['source_trace_id'] for s in model['signals']].index(net)
            gmsh.model.addPhysicalGroup(2,sorted(faces),attr,'pec:'+net)
            pec |= faces
        contacts = []
        port_surfaces = set()
        for port,faces in zip(ports,port_groups):
            if not faces or pec & faces: raise ValueError('Invalid port/conductor surface overlap')
            contacts.append(verify_port_contacts(port,faces,copper_nets))
            gmsh.model.addPhysicalGroup(2,sorted(faces),20+port['index'],'port:'+str(port['index']))
            port_surfaces |= faces
        save(directory/'cad-port-audit.json',contacts)
        fields = groups['air'] | groups['dielectric']
        boundary = {t for d,t in gmsh.model.getBoundary([(3,t) for t in sorted(fields)],combined=True,oriented=False) if d == 2}
        outer = boundary - pec
        # Outer faces must coincide with the explicit padded air box, never an internal interface.
        for face in outer:
            b = gmsh.model.getBoundingBox(2,face)
            if not any(abs(b[a]-edge)<2e-7 and abs(b[a+3]-edge)<2e-7 for a,edge in [(0,x0-pad),(0,x1+pad),(1,y0-pad),(1,y1+pad),(2,-pad),(2,height+pad)]):
                raise ValueError('Absorbing attribute includes an internal surface')
        gmsh.model.addPhysicalGroup(2,sorted(outer),13,'outer-air')
        for name,attr in [('air',1),('dielectric',2)]:
            gmsh.model.addPhysicalGroup(3,sorted(groups[name]),attr,name)
        edges = {t for d,t in gmsh.model.getBoundary([(2,t) for t in pec|port_surfaces],combined=False,oriented=False) if d == 1}
        distance = gmsh.model.mesh.field.add('Distance')
        gmsh.model.mesh.field.setNumbers(distance,'CurvesList',sorted(edges))
        gmsh.model.mesh.field.setNumber(distance,'Sampling',50)
        threshold = gmsh.model.mesh.field.add('Threshold')
        for name,value in {'InField':distance,'SizeMin':cfg['mesh']['near_mm'],'SizeMax':cfg['mesh']['far_mm'],'DistMin':0,'DistMax':cfg['mesh']['transition_mm']}.items():
            gmsh.model.mesh.field.setNumber(threshold,name,value)
        gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
        for name,value in {'General.NumThreads':1,'Mesh.MaxNumThreads1D':1,'Mesh.MaxNumThreads2D':1,'Mesh.MaxNumThreads3D':1,'Mesh.MeshSizeExtendFromBoundary':0,'Mesh.MeshSizeFromPoints':0,'Mesh.MeshSizeFromCurvature':0,'Mesh.Algorithm3D':1,'Mesh.RandomSeed':1,'Mesh.MshFileVersion':2.2,'Mesh.Binary':0,'Mesh.SaveAll':0}.items():
            gmsh.option.setNumber(name,value)
        gmsh.write(str(directory/'geometry.brep'))
        gmsh.model.mesh.generate(3)
        tets = [int(t) for v in fields for row in gmsh.model.mesh.getElements(3,v)[1] for t in row]
        quality = float(min(gmsh.model.mesh.getElementQualities(tets,'minSICN')))
        if quality <= 1e-8: raise ValueError('Degenerate field tetrahedra')
        gmsh.write(str(directory/'model.msh'))
        save(directory/'mesh-report.json',{'gmsh_version':gmsh.__version__,'field_tetrahedra':len(tets),'minimum_sicn':quality,
             'copper_volume_mm3':actual_copper,'dielectric_volume_mm3':actual_dielectric,'geometry_approximation_mm':0,
             'elapsed_seconds':time.monotonic()-started,'conductor_interiors':'excluded','status':'passed'})
    finally:
        gmsh.finalize()
    audit_exported_mesh(directory,ports)


if __name__ == '__main__':
    build(Path(sys.argv[1]).resolve())
