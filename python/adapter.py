"""Extend the real exporter's conformal BREP with air and EM port surfaces.

PCB copper/dielectric geometry is imported, never reconstructed here. The
extended domain must be remeshed because the exporter supplies no air or ports.
"""
import json
from pathlib import Path
import sys
import time
from collections import Counter

import gmsh
import meshio
import numpy as np


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def aperture(p):
    x, y0, y1, z0, z1 = [p[k] for k in ['x_mm','y_min_mm','y_max_mm','reference_z_mm','signal_z_mm']]
    points = [gmsh.model.occ.addPoint(*v) for v in [(x,y0,z0),(x,y1,z0),(x,y1,z1),(x,y0,z1)]]
    lines = [gmsh.model.occ.addLine(points[i],points[(i+1)%4]) for i in range(4)]
    return (2, gmsh.model.occ.addPlaneSurface([gmsh.model.occ.addCurveLoop(lines)]))


def source_ownership(board):
    manifest = json.loads((board/'mesh-manifest.json').read_text())
    report = json.loads((board/'report.json').read_text())
    validation = json.loads((board/'validation.json').read_text())
    if not validation['passed'] or not validation['pcbChecksComplete'] or manifest['units'] != 'mm':
        raise ValueError('A completely validated mm exporter mesh is required')
    gmsh.open(str(board/'board.msh'))
    gmsh.model.setCurrent(gmsh.model.getCurrent())
    tags = {t for _,t in gmsh.model.getEntities(3)}
    if tags != {v['tag'] for v in manifest['volumes']}:
        raise ValueError('Exporter manifest does not cover its mesh volume entities')
    for v in manifest['volumes']:
        if not any(v['tag'] in m['volumes'] and v['name'] == m['name'] for m in report['materials']):
            raise ValueError('Exporter report/manifest material ownership differs')
        v['bounds_mm'] = list(gmsh.model.getBoundingBox(3,v['tag']))
    gmsh.model.remove()
    gmsh.model.add('palace-extension')
    gmsh.model.occ.importShapes(str(board/'board.brep'))
    gmsh.model.occ.synchronize()
    available = {t for _,t in gmsh.model.getEntities(3)}
    if len(available) != len(manifest['volumes']):
        raise ValueError('BREP/mesh volume counts differ')
    groups, identity = {}, []
    for v in manifest['volumes']:
        candidates = [t for t in available
                      if np.max(np.abs(np.array(gmsh.model.getBoundingBox(3,t))-v['bounds_mm'])) < 2e-6
                      and abs(gmsh.model.occ.getMass(3,t)-v['volumeMm3']) < max(1e-9,v['volumeMm3']*1e-7)]
        if len(candidates) != 1:
            raise ValueError(f'BREP identity is ambiguous or missing for {v["name"]}')
        t = candidates[0]
        available.remove(t)
        groups.setdefault(v['name'],set()).add(t)
        identity.append({'source_tag':v['tag'],'imported_tag':t,'name':v['name'],
                         'volume_mm3':v['volumeMm3'],'bounds_mm':v['bounds_mm']})
    if available: raise ValueError('Unowned imported CAD')
    return groups, identity


def audit_mesh(directory, ports):
    mesh = meshio.read(directory/'model.msh')
    triangles, attrs, tets = [], [], []
    for i,b in enumerate(mesh.cells):
        if b.type == 'triangle':
            triangles.extend(b.data); attrs.extend(mesh.cell_data['gmsh:physical'][i])
        elif b.type == 'tetra': tets.extend(b.data)
        else: raise ValueError('Only linear triangles/tetrahedra are supported')
    triangles, attrs, tets = np.asarray(triangles),np.asarray(attrs),np.asarray(tets)
    facets = Counter(tuple(sorted(t[list(indices)])) for t in tets
                     for indices in [(0,1,2),(0,1,3),(0,2,3),(1,2,3)])
    if any(count > 2 for count in facets.values()): raise ValueError('Nonmanifold field mesh')
    for attr, incidence in [(11,1),(12,1),(14,1),(13,1),(15,2)]:
        faces = triangles[attrs == attr]
        if not len(faces) or any(facets[tuple(sorted(f))] != incidence for f in faces):
            raise ValueError(f'Boundary/interface attribute {attr} has wrong tetrahedron adjacency')
    rows = []
    for p in ports:
        faces = triangles[attrs == 20+p['index']]
        if not len(faces) or any(facets[tuple(sorted(f))] != 2 for f in faces):
            raise ValueError('Port must have two adjacent field tetrahedra')
        xyz = mesh.points[faces]
        area = float(np.linalg.norm(np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0]),axis=1).sum()/2)
        if abs(area-p['width_mm']*(p['signal_z_mm']-p['reference_z_mm'])) > 1e-9:
            raise ValueError('Port aperture area changed')
        nodes = np.unique(faces)
        contacts = {}
        for side,z,attr in [('signal',p['signal_z_mm'],p['signal_attribute']),('reference',p['reference_z_mm'],14)]:
            ends = nodes[np.abs(mesh.points[nodes,2]-z)<1e-8]
            pec = triangles[attrs == attr]
            flat = pec[np.all(np.abs(mesh.points[pec,2]-z)<1e-8,axis=1)]
            if len(ends)<2 or not set(ends)<=set(flat.ravel()):
                raise ValueError('Port must share literal nodes with its selected PEC conductor')
            xyz = mesh.points[ends]
            if np.max(abs(xyz[:,0]-p['x_mm']))>1e-8 or abs(xyz[:,1].min()-p['y_min_mm'])>1e-8 or abs(xyz[:,1].max()-p['y_max_mm'])>1e-8:
                raise ValueError('Port contact span changed')
            contacts[side] = {'attribute':attr,'shared_node_count':len(ends)}
        rows.append({'index':p['index'],'triangles':len(faces),'area_mm2':area,'field_tet_adjacency':2,**contacts})
    save(directory/'mesh-port-audit.json',{'status':'passed','ports':rows,'material_interfaces':'two shared field tetrahedra per saved triangle'})


def build(directory):
    started = time.monotonic()
    prepared = json.loads((directory/'model.json').read_text())
    exported = json.loads((directory/'exporter-input.json').read_text())
    cfg = prepared['setup']
    layers = exported['model']['multilayer']['stackup']['copperLayers']
    signal_z = next(l['zMin'] for l in layers if l['name']=='top')
    reference_z = next(l['zMax'] for l in layers if l['name']=='bottom')
    zmin,zmax = min(l['zMin'] for l in layers),max(l['zMax'] for l in layers)
    ports = []
    for i,s in enumerate(prepared['signals']):
        for x in [s['x_min_mm']+cfg['port_inset_mm'],s['x_max_mm']-cfg['port_inset_mm']]:
            ports.append({'index':len(ports)+1,'x_mm':x,'y_min_mm':s['y_mm']-s['width_mm']/2,
                          'y_max_mm':s['y_mm']+s['width_mm']/2,'width_mm':s['width_mm'],
                          'signal_z_mm':signal_z,'reference_z_mm':reference_z,
                          'signal_net':exported['signal_net_ids'][i],'reference_net':exported['reference_net_id'],
                          'signal_attribute':11+i})
    save(directory/'terminal-map.json',ports)
    gmsh.initialize()
    try:
        gmsh.option.setNumber('General.NumThreads',1)
        original, identity = source_ownership(directory/'board')
        expected = {name:sum(gmsh.model.occ.getMass(3,t) for t in tags) for name,tags in original.items()}
        names, entities = [], []
        for name,tags in original.items():
            for t in sorted(tags): names.append(name);entities.append((3,t))
        x0,y0,x1,y1 = prepared['board']['bounds_mm']
        pad = cfg['air_padding_mm']
        bounds = [x0-pad,y0-pad,zmin-pad,x1+pad,y1+pad,zmax+pad]
        air = (3,gmsh.model.occ.addBox(*bounds[:3],*[bounds[i+3]-bounds[i] for i in range(3)]))
        box_volume = gmsh.model.occ.getMass(*air)
        surfaces = [aperture(p) for p in ports]
        _,mapping = gmsh.model.occ.fragment([air],[*entities,*surfaces])
        gmsh.model.occ.synchronize()
        groups = {name:set() for name in original}
        for name,row in zip(names,mapping[1:1+len(entities)]): groups[name].update(t for d,t in row if d==3)
        all_board = set().union(*groups.values())
        if sum(map(len,groups.values())) != len(all_board): raise ValueError('Fragmented materials overlap')
        air_volumes = {t for d,t in mapping[0] if d==3}-all_board
        actual = {name:sum(gmsh.model.occ.getMass(3,t) for t in tags) for name,tags in groups.items()}
        if any(abs(actual[n]-v)>max(1e-8,v*1e-7) for n,v in expected.items()): raise ValueError('Extension changed exporter material volume')
        air_volume = sum(gmsh.model.occ.getMass(3,t) for t in air_volumes)
        if abs(air_volume+sum(actual.values())-box_volume)>box_volume*1e-7: raise ValueError('Air/board coverage differs from explicit box')
        copper_names = ['copper:'+n for n in [*exported['signal_net_ids'],exported['reference_net_id']]]
        if set(n for n in groups if n.startswith('copper:')) != set(copper_names): raise ValueError('Unselected copper in exporter output')
        dielectric_names = [n for n in groups if n.startswith('dielectric:')]
        if len(dielectric_names)!=1: raise ValueError('Only one explicit dielectric is supported')
        dielectric = groups[dielectric_names[0]]
        pec = set()
        for name,attr in zip(copper_names,[11,12,14]):
            faces = {t for d,t in gmsh.model.getBoundary([(3,t) for t in groups[name]],combined=True,oriented=False) if d==2}
            gmsh.model.addPhysicalGroup(2,sorted(faces),attr,name)
            pec |= faces
        port_faces = []
        for p,row in zip(ports,mapping[1+len(entities):]):
            faces = {t for d,t in row if d==2}
            if not faces or faces & pec: raise ValueError('Port/PEC surface overlap')
            if abs(sum(gmsh.model.occ.getMass(2,t) for t in faces)-p['width_mm']*(signal_z-reference_z))>1e-9: raise ValueError('CAD port area changed')
            gmsh.model.addPhysicalGroup(2,sorted(faces),20+p['index'],'port:'+str(p['index']))
            port_faces.append(faces)
        fields = air_volumes|dielectric
        boundary = {t for d,t in gmsh.model.getBoundary([(3,t) for t in fields],combined=True,oriented=False) if d==2}
        outer = boundary-pec
        for f in outer:
            b = gmsh.model.getBoundingBox(2,f)
            if not any(abs(b[i]-edge)<2e-7 and abs(b[i+3]-edge)<2e-7 for i in range(3) for edge in [bounds[i],bounds[i+3]]):
                raise ValueError('Absorbing face is not on the explicit air box')
        gmsh.model.addPhysicalGroup(2,sorted(outer),13,'outer-air')
        interfaces = {t for _,t in gmsh.model.getEntities(2) if len(gmsh.model.getAdjacencies(2,t)[0])==2 and set(gmsh.model.getAdjacencies(2,t)[0])<=fields}-set().union(*port_faces)
        gmsh.model.addPhysicalGroup(2,sorted(interfaces),15,'field-interfaces')
        gmsh.model.addPhysicalGroup(3,sorted(air_volumes),1,'air')
        gmsh.model.addPhysicalGroup(3,sorted(dielectric),2,dielectric_names[0])
        edges = {t for d,t in gmsh.model.getBoundary([(2,t) for t in pec|set().union(*port_faces)],combined=False,oriented=False) if d==1}
        distance = gmsh.model.mesh.field.add('Distance')
        gmsh.model.mesh.field.setNumbers(distance,'CurvesList',sorted(edges))
        gmsh.model.mesh.field.setNumber(distance,'Sampling',50)
        threshold = gmsh.model.mesh.field.add('Threshold')
        for key,val in {'InField':distance,'SizeMin':cfg['mesh']['near_mm'],'SizeMax':cfg['mesh']['far_mm'],'DistMin':0,'DistMax':cfg['mesh']['transition_mm']}.items(): gmsh.model.mesh.field.setNumber(threshold,key,val)
        gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
        for key,val in {'General.NumThreads':1,'Mesh.MaxNumThreads1D':1,'Mesh.MaxNumThreads2D':1,'Mesh.MaxNumThreads3D':1,'Mesh.MeshSizeExtendFromBoundary':0,'Mesh.MeshSizeFromPoints':0,'Mesh.MeshSizeFromCurvature':0,'Mesh.Algorithm3D':1,'Mesh.RandomSeed':1,'Mesh.MshFileVersion':2.2,'Mesh.Binary':0,'Mesh.SaveAll':0}.items(): gmsh.option.setNumber(key,val)
        gmsh.write(str(directory/'geometry.brep'))
        gmsh.model.mesh.generate(3)
        tets = [int(t) for v in fields for row in gmsh.model.mesh.getElements(3,v)[1] for t in row]
        quality = float(min(gmsh.model.mesh.getElementQualities(tets,'minSICN')))
        if quality<=1e-8: raise ValueError('Degenerate field tetrahedra')
        gmsh.write(str(directory/'model.msh'))
        save(directory/'mesh-report.json',{'status':'passed','gmsh_version':gmsh.__version__,'field_tetrahedra':len(tets),'minimum_sicn':quality,
             'geometry_source':'circuit-json-to-gmsh conformal board.brep','source_volume_identity':identity,
             'source_material_volumes_mm3':expected,'extended_material_volumes_mm3':actual,'air_volume_mm3':air_volume,
             'air_bounds_mm':bounds,'conductor_interiors':'excluded','geometry_approximation_mm':0,'elapsed_seconds':time.monotonic()-started})
    finally: gmsh.finalize()
    audit_mesh(directory,ports)


if __name__=='__main__': build(Path(sys.argv[1]).resolve())
